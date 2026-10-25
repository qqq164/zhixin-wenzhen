"""相位帧契约与少量复数换算；不执行CPU逐样本IQ或FFT。"""
import cmath
import math

N = 8192
CLOCK_HZ = 25_000_000
MASK = (1 << 32) - 1


def validate_metadata(meta, point):
    """缺少首点参考、实际步进或帧身份时拒绝给出相位。"""
    required = ('metadata_version', 'generation', 'record_id', 'sample_count',
                'complete', 'errors', 'dds_ftw', 'dac_tag', 'inverted',
                'interval_ticks', 'first_tick', 'last_tick',
                'first_phase_word', 'last_phase_word', 'raw_clock_hz')
    if any(name not in meta for name in required):
        raise ValueError('缺少硬件相位元数据，不能用DDS配置相位代替ADC首点相位')
    integer_fields = [name for name in required if name not in ('complete', 'inverted')]
    if any(type(meta[name]) is not int for name in integer_fields):
        raise ValueError('硬件相位字段必须是整数')
    if type(meta['complete']) is not bool or type(meta['inverted']) is not bool:
        raise ValueError('相位完成与反相标志必须是布尔值')
    if meta['metadata_version'] != 1 or not meta['complete'] or meta['errors']:
        raise ValueError('相位记录未完成或存在硬件错误')
    if not 1 <= meta['generation'] <= MASK or not 1 <= meta['record_id'] <= MASK or meta['sample_count'] != N:
        raise ValueError('相位帧号、代数或长度不符')
    if meta['raw_clock_hz'] != CLOCK_HZ or meta['dds_ftw'] != point['dds_ftw'] or meta['interval_ticks'] != point['interval_ticks']:
        raise ValueError('实际时钟、DDS频率字或抽取率与测量点不符')
    ratio = meta['interval_ticks']
    if type(ratio) is not int or not 1 <= ratio <= (1 << 20) or ratio & (ratio - 1):
        raise ValueError('抽取率必须是1至2^20之间的2次幂')
    if not 0 < meta['dds_ftw'] < 0x80000000 or not 1 <= meta['dac_tag'] <= MASK:
        raise ValueError('DDS配置身份无效')
    if any(not 0 <= meta[key] <= MASK for key in ('first_phase_word', 'last_phase_word')):
        raise ValueError('DDS相位字越界')
    if not 0 <= meta['first_tick'] <= meta['last_tick'] < (1 << 64):
        raise ValueError('原始时钟计数无效')
    if meta['last_tick'] - meta['first_tick'] != (N - 1)*ratio:
        raise ValueError('采样间隔不连续')
    step = meta['dds_ftw']*ratio
    if point['pl_ftw'] != step or not 0x100000 <= step < 0x80000000:
        raise ValueError('PL参考频率字错误或存在抽取混叠')
    if meta['last_phase_word'] != (meta['first_phase_word'] + (N - 1)*step) & MASK:
        raise ValueError('首末相位与真实采样步进不一致')
    return meta


def reference_phasor(pl_result, meta, point):
    """从PL返回的I/Q恢复相对DDS的正弦相量，补偿已知数字CIC响应。"""
    validate_metadata(meta, point)
    if pl_result.get('frame_id') != meta['record_id']:
        raise ValueError('PL结果与ADC相位帧号不同')
    if pl_result.get('abi') != 0x20261014:
        raise ValueError('PL算法ABI不符')
    # PL使用零初相的余弦/正弦基底；相量约定为cosine-j*sine。
    value = complex(pl_result['cosine_q16'], -pl_result['sine_q16'])/65536
    if not all(math.isfinite(x) for x in (value.real, value.imag)) or abs(value) <= point.get('minimum_amplitude_counts', 1e-9):
        raise ValueError('有效信号幅度不足，不能给出相位')
    phase = meta['first_phase_word']*2*math.pi/(1 << 32)
    # DDS生成正弦，乘j后以DDS正弦本身为零相位；反相属于激励身份的一部分。
    value *= 1j*cmath.exp(-1j*phase)
    if meta['inverted']:
        value = -value
    gain = point['filter_gain']
    if not math.isfinite(gain) or gain <= 0:
        raise ValueError('数字滤波增益无效')
    value *= cmath.exp(-1j*point['filter_phase_radians'])/gain
    return value


def calibrated_phasor(pl_result, meta, point, calibration, path):
    """只有同频点、同支路、同滤波配置的实板校准才可修正模拟相位。"""
    value = reference_phasor(pl_result, meta, point)
    if calibration.get('hardware_verified') is not True:
        raise ValueError('模拟通路尚未实板校准，不能标为已校准相位')
    for key, expected in (('dds_ftw', point['dds_ftw']), ('interval_ticks', point['interval_ticks']), ('path', path)):
        if calibration.get(key) != expected:
            raise ValueError('校准频点、抽取率或测量支路不匹配')
    if not calibration.get('evidence_sha256') or not calibration.get('calibration_id'):
        raise ValueError('校准缺少证据身份')
    transfer = complex(*calibration['transfer_real_imag'])
    if not all(math.isfinite(x) for x in (transfer.real, transfer.imag)) or abs(transfer) < 1e-12:
        raise ValueError('校准传递系数无效')
    return value/transfer


def impedance(va, vb, resistance_ohm, uncertainty_floor_counts):
    """压差小于误差门限时不给出虚假的大阻抗或开路定论。"""
    if not math.isfinite(resistance_ohm) or resistance_ohm <= 0 or not math.isfinite(uncertainty_floor_counts) or uncertainty_floor_counts <= 0:
        raise ValueError('采样电阻与误差门限必须为正数')
    if not all(math.isfinite(x) for x in (va.real, va.imag, vb.real, vb.imag)):
        raise ValueError('电压相量无效')
    difference = va-vb
    if abs(difference) <= uncertainty_floor_counts:
        raise ValueError('采样电阻压差低于可分辨门限')
    # 两支路已校准到相同尺度时，共同的码到伏比例在该比值中抵消。
    return resistance_ohm*vb/difference
