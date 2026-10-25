"""验证内部 PWM 的真实 PL 记录和 TCP 读回，不代表外部 DUT 或 ADC 采样。"""

import argparse
import csv
import hashlib
import json
import time
import zlib
from pathlib import Path

from pwm_client import command


def verify(host, output):
    """逐帧比对完整记录，保存数据与失败信息，结束关闭 PWM 和采集。"""
    report = {"host": host, "passed": False, "source": "internal_pwm",
              "adc_sampled": False, "external_dut_measured": False,
              "wujing_pl_executed": False, "npu_executed": False,
              "frames": [], "commands": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    info = {}

    def run(text):
        response = command(host, text)
        report["commands"].append({"command": text, "response": response})
        return response

    def require(text):
        response = run(text)
        if not response.get("ok"):
            raise AssertionError("命令失败：{}，回复：{}".format(text, response))
        return response

    def wait_status(text, predicate):
        deadline = time.monotonic() + 3.0
        while True:
            status = require(text)
            if predicate(status):
                return status
            if time.monotonic() > deadline:
                raise TimeoutError("状态等待超时：{}，最后回复：{}".format(text, status))
            time.sleep(0.01)

    try:
        info = require("INFO")
        assert info["project"] == "FDW_PWM"
        if info.get("protocol", 1) >= 2:
            require("MODE_SET 1 1 0")
            info = require("INFO")
            assert not info["external_output_enabled"]
        else:
            assert not info["external_pwm_pin"]
        assert info["digital_source"] == "internal_pwm"
        report["clock_hz"] = info["clock_hz"]
        require("PWM_ENABLE 0")
        require("DIG_CANCEL")
        capacity = require("DIG_STATUS")
        assert capacity["sample_width"] == 2 and capacity["capacity"] >= 1024
        assert not run("DIG_ARM 0 4 1")["ok"]
        assert not run("DIG_ARM 1 0 1")["ok"]
        assert not run("DIG_ARM 1 {} 1".format(capacity["capacity"] + 1))["ok"]
        assert not run("DIG_TRIGGER")["ok"]
        previous_id = None
        cases = [(1000, 400, 1, 1024), (1000, 400, 4, 1024),
                 (37, 11, 3, 511), (5, 0, 1, 32), (5, 5, 2, 33)]
        for frame_index, (period, high, interval, count) in enumerate(cases):
            require("PWM_ENABLE 0")
            require("PWM_SET {} {}".format(period, high))
            wait_status("PWM_STATUS", lambda s: s["period_ticks"] == period and
                        s["high_ticks"] == high and not (s["flags"] & 2))
            arm = require("DIG_ARM {} {} 1".format(interval, count))
            record_id = arm["record_id"]
            assert record_id != previous_id and record_id != 0
            if previous_id is not None:
                assert run("DIG_READ {} 0 1".format(previous_id))["code"] == -4
            status = require("DIG_STATUS")
            assert status["record_id"] == record_id and status["flags"] & 1
            assert status["captured_count"] == 0
            assert run("DIG_ARM 1 1 1")["code"] == -2
            require("PWM_ENABLE 1")
            status = wait_status("DIG_STATUS", lambda s: (s["flags"] & 15) == 12)
            require("PWM_ENABLE 0")
            assert status["record_id"] == record_id and status["interval_ticks"] == interval
            assert status["sample_count"] == count and status["captured_count"] == count
            data = bytearray()
            for start in range(0, count, 32):
                chunk_count = min(32, count - start)
                chunk = require("DIG_READ {} {} {}".format(record_id, start, chunk_count))
                assert chunk["record_id"] == record_id and chunk["start"] == start
                assert chunk["count"] == chunk_count
                block = bytes.fromhex(chunk["hex"])
                assert len(block) == chunk_count
                data.extend(block)
            expected = bytes((int((i * interval) % period < high) |
                              (int((i * interval) % period == 0) << 1)) for i in range(count))
            mismatch = [i for i, (actual, wanted) in enumerate(zip(data, expected)) if actual != wanted]
            prefix = "frame_{:02d}".format(frame_index)
            binary_path = output.parent / (prefix + ".bin")
            binary_path.write_bytes(data)
            csv_path = output.parent / (prefix + ".csv")
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["样点索引", "相对时钟数", "PWM位", "周期事件位", "期望值", "实际值"])
                for i, sample in enumerate(data):
                    writer.writerow([i, i * interval, sample & 1, (sample >> 1) & 1, expected[i], sample])
            frame = {"record_id": record_id, "period_ticks": period, "high_ticks": high,
                     "interval_ticks": interval, "sample_count": count,
                     "nominal_record_rate_hz": info["clock_hz"] / interval,
                     "first_to_last_ticks": (count - 1) * interval,
                     "sha256": hashlib.sha256(data).hexdigest(),
                     "host_calculated_crc32": "{:08x}".format(zlib.crc32(data) & 0xffffffff),
                     "binary_path": str(binary_path), "csv_path": str(csv_path),
                     "mismatch_indices": mismatch, "status": status}
            report["frames"].append(frame)
            assert len(data) == count and not mismatch, "完整记录与 PWM 预期不符"
            assert not run("DIG_READ {} {} 1".format(record_id, count))["ok"]
            assert not run("DIG_READ {} 0 33".format(record_id))["ok"]
            previous_id = record_id
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        cleanup_errors = []
        for text in ("PWM_ENABLE 0", "DIG_CANCEL"):
            try:
                require(text)
            except Exception as error:
                cleanup_errors.append(str(error))
        if info.get("protocol", 1) >= 2:
            try:
                require("HALT")
                report["final_mode_status"] = require("MODE_STATUS")
                assert report["final_mode_status"]["mode"] == 0
                assert not report["final_mode_status"]["output_enabled"]
            except Exception as error:
                cleanup_errors.append(str(error))
        try:
            report["final_pwm_status"] = require("PWM_STATUS")
            report["final_capture_status"] = require("DIG_STATUS")
            assert not report["final_pwm_status"]["enabled"]
            assert not (report["final_capture_status"]["flags"] & 0x83)
        except Exception as error:
            cleanup_errors.append(str(error))
        if cleanup_errors:
            report["cleanup_errors"] = cleanup_errors
            report["passed"] = False
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if cleanup_errors and "error" not in report:
            raise RuntimeError("测试结束状态未确认：" + "; ".join(cleanup_errors))
    return report


def main():
    """兼容悟净 Python 3.8；输出目录必须由调用者明确指定。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.126.50")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.host, args.output)
    print("FDW_DIGITAL_VERIFY=" + json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
