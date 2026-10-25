"""校验原码打包、整数矩和有解析解的单阶跃，不把合成数据当实板。"""

import math
import unittest
from adc_features import decode_words, code_statistics, step_features


class AdcFeaturesTest(unittest.TestCase):
    def test_explicit_encoding_and_byteorder(self):
        values = [0, 0x8FFF, 0x7FF, 0x800]
        for order in ("little", "big"):
            payload = b"".join(value.to_bytes(2, order) for value in values)
            codes, raw, otr = decode_words(payload, order, "twos_complement")
            self.assertEqual(codes, [0, -1, 2047, -2048])
            self.assertEqual(raw, [0, 4095, 2047, 2048])
            self.assertEqual(otr, [False, True, False, False])
            self.assertEqual(decode_words(payload, order, "straight_binary")[0], raw)

    def test_invalid_format_is_rejected(self):
        for payload, order, encoding in ((b"", "little", "straight_binary"),
                (b"\x00", "little", "straight_binary"), (b"\x00\x10", "little", "straight_binary"),
                (b"\x00\x00", "guess", "straight_binary"), (b"\x00\x00", "little", "guess")):
            with self.subTest(payload=payload, order=order, encoding=encoding):
                with self.assertRaises(ValueError):
                    decode_words(payload, order, encoding)

    def test_small_ripple_with_large_dc(self):
        values = [4094, 4095] * 4096
        result = code_statistics(values, values, [False] * len(values))
        self.assertEqual(result["mean_code"], 4094.5)
        self.assertEqual(result["variance_codes_squared"], 0.25)
        self.assertEqual(result["ac_rms_codes"], 0.5)
        self.assertFalse(result["voltage_calibrated"])

    def test_rising_and_falling_exponential(self):
        rate, tau = 200000, 0.001
        for initial, final in ((200, 3500), (3500, 200)):
            values = [round(final + (initial - final) * math.exp(-i / rate / tau)) for i in range(801)]
            result = step_features(values, [False] * len(values), rate,
                {"start_index": 0, "end_index": len(values), "initial_code": initial, "final_code": final})
            self.assertAlmostEqual(result["tau_63_seconds"], tau, delta=1 / rate)
            self.assertAlmostEqual(result["tau_log_fit_seconds"], tau, delta=tau * 0.001)
            self.assertGreater(result["fit_r_squared"], 0.99999)

    def test_unavailable_and_overrange(self):
        step = {"start_index": 0, "end_index": 10, "initial_code": 0, "final_code": 4095}
        result = step_features([0] * 10, [False] * 10, 200000, step)
        self.assertIsNone(result["tau_63_seconds"])
        self.assertIsNone(result["tau_log_fit_seconds"])
        otr = [False] * 10; otr[5] = True
        result = step_features(list(range(10)), otr, 200000, step)
        self.assertIsNone(result["tau_63_seconds"])
        self.assertIn("OTR", result["reason"])
        step["final_code"] = 0
        result = step_features([0] * 10, [False] * 10, 200000, step)
        self.assertIn("初末码相同", result["reason"])


if __name__ == "__main__":
    unittest.main()
