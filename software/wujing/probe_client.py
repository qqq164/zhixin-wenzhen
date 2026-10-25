"""通过内部回环验证有限 PWM 与模式切换；不启用外部 PWM。"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from pwm_client import command


def verify(host, output):
    """逐点核对内部有限波形，结束进入 STOP 并检查旧帧失效。"""
    report = {"passed": False, "host": host, "external_pwm_enabled": False,
              "external_dut_measured": False, "adc_sampled": False,
              "wujing_pl_executed": False, "npu_executed": False,
              "commands": [], "frames": []}
    output.parent.mkdir(parents=True, exist_ok=True)

    def run(text, require=True):
        reply = command(host, text)
        report["commands"].append({"command": text, "response": reply})
        if require and not reply.get("ok"):
            raise RuntimeError("命令失败：{}：{}".format(text, reply))
        return reply

    try:
        info = run("INFO")
        assert info.get("protocol", 1) == 2 and not info["external_output_enabled"]
        run("HALT")
        assert not run("PWM_ENABLE 1", False)["ok"]
        assert not run("MODE_SET 4 1 0", False)["ok"]
        # 每个窗口覆盖完整输出和停止后的低电平，包含 0% 和 100%。
        for index, (period, high, cycles) in enumerate(((16, 8, 5), (13, 0, 4), (11, 11, 3))):
            run("MODE_SET 1 1 1")
            count = period * cycles + period
            armed = run("DIG_ARM 1 {} 1".format(count))
            record_id = armed["record_id"]
            run("PWM_BURST {} {} {}".format(period, high, cycles))
            deadline = time.monotonic() + 3
            while True:
                status = run("DIG_STATUS")
                if status["flags"] & 15 == 12:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("有限 PWM 记录未完成")
                time.sleep(0.01)
            assert status["mode"] == 1 and status["finite"] == 1
            assert status["source"] == "internal_burst"
            data = bytearray()
            for start in range(0, count, 32):
                reply = run("DIG_READ {} {} {}".format(record_id, start, min(32, count - start)))
                data.extend(bytes.fromhex(reply["hex"]))
            expected = bytes(int(i < period * cycles and i % period < high) |
                             (int(i == 0) << 1) for i in range(count))
            mismatch = [i for i in range(count) if data[i] != expected[i]]
            path = output.parent / ("burst_{:02d}.bin".format(index))
            path.write_bytes(data)
            burst = run("PWM_BURST_STATUS")
            report["frames"].append({"record_id": record_id, "period_ticks": period,
                                     "high_ticks": high, "cycles": cycles,
                                     "sample_count": count, "sha256": hashlib.sha256(data).hexdigest(),
                                     "binary_path": str(path), "status": status, "burst_status": burst,
                                     "mismatch_indices": mismatch})
            assert not mismatch and burst["completed"] == cycles and burst["flags"] & 8
            run("MODE_SET 2 0 0")
            assert run("DIG_READ {} 0 1".format(record_id), False)["code"] == -4
            assert not run("PWM_ENABLE 1", False)["ok"]
            assert not run("OUTPUT_ENABLE 1", False)["ok"]
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        try:
            run("HALT")
            report["final_mode_status"] = run("MODE_STATUS")
            assert not report["final_mode_status"]["mode"]
            assert not report["final_mode_status"]["output_enabled"]
        except Exception as error:
            report["cleanup_error"] = str(error)
            report["passed"] = False
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.126.50")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.host, args.output)
    print("FDW_PROBE_VERIFY=" + json.dumps(result, ensure_ascii=True), flush=True)
    if not result["passed"]:
        raise SystemExit(1)
