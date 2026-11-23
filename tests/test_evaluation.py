import unittest

from emolabel2.analytics.evaluation import classification_report


class EvaluationTests(unittest.TestCase):
    def test_no_gold_returns_none(self):
        self.assertIsNone(classification_report([]))

    def test_confusion_precision_recall_f1_and_macro(self):
        report = classification_report([
            ("happy", "happy"),
            ("happy", "sad"),
            ("sad", "sad"),
            ("angry", None),
            ("neutral", "happy"),
        ])
        self.assertEqual(report.evaluated, 5)
        self.assertEqual(report.abstained, 1)
        self.assertEqual(report.confusion_matrix[3][3], 1)  # happy -> happy
        self.assertAlmostEqual(report.per_class["happy"].recall, 0.5)
        self.assertAlmostEqual(report.per_class["sad"].precision, 0.5)
        self.assertGreaterEqual(report.macro_f1, 0)
        self.assertLessEqual(report.macro_f1, 1)

    def test_invalid_labels_are_rejected(self):
        with self.assertRaises(ValueError):
            classification_report([("invented", "happy")])
        with self.assertRaises(ValueError):
            classification_report([("happy", "invented")])


if __name__ == "__main__":
    unittest.main()
