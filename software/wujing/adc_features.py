"""ADC 原码与单阶跃的 CPU 参考；当前只接受仿真记录，不生成电压或故障标签。"""

import argparse
import hashlib
import json
import math
from pathlib import Path


def decode_words(payload, byteorder, encoding):
    """显式解释字节序和编码，OTR 保持对应样点，保留位非零即拒绝。"""
    if byteorder not in ("little", "big") or encoding not in ("straight_binary", "twos_complement"):
        raise ValueError("必须明确指定字节序与 ADC 原码编码")
    if not payload or len(payload) % 2:
        raise ValueError("原码记录为空或包含半个 16 位字")
    codes, raw_codes, overrange = [], [], []
    for offset in range(0, len(payload), 2):
        word = int.from_bytes(payload[offset:offset + 2], byteorder)
        if word & 0x7000:
            raise ValueError("原码保留位非零，不能交给此阶段的解码器")
        raw = word & 0xFFF
        raw_codes.append(raw)
        codes.append(raw - 4096 if encoding == "twos_complement" and raw & 0x800 else raw)
        overrange.append(bool(word & 0x8000))
    return codes, raw_codes, overrange


def code_statistics(codes, raw_codes, overrange):
    """使用整数一、二阶矩计算方差，避免高直流小纹波的浮点相减消减。"""
    count = len(codes)
    total = sum(codes)
    square_total = sum(value * value for value in codes)
    variance_numerator = count * square_total - total * total
    return {
        "sample_count": count, "sum_codes": total, "sum_squared_codes": square_total,
        "minimum_code": min(codes), "maximum_code": max(codes),
        "mean_code": total / count, "peak_to_peak_codes": max(codes) - min(codes),
        "rms_codes": math.sqrt(square_total / count),
        "ac_rms_codes": math.sqrt(variance_numerator) / count,
        "variance_codes_squared": variance_numerator / (count * count),
        "overrange_count": sum(overrange),
        "raw_zero_count": raw_codes.count(0), "raw_all_ones_count": raw_codes.count(4095),
        "unit": "ADC code", "voltage_calibrated": False,
    }


def step_features(codes, overrange, sample_rate_hz, step):
    """已知初末码和阶跃区间的一阶模型参考；不足或过量程时不给出时间常数。"""
    start, end = step["start_index"], step["end_index"]
    initial, final = step["initial_code"], step["final_code"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(codes)
            or type(initial) not in (int, float) or type(final) not in (int, float)
            or not math.isfinite(initial) or not math.isfinite(final)):
        raise ValueError("阶跃区间或已知初末码无效")
    result = {"start_index": start, "end_index": end, "initial_code": initial, "final_code": final,
              "tau_63_seconds": None, "tau_log_fit_seconds": None, "fit_point_count": 0,
              "fit_r_squared": None, "reason": None,
              "model_assumption": "已知初末码的一阶单阶跃；不是物理故障分类"}
    if any(overrange[start:end]):
        result["reason"] = "阶跃窗口包含 OTR，拒绝时间常数估计"
        return result
    delta = final - initial
    if delta == 0:
        result["reason"] = "已知初末码相同，没有可归一化的阶跃"
        return result
    progress = [(value - initial) / delta for value in codes[start:end]]
    threshold = 1 - math.exp(-1)
    for index in range(1, len(progress)):
        left, right = progress[index - 1:index + 1]
        if left < threshold <= right and right > left:
            result["tau_63_seconds"] = (index - 1 + (threshold - left) / (right - left)) / sample_rate_hz
            break
    points = [(index / sample_rate_hz, math.log(1 - value))
              for index, value in enumerate(progress) if 0.05 <= 1 - value <= 0.95]
    result["fit_point_count"] = len(points)
    if len(points) >= 8:
        mean_x = math.fsum(x for x, _ in points) / len(points)
        mean_y = math.fsum(y for _, y in points) / len(points)
        xx = math.fsum((x - mean_x) ** 2 for x, _ in points)
        yy = math.fsum((y - mean_y) ** 2 for _, y in points)
        xy = math.fsum((x - mean_x) * (y - mean_y) for x, y in points)
        slope = xy / xx if xx else 0
        if slope < 0 and yy > 0:
            result["tau_log_fit_seconds"] = -1 / slope
            result["fit_r_squared"] = min(1.0, max(0.0, xy * xy / (xx * yy)))
    if result["tau_63_seconds"] is None or result["tau_log_fit_seconds"] is None:
        result["reason"] = "窗口或有效变化不足，至少一种时间常数估计不可用"
    return result


def analyze_report(report):
    """核验输入字节和显式元数据；实板正式格式确认前只允许仿真来源。"""
    if report.get("schema") != "fdw_adc_raw_reference_v1" or report.get("source") != "simulation":
        raise ValueError("当前参考只接受明确标记的仿真原码记录")
    rate = report["sample_rate_hz"]
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("有效记录速率必须是有限正数")
    if not report.get("frames"):
        raise ValueError("记录没有帧")
    frames = []
    for frame in report["frames"]:
        payload = Path(frame["binary_path"]).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        count = frame["sample_count"]
        if (digest != frame["sha256"] or type(count) is not int or count <= 0
                or len(payload) != count * 2):
            raise ValueError("原码记录的哈希或点数不符")
        codes, raw, otr = decode_words(payload, frame["byteorder"], frame["encoding"])
        item = {"record_id": frame["record_id"], "input_sha256": digest,
                "encoding": frame["encoding"], "byteorder": frame["byteorder"],
                "statistics": code_statistics(codes, raw, otr), "step": None,
                "fault_label": None}
        if frame.get("step") is not None:
            item["step"] = step_features(codes, otr, rate, frame["step"])
        frames.append(item)
    return {"passed": True, "processing": "CPU reference", "source": "simulation",
            "physical_adc_sampled": False, "training_eligible": False,
            "fault_labels_generated": False, "wujing_pl_executed": False, "npu_executed": False,
            "sample_rate_hz": rate, "sample_rate_origin": "仿真显式参数，非物理 ADC 选频",
            "frames": frames}


def main():
    """同一份源码支持电脑与悟净 Python 3.8，输出拒绝覆盖。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze_report(json.loads(args.report.read_text(encoding="utf-8-sig")))
    result["reference_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with args.output.open("xb") as stream:
        stream.write(payload)
    print("FDW_ADC_FEATURES=" + json.dumps({"passed": True, "sha256": hashlib.sha256(payload).hexdigest(),
          "frames": len(result["frames"]), "source": "simulation"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
