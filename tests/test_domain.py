import math
import unittest

from emolabel2.domain import CandidateRecord, Emotion, ReviewRecord, ReviewStatus, RightsStatus, VideoRecord


class DomainTests(unittest.TestCase):
    def test_valid_video(self):
        video = VideoRecord(
            "v1", "data/inbox/sample.bin", "a" * 64, "synthetic",
            RightsStatus.APPROVED_RESEARCH, "test permission", "unit tests",
            10.0, "2026-11-23T00:00:00+00:00",
        )
        self.assertEqual(video.video_id, "v1")

    def test_invalid_hash_and_duration(self):
        common = dict(
            video_id="v1", local_path="data/inbox/sample.bin", source_name="synthetic",
            rights_status=RightsStatus.APPROVED_RESEARCH, rights_basis="test permission",
            allowed_use="unit tests", added_at="2026-11-23T00:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            VideoRecord(file_hash="bad", duration_s=1, **common)
        with self.assertRaises(ValueError):
            VideoRecord(file_hash="a" * 64, duration_s=math.nan, **common)

    def test_gold_label_requires_explicit_acceptance(self):
        with self.assertRaises(ValueError):
            ReviewRecord("c1", ReviewStatus.PENDING, Emotion.HAPPY, 1, "now")
        with self.assertRaises(ValueError):
            ReviewRecord("c1", ReviewStatus.ACCEPTED, None, 1, "now")
        accepted = ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 1, "now")
        self.assertEqual(accepted.gold_emotion, Emotion.HAPPY)

    def test_rejection_requires_reason(self):
        with self.assertRaises(ValueError):
            ReviewRecord("c1", ReviewStatus.REJECTED, None, 1, "now")

    def test_candidate_duration_bounds(self):
        CandidateRecord("c1", "v1", 0, 3_000)
        CandidateRecord("c2", "v1", 0, 12_000)
        with self.assertRaises(ValueError):
            CandidateRecord("short", "v1", 0, 2_999)
        with self.assertRaises(ValueError):
            CandidateRecord("long", "v1", 0, 12_001)


if __name__ == "__main__":
    unittest.main()
