"""读取迁入的旧 ADC 基线；只报告码值和传输状态，不标注 DUT 故障。"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from pwm_client import command
from adc_features import code_statistics


def verify(host, output):
    """分块回收实际样本，核对格式、帧号、长度和连续性状态。"""
    # 仅接收总线不能证明ADC供电或模拟转换；旧字段保留为未验证。
    report = {"host": host, "passed": False, "adc_sampled": False,
              "adc_bus_captured": False, "adc_module_powered": None,
              "adc_conversion_verified": False, "analog_signal_verified": False,
              "input_source_confirmed": False, "external_dut_measured": False,
              "calibrated": False, "wujing_pl_executed": False, "npu_executed": False,
              "commands": [], "frames": []}
    output.parent.mkdir(parents=True, exist_ok=True)

    def run(text, require=True):
        reply = command(host, text)
        # 分块原数据另存二进制；命令记录保留读块身份，不复制大段十六进制。
        brief = {key: value for key, value in reply.items() if key != "hex"}
        report["commands"].append({"command": text, "response": brief})
        if require and not reply.get("ok"):
            raise RuntimeError("命令失败：{}：{}".format(text, reply))
        return reply

    try:
        run("HALT")
        info = run("INFO")
        assert info["adc_ready"] and not info["external_output_enabled"]
        before = run("ADC_STATUS")
        assert before["baseline_points"] == 8192 and before["sample_rate_hz"] == 25000000
        assert before["encoding"] == "signed16_le" and not before["calibrated"]
        assert not run("ADC_CAPTURE 1", False)["ok"]
        previous_id = None
        for index in range(3):
            record_id = run("ADC_CAPTURE 8192")["record_id"]
            report["adc_bus_captured"] = True
            status = run("ADC_STATUS")
            assert status["record_id"] == record_id and status["sample_count"] == 8192
            assert status["flags"] == 8, "旧链路报告数据不连续或缺少格式核验"
            if previous_id is not None:
                assert run("ADC_READ {} 0 1".format(previous_id), False)["code"] == -4
            data = bytearray()
            for start in range(0, 8192, 32):
                block = run("ADC_READ {} {} 32".format(record_id, start))
                assert block["record_id"] == record_id and block["start"] == start and block["count"] == 32
                received = bytes.fromhex(block["hex"])
                assert len(received) == 64
                data.extend(received)
            assert len(data) == 16384
            samples = struct.unpack("<8192h", data)
            assert all(-2048 <= x <= 2047 for x in samples)
            # 复用已验证的整数矩统计；旧驱动只给OTR累计，不能虚构其样点位置。
            features = code_statistics(list(samples), [x + 2048 for x in samples], [])
            features["overrange_count"] = status["overrange_count"]
            features["overrange_locations_available"] = False
            assert not run("ADC_READ {} 8191 2".format(record_id), False)["ok"]
            assert not run("ADC_READ {} 0 33".format(record_id), False)["ok"]
            path = output.parent / ("adc_{:02d}.bin".format(index))
            path.write_bytes(data)
            report["frames"].append({"record_id": record_id, "sample_count": 8192,
                                     "sha256": hashlib.sha256(data).hexdigest(), "binary_path": str(path),
                                     "minimum_code": min(samples), "maximum_code": max(samples),
                                     "mean_code": sum(samples) / 8192.0, "status": status,
                                     "cpu_features": features})
            previous_id = record_id
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        try:
            run("HALT")
            report["final_mode_status"] = run("MODE_STATUS")
            assert report["final_mode_status"]["mode"] == 0
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
    print("FDW_ADC_VERIFY=" + json.dumps(result, ensure_ascii=True), flush=True)
    if not result["passed"]:
        raise SystemExit(1)
