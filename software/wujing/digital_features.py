"""数字时间特征的 CPU 参考实现，供后续悟净 PL 对照，不产生故障标签。"""

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def channel_features(data, bit, interval_ticks, clock_hz):
    """从已记录的离散样点提取边沿和完整片段，窗口两端的半脉冲不算完整宽度。"""
    if not data or not 0 <= bit < 8 or interval_ticks <= 0 or clock_hz <= 0:
        raise ValueError("空记录或非法数字时间参数")
    levels = [(sample >> bit) & 1 for sample in data]
    rising = [i for i in range(1, len(levels)) if levels[i - 1] == 0 and levels[i] == 1]
    falling = [i for i in range(1, len(levels)) if levels[i - 1] == 1 and levels[i] == 0]
    periods = [(right - left) * interval_ticks for left, right in zip(rising, rising[1:])]
    high_widths, low_widths = [], []
    start = 0
    for end in range(1, len(levels) + 1):
        if end == len(levels) or levels[end] != levels[start]:
            if start > 0 and end < len(levels):
                destination = high_widths if levels[start] else low_widths
                destination.append((end - start) * interval_ticks)
            start = end
    period_median = statistics.median(periods) if periods else None
    cycle_high_fraction = None
    if len(rising) >= 2:
        cycle_high_fraction = sum(levels[rising[0]:rising[-1]]) / (rising[-1] - rising[0])
    return {
        "bit": bit, "sample_count": len(levels), "high_sample_count": sum(levels),
        "transition_count": len(rising) + len(falling),
        "steady_level": levels[0] if not rising and not falling else None,
        "rising_indices": rising, "falling_indices": falling,
        "period_ticks": periods, "period_ticks_median": period_median,
        "period_ticks_min": min(periods) if periods else None,
        "period_ticks_max": max(periods) if periods else None,
        "frequency_hz_from_edges": clock_hz / period_median if period_median else None,
        "complete_high_width_ticks": high_widths, "complete_low_width_ticks": low_widths,
        "high_width_ticks_median": statistics.median(high_widths) if high_widths else None,
        "low_width_ticks_median": statistics.median(low_widths) if low_widths else None,
        "sampled_high_fraction": sum(levels) / len(levels),
        "full_cycle_high_fraction": cycle_high_fraction,
        "edge_resolution_ticks": interval_ticks,
        "edge_resolution_seconds": interval_ticks / clock_hz,
        "width_period_quantization_bound_ticks": interval_ticks,
        "first_to_last_ticks": (len(levels) - 1) * interval_ticks,
        "boundary_partial_runs_excluded": True,
        "fault_label": None,
    }


def analyze_report(report):
    """只消费真实存档字节，核验哈希后处理；内部回环不进入故障训练集。"""
    if not report.get("passed") or report.get("source") != "internal_pwm":
        raise ValueError("此阶段只接受已经通过的内部回环记录")
    clock_hz = report["clock_hz"]
    if type(clock_hz) is not int or clock_hz <= 0:
        raise ValueError("记录缺少有效的时钟元数据")
    results = []
    for frame in report["frames"]:
        path = Path(frame.get("local_binary_path", frame["binary_path"]))
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != frame["sha256"]:
            raise ValueError("输入记录哈希不符：" + str(path))
        if len(data) != frame["sample_count"] or any(sample > 3 for sample in data):
            raise ValueError("输入点数或两位数字格式不符")
        interval = frame["interval_ticks"]
        if type(interval) is not int or interval <= 0:
            raise ValueError("记录间隔无效")
        channels = [channel_features(data, bit, interval, clock_hz) for bit in (0, 1)]
        pwm = channels[0]
        # 已知内部 PWM 条件仅用于验证特征计算，不生成 DUT 故障分类。
        if pwm["period_ticks_median"] is not None:
            if abs(pwm["period_ticks_median"] - frame["period_ticks"]) > interval:
                raise AssertionError("边沿周期超出记录量化误差")
        if pwm["high_width_ticks_median"] is not None:
            if abs(pwm["high_width_ticks_median"] - frame["high_ticks"]) > interval:
                raise AssertionError("完整高电平宽度超出记录量化误差")
        if pwm["full_cycle_high_fraction"] is not None:
            if abs(pwm["full_cycle_high_fraction"] - frame["high_ticks"] / frame["period_ticks"]) > interval / frame["period_ticks"]:
                raise AssertionError("完整周期的占空比超出记录量化误差")
        if frame["high_ticks"] == 0 and pwm["steady_level"] != 0:
            raise AssertionError("已知 0% 内部 PWM 应记录为恒低")
        if frame["high_ticks"] == frame["period_ticks"] and pwm["steady_level"] != 1:
            raise AssertionError("已知 100% 内部 PWM 应记录为恒高")
        results.append({"record_id": frame["record_id"], "input_sha256": frame["sha256"],
                        "interval_ticks": interval, "sample_count": len(data),
                        "channel_names": ["pwm", "period_event"], "channels": channels})
    return {"passed": True, "processing": "CPU reference", "source": "internal_pwm",
            "training_eligible": False, "fault_labels_generated": False,
            "wujing_pl_executed": False, "npu_executed": False,
            "clock_hz": clock_hz, "frames": results}


def main():
    """同时支持电脑与悟净 Python 3.8，原记录保持只读。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.report.read_text(encoding="utf-8-sig"))
    result = analyze_report(source)
    result["reference_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    marker = {"passed": result["passed"], "frames": len(result["frames"]),
              "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "output": str(args.output)}
    print("FDW_DIGITAL_FEATURES=" + json.dumps(marker, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
