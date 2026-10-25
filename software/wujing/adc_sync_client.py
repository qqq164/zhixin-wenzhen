"""验证DIG4内部PWM周期事件触发ADC；保持外部PWM输出关闭。"""

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path

from adc_features import code_statistics
from pwm_client import command


def verify(host, output):
    """回收真实ADC总线记录，分别保存事件触发与立即模式的证据。"""
    output = Path(output)
    if output.exists():
        raise ValueError("报告已经存在，请指定新的证据目录")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "host": host, "passed": False, "hardware_revision": "DIG4",
        "adc_bus_captured": False, "adc_module_powered": None,
        "adc_conversion_verified": False, "analog_signal_verified": False,
        "input_source_confirmed": False, "external_pwm_enabled": False,
        "external_dut_measured": False, "calibrated": False,
        "analog_phase_reference_verified": False,
        "wujing_pl_executed": False, "npu_executed": False,
        "commands": [], "frames": [],
    }

    def run(text, required=True):
        response = command(host, text)
        report["commands"].append({
            "command": text,
            "response": {key: value for key, value in response.items() if key != "hex"},
        })
        if required and not response.get("ok"):
            raise RuntimeError("命令失败：{}：{}".format(text, response))
        return response

    def halt():
        run("HALT")
        state = run("MODE_STATUS")
        if state["mode"] != 0 or state["output_enabled"] or state["pwm_active"]:
            raise RuntimeError("停止后模式或输出状态异常")
        return state

    def receive(index, synchronous, route, period, high):
        request = "ADC_CAPTURE_SYNC 8192" if synchronous else "ADC_CAPTURE 8192"
        record_id = run(request)["record_id"]
        status = run("ADC_STATUS")
        expected_trigger = "pwm_period_event" if synchronous else "software_immediate"
        if (record_id == 0 or status["record_id"] != record_id or
                status["sample_count"] != 8192 or status["flags"] != 8 or
                status["encoding"] != "signed16_le" or status["sample_rate_hz"] != 25000000 or
                status["trigger"] != expected_trigger or status["mode"] != route["mode"] or
                status["route_generation"] != route["generation"]):
            raise RuntimeError("ADC帧的身份、连续性或触发元数据不符")
        if synchronous and status["trigger_status"] & 0x50 != 0x50:
            raise RuntimeError("同步帧缺少周期事件接受或事件模式确认")
        if not synchronous and status["trigger_status"] != 0:
            raise RuntimeError("立即模式残留上次触发状态")
        data = bytearray()
        for start in range(0, 8192, 32):
            block = run("ADC_READ {} {} 32".format(record_id, start))
            received = bytes.fromhex(block["hex"])
            if (block["record_id"] != record_id or block["start"] != start or
                    block["count"] != 32 or len(received) != 64):
                raise RuntimeError("ADC分块身份或长度不符")
            data.extend(received)
        after = run("ADC_STATUS")
        if any(after[key] != status[key] for key in
               ("record_id", "sample_count", "flags", "trigger", "trigger_status", "mode", "route_generation")):
            raise RuntimeError("读回期间ADC帧状态改变")
        samples = list(struct.unpack("<8192h", data))
        if not all(-2048 <= code <= 2047 for code in samples):
            raise RuntimeError("ADC码值超出12位转换范围")
        features = code_statistics(samples, [code + 2048 for code in samples], [])
        features["overrange_count"] = status["overrange_count"]
        features["overrange_locations_available"] = False
        binary = output.parent / "adc_sync_{:02d}.bin".format(index)
        if binary.exists():
            raise ValueError("二进制记录已经存在，停止覆盖")
        binary.write_bytes(data)
        report["frames"].append({
            "record_id": record_id, "sample_count": 8192, "trigger": expected_trigger,
            "period_ticks": period, "high_ticks": high,
            "sha256": hashlib.sha256(data).hexdigest(), "binary_path": str(binary),
            "status": status, "cpu_features": features,
        })
        report["adc_bus_captured"] = True
        return record_id

    try:
        info = run("INFO")
        if (info.get("project") != "FDW_PWM" or info.get("hardware_revision") != "DIG4" or
                info.get("protocol") != 2 or not info.get("adc_pwm_sync") or not info.get("adc_ready")):
            raise RuntimeError("当前不是配套DIG4同步ADC服务，停止验证")
        report["info"] = info
        halt()
        if run("ADC_CAPTURE_SYNC 8192", False).get("code") != -19:
            raise RuntimeError("停止状态必须拒绝无PWM事件的同步采集")
        for index, (period, high) in enumerate(((1000, 400), (2000, 0), (2000, 2000))):
            # 内部模式固定关闭管脚输出；0%/100%仍应有周期事件。
            run("MODE_SET 1 1 0")
            run("PWM_SET {} {}".format(period, high))
            time.sleep(0.01)
            run("PWM_ENABLE 1")
            route = run("MODE_STATUS")
            if (route["mode"] != 1 or route["output_enabled"] or route["finite"] or
                    route["trigger_source"] != 1 or not route["pwm_active"] or route["settling"]):
                raise RuntimeError("同步采集的内部PWM模式未就绪")
            record_id = receive(index, True, route, period, high)
            halt()
            if run("ADC_READ {} 0 1".format(record_id), False).get("code") != -4:
                raise RuntimeError("停止切换后旧ADC帧没有失效")
        # 最后退回旧立即采集，证明触发状态和等待请求不会污染下一帧。
        route = run("MODE_STATUS")
        receive(3, False, route, None, None)
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        try:
            report["final_mode_status"] = halt()
        except Exception as error:
            report["cleanup_error"] = str(error)
            report["passed"] = False
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.126.50")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.host, args.output)
    print("FDW_ADC_SYNC_VERIFY=" + json.dumps(result, ensure_ascii=True), flush=True)
    if not result["passed"]:
        raise SystemExit(1)
