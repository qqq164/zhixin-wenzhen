"""模拟DIG5服务，验证采集过程中激励身份变化会被拒绝且异常后关断。"""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analog_dig5_client as client


class Model:
    def __init__(self, change_tag=False, revision='DIG5'):
        self.enabled = False
        self.tag = 1
        self.record_id = 0
        self.status_reads = 0
        self.change_tag = change_tag
        self.revision = revision
        self.commands = []

    def command(self, host, text):
        self.commands.append(text)
        words = text.split()
        kind = words[0]
        reply = {'ok': True}
        if kind == 'HALT':
            self.enabled = False
        elif kind == 'INFO':
            reply.update(hardware_revision=self.revision, dac_model='DAC902', dds_ready=True, adc_ready=True)
        elif kind == 'DAC_SET':
            self.frequency, self.gain, self.offset, self.phase, self.invert = map(int, words[1:])
        elif kind == 'DAC_ENABLE':
            self.enabled = bool(int(words[1]))
        elif kind == 'DAC_STATUS':
            self.status_reads += 1
            if self.change_tag and self.status_reads == 3:
                self.tag += 1
            reply.update(ready=1, configured=1, enabled=int(self.enabled), tag=self.tag,
                         frequency_word=((self.frequency << 32) + 12500000) // 25000000,
                         gain_q11=self.gain, offset_code=self.offset, phase_word=self.phase)
        elif kind == 'ADC_CAPTURE':
            self.record_id += 1
            reply['record_id'] = self.record_id
        elif kind == 'ADC_STATUS':
            reply.update(record_id=self.record_id, sample_count=8192, flags=8,
                         encoding='signed16_le', sample_rate_hz=25000000)
        elif kind == 'ADC_READ':
            record_id, start, count = map(int, words[1:])
            reply.update(record_id=record_id, start=start, count=count,
                         hex=struct.pack('<{}h'.format(count), *[(start + index) % 4096 - 2048 for index in range(count)]).hex())
        elif kind == 'MODE_STATUS':
            reply.update(mode=0, output_enabled=0)
        else:
            raise AssertionError('模型收到未知命令：' + text)
        return reply


class CaptureTests(unittest.TestCase):
    def test_three_frames_and_output_disabled(self):
        model = Model()
        with tempfile.TemporaryDirectory() as directory, patch.object(client, 'command', model.command):
            result = client.capture('model', Path(directory), settle_seconds=0)
            self.assertTrue(result['passed'])
            self.assertEqual(len(result['frames']), 3)
            self.assertFalse(model.enabled)
            self.assertFalse(result['dac_analog_output_verified'])
            self.assertTrue(all(row['excitation_before']['tag'] == row['excitation_after']['tag'] for row in result['frames']))

    def test_changed_excitation_rejected_and_halt(self):
        model = Model(change_tag=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(client, 'command', model.command):
            output = Path(directory)
            with self.assertRaisesRegex(RuntimeError, '激励条件发生改变'):
                client.capture('model', output, settle_seconds=0)
            self.assertFalse(model.enabled)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['passed'])
            self.assertEqual(report['frames'], [])


if __name__ == '__main__':
    unittest.main()
