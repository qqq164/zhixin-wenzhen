"""检查接口格式、顺序、数值转换和导出边界，不连接任何硬件。"""
import copy
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from example_factory import example_condition, example_training_profile
from model_export import CONTRACT, ORDER, ExportError, export_condition, export_report, frame_result, model_input

HERE = Path(__file__).resolve().parent


class FormatTests(unittest.TestCase):
    """以已知复数结果检查六列的含义与模型输入布局。"""
    def setUp(self):
        self.request = example_condition(frames=2)

    def test_fixed_order_and_known_values(self):
        result = export_condition(self.request)
        expected = [math.sqrt(2) / 4, math.sqrt(2) / 4, .2, .05,
                    .8 * math.cos(math.pi / 6), -.4]
        self.assertEqual(result['order'], ORDER)
        self.assertEqual(result['available'], [True] * 6)
        for actual, wanted in zip(result['value'], expected):
            self.assertAlmostEqual(actual, wanted, places=12)

    def test_both_actual_rs_settings(self):
        for rs in (100, 10000):
            self.request['condition']['rs_nominal_ohm'] = rs
            self.assertEqual(export_condition(self.request)['condition']['rs_nominal_ohm'], rs)

    def test_old_one_k_setting_rejected(self):
        self.request['condition']['rs_nominal_ohm'] = 1000
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_float32_layout_is_six_columns(self):
        result = export_condition(self.request)
        row, binary = model_input([result])
        self.assertEqual(row['shape'], [1, 6])
        self.assertEqual(len(binary), 24)
        self.assertEqual(list(struct.unpack('<6f', binary)), row['conditions'][0]['value'])

    def test_multi_condition_row_order_and_bytes(self):
        second = example_condition(frames=2, rs=10000, condition_id='second')
        first, last = export_condition(self.request), export_condition(second)
        result, binary = model_input([first, last])
        self.assertEqual(result['shape'], [2, 6])
        self.assertEqual([r['condition']['condition_id'] for r in result['conditions']],
                         [self.request['condition']['condition_id'], 'second'])
        self.assertEqual(len(binary), 48)

    def test_duplicate_condition_rejected(self):
        result = export_condition(self.request)
        with self.assertRaises(ExportError):
            model_input([result, result])

    def test_nonfinite_result_rejected(self):
        self.request['channels']['C'][0]['actual']['dc_counts'] = float('nan')
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_float32_overflow_rejected(self):
        result = export_condition(self.request)
        result['value'][0] = 1e100
        with self.assertRaises(ExportError):
            model_input([result])

    def test_not_mislabeled_as_periodic_training_contract(self):
        result = export_condition(self.request)
        self.assertEqual(result['feature_contract'], CONTRACT)
        self.assertEqual(result['not_equivalent_to'], 'sense-periodic4-f64-v1')
        self.assertTrue(result['integration']['format_ready'])
        self.assertFalse(result['integration']['hardware_validation_performed'])

    def test_training_profile_matching_and_mismatch(self):
        profile = example_training_profile(frames=2)
        self.assertTrue(export_condition(self.request, profile)['integration']['training_profile_matches'])
        for key, value in (('feature_contract', 'sense-periodic4-f64-v1'),
                           ('frames_per_channel', 16), ('rs_nominal_ohm', [10000]),
                           ('order', list(reversed(ORDER))), ('frontend_profile_id', 'different')):
            changed = dict(profile, **{key: value})
            with self.subTest(key=key), self.assertRaises(ExportError):
                export_condition(self.request, changed)

    def test_tick_values_remain_decimal_strings(self):
        result = export_condition(self.request)
        self.assertTrue(all(isinstance(x, str) and x.isdigit()
                            for x in result['channel_statistics']['C']['first_ticks']))

    def test_missing_channel_not_zero_filled(self):
        del self.request['channels']['B']
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_same_frame_cannot_fill_two_channels(self):
        self.request['channels']['C'][0] = copy.deepcopy(self.request['channels']['A'][0])
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_changed_excitation_rejected(self):
        self.request['channels']['B'][0]['metadata']['dac_tag'] += 1
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_broken_phase_rejected(self):
        self.request['channels']['B'][0]['metadata']['last_phase_word'] ^= 1
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_zero_reference_rejected(self):
        for frame in self.request['channels']['A']:
            frame['actual']['dds_relative_real_imag_counts'] = [0, 0]
        with self.assertRaises(ExportError):
            export_condition(self.request)

    def test_optional_voltage_conversion(self):
        first = self.request['channels']['A'][0]['metadata']
        self.request['calibration'] = {
            'frontend_profile_id': 'SYNTHETIC_ONLY', 'calibration_id': 'SYNTHETIC_CAL',
            'evidence_sha256': 'a' * 64, 'hardware_verified': False,
            'dds_ftw': first['dds_ftw'], 'interval_ticks': 1,
            'channels': {name: {'volts_per_count': .001, 'dc_zero_counts': 0,
                                'fundamental_correction': [1, 0], 'dc_correction': 1,
                                'residual_correction': 1}
                         for name in self.request['channels']}}
        result = export_condition(self.request)
        self.assertEqual(result['channel_unit'], 'V')
        self.assertAlmostEqual(result['channel_statistics']['A']['phasor_peak'][0], .1)
        self.assertAlmostEqual(result['value'][2], .2)

    def test_unverified_status_does_not_block_format_export(self):
        self.request['qualification'] = {'contact_confirmed': None, 'hardware_calibrated': False}
        result = export_condition(self.request)
        self.assertTrue(result['integration']['format_ready'])
        self.assertEqual(result['qualification'], self.request['qualification'])

    def test_report_does_not_invent_channels_or_rs(self):
        report = {'hardware_sampled': True, 'frames': self.request['channels']['A']}
        result = export_report(report, 'b' * 64, 'direct_loopback')
        self.assertEqual(len(result['frames']), 2)
        self.assertIsNone(result['frames'][0]['channel'])
        self.assertIsNone(result['frames'][0]['rs_nominal_ohm'])
        self.assertFalse(result['condition_features_ready'])

    def test_cli_json_csv_and_float32_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'input.json'
            source.write_text(json.dumps(self.request), encoding='utf-8')
            command = [sys.executable, '-B', str(HERE / 'export_results.py'), 'condition',
                       '--input', str(source), '--output', str(root / 'result.json'),
                       '--csv', str(root / 'result.csv'), '--model-json', str(root / 'model.json'),
                       '--f32', str(root / 'model.f32')]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            model = json.loads((root / 'model.json').read_text(encoding='utf-8'))
            self.assertEqual(model['shape'], [1, 6])
            self.assertEqual(list(struct.unpack('<6f', (root / 'model.f32').read_bytes())),
                             model['conditions'][0]['value'])
            self.assertEqual((root / 'result.csv').read_text(encoding='utf-8-sig').splitlines()[0],
                             'condition_id,point_id,rs_nominal_ohm,frequency_hz,interval_ticks,frames,frontend_profile_id,origin,feature_contract,F0,F1,F2,F3,F4,F5')
            repeated = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(repeated.returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
