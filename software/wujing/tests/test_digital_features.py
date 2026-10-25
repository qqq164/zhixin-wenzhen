"""验证完整脉冲、窗口截断、量化与常电平语义，测试数据是明确的人工样例。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from digital_features import channel_features


class FeatureTests(unittest.TestCase):
    def test_complete_cycles_and_clipped_boundaries(self):
        features = channel_features(bytes([1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1]), 0, 3, 100)
        self.assertEqual(features["rising_indices"], [4, 9])
        self.assertEqual(features["falling_indices"], [2, 7])
        self.assertEqual(features["period_ticks"], [15])
        self.assertEqual(features["complete_high_width_ticks"], [9])
        self.assertEqual(features["complete_low_width_ticks"], [6, 6])
        self.assertEqual(features["full_cycle_high_fraction"], 3 / 5)
        self.assertEqual(features["first_to_last_ticks"], 30)
        self.assertIsNone(features["fault_label"])

    def test_constant_levels_do_not_invent_frequency(self):
        for level in (0, 1):
            features = channel_features(bytes([level] * 12), 0, 2, 100)
            self.assertEqual(features["steady_level"], level)
            self.assertIsNone(features["frequency_hz_from_edges"])
            self.assertEqual(features["complete_high_width_ticks"], [])
            self.assertEqual(features["complete_low_width_ticks"], [])
            self.assertIsNone(features["full_cycle_high_fraction"])

    def test_single_edge_cannot_measure_a_period(self):
        features = channel_features(bytes([0, 0, 1, 1]), 0, 1, 100)
        self.assertEqual(features["transition_count"], 1)
        self.assertIsNone(features["frequency_hz_from_edges"])
        self.assertEqual(features["complete_high_width_ticks"], [])

    def test_channels_are_independent(self):
        data = bytes([0, 1, 3, 2])
        self.assertEqual(channel_features(data, 0, 1, 100)["falling_indices"], [3])
        self.assertEqual(channel_features(data, 1, 1, 100)["rising_indices"], [2])

    def test_invalid_parameters(self):
        for data, bit, interval, clock in ((b"", 0, 1, 100), (b"\x00", 8, 1, 100),
                                           (b"\x00", 0, 0, 100), (b"\x00", 0, 1, 0)):
            with self.assertRaises(ValueError):
                channel_features(data, bit, interval, clock)


if __name__ == "__main__":
    unittest.main()
