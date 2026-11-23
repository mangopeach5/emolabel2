"""Synthetic input unit tests; do NOT use these numbers as research evidence."""
import sys
import math
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from emolabel2.metrics import RunMetrics, labor_time_reduction


class MetricsTests(unittest.TestCase):
    def test_rates(self):
        m = RunMetrics(12, 6, candidates=5, reviewed=4, accepted=2, manual_review_s=40)
        self.assertAlmostEqual(m.rtf, .5)
        self.assertAlmostEqual(m.accepted_per_reviewed, .5)
        self.assertAlmostEqual(m.human_s_per_gold, 20)

    def test_zero_denominator(self):
        m = RunMetrics(0, 0)
        self.assertIsNone(m.rtf)
        self.assertIsNone(m.accepted_per_reviewed)
        self.assertIsNone(m.human_s_per_gold)
        self.assertIsNone(labor_time_reduction(0, 2))

    def test_invalid_counts(self):
        with self.assertRaises(ValueError):
            RunMetrics(10, 2, candidates=1, reviewed=1, accepted=2).check()

    def test_reduction(self):
        self.assertAlmostEqual(labor_time_reduction(300, 120), .6)
        self.assertAlmostEqual(labor_time_reduction(120, 180), -.5)

    def test_nan_infinity_and_non_integer_counts_are_rejected(self):
        for value in (math.nan, math.inf):
            with self.assertRaises(ValueError):
                RunMetrics(value, 1).check()
            with self.assertRaises(ValueError):
                labor_time_reduction(value, 1)
        with self.assertRaises(ValueError):
            RunMetrics(10, 1, candidates=1.5).check()


if __name__ == "__main__":
    unittest.main()
