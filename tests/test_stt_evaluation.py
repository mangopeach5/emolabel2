import unittest

from emolabel2.stt.evaluation import character_error_rate, timestamp_mean_absolute_error


class SttEvaluationTests(unittest.TestCase):
    def test_cer_uses_human_reference(self):
        self.assertEqual(character_error_rate("안녕", "안녕"), 0)
        self.assertEqual(character_error_rate("안녕", "안경"), 0.5)

    def test_missing_gold_returns_none(self):
        self.assertIsNone(character_error_rate(None, "추측"))
        self.assertIsNone(timestamp_mean_absolute_error(None, [(0, 100)]))

    def test_word_timestamp_mae_requires_paired_boundaries(self):
        result = timestamp_mean_absolute_error(
            [(100, 500), (600, 900)], [(120, 480), (650, 950)]
        )
        self.assertEqual(result, {"start_mae_ms": 35, "end_mae_ms": 35})
        self.assertIsNone(timestamp_mean_absolute_error([(0, 1)], []))


if __name__ == "__main__":
    unittest.main()
