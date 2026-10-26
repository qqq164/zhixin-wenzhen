"""把既有PL结果整理为训练接口；不采样、不加载SDK、不执行NPU。"""
import cmath
import hashlib
import json
import math
import struct

SCHEMA = 'zhixin.pl-features/v1'
CONTRACT = 'pl-lsq8192-dds-mean-v1'
ORDER = ['Re(C/A)', 'Im(C/A)', 'DC_C/abs(A)',
         'residual_C_RMS/abs(A)', 'Re(B/A)', 'Im(B/A)']
CHANNELS = ('A', 'B', 'C', 'A_check')
RS_SETTINGS = (100, 10000)
ABI = 0x20261014
MASK = (1 << 32) - 1


class ExportError(ValueError):
    """输入身份、采集质量或数值不满足导出条件。"""


def require(ok, message):
    """拒绝用缺失值或不一致数据构造成功结果。"""
    if not ok:
        raise ExportError(message)


def number(value, name, positive=False):
    """仅接受有限实数，排除布尔值和无穷。"""
    require(type(value) in (int, float) and math.isfinite(value), name + '必须是有限数')
    require(not positive or value > 0, name + '必须为正')
    return float(value)


def pair(value, name):
    """JSON复数统一以实部、虚部两个数表达。"""
    require(isinstance(value, (list, tuple)) and len(value) == 2, name + '须为两个数')
    return complex(number(value[0], name), number(value[1], name))


def digest(value):
    """为配置和输出提供与JSON键顺序无关的内容身份。"""
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def model_input(results):
    """生成逐条件float32六列矩阵，后续模型总张量由训练端编码器组成。"""
    require(isinstance(results, list) and results, '至少提供一个条件结果')
    ids, rows, binary = set(), [], bytearray()
    for result in results:
        require(result.get('schema') == SCHEMA and result.get('feature_contract') == CONTRACT
                and result.get('order') == ORDER, '特征版本或顺序不匹配')
        values = result.get('value')
        require(isinstance(values, list) and len(values) == 6
                and result.get('available') == [True] * 6, '需要完整六项特征')
        values = [number(v, '模型特征') for v in values]
        try:
            payload = struct.pack('<6f', *values)
        except (OverflowError, struct.error):
            raise ExportError('特征超出float32表示范围')
        narrowed = list(struct.unpack('<6f', payload))
        require(all(math.isfinite(v) for v in narrowed), 'float32转换溢出')
        condition = result['condition']
        require(condition['condition_id'] not in ids, '会话包含重复condition_id')
        ids.add(condition['condition_id'])
        rows.append({'condition': dict(condition), 'actual_frequency_hz': result['actual_frequency_hz'],
                     'value': narrowed, 'available': [True] * 6, 'frames': result['frames'],
                     'interval_ticks': result['interval_ticks'], 'channel_unit': result['channel_unit'],
                     'calibration_id': result['calibration_id'],
                     'calibration_evidence_sha256': result['calibration_evidence_sha256']})
        binary.extend(payload)
    return ({'schema': 'zhixin.model-input/v1', 'feature_contract': CONTRACT,
             'order': ORDER, 'dtype': 'float32', 'byte_order': 'little',
             'shape': [len(rows), 6], 'conditions': rows,
             'normalization': '由训练端保存并应用，本导出器不猜测均值或标准差',
             'requires_model_encoder': True, 'npu_executed': False}, bytes(binary))


def frame_result(frame):
    """读取原R3/R4报告的一帧PL结果，保留码单位和原始身份。"""
    require(isinstance(frame, dict), '帧必须是对象')
    meta, actual = frame.get('metadata', {}), frame.get('actual', {})
    names = ('record_id', 'generation', 'sample_count', 'raw_clock_hz', 'dds_ftw',
             'interval_ticks', 'first_tick', 'last_tick', 'first_phase_word',
             'last_phase_word', 'dac_tag', 'metadata_version', 'errors')
    require(all(type(meta.get(k)) is int for k in names), '缺少整数相位元数据')
    require(meta['metadata_version'] in (1, 2), '不支持的相位元数据版本')
    require(meta.get('complete') is True and meta['errors'] == 0, '采集未完成或存在错误')
    require(meta['sample_count'] == 8192 and meta['raw_clock_hz'] == 25000000, '采样配置不匹配')
    require(0 < meta['record_id'] <= MASK and 0 < meta['generation'] <= MASK,
            '帧身份无效')
    require(0 < meta['dac_tag'] <= MASK and type(meta.get('inverted')) is bool,
            '激励身份无效')
    ratio = meta['interval_ticks']
    require(1 <= ratio <= 65536 and ratio & (ratio - 1) == 0, '抽取率无效')
    step = meta['dds_ftw'] * ratio
    require(0 < meta['dds_ftw'] < 0x80000000 and 0 < step < 0x80000000,
            '激励频率无效或发生抽取混叠')
    require(0 <= meta['first_tick'] <= meta['last_tick'] < (1 << 64)
            and meta['last_tick'] - meta['first_tick'] == 8191 * ratio,
            '采样时间不连续')
    require(0 <= meta['first_phase_word'] <= MASK and 0 <= meta['last_phase_word'] <= MASK
            and meta['last_phase_word'] == (meta['first_phase_word'] + 8191 * step) & MASK,
            '首末相位不一致')
    if meta['metadata_version'] == 2:
        exponent = ratio.bit_length() - 1
        require(meta.get('decimation_log2') == exponent
                and meta.get('filter_id') == int(exponent != 0)
                and meta.get('pipeline_tick_compensation') == (2 if exponent else 0),
                'CIC身份不匹配')
    else:
        require(ratio == 1, '旧相位版本只接受未经抽取的历史帧')
    require(actual.get('abi') == ABI and actual.get('frame_id') == meta['record_id'],
            'PL ABI或结果帧号不匹配')
    require(actual.get('backend') == 'pl' and actual.get('cpu_fallback') is False,
            '不是已声明的PL计算结果')
    require(actual.get('digital_phase_record_consistent') is True, '数字相位未经核对')
    flags = actual.get('flags')
    require(type(flags) is int and flags & 8 and not flags & 4, 'ADC连续性标志不合格')
    status = frame.get('status', {})
    require('flags' not in status or status['flags'] == flags, '状态与PL flags不一致')
    require(type(actual.get('saturation_count')) is int
            and actual['saturation_count'] == 0, 'ADC触轨统计不合格或未知')
    overrange = status.get('overrange_count')
    require(overrange is None or type(overrange) is int and overrange == 0,
            'ADC过量程')
    phasor = pair(actual.get('dds_relative_real_imag_counts'), 'DDS相量')
    dc = number(actual.get('dc_counts'), 'DC码值')
    residual = number(actual.get('residual_rms_counts'), '拟合残差')
    require(residual >= 0, '拟合残差不能为负')
    # 这些相量已由原运行端处理DDS首相位及数字CIC响应，适配器不重复补偿。
    return {
        'record_id': meta['record_id'], 'generation': meta['generation'],
        'first_tick': str(meta['first_tick']), 'last_tick': str(meta['last_tick']),
        'first_phase_word': meta['first_phase_word'],
        'dds_ftw': meta['dds_ftw'], 'dac_tag': meta['dac_tag'],
        'inverted': meta['inverted'], 'interval_ticks': ratio,
        'sample_count': 8192, 'sample_rate_hz': 25000000.0 / ratio,
        'frequency_hz': meta['dds_ftw'] * 25000000.0 / (1 << 32),
        'phasor_counts': [phasor.real, phasor.imag], 'dc_counts': dc,
        'ls_residual_rms_counts': residual,
        'adc_overrange_observed': overrange == 0,
        'hardware_phase_verified': actual.get('phase_reference_hardware_verified') is True,
        'input_sha256': frame.get('input_sha256'),
    }


def export_report(report, source_sha256, scope='unspecified'):
    """直接导出现有报告；单点直连数据不冒充A/B/C实物测量。"""
    require(scope in ('unspecified', 'direct_loopback', 'virtual_network', 'real_dut'),
            '未知数据范围')
    rows, rejected = [], []
    for frame in report.get('frames', []):
        try:
            row = frame_result(frame)
            row.update(name=frame.get('name'), point_index=frame.get('point_index'),
                       channel=None, rs_nominal_ohm=None)
            rows.append(row)
        except ExportError as error:
            rejected.append({'name': frame.get('name'), 'reason': str(error)})
    return {'schema': 'zhixin.pl-frame-results/v1', 'feature_contract': CONTRACT,
            'source_report_sha256': source_sha256, 'scope': scope,
            'hardware_sampled_in_source': report.get('hardware_sampled') is True,
            'npu_executed_by_exporter': False, 'frames': rows, 'rejected': rejected,
            'condition_features_ready': False,
            'reason': '仅导出实际已有结果；报告未提供同条件实物A/B/C和Rs身份，不构造六项输入'}


def _channel_statistics(rows, calibration):
    """平均共同参考复相量，残差按逐帧拟合残差功率平均定义。"""
    count = len(rows)
    phasors = [complex(*r['phasor_counts']) for r in rows]
    value = sum(phasors) / count
    dc = math.fsum(r['dc_counts'] for r in rows) / count
    residual = math.sqrt(math.fsum(r['ls_residual_rms_counts'] ** 2 for r in rows) / count)
    scatter = math.sqrt(math.fsum(abs(v - value) ** 2 for v in phasors) / count)
    if calibration is not None:
        gain = number(calibration.get('volts_per_count'), '电压比例', True)
        correction = pair(calibration.get('fundamental_correction'), '基波校准系数')
        require(abs(correction) > 0, '基波校准系数不能为零')
        value *= gain * correction
        dc = (dc - number(calibration.get('dc_zero_counts'), 'DC零点')) * gain
        dc *= number(calibration.get('dc_correction'), 'DC校准系数', True)
        residual *= gain * number(calibration.get('residual_correction'), '残差校准系数', True)
        scatter *= gain * abs(correction)
    return {'frames': count, 'phasor_peak': [value.real, value.imag],
            'dc': dc, 'ls_residual_rms': residual, 'phasor_scatter_rms': scatter,
            'frame_ids': [r['record_id'] for r in rows],
            'first_ticks': [r['first_tick'] for r in rows]}


def export_condition(request, training_profile=None):
    """把同条件A/B/C的PL结果导出为有版本标识的六项特征。"""
    require(request.get('schema') == 'zhixin.pl-condition/v1', '输入条件格式不符')
    context = request.get('condition', {})
    for key in ('condition_id', 'point_id', 'frontend_profile_id', 'acquisition_session_id'):
        require(isinstance(context.get(key), str) and context[key], '缺少条件身份：' + key)
    rs = context.get('rs_nominal_ohm')
    require(type(rs) in (int, float) and rs in RS_SETTINGS, 'Rs只能为100Ω或10kΩ实装档位')
    require(context.get('origin') in ('hardware', 'simulation', 'synthetic_test_vector'), '数据来源不明确')
    require(context.get('scope') in ('real_dut', 'direct_loopback', 'virtual_network', 'synthetic'),
            '测量范围不明确')
    channels = request.get('channels', {})
    require(isinstance(channels, dict) and set(channels).issubset(CHANNELS)
            and all(channels.get(k) for k in ('A', 'B', 'C')),
            '必须提供同条件A/B/C，不能用同一帧复制替代')
    rows, seen, signature = {}, set(), None
    for channel, frames in channels.items():
        require(isinstance(frames, list) and frames, '通道必须包含实际采集帧')
        rows[channel] = []
        previous_tick = -1
        for frame in frames:
            item = frame_result(frame)
            require(item['record_id'] not in seen, '同一物理帧被重复使用')
            seen.add(item['record_id'])
            require(int(item['first_tick']) > previous_tick, '同通道采样时间未递增')
            previous_tick = int(item['first_tick'])
            config = tuple(item[k] for k in ('dds_ftw', 'dac_tag', 'interval_ticks', 'inverted'))
            require(signature is None or config == signature, '激励/抽取/参考身份跨条件混用')
            signature = config
            require('rs_nominal_ohm' not in frame or frame['rs_nominal_ohm'] == rs, '帧Rs与条件不符')
            require('acquisition_session_id' not in frame or
                    frame['acquisition_session_id'] == context['acquisition_session_id'], '跨采集会话混用')
            rows[channel].append(item)
    require(len({len(v) for v in rows.values()}) == 1, '各路重复帧数不一致')
    first = rows['A'][0]
    target = context.get('requested_frequency_hz')
    if target is not None:
        target = number(target, '目标频率', True)
        tolerance = number(context.get('frequency_tolerance_ppm', 10), '频率容差')
        require(tolerance >= 0 and abs(first['frequency_hz'] - target) <= target * tolerance / 1e6,
                '实际DDS频率超出条件容差')
    calibration = request.get('calibration')
    if calibration is not None:
        require(calibration.get('frontend_profile_id') == context['frontend_profile_id']
                and calibration.get('dds_ftw') == first['dds_ftw']
                and calibration.get('interval_ticks') == first['interval_ticks'], '标定条件不匹配')
        require(isinstance(calibration.get('calibration_id'), str) and calibration['calibration_id'], '标定缺少编号')
        evidence = calibration.get('evidence_sha256')
        require(isinstance(evidence, str) and len(evidence) == 64
                and all(c in '0123456789abcdef' for c in evidence), '标定缺少内容摘要')
        require(all(k in calibration.get('channels', {}) for k in rows), '标定缺少所需通道')
    stats = {k: _channel_statistics(v, calibration['channels'][k] if calibration else None)
             for k, v in rows.items()}
    a = complex(*stats['A']['phasor_peak'])
    floor = number(context.get('minimum_reference', 1e-9), '参考下限', True)
    require(abs(a) >= floor, 'A参考信号不足，不能构造比值')
    c, b = complex(*stats['C']['phasor_peak']) / a, complex(*stats['B']['phasor_peak']) / a
    values = [c.real, c.imag, stats['C']['dc'] / abs(a),
              stats['C']['ls_residual_rms'] / abs(a), b.real, b.imag]
    require(all(math.isfinite(v) for v in values), '特征计算出现非有限值')
    drift = None
    if 'A_check' in stats:
        change = complex(*stats['A_check']['phasor_peak']) / a
        drift = {'relative_amplitude': abs(abs(change) - 1), 'phase_radians': cmath.phase(change)}
    measured_rs = context.get('rs_measured_ohm')
    if measured_rs is not None:
        number(measured_rs, 'Rs实测值', True)
    compatible = None
    if training_profile is not None:
        compatible = (training_profile.get('feature_contract') == CONTRACT
                      and training_profile.get('order') == ORDER
                      and rs in training_profile.get('rs_nominal_ohm', [])
                      and len(rows['A']) == training_profile.get('frames_per_channel')
                      and training_profile.get('frontend_profile_id') == context['frontend_profile_id'])
        require(compatible, '训练接口配置与特征定义/顺序/Rs/帧数/前端不匹配')
    # 本期只判断接口和数值是否完整，实物验收状态原样保留，不阻止格式导出。
    result = {'schema': SCHEMA, 'feature_contract': CONTRACT, 'order': ORDER,
              'value': values, 'available': [True] * 6, 'condition': dict(context),
              'actual_frequency_hz': first['frequency_hz'], 'dds_ftw': first['dds_ftw'],
              'interval_ticks': first['interval_ticks'], 'sample_rate_hz': first['sample_rate_hz'],
              'filter': {'id': int(first['interval_ticks'] != 1),
                         'decimation_factor': first['interval_ticks'],
                         'cic_order': 3 if first['interval_ticks'] != 1 else 0,
                         'dc_normalized': True, 'fundamental_compensated': True,
                         'residual_compensated': False},
              'frames': len(rows['A']), 'channel_unit': 'V' if calibration else 'ADC_counts',
              'calibration_id': calibration.get('calibration_id') if calibration else None,
              'calibration_evidence_sha256': calibration.get('evidence_sha256') if calibration else None,
              'channel_statistics': stats, 'source_drift': drift,
              'qualification': dict(request.get('qualification', {})),
              'integration': {'format_ready': True, 'training_profile_matches': compatible,
                              'hardware_validation_performed': False, 'npu_executed': False},
              'not_equivalent_to': 'sense-periodic4-f64-v1',
              'npu_executed_by_exporter': False}
    result['content_sha256'] = digest(result)
    return result
