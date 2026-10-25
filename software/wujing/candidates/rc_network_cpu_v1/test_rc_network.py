"""解析电路、输入边界及测量身份测试，可直接在悟净标准Python运行。"""
import copy
import json
import math
from pathlib import Path
import unittest

from rc_network import (NetworkError, compare_measurements, normalize_pl_group,
                        predict_sweep, solve_frequency, solve_linear, validate_model)


def model(elements=None):
    """构造有解析解的串联电阻加并联RC负载。"""
    return {'schema': 'FDW_RC_NETWORK_V1', 'model_id': 'test_parallel_rc',
            'nodes': ['A', 'B'], 'ground': '0', 'reference_node': 'A',
            'boundary_voltages_relative': {'A': [1, 0]},
            'elements': elements or [
                {'id': 'Rs', 'kind': 'R', 'a': 'A', 'b': 'B', 'value': 100},
                {'id': 'R', 'kind': 'R', 'a': 'B', 'b': '0', 'value': 1000},
                {'id': 'C', 'kind': 'C', 'a': 'B', 'b': '0', 'value': 1e-8}]}


def measurements(source='simulation'):
    """生成明确标注为仿真的解析幅相；不调用待测求解器生成期望。"""
    frequency = 10000
    z = 1 / (1 / 1000 + 2j * math.pi * frequency * 1e-8)
    h = z / (100 + z)
    return {'schema': 'FDW_RC_MEASUREMENTS_V1', 'model_id': 'test_parallel_rc',
            'reference_node': 'A', 'phasor_convention': 'exp(+j*omega*t)', 'source': source,
            'records': [{'measurement_group_id': 'test_only_group', 'phase_reference_id': 'dds_test',
                         'frequency_hz': frequency, 'transfer_real_imag': {'B': [h.real, h.imag]},
                         'analog_paths_calibrated': False, 'frame_ids': {'A': 1, 'B': 2},
                         'digital_phase_consistent': True}]}


def pl_frame(record_id, value):
    """仅用于适配器边界测试的假帧，不是硬件证据。"""
    ftw, interval, first = 1717987, 1, 12345
    return {'metadata': {'metadata_version': 2, 'complete': True, 'errors': 0,
                         'sample_count': 8192, 'record_id': record_id, 'dds_ftw': ftw,
                         'raw_clock_hz': 25000000, 'interval_ticks': interval,
                         'dac_tag': 7, 'inverted': False, 'first_tick': 100,
                         'last_tick': 100 + 8191 * interval, 'first_phase_word': first,
                         'last_phase_word': (first + 8191 * ftw * interval) & 0xffffffff,
                         'filter_id': 0, 'decimation_log2': 0, 'pipeline_tick_compensation': 0},
            'actual': {'backend': 'pl', 'abi': 0x20261014, 'frame_id': record_id,
                       'cpu_fallback': False, 'digital_phase_record_consistent': True, 'saturation_count': 0,
                       'dds_relative_real_imag_counts': value,
                       'analog_phase_calibrated': False}}


class CircuitTests(unittest.TestCase):
    """使用串并联解析结果验证节点法和相位符号。"""

    def assertComplexClose(self, left, right, tolerance=1e-11):
        self.assertLessEqual(abs(left - right), tolerance * max(1, abs(right)))

    def test_resistive_divider(self):
        circuit = model()
        circuit['elements'] = circuit['elements'][:2]
        result = solve_frequency(circuit, 1)
        self.assertComplexClose(complex(*result['transfer_real_imag']['B']), 1000 / 1100)
        self.assertComplexClose(complex(*result['reference_v_over_boundary_current_ohm']), 1100)

    def test_parallel_rc_six_decades(self):
        for frequency in (1, 10, 100, 1000, 10000, 100000):
            with self.subTest(frequency=frequency):
                z = 1 / (1 / 1000 + 2j * math.pi * frequency * 1e-8)
                result = solve_frequency(model(), frequency)
                self.assertComplexClose(complex(*result['transfer_real_imag']['B']), z / (100 + z))
                self.assertComplexClose(complex(*result['reference_v_over_boundary_current_ohm']), 100 + z)
                self.assertLess(result['numeric']['normalized_backward_error'], 1e-12)

    def test_ladder_against_nested_impedances(self):
        path = Path(__file__).parent / 'examples/model_rc_ladder.json'
        circuit = json.loads(path.read_text(encoding='utf-8'))
        for frequency in (1, 123, 10000, 100000):
            omega = 2 * math.pi * frequency
            zc = 1 / (1 / 10000 + 1j * omega * 1e-8)
            zb = 1 / (1j * omega * 1e-7 + 1 / (1000 + zc))
            vb = zb / (100 + zb)
            vc = vb * zc / (1000 + zc)
            result = solve_frequency(circuit, frequency)
            self.assertComplexClose(complex(*result['transfer_real_imag']['B']), vb)
            self.assertComplexClose(complex(*result['transfer_real_imag']['C']), vc)
            self.assertLess(result['unknown_node_kcl_max_per_reference_volt_siemens'], 1e-12)

    def test_capacitive_divider(self):
        circuit = model()
        circuit['elements'] = [
            {'id': 'C1', 'kind': 'C', 'a': 'A', 'b': 'B', 'value': 1e-9},
            {'id': 'C2', 'kind': 'C', 'a': 'B', 'b': '0', 'value': 2e-9}]
        for frequency in (1e-6, 1, 1e5):
            result = solve_frequency(circuit, frequency)
            self.assertComplexClose(complex(*result['transfer_real_imag']['B']), 1 / 3)

    def test_multiple_fixed_boundaries(self):
        circuit = model()
        circuit['nodes'].append('C')
        circuit['boundary_voltages_relative']['C'] = [0, 1]
        circuit['elements'] = [
            {'id': 'R1', 'kind': 'R', 'a': 'A', 'b': 'B', 'value': 1000},
            {'id': 'R2', 'kind': 'R', 'a': 'B', 'b': 'C', 'value': 1000}]
        result = solve_frequency(circuit, 1)
        self.assertComplexClose(complex(*result['transfer_real_imag']['B']), 0.5 + 0.5j)

    def test_highpass_positive_phase(self):
        circuit = model()
        circuit['elements'] = [
            {'id': 'C', 'kind': 'C', 'a': 'A', 'b': 'B', 'value': 1e-8},
            {'id': 'R', 'kind': 'R', 'a': 'B', 'b': '0', 'value': 1000}]
        result = solve_frequency(circuit, 1000)
        expected = 1000 / (1000 + 1 / (2j * math.pi * 1000 * 1e-8))
        actual = complex(*result['transfer_real_imag']['B'])
        self.assertComplexClose(actual, expected)
        self.assertGreater(actual.imag, 0)

    def test_maximum_node_count_series_chain(self):
        nodes = ['A'] + ['N' + str(i) for i in range(1, 64)]
        circuit = model()
        circuit['nodes'] = nodes
        circuit['elements'] = [
            {'id': 'R' + str(i), 'kind': 'R', 'a': a, 'b': b, 'value': 1000}
            for i, (a, b) in enumerate(zip(nodes, nodes[1:] + ['0']))]
        result = solve_frequency(circuit, 1)
        for i, node in enumerate(nodes):
            self.assertComplexClose(complex(*result['transfer_real_imag'][node]), (64 - i) / 64)
        self.assertComplexClose(complex(*result['reference_v_over_boundary_current_ohm']), 64000)

    def test_matrix_pivot_and_row_equilibration(self):
        matrix = [[0j, 2j], [1e-12, 2e-12]]
        expected = [2 + 3j, 4 - 1j]
        rhs = [sum(a * b for a, b in zip(row, expected)) for row in matrix]
        actual, numeric = solve_linear(matrix, rhs)
        for left, right in zip(actual, expected):
            self.assertComplexClose(left, right)
        self.assertGreater(numeric['condition_inf_original'], 1e10)
        self.assertLess(numeric['condition_inf_equilibrated'], 10)

    def test_condition_and_singularity(self):
        for matrix in ([[1, 1], [1, 1]], [[1, 1], [1, 1 + 1e-12]], [[0, 0], [0, 1]]):
            with self.subTest(matrix=matrix), self.assertRaises(NetworkError):
                solve_linear(matrix, [1, 1])

    def test_invalid_components(self):
        for value in (0, -1, float('nan'), float('inf'), True, '1000'):
            circuit = model()
            circuit['elements'][0]['value'] = value
            with self.subTest(value=value), self.assertRaises(NetworkError):
                solve_frequency(circuit, 1)

    def test_invalid_topology(self):
        cases = []
        circuit = model(); circuit['nodes'].append('floating'); cases.append(circuit)
        circuit = model(); circuit['elements'][0]['b'] = 'missing'; cases.append(circuit)
        circuit = model(); circuit['elements'][0]['kind'] = 'L'; cases.append(circuit)
        circuit = model(); circuit['elements'][1]['id'] = 'Rs'; cases.append(circuit)
        circuit = model(); circuit['boundary_voltages_relative']['A'] = [2, 0]; cases.append(circuit)
        circuit = model(); circuit['nodes'].append('A'); cases.append(circuit)
        for circuit in cases:
            with self.subTest(circuit=circuit), self.assertRaises(NetworkError):
                validate_model(circuit)

    def test_invalid_frequency_and_size(self):
        for frequency in (0, -1, True, float('nan')):
            with self.subTest(frequency=frequency), self.assertRaises(NetworkError):
                solve_frequency(model(), frequency)
        for frequencies in ([], [1, 1], [1] * 257):
            with self.assertRaises(NetworkError):
                predict_sweep(model(), frequencies)

    def test_predict_is_not_measurement(self):
        result = predict_sweep(model(), [1, 100000])
        self.assertFalse(result['physical_dut_measured'])
        self.assertFalse(result['pl_executed'])
        self.assertEqual(result['voltage_unit'], 'ratio_to_reference_node')
        self.assertFalse(result['parameter_inversion_performed'])


class MeasurementTests(unittest.TestCase):
    """比较身份、误差门限与未校准数据的使用边界。"""

    def test_exact_simulation_comparison(self):
        result = compare_measurements(model(), measurements(), 1e-12, 1e-12)
        self.assertTrue(result['within_configured_tolerance'])
        self.assertFalse(result['physical_judgment_eligible'])

    def test_detect_deviation(self):
        data = measurements()
        data['records'][0]['transfer_real_imag']['B'][0] += 0.3
        result = compare_measurements(model(), data)
        self.assertFalse(result['within_configured_tolerance'])
        self.assertFalse(result['fault_classification_performed'])

    def test_uncalibrated_hardware_validation_only(self):
        data = measurements('hardware_pl_features')
        result = compare_measurements(model(), data)
        self.assertFalse(result['physical_judgment_eligible'])
        self.assertEqual(result['points'][0]['physical_comparison'], 'validation_only')

    def test_calibration_contract(self):
        data = measurements('hardware_pl_features')
        data['records'][0].update(analog_paths_calibrated=True, calibration_id='test_contract_only')
        self.assertTrue(compare_measurements(model(), data)['physical_judgment_eligible'])
        data['source'] = 'simulation'
        self.assertFalse(compare_measurements(model(), data)['physical_judgment_eligible'])

    def test_invalid_group_or_reference(self):
        for field, value in (('phase_reference_id', ''), ('measurement_group_id', ''),
                             ('digital_phase_consistent', False), ('frame_ids', {})):
            data = measurements('hardware_pl_features')
            data['records'][0][field] = value
            with self.subTest(field=field), self.assertRaises(NetworkError):
                compare_measurements(model(), data)
        data = measurements(); data['reference_node'] = 'wrong'
        with self.assertRaises(NetworkError):
            compare_measurements(model(), data)

    def test_boundary_only_and_unknown_node_rejected(self):
        for observed in ({'A': [1, 0]}, {'missing': [1, 0]}):
            data = measurements()
            data['records'][0]['transfer_real_imag'] = observed
            with self.assertRaises(NetworkError):
                compare_measurements(model(), data)

    def test_zero_observed_has_no_phase(self):
        data = measurements()
        data['records'][0]['transfer_real_imag']['B'] = [0, 0]
        result = compare_measurements(model(), data)
        self.assertIsNone(result['points'][0]['nodes']['B']['phase_error_degrees'])

    def test_pl_group_adapter(self):
        data = normalize_pl_group({'A': pl_frame(1, [100, 0]), 'B': pl_frame(2, [50, -20])},
                                  'A', 'adapter_test', 'dds_test', 'test_parallel_rc')
        self.assertEqual(data['source'], 'historical_pl_features')
        self.assertEqual(data['records'][0]['transfer_real_imag']['B'], [0.5, -0.2])
        self.assertFalse(compare_measurements(model(), data)['physical_judgment_eligible'])

    def test_adapter_rejects_bad_frame(self):
        for field, value in (('dac_tag', 8), ('dds_ftw', 111), ('errors', 1),
                             ('last_tick', 0), ('last_phase_word', 0)):
            frames = {'A': pl_frame(1, [100, 0]), 'B': pl_frame(2, [50, 0])}
            frames['B']['metadata'][field] = value
            with self.subTest(field=field), self.assertRaises(NetworkError):
                normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc')

    def test_adapter_rejects_small_reference_and_wrong_abi(self):
        frames = {'A': pl_frame(1, [0, 0]), 'B': pl_frame(2, [50, 0])}
        with self.assertRaises(NetworkError):
            normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc')

    def test_adapter_calibration_requires_corrected_values(self):
        frames = {'A': pl_frame(1, [100, 0]), 'B': pl_frame(2, [50, 0])}
        for frame in frames.values():
            frame['actual'].update(analog_phase_calibrated=True, analog_gain_calibrated=True,
                                   calibration_id='test_only', calibration_evidence_sha256='a' * 64)
        uncorrected = normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc', historical=False)
        self.assertFalse(uncorrected['records'][0]['analog_paths_calibrated'])
        frames['A']['actual']['calibrated_dds_relative_real_imag_counts'] = [100, 0]
        frames['B']['actual']['calibrated_dds_relative_real_imag_counts'] = [25, 0]
        corrected = normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc', historical=False)
        self.assertTrue(corrected['records'][0]['analog_paths_calibrated'])
        self.assertEqual(corrected['records'][0]['transfer_real_imag']['B'], [0.25, 0])
        frames['A']['actual']['calibrated_dds_relative_real_imag_counts'] = [200, 0]
        del frames['B']['actual']['calibrated_dds_relative_real_imag_counts']
        mixed = normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc', historical=False)
        self.assertFalse(mixed['records'][0]['analog_paths_calibrated'])
        self.assertEqual(mixed['records'][0]['transfer_real_imag']['B'], [0.5, 0])

    def test_adapter_rejects_aliasing_and_saturation(self):
        frames = {'A': pl_frame(1, [100, 0]), 'B': pl_frame(2, [50, 0])}
        frames['B']['actual']['saturation_count'] = 1
        with self.assertRaises(NetworkError):
            normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc')
        frames['B'] = pl_frame(2, [50, 0])
        frames['B']['metadata']['interval_ticks'] = 65536
        with self.assertRaises(NetworkError):
            normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc')
        frames['A'] = pl_frame(1, [100, 0]); frames['A']['actual']['abi'] = 0
        with self.assertRaises(NetworkError):
            normalize_pl_group(frames, 'A', 'test', 'dds', 'test_parallel_rc')


if __name__ == '__main__':
    unittest.main(verbosity=2)
