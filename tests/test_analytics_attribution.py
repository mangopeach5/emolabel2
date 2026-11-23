import tempfile
import unittest
from pathlib import Path

from emolabel2.analytics.runtime import create_run, finish_run, link_candidate
from emolabel2.analytics.summary import build_run_summaries, compare_workflows
from emolabel2.domain import CandidateRecord, Emotion, ReviewRecord, ReviewStatus, RightsStatus, VideoRecord
from emolabel2.review.service import record_review
from emolabel2.storage import initialize_schema, insert_candidate, insert_video, open_database


class AnalyticsAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        insert_video(self.connection, VideoRecord(
            "v1", "data/inbox/v.mp4", "a" * 64, "synthetic",
            RightsStatus.APPROVED_RESEARCH, "test", "test", 11.4, "now",
        ))
        insert_candidate(
            self.connection, CandidateRecord("c1", "v1", 0, 3200),
            clip_path="data/candidates/c1.mp4", file_hash="b" * 64,
        )
        for run_id in ("run-a", "run-b"):
            create_run(
                self.connection, run_id=run_id, video_id="v1",
                test_case_id="same", workflow_mode="automated", started_at="s",
            )
            finish_run(self.connection, run_id, elapsed_s=1, success=True, ended_at="e")
            link_candidate(self.connection, run_id, "c1")

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_current_review_is_not_duplicated_as_historical_run_outcome(self):
        record_review(
            self.connection,
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.SAD, 0, "t"),
        )
        summaries = {item.run_id: item for item in build_run_summaries(self.connection, self.root)}
        for summary in summaries.values():
            self.assertEqual(summary.current_linked_state["accepted"], 1)
            self.assertIsNone(summary.accepted)
            self.assertIsNone(summary.valid_clips)

    def test_explicit_run_outcome_is_attributed_to_one_run(self):
        record_review(
            self.connection,
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.SAD, 4, "t"),
            run_id="run-b", human_time_measured=True,
        )
        summaries = {item.run_id: item for item in build_run_summaries(self.connection, self.root)}
        self.assertIsNone(summaries["run-a"].accepted)
        self.assertEqual(summaries["run-b"].accepted, 1)
        self.assertEqual(summaries["run-b"].human_review_s, 4)

    def test_missing_measurements_are_null_and_do_not_create_comparison(self):
        summaries = build_run_summaries(self.connection, self.root)
        self.assertTrue(all(item.human_review_s is None for item in summaries))
        self.assertTrue(all(item.manual_interventions is None for item in summaries))
        self.assertEqual(compare_workflows(summaries), [])


if __name__ == "__main__":
    unittest.main()
