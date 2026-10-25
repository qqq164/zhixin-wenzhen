"""验证已确认的 W10→V11 杜邦线回环；只测 D0，结束关闭外部输出。"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from pwm_client import command
from digital_features import channel_features


def verify(host, output, fine=False):
    """保存真实杜邦线回环数据，核对电平、时间参数、触发和取消。"""
    if output.exists():
        raise ValueError("报告已存在，请指定新的证据目录")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "host": host, "source": "external_w10_v11_loopback",
              "connection_confirmed_by_user": True, "external_pwm_enabled": False,
              "fine_resolution_test": fine,
              "verified_input_bits": [0], "unconnected_input_bits": [1],
              "external_dut_measured": False, "training_eligible": False,
              "adc_sampled": False, "wujing_pl_executed": False, "npu_executed": False,
              "commands": [], "frames": []}

    def run(text, require=True):
        reply = command(host, text)
        brief = {key: value for key, value in reply.items() if key != "hex"}
        report["commands"].append({"command": text, "response": brief})
        if require and not reply.get("ok"):
            raise RuntimeError("命令失败：{}：{}".format(text, reply))
        return reply

    def wait_status(text, predicate):
        deadline = time.monotonic() + 3
        while True:
            status = run(text)
            if predicate(status):
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError("等待超时：{}：{}".format(text, status))
            time.sleep(0.01)

    def prepare(trigger, finite):
        run("MODE_SET 3 {} {}".format(trigger, finite))
        assert not run("MODE_STATUS")["output_enabled"]
        run("OUTPUT_ENABLE 1")
        report["external_pwm_enabled"] = True
        status = run("MODE_STATUS")
        assert status["mode"] == 3 and status["output_enabled"]

    def collect(name, record_id, interval, count, period, high, trigger, cycles=None):
        status = wait_status("DIG_STATUS", lambda s: (s["flags"] & 15) == 12)
        assert status["record_id"] == record_id and status["sample_count"] == count
        assert status["captured_count"] == count and status["interval_ticks"] == interval
        assert status["source"] == "external_digital" and status["mode"] == 3
        assert status["trigger_source"] == trigger and status["finite"] == int(cycles is not None)
        data = bytearray()
        for start in range(0, count, 32):
            size = min(32, count - start)
            block = run("DIG_READ {} {} {}".format(record_id, start, size))
            assert block["record_id"] == record_id and block["start"] == start and block["count"] == size
            raw = bytes.fromhex(block["hex"])
            assert len(raw) == size and all(sample <= 3 for sample in raw)
            data.extend(raw)
        assert len(data) == count
        features = channel_features(data, 0, interval, report["clock_hz"])
        path = output.parent / (name + ".bin")
        path.write_bytes(data)
        frame = {"name": name, "record_id": record_id, "sample_count": count,
                 "interval_ticks": interval, "period_ticks": period, "high_ticks": high,
                 "trigger_source": trigger, "cycles": cycles, "status": status,
                 "sha256": hashlib.sha256(data).hexdigest(), "binary_path": str(path),
                 "d0_sha256": hashlib.sha256(bytes(x & 1 for x in data)).hexdigest(),
                 "cpu_features": features, "checks_passed": False}
        report["frames"].append(frame)
        # D1未接线，只存原始读数，不比较其状态或用于训练。
        if cycles is None:
            if high in (0, period):
                assert features["steady_level"] == int(high != 0)
            else:
                assert bool(data[0] & 1) == (trigger == 2)
                assert len(features["period_ticks"]) >= 3
                assert features["complete_high_width_ticks"] and features["complete_low_width_ticks"]
                for actual in features["period_ticks"]:
                    assert abs(actual - period) <= interval + 1, "外部周期不符"
                for actual in features["complete_high_width_ticks"]:
                    assert abs(actual - high) <= interval + 1, "外部高电平宽度不符"
                for actual in features["complete_low_width_ticks"]:
                    assert abs(actual - (period - high)) <= interval + 1, "外部低电平宽度不符"
        else:
            levels = [sample & 1 for sample in data]
            # 边界容许一个记录间隔：跨域同步和离散采样不能当作模拟相位标定。
            expected = [int(i * interval < period * cycles and i * interval % period < high)
                        for i in range(count)]
            boundaries = [0, period * cycles]
            boundaries.extend(cycle * period for cycle in range(cycles))
            boundaries.extend(cycle * period + high for cycle in range(cycles))
            mismatch = [i for i, (actual, wanted) in enumerate(zip(levels, expected))
                        if actual != wanted]
            hard_mismatch = [i for i in mismatch
                             if min(abs(i * interval - edge) for edge in boundaries) > interval + 1]
            frame["boundary_mismatch_indices"] = mismatch
            frame["hard_mismatch_indices"] = hard_mismatch
            assert not hard_mismatch and not any(levels[-10:]), "有限输出或停止后的低电平不符"
            if high == 0:
                assert not any(levels)
            else:
                assert levels[0] == 1
                assert len(features["falling_indices"]) == (1 if high == period else cycles)
                assert len(features["rising_indices"]) == (0 if high == period else cycles - 1)
            burst = run("PWM_BURST_STATUS")
            frame["burst_status"] = burst
            assert burst["completed"] == cycles and burst["flags"] & 8
        frame["checks_passed"] = True
        return frame

    try:
        run("HALT")
        info = run("INFO")
        assert info["project"] == "FDW_PWM" and info["protocol"] == 2
        assert info["external_pwm_pin"] and not info["external_output_enabled"]
        report["clock_hz"] = info["clock_hz"]
        # 首轮从低速开始；周期/脉宽/两个D0边沿触发分别验证。
        cases = ((10000, 2500, 100, 1024, 2), (1000, 400, 10, 1024, 2),
                 (400, 100, 5, 1024, 3), (1000, 0, 10, 64, 0),
                 (1000, 1000, 10, 64, 0))
        if fine:
            cases = ((100, 30, 1, 1024, 2), (100, 30, 1, 1024, 3),
                     (37, 11, 1, 1024, 2), (100, 0, 1, 64, 0), (100, 100, 1, 64, 0))
        for index, (period, high, interval, count, trigger) in enumerate(cases):
            prepare(trigger, 0)
            run("PWM_SET {} {}".format(period, high))
            wait_status("PWM_STATUS", lambda s: s["period_ticks"] == period and
                        s["high_ticks"] == high and not (s["flags"] & 2))
            if trigger == 0:
                run("PWM_ENABLE 1")
                time.sleep(0.01)
            record_id = run("DIG_ARM {} {} {}".format(interval, count, int(trigger != 0)))["record_id"]
            if trigger:
                run("PWM_ENABLE 1")
            collect("continuous_{:02d}".format(index), record_id, interval, count, period, high, trigger)
            run("PWM_ENABLE 0")

        # 无上升沿时保持等待，不虚构触发；取消后半帧不得读出。
        prepare(2, 0)
        run("PWM_SET 1000 0")
        wait_status("PWM_STATUS", lambda s: s["high_ticks"] == 0 and not (s["flags"] & 2))
        cancelled_id = run("DIG_ARM 10 64 1")["record_id"]
        run("PWM_ENABLE 1")
        time.sleep(0.03)
        waiting = run("DIG_STATUS")
        assert waiting["flags"] & 1 and waiting["captured_count"] == 0
        run("DIG_CANCEL")
        assert not run("DIG_READ {} 0 1".format(cancelled_id), False)["ok"]
        run("PWM_ENABLE 0")
        report["absent_edge_wait_and_cancel_passed"] = True

        burst_cases = ((100, 40, 5, 2), (100, 0, 4, 1), (100, 100, 3, 2)) if fine else (
            (200, 80, 5, 2), (200, 0, 4, 1), (200, 200, 3, 2))
        for index, (period, high, cycles, trigger) in enumerate(burst_cases):
            prepare(trigger, 1)
            interval, count = (1, 700) if fine else (10, 128)
            record_id = run("DIG_ARM {} {} 1".format(interval, count))["record_id"]
            run("PWM_BURST {} {} {}".format(period, high, cycles))
            collect("burst_{:02d}".format(index), record_id, interval, count, period, high, trigger, cycles)

        # 纯数字模式应断开输出、禁止发生器，并拒绝已失效的旧帧。
        run("MODE_SET 2 2 0")
        state = run("MODE_STATUS")
        assert state["mode"] == 2 and not state["output_enabled"] and not state["pwm_active"]
        assert run("DIG_READ {} 0 1".format(record_id), False)["code"] == -4
        assert not run("PWM_ENABLE 1", False)["ok"]
        assert not run("OUTPUT_ENABLE 1", False)["ok"]
        report["digital_mode_switch_passed"] = True
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        try:
            run("HALT")
            state = run("MODE_STATUS")
            report["final_mode_status"] = state
            assert state["mode"] == 0 and not state["output_enabled"]
            assert not state["pwm_active"] and not state["capture_active"]
        except Exception as error:
            report["cleanup_error"] = str(error)
            report["passed"] = False
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.126.50")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fine", action="store_true", help="增加10ns间隔的完整边界观察；不代表模拟相位标定")
    args = parser.parse_args()
    result = verify(args.host, args.output, args.fine)
    print("FDW_LOOPBACK_VERIFY=" + json.dumps(result, ensure_ascii=True), flush=True)
    if not result["passed"]:
        raise SystemExit(1)
