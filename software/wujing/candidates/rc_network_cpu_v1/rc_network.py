"""CPU侧无源RC节点方程求解；只处理少量复相量，不访问采集设备或PL。"""
import cmath
import hashlib
import json
import math
import time

VERSION = 'FDW_RC_CPU_V1'
MAX_NODES = 64
MAX_ELEMENTS = 512
MAX_FREQUENCIES = 256


class NetworkError(ValueError):
    """输入、拓扑或数值条件不满足求解要求。"""


def number(value, name, positive=False):
    """拒绝布尔值、非有限值及要求为正时的零和负数。"""
    if type(value) not in (int, float):
        raise NetworkError(name + '必须是有限实数')
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise NetworkError(name + '超出浮点范围') from error
    if not math.isfinite(value) or (positive and value <= 0):
        raise NetworkError(name + '数值无效')
    return value


def phasor(value, name):
    """复相量统一用实部、虚部二元数组交换，避免角度单位歧义。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise NetworkError(name + '须为[实部,虚部]')
    return complex(number(value[0], name), number(value[1], name))


def pair(value):
    """仅导出有限复数。"""
    if not math.isfinite(value.real) or not math.isfinite(value.imag):
        raise NetworkError('计算出现非有限相量')
    return [value.real, value.imag]


def validate_model(model):
    """首版使用固定节点电压边界，支持任意连接的正值电阻、电容。"""
    if not isinstance(model, dict) or model.get('schema') != 'FDW_RC_NETWORK_V1':
        raise NetworkError('电路模型协议不符')
    if not isinstance(model.get('model_id'), str) or not model['model_id']:
        raise NetworkError('缺少模型身份')
    nodes = model.get('nodes')
    ground = model.get('ground')
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES
            or any(not isinstance(node, str) or not node for node in nodes)
            or len(set(nodes)) != len(nodes) or not isinstance(ground, str)
            or not ground or ground in nodes):
        raise NetworkError('节点须唯一，地节点须单独声明，最多64个非地节点')
    allowed = set(nodes) | {ground}
    boundary = model.get('boundary_voltages_relative')
    if not isinstance(boundary, dict) or not boundary or set(boundary) - set(nodes):
        raise NetworkError('固定电压边界须对应非地节点')
    known = {ground: 0j}
    for node, value in boundary.items():
        known[node] = phasor(value, '边界' + node)
    reference = model.get('reference_node')
    if not isinstance(reference, str) or reference not in boundary or abs(known[reference] - 1) > 1e-14:
        raise NetworkError('参考节点须是固定边界，归一化电压必须为[1,0]')
    elements = model.get('elements')
    if not isinstance(elements, list) or not 1 <= len(elements) <= MAX_ELEMENTS:
        raise NetworkError('须提供1至512个元件')
    identifiers = set()
    adjacency = {node: set() for node in allowed}
    for element in elements:
        if not isinstance(element, dict):
            raise NetworkError('元件记录须为对象')
        name = element.get('id')
        if not isinstance(name, str) or not name or name in identifiers:
            raise NetworkError('元件名称缺失或重复')
        identifiers.add(name)
        a, b = element.get('a'), element.get('b')
        if not isinstance(a, str) or not isinstance(b, str) or a not in allowed or b not in allowed or a == b:
            raise NetworkError(name + '端点无效')
        if element.get('kind') not in ('R', 'C'):
            raise NetworkError(name + '首版仅支持R和C')
        number(element.get('value'), name + '参数', positive=True)
        adjacency[a].add(b)
        adjacency[b].add(a)
    reached = set(known)
    pending = list(known)
    while pending:
        for node in adjacency[pending.pop()] - reached:
            reached.add(node)
            pending.append(node)
    if set(nodes) - reached:
        raise NetworkError('存在无固定电压参考的悬空子网络：' + ','.join(sorted(set(nodes) - reached)))
    return known


def _norm_inf(matrix):
    """矩阵无穷范数，使用复数模。"""
    return max((sum(abs(value) for value in row) for row in matrix), default=0.0)


def _factor(matrix):
    """行均衡后的复数LU分解，使用部分主元选择。"""
    lu = [list(row) for row in matrix]
    size = len(lu)
    permutation = list(range(size))
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(lu[row][column]))
        if abs(lu[pivot][column]) <= 1e-14:
            raise NetworkError('节点方程奇异或接近奇异')
        lu[column], lu[pivot] = lu[pivot], lu[column]
        permutation[column], permutation[pivot] = permutation[pivot], permutation[column]
        for row in range(column + 1, size):
            lu[row][column] /= lu[column][column]
            multiplier = lu[row][column]
            for index in range(column + 1, size):
                lu[row][index] -= multiplier * lu[column][index]
    return lu, permutation


def _substitute(lu, permutation, rhs):
    """复用LU做前代、回代；不显式构造逆矩阵求解电压。"""
    result = [rhs[index] for index in permutation]
    size = len(result)
    for row in range(size):
        result[row] -= sum(lu[row][column] * result[column] for column in range(row))
    for row in range(size - 1, -1, -1):
        result[row] = (result[row] - sum(lu[row][column] * result[column]
                                       for column in range(row + 1, size))) / lu[row][row]
    return result


def solve_linear(matrix, rhs, max_condition=1e10):
    """求解小规模复数方程，并返回条件数及归一化后向误差。"""
    size = len(matrix)
    if not 1 <= size <= MAX_NODES or len(rhs) != size or any(len(row) != size for row in matrix):
        raise NetworkError('矩阵尺寸不符')
    max_condition = number(max_condition, '条件数门限', positive=True)
    if any(not math.isfinite(complex(v).real) or not math.isfinite(complex(v).imag)
           for row in matrix for v in row) or any(
               not math.isfinite(complex(v).real) or not math.isfinite(complex(v).imag) for v in rhs):
        raise NetworkError('方程包含非有限值')
    scales = [max(abs(value) for value in row) for row in matrix]
    if min(scales) == 0:
        raise NetworkError('节点方程有零行，可能存在悬空节点')
    equilibrated = [[value / scale for value in row] for row, scale in zip(matrix, scales)]
    lu, permutation = _factor(equilibrated)
    value = _substitute(lu, permutation, [v / scale for v, scale in zip(rhs, scales)])
    # 逐列求解仅用于评估小矩阵条件数；正式电压仍通过LU回代获得。
    inverse_rows_scaled = [0.0] * size
    inverse_rows_original = [0.0] * size
    for column in range(size):
        unit = [complex(index == column) for index in range(size)]
        inverse_column = _substitute(lu, permutation, unit)
        for row in range(size):
            inverse_rows_scaled[row] += abs(inverse_column[row])
            inverse_rows_original[row] += abs(inverse_column[row] / scales[column])
    condition = _norm_inf(equilibrated) * max(inverse_rows_scaled)
    if not math.isfinite(condition) or condition > max_condition:
        raise NetworkError('行均衡后条件数超过门限，拒绝输出可信电压')
    original_condition = _norm_inf(matrix) * max(inverse_rows_original)
    residual = max(abs(sum(a * x for a, x in zip(row, value)) - b)
                   for row, b in zip(matrix, rhs))
    denominator = _norm_inf(matrix) * max(abs(x) for x in value) + max(abs(b) for b in rhs)
    backward_error = residual / denominator if denominator else 0.0
    if not math.isfinite(backward_error) or backward_error > 1e-10:
        raise NetworkError('方程后向误差超过门限')
    for item in value:
        pair(item)
    return value, {'condition_inf_equilibrated': condition,
                   'condition_inf_original': original_condition if math.isfinite(original_condition) else None,
                   'normalized_backward_error': backward_error}


def solve_frequency(model, frequency_hz, max_condition=1e10):
    """固定参考电压为1，输出无量纲节点传递函数及每参考伏的支路电流。"""
    known = validate_model(model)
    frequency = number(frequency_hz, '频率Hz', positive=True)
    nodes = model['nodes']
    unknown = [node for node in nodes if node not in known]
    index = {node: i for i, node in enumerate(unknown)}
    matrix = [[0j] * len(unknown) for _ in unknown]
    rhs = [0j] * len(unknown)
    branches = []
    for element in model['elements']:
        y = (1 / element['value'] if element['kind'] == 'R'
             else 2j * math.pi * frequency * element['value'])
        pair(complex(y))
        branches.append((element, y))
        for a, b in ((element['a'], element['b']), (element['b'], element['a'])):
            if a in index:
                row = index[a]
                matrix[row][row] += y
                if b in index:
                    matrix[row][index[b]] -= y
                else:
                    rhs[row] += y * known[b]
    if unknown:
        solution, numeric = solve_linear(matrix, rhs, max_condition)
    else:
        solution = []
        numeric = {'condition_inf_equilibrated': 1.0, 'condition_inf_original': 1.0,
                   'normalized_backward_error': 0.0}
    voltages = dict(known)
    voltages.update(zip(unknown, solution))
    node_balance = {node: 0j for node in [model['ground']] + nodes}
    currents = {}
    for element, y in branches:
        current = y * (voltages[element['a']] - voltages[element['b']])
        currents[element['id']] = pair(current)
        node_balance[element['a']] += current
        node_balance[element['b']] -= current
    reference_current = node_balance[model['reference_node']]
    port_impedance = pair(1 / reference_current) if abs(reference_current) > 0 else None
    return {'frequency_hz': frequency,
            'transfer_real_imag': {node: pair(voltages[node]) for node in nodes},
            'branch_current_per_reference_volt_siemens': currents,
            'boundary_injection_per_reference_volt_siemens': {node: pair(node_balance[node]) for node in known},
            'reference_v_over_boundary_current_ohm': port_impedance,
            'unknown_node_kcl_max_per_reference_volt_siemens': max((abs(node_balance[n]) for n in unknown), default=0.0),
            'numeric': numeric}


def model_digest(model):
    """将参数、拓扑和边界一并纳入身份。"""
    return hashlib.sha256(json.dumps(model, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def predict_sweep(model, frequencies):
    """CPU扫频预测；频率和数据来源与实际测量分开记录。"""
    validate_model(model)
    if not isinstance(frequencies, list) or not 1 <= len(frequencies) <= MAX_FREQUENCIES:
        raise NetworkError('扫频须提供1至256个频点')
    frequencies = [number(value, '频率Hz', positive=True) for value in frequencies]
    if len(set(frequencies)) != len(frequencies):
        raise NetworkError('频点重复')
    started = time.monotonic()
    rows = [solve_frequency(model, frequency) for frequency in frequencies]
    return {'schema': 'FDW_RC_PREDICTION_V1', 'solver': VERSION,
            'model_id': model['model_id'], 'model_sha256': model_digest(model),
            'source': 'theoretical_model', 'voltage_unit': 'ratio_to_reference_node',
            'phasor_convention': 'exp(+j*omega*t)', 'reference_node': model['reference_node'],
            'physical_dut_measured': False, 'pl_executed': False,
            'parameter_inversion_performed': False,
            'cpu_elapsed_seconds': time.monotonic() - started, 'points': rows}


def compare_measurements(model, measurements, absolute_tolerance=0.01, relative_tolerance=0.05):
    """比较复传递函数；容差是显式工程输入，不自动当作故障分类标准。"""
    validate_model(model)
    absolute_tolerance = number(absolute_tolerance, '绝对容差')
    relative_tolerance = number(relative_tolerance, '相对容差')
    if absolute_tolerance < 0 or relative_tolerance < 0:
        raise NetworkError('容差不能为负数')
    if (not isinstance(measurements, dict) or measurements.get('schema') != 'FDW_RC_MEASUREMENTS_V1'
            or measurements.get('model_id') != model['model_id']
            or measurements.get('reference_node') != model['reference_node']
            or measurements.get('phasor_convention') != 'exp(+j*omega*t)'):
        raise NetworkError('测量协议、模型身份或相量参考不符')
    source = measurements.get('source')
    if source not in ('simulation', 'historical_pl_features', 'hardware_pl_features'):
        raise NetworkError('测量来源未声明')
    records = measurements.get('records')
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_FREQUENCIES:
        raise NetworkError('测量记录数量无效')
    results = []
    groups = set()
    for record in records:
        if not isinstance(record, dict):
            raise NetworkError('测量记录须为对象')
        group = record.get('measurement_group_id')
        if not isinstance(group, str) or not group or group in groups:
            raise NetworkError('测量组身份缺失或重复')
        groups.add(group)
        if not isinstance(record.get('phase_reference_id'), str) or not record['phase_reference_id']:
            raise NetworkError('测量组缺少共同相位参考身份')
        observed = record.get('transfer_real_imag')
        if (not isinstance(observed, dict) or not observed or set(observed) - set(model['nodes'])
                or not (set(observed) - set(model['boundary_voltages_relative']))):
            raise NetworkError('须测量至少一个非边界节点，且不能包含未知节点名')
        calibrated = (record.get('analog_paths_calibrated') is True
                      and isinstance(record.get('calibration_id'), str) and bool(record['calibration_id']))
        eligible = source == 'hardware_pl_features' and calibrated
        if source != 'simulation':
            identities = record.get('frame_ids')
            required = set(observed) | {model['reference_node']}
            if (not isinstance(identities, dict) or not required <= set(identities)
                    or any(type(identities[n]) is not int or identities[n] < 0 for n in required)
                    or record.get('digital_phase_consistent') is not True):
                raise NetworkError('实测特征缺少帧身份或数字相位一致性检查')
        predicted = solve_frequency(model, record.get('frequency_hz'))
        comparisons = {}
        for node, value in observed.items():
            measured = phasor(value, '测点' + node)
            expected = complex(*predicted['transfer_real_imag'][node])
            error = abs(measured - expected)
            limit = absolute_tolerance + relative_tolerance * abs(expected)
            comparisons[node] = {
                'measured_real_imag': pair(measured), 'predicted_real_imag': pair(expected),
                'complex_absolute_error': error, 'tolerance_limit': limit,
                'within_configured_tolerance': error <= limit,
                'relative_error': error / abs(expected) if abs(expected) > 1e-12 else None,
                'phase_error_degrees': math.degrees(cmath.phase(measured / expected))
                if min(abs(expected), abs(measured)) > 1e-12 else None}
        within = all(item['within_configured_tolerance'] for item in comparisons.values())
        results.append({'measurement_group_id': group, 'frequency_hz': predicted['frequency_hz'],
                        'nodes': comparisons, 'numeric': predicted['numeric'],
                        'within_configured_tolerance': within,
                        'physical_judgment_eligible': eligible,
                        'physical_comparison': ('within_configured_tolerance' if within else 'outside_configured_tolerance')
                        if eligible else 'validation_only'})
    return {'schema': 'FDW_RC_COMPARISON_V1', 'solver': VERSION,
            'model_id': model['model_id'], 'model_sha256': model_digest(model),
            'source': source, 'absolute_tolerance': absolute_tolerance,
            'relative_tolerance': relative_tolerance,
            'physical_judgment_eligible': all(row['physical_judgment_eligible'] for row in results),
            'within_configured_tolerance': all(row['within_configured_tolerance'] for row in results),
            'fault_classification_performed': False, 'parameter_inversion_performed': False,
            'points': results}


def normalize_pl_group(node_frames, reference_node, group_id, phase_reference_id,
                       model_id, historical=True, minimum_reference_counts=1.0):
    """将调用方明确映射到节点的PL结果转为同一参考下的传递函数，不假定帧号就是测点。"""
    if not isinstance(node_frames, dict) or reference_node not in node_frames or len(node_frames) < 2:
        raise NetworkError('须提供参考节点及至少一个测量节点')
    if type(historical) is not bool or any(not isinstance(v, str) or not v
                                          for v in (group_id, phase_reference_id, model_id)):
        raise NetworkError('来源或组身份无效')
    minimum = number(minimum_reference_counts, '参考幅度门限', positive=True)
    values, corrected_values, identities, calibrated, calibration_ids = {}, {}, {}, [], set()
    excitation = None
    for node, frame in node_frames.items():
        if not isinstance(node, str) or not isinstance(frame, dict):
            raise NetworkError('节点与帧对象无效')
        meta, actual = frame.get('metadata', {}), frame.get('actual', {})
        if not isinstance(meta, dict) or not isinstance(actual, dict):
            raise NetworkError('帧元数据与PL结果须为对象')
        if (meta.get('metadata_version') != 2 or meta.get('complete') is not True
                or meta.get('errors') != 0 or meta.get('sample_count') != 8192
                or actual.get('backend') != 'pl' or actual.get('cpu_fallback') is not False
                or actual.get('digital_phase_record_consistent') is not True
                or actual.get('abi') != 0x20261014 or actual.get('frame_id') != meta.get('record_id')
                or actual.get('saturation_count') != 0):
            raise NetworkError('PL输入帧缺少有效身份、完整性或相位检查')
        clock = number(meta.get('raw_clock_hz'), '原始采样时钟', positive=True)
        ftw = meta.get('dds_ftw')
        interval = meta.get('interval_ticks')
        tag = meta.get('dac_tag')
        if (type(ftw) is not int or not 0 < ftw < 2**31 or type(interval) is not int
                or not 1 <= interval <= 65536 or interval & (interval - 1)
                or ftw * interval >= 2**31
                or type(tag) is not int or type(meta.get('record_id')) is not int
                or meta['record_id'] < 0 or type(meta.get('first_tick')) is not int
                or type(meta.get('last_tick')) is not int
                or meta['last_tick'] - meta['first_tick'] != 8191 * interval
                or type(meta.get('first_phase_word')) is not int
                or type(meta.get('last_phase_word')) is not int
                or meta['last_phase_word'] != (meta['first_phase_word'] + 8191 * ftw * interval) & 0xffffffff):
            raise NetworkError('DDS或帧连续性元数据无效')
        identity = (clock, ftw, tag, meta.get('inverted'))
        if excitation is not None and excitation != identity:
            raise NetworkError('跨节点激励频率、配置tag或极性不一致')
        excitation = identity
        calibration_id = actual.get('calibration_id')
        calibration_verified = (actual.get('analog_phase_calibrated') is True
                                and actual.get('analog_gain_calibrated') is True
                                and isinstance(calibration_id, str) and bool(calibration_id)
                                and isinstance(actual.get('calibration_evidence_sha256'), str)
                                and len(actual['calibration_evidence_sha256']) == 64
                                and 'calibrated_dds_relative_real_imag_counts' in actual)
        values[node] = phasor(actual.get('dds_relative_real_imag_counts'), 'PL相量')
        if calibration_verified:
            corrected_values[node] = phasor(actual['calibrated_dds_relative_real_imag_counts'], '校准PL相量')
        identities[node] = meta['record_id']
        calibrated.append(calibration_verified)
        if calibration_verified:
            calibration_ids.add(calibration_id)
    if all(calibrated):
        values = corrected_values
    reference = values[reference_node]
    if abs(reference) <= minimum:
        raise NetworkError('参考节点幅度低于门限，拒绝相除')
    record = {'measurement_group_id': group_id, 'phase_reference_id': phase_reference_id,
              'frequency_hz': excitation[0] * excitation[1] / 2**32,
              'transfer_real_imag': {node: pair(value / reference) for node, value in values.items()
                                     if node != reference_node},
              'frame_ids': identities, 'digital_phase_consistent': True,
              'analog_paths_calibrated': all(calibrated),
              'calibration_id': '|'.join(sorted(calibration_ids)) if all(calibrated) else None}
    return {'schema': 'FDW_RC_MEASUREMENTS_V1', 'model_id': model_id,
            'reference_node': reference_node, 'phasor_convention': 'exp(+j*omega*t)',
            'source': 'historical_pl_features' if historical else 'hardware_pl_features',
            'records': [record]}
