"""离线验证同步ADC客户端的拒绝、清理和分块接收，不连接真实板卡。"""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "software/wujing"))
import adc_sync_client


class BoardModel:
    """仅提供协议行为模型；模拟字节不作为ADC实测证据。"""
    def __init__(self, fault=None):
        self.fault = fault
        self.calls = []
        self.mode = self.generation = self.record = 0
        self.period = self.high = self.enabled = 0
        self.trigger = "software_immediate"
        self.trigger_status = 0
        self.payload = struct.pack("<8192h", *[index % 4096 - 2048 for index in range(8192)])

    def command(self, host, text):
        self.calls.append(text)
        values = text.split()
        name = values[0]
        if name == "INFO":
            return {"ok": True, "project": "FDW_PWM", "protocol": 2,
                    "hardware_revision": "DIG3" if self.fault == "old_revision" else "DIG4",
                    "adc_pwm_sync": True, "adc_ready": True}
        if name == "HALT":
            if self.fault == "cleanup" and self.record >= 4:
                return {"ok": False, "code": -1}
            self.mode = self.enabled = 0
            self.generation += 1
            return {"ok": True}
        if name == "MODE_STATUS":
            return {"ok": True, "mode": self.mode, "generation": self.generation,
                    "output_enabled": 0, "pwm_active": self.enabled,
                    "trigger_source": 1, "finite": 0, "settling": 0}
        if name == "MODE_SET":
            if values[1:] != ["1", "1", "0"]:
                raise AssertionError("客户端试图启用非内部模式")
            self.mode = 1
            self.generation += 1
            return {"ok": True}
        if name == "PWM_SET":
            self.period, self.high = map(int, values[1:])
            return {"ok": True}
        if name == "PWM_ENABLE":
            self.enabled = int(values[1])
            return {"ok": True}
        if name in ("ADC_CAPTURE", "ADC_CAPTURE_SYNC"):
            if name.endswith("SYNC") and not self.enabled:
                return {"ok": False, "code": -19}
            self.record += 1
            self.trigger = "pwm_period_event" if name.endswith("SYNC") else "software_immediate"
            self.trigger_status = 0x50 if name.endswith("SYNC") else 0
            self.frame_generation = self.generation
            return {"ok": True, "record_id": self.record}
        if name == "ADC_STATUS":
            return {"ok": True, "record_id": self.record, "sample_count": 8192,
                    "flags": 4 if self.fault == "discontinuous" else 8,
                    "sample_rate_hz": 25000000, "encoding": "signed16_le",
                    "mode": self.mode, "route_generation": self.frame_generation,
                    "trigger": self.trigger,
                    "trigger_status": 0 if self.fault == "missing_trigger" else self.trigger_status,
                    "overrange_count": 0}
        if name == "ADC_READ":
            record, start, count = map(int, values[1:])
            if self.generation != self.frame_generation or record != self.record:
                return {"ok": False, "code": -4}
            data = self.payload[start * 2:(start + count) * 2]
            if self.fault == "short_block" and start == 32: data = data[:-1]
            if self.fault == "wrong_block_id" and start == 32: record += 1
            if self.fault == "changed_route" and start == 64: self.generation += 1
            return {"ok": True, "record_id": record, "start": start, "count": count, "hex": data.hex()}
        raise AssertionError("模型不支持的命令：" + text)


class SyncClientTests(unittest.TestCase):
    def execute(self, fault=None):
        model = BoardModel(fault)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with patch.object(adc_sync_client, "command", model.command), patch.object(adc_sync_client.time, "sleep"):
                if fault and fault != "cleanup":
                    with self.assertRaises(RuntimeError): adc_sync_client.verify("offline_model", output)
                else:
                    adc_sync_client.verify("offline_model", output)
            report = json.loads(output.read_text(encoding="utf-8"))
            if fault:
                self.assertFalse(report["passed"])
            else:
                self.assertTrue(report["passed"])
                self.assertEqual(len(report["frames"]), 4)
                self.assertEqual([frame["high_ticks"] for frame in report["frames"]], [400, 0, 2000, None])
                for frame in report["frames"]:
                    self.assertEqual(Path(frame["binary_path"]).read_bytes(), model.payload)
                self.assertFalse(report["analog_signal_verified"])
                self.assertFalse(report["analog_phase_reference_verified"])
            self.assertNotIn("OUTPUT_ENABLE 1", model.calls)
            self.assertEqual(model.calls[-1], "MODE_STATUS" if fault != "cleanup" else "HALT")
            if fault != "cleanup": self.assertFalse(model.enabled)
        return report

    def test_complete_internal_and_immediate(self): self.execute()
    def test_old_revision_rejected(self):
        report = self.execute("old_revision")
        self.assertEqual(report["frames"], [])
    def test_missing_trigger_rejected(self): self.execute("missing_trigger")
    def test_discontinuous_frame_rejected(self): self.execute("discontinuous")
    def test_short_block_rejected(self): self.execute("short_block")
    def test_wrong_block_identity_rejected(self): self.execute("wrong_block_id")
    def test_route_change_rejected(self): self.execute("changed_route")
    def test_cleanup_failure_marks_report_failed(self):
        report = self.execute("cleanup")
        self.assertIn("cleanup_error", report)
    def test_existing_report_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            output.write_text("原证据", encoding="utf-8")
            with patch.object(adc_sync_client, "command") as command:
                with self.assertRaises(ValueError): adc_sync_client.verify("offline_model", output)
                command.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"), "原证据")


if __name__ == "__main__": unittest.main(verbosity=2)
