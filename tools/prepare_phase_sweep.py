"""在电脑离线生成扫频配置；悟净只读取小配置并调用PL，不生成逐样本基底。"""
import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
CLOCK_HZ = 25_000_000
N = 8192


def plan_point(frequency_hz, minimum_cycles=4):
    """按实际DDS频率字选择窗口；抽取只改变记录速率，不改变物理时钟。"""
    requested = Fraction(str(frequency_hz))
    if requested <= 0 or requested >= CLOCK_HZ/2 or type(minimum_cycles) is not int or minimum_cycles < 2:
        raise ValueError('频率和最少周期数越界')
    exact_word = requested*(1 << 32)/CLOCK_HZ
    ftw = (2*exact_word.numerator + exact_word.denominator)//(2*exact_word.denominator)
    if not 0 < ftw < 0x80000000:
        raise ValueError('频率小于DDS分辨率或越过奈奎斯特频率')
    for log2 in range(21):
        ratio = 1 << log2
        step = ftw*ratio
        if (N - 1)*step >= minimum_cycles*(1 << 32):
            break
    else:
        raise ValueError('最大抽取率下窗口仍不够长')
    if step >= 0x10000000:
        raise ValueError('每周期少于16个有效点，本扫频候选不接受该频段')
    normalized_frequency = ftw/(1 << 32)
    gain = 1.0 if ratio == 1 else (math.sin(math.pi*ratio*normalized_frequency)/(ratio*math.sin(math.pi*normalized_frequency)))**3
    group_delay = 3*(ratio - 1)/2
    return {'requested_frequency_hz': float(requested), 'dds_ftw': ftw,
            'actual_frequency_hz': ftw*CLOCK_HZ/(1 << 32), 'raw_clock_hz': CLOCK_HZ,
            'decimation_log2': log2, 'interval_ticks': ratio, 'pl_ftw': step,
            'sample_rate_hz': CLOCK_HZ/ratio, 'sample_count': N,
            'first_to_last_seconds': (N - 1)*ratio/CLOCK_HZ,
            'cycles_in_record': (N - 1)*step/(1 << 32),
            'filter': 'bypass' if ratio == 1 else 'cic3', 'filter_gain': gain,
            'filter_group_delay_raw_ticks': group_delay,
            'filter_phase_radians': -2*math.pi*normalized_frequency*group_delay,
            'warmup_raw_ticks': 0 if ratio == 1 else 3*ratio,
            'capture_deadline_seconds': max(0.1, (N+3)*ratio/CLOCK_HZ + 1),
            'requires_new_capture_firmware': True,
            'r2_frequency_command_exactly_representable': requested.denominator == 1,
            'phase_hardware_verified': False, 'analog_calibrated': False}


def compile_schedule(frequencies, raw_only=False):
    """旧参考算法仅允许在电脑运行；每个频点的矩阵提前存入配置包。"""
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('离线配置生成器只在电脑运行，不在悟净执行')
    reference = ROOT/'outputs/dig4_adc_board_20261015_01/pl_replay/driver_snapshot'
    sys.path.insert(0, str(reference))
    from fdw_pl_dsp_reference import prepare_iq, HANN_Q17
    points = []
    for frequency in frequencies:
        point = plan_point(frequency, minimum_cycles=2 if raw_only else 4)
        if raw_only and point['interval_ticks'] != 1:
            raise ValueError('R3原速率相位候选的窗口不足两个周期；低频抽取尚未接入固件')
        config = prepare_iq(point['actual_frequency_hz'], point['sample_rate_hz'], 0)
        if config['ftw'] != point['pl_ftw']:
            raise ValueError('离线矩阵的频率字与实际抽取步进不同')
        point['pl_config'] = {key: config[key] for key in ('ftw', 'phase_word', 'inverse_q30', 'gram_normalized', 'sample_rate_hz', 'actual_frequency_hz')}
        point['pl_config']['hann_sum_q17'] = sum(HANN_Q17)
        points.append(point)
    return {'schema': 'FDW_PHASE_SWEEP_V1', 'backend': 'pl_only', 'cpu_fallback': False,
            'required_pl_abi': 0x20261014, 'required_adc_metadata_version': 1,
            'hardware_integrated': False, 'hardware_verified': False,
            'reference_source_sha256': hashlib.sha256((reference/'fdw_pl_dsp_reference.py').read_bytes()).hexdigest(),
            'capture_profile': 'RAW25M_PHASE' if raw_only else 'CIC3_LOW_RATE_CANDIDATE', 'points': points}


def main():
    """生成候选配置，禁止覆盖原配置或接触板卡。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frequencies', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--raw-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT/'outputs'):
        parser.error('输出必须位于FDW_NEXT/outputs')
    result = compile_schedule(args.frequencies, raw_only=args.raw_only)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('离线PL配置已生成，尚未集成或下载：'+str(output))


if __name__ == '__main__':
    main()
