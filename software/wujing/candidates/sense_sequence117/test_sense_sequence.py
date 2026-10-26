"""编排离线故障注入：合成帧不冒充实物ADC或PL结果。"""
import contextlib
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from sense_sequence import SenseSequence, PATHS

POINT = {'dds_ftw': 1717987, 'interval_ticks': 1, 'decimation_log2': 0,
         'filter': 'bypass', 'pl_ftw': 1717987, 'filter_gain': 1.,
         'filter_phase_radians': 0., 'minimum_amplitude_counts': 1.}


class FakeBoard:
    def __init__(self):
        self.now, self.deadline, self.record = 0., 0., 0
        self.generation, self.mask, self.selected = 2, 33, 3
        self.commands, self.captures = [], []
        self.old, self.mutation, self.never_ready = False, None, False
        self.dac = dict(ready=1, enabled=1, configured=1, waveform_active=1, busy=0, clipped=0,
                        error_code=0, tag=7, frequency_word=POINT['dds_ftw'], phase_word=0,
                        gain_q11=64, offset_code=2048)

    def sleep(self, seconds):
        self.now += seconds

    def sense(self):
        return dict(ok=True, sense_protocol=1, generation=self.generation, relay_mask=self.mask,
                    selected=self.selected, rs_nominal_ohm=100, fault=0,
                    settling=int(self.never_ready or self.now < self.deadline), remaining_ms=10)

    def send(self, text):
        self.commands.append(text)
        if text == 'INFO':
            return dict(board_revision='LXB-R4-PORT1' if self.old else 'LXB-R4-SENSE117', sense_protocol=1)
        if text == 'DAC_STATUS':
            return copy.deepcopy(self.dac)
        if text == 'MODE_STATUS':
            return dict(mode=0, output_enabled=0, capture_active=0, settling=0)
        if text == 'SENSE_STATUS':
            return self.sense()
        if text == 'ADC_SENSE_STATUS':
            return dict(record_id=self.record, sense_generation=self.generation, relay_mask=self.mask)
        if text.startswith('SENSE_SELECT '):
            _, path, wait = text.split()
            self.selected = PATHS[path]
            self.mask = 33 | (16 if path != 'PROBE' else 0) | (64 if path == 'VA' else 0)
            self.generation += 1
            self.deadline = self.now + int(wait) / 1000.
            return dict(ok=True)
        if text == 'HALT':
            self.dac['enabled'] = 0
            return dict(ok=True)
        raise AssertionError('未知模拟命令:' + text)

    def capture(self, log2):
        assert self.now >= self.deadline and log2 == 0
        self.captures.append(self.selected)
        self.record += 1
        tick = self.record * 1000000 + round(self.now * 25000000)
        meta = dict(metadata_version=2, generation=self.record, record_id=self.record, sample_count=8192,
                    complete=True, errors=0, dds_ftw=POINT['dds_ftw'], dac_tag=7, inverted=False,
                    interval_ticks=1, first_tick=tick, last_tick=tick+8191,
                    first_phase_word=tick*POINT['dds_ftw'] & 0xffffffff,
                    last_phase_word=(tick+8191)*POINT['dds_ftw'] & 0xffffffff,
                    raw_clock_hz=25000000, decimation_log2=0, filter_id=0, pipeline_tick_compensation=0)
        decoded = dict(metadata=meta, samples=[0]*8192, flags=8, overrange_count=0, mode=0)
        if self.mutation:
            self.mutation(self, decoded)
        self.now += .001
        return decoded, '明确标为合成帧'.encode('utf-8')


class SenseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.board = FakeBoard()
        self.flow = SenseSequence(send=self.board.send, capture=self.board.capture,
                                  clock=lambda: self.board.now, sleep=self.board.sleep,
                                  lock=contextlib.nullcontext)
        self.args = dict(folder=Path(self.temporary.name)/'result', point=POINT,
                         group_id='g1', node_id='C2', rs_nominal_ohm=100,
                         rs_measured_ohm=100.12, calibration_id='uncalibrated',
                         path_wait_ms=37, dut_wait_seconds=.123)

    def run_group(self, **updates):
        args = dict(self.args, **updates)
        return self.flow.capture_group(**args)

    def fails_and_halts(self):
        with self.assertRaises((ValueError, TimeoutError, RuntimeError)):
            self.run_group()
        self.assertEqual(self.board.commands[-1], 'HALT')
        report = json.loads((self.args['folder']/'group.json').read_text(encoding='utf-8'))
        self.assertFalse(report['acquisition_complete'])
        self.assertIn('error', report)

    def test_continuous_group_and_log(self):
        result = self.run_group()
        self.assertEqual(self.board.captures, [1, 2, 3, 1])
        self.assertTrue(result['acquisition_complete'])
        self.assertFalse(result['measurement_qualified'])
        self.assertTrue(result['dac_left_enabled'])
        self.assertFalse(any(c.startswith(('DAC_SET', 'DAC_ENABLE', 'RELAY_SET', 'HALT')) for c in self.board.commands))
        self.assertEqual(len(list(self.args['folder'].glob('*.packet'))), 4)
        self.assertEqual(result['frames'][-1]['role'], 'reference_check')

    def test_impedance_sequence(self):
        self.run_group(include_probe=False)
        self.assertEqual(self.board.captures, [1, 2, 1])

    def test_old_firmware_refused_without_control(self):
        self.board.old = True
        with self.assertRaises(ValueError): self.run_group()
        self.assertEqual(self.board.commands, ['INFO'])

    def test_wait_timeout_halts(self):
        self.board.never_ready = True
        self.fails_and_halts()

    def test_wrong_rs(self):
        with self.assertRaises(ValueError): self.run_group(rs_nominal_ohm=1000)
        self.assertEqual(self.board.commands, [])

    def test_zero_or_nonfinite_measured_rs(self):
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError): self.run_group(rs_measured_ohm=value)

    def test_short_path_wait(self):
        with self.assertRaises(ValueError): self.run_group(path_wait_ms=9)

    def test_unknown_calibration_identity(self):
        with self.assertRaises(ValueError): self.run_group(calibration_id='')

    def test_wrong_frequency(self):
        self.board.dac['frequency_word'] += 1
        self.fails_and_halts()

    def test_bad_dac_state(self):
        self.board.dac['clipped'] = 1
        self.fails_and_halts()

    def test_route_binding_changed(self):
        def change(board, decoded):
            board.generation += 1
            board.mask = 49
        self.board.mutation = change
        self.fails_and_halts()

    def test_excitation_changed(self):
        self.board.mutation = lambda board, decoded: board.dac.update(gain_q11=65)
        self.fails_and_halts()

    def test_adc_overrange(self):
        self.board.mutation = lambda board, decoded: decoded.update(overrange_count=1)
        self.fails_and_halts()

    def test_adc_fullscale(self):
        self.board.mutation = lambda board, decoded: decoded['samples'].__setitem__(0, 2047)
        self.fails_and_halts()

    def test_phase_error(self):
        self.board.mutation = lambda board, decoded: decoded['metadata'].update(last_phase_word=1)
        self.fails_and_halts()

    def test_wrong_tag(self):
        self.board.mutation = lambda board, decoded: decoded['metadata'].update(dac_tag=8)
        self.fails_and_halts()

    def test_transport_crc_failure(self):
        def bad_capture(log2):
            error = ValueError('合成CRC失败'); error.packet = b'bad crc'; raise error
        self.flow.capture = bad_capture
        self.fails_and_halts()
        self.assertEqual((self.args['folder']/'failure.packet').read_bytes(), b'bad crc')

    def test_pl_callback_failure(self):
        def bad_pl(decoded, point): raise RuntimeError('PL失败')
        with self.assertRaises(RuntimeError): self.run_group(process_frame=bad_pl)
        self.assertEqual(self.board.commands[-1], 'HALT')

    def test_cancel_during_wait(self):
        def cancel(seconds): raise KeyboardInterrupt('取消任务')
        self.flow.sleep = cancel
        with self.assertRaises(KeyboardInterrupt): self.run_group()
        self.assertEqual(self.board.commands[-1], 'HALT')

    def test_existing_output_not_overwritten(self):
        self.run_group()
        with self.assertRaises(FileExistsError): self.run_group()

    def test_reconfigure_requires_lifted(self):
        with self.assertRaises(RuntimeError):
            self.flow.configure_analog_route(100, lambda: False)
        self.assertEqual(self.board.commands, ['INFO', 'HALT'])
        self.assertEqual(self.board.mask, 33)

    def test_invalid_rs_route_without_commands(self):
        with self.assertRaises(ValueError):
            self.flow.configure_analog_route(1000, lambda: True)
        self.assertEqual(self.board.commands, [])

    def test_reused_frame_rejected(self):
        def reused(board, decoded):
            if board.record == 2: decoded['metadata']['record_id'] = 1
        self.board.mutation = reused
        self.fails_and_halts()

    def test_cross_frame_phase_jump(self):
        def jump(board, decoded):
            if board.record == 2:
                for key in ('first_phase_word', 'last_phase_word'):
                    decoded['metadata'][key] = (decoded['metadata'][key] + 123) & 0xffffffff
        self.board.mutation = jump
        self.fails_and_halts()

    @staticmethod
    def mock_pl(decoded, point, amplitude=100.):
        meta = decoded['metadata']
        phase = meta['first_phase_word'] * 2 * math.pi / (1 << 32)
        return dict(frame_id=meta['record_id'], abi=0x20261014,
                    cosine_q16=round(amplitude*math.sin(phase)*65536),
                    sine_q16=round(amplitude*math.cos(phase)*65536))

    def test_reference_drift_computed_with_dds(self):
        result = self.run_group(process_frame=self.mock_pl,
                                reference_limits=dict(relative_amplitude=.01, phase_degrees=.1))
        self.assertTrue(result['reference_drift']['within_limits'])
        self.assertFalse(result['measurement_qualified'])

    def test_reference_drift_failure_halts(self):
        def process(decoded, point):
            return self.mock_pl(decoded, point, 110. if decoded['metadata']['record_id'] == 4 else 100.)
        with self.assertRaises(ValueError):
            self.run_group(process_frame=process,
                           reference_limits=dict(relative_amplitude=.01, phase_degrees=.1))
        self.assertEqual(self.board.commands[-1], 'HALT')

    def test_reference_limits_require_pl(self):
        with self.assertRaises(ValueError):
            self.run_group(reference_limits=dict(relative_amplitude=.01, phase_degrees=.1))
        self.assertEqual(self.board.commands, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
