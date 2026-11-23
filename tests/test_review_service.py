import sqlite3
import unittest

from emolabel2.domain import (
    CandidateRecord,
    Emotion,
    PredictionRecord,
    ReviewRecord,
    ReviewStatus,
    RightsStatus,
    VideoRecord,
)
from emolabel2.review.service import CandidateNotFoundError, list_review_events, record_review
from emolabel2.storage import initialize_schema, insert_candidate, insert_prediction, insert_video, open_database


class ReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        insert_video(
            self.connection,
            VideoRecord(
                "v1", "data/inbox/source.bin", "a" * 64, "synthetic",
                RightsStatus.APPROVED_RESEARCH, "test fixture", "unit tests",
                10, "2026-11-23T00:00:00+00:00",
            ),
        )
        insert_candidate(
            self.connection,
            CandidateRecord("c1", "v1", 0, 3_000),
            clip_path="data/candidates/c1.bin",
            file_hash="b" * 64,
        )

    def tearDown(self):
        self.connection.close()

    def test_all_review_states_and_history_are_preserved(self):
        reviews = [
            ReviewRecord("c1", ReviewStatus.PENDING, None, 1, "t1"),
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 2, "t2"),
            ReviewRecord("c1", ReviewStatus.REJECTED, None, 3, "t3", "noise"),
            ReviewRecord("c1", ReviewStatus.DEFERRED, None, 4, "t4"),
        ]
        for review in reviews:
            record_review(self.connection, review)

        current = self.connection.execute(
            "SELECT * FROM reviews WHERE candidate_id = 'c1'"
        ).fetchone()
        self.assertEqual(current["review_status"], "deferred")
        self.assertIsNone(current["gold_emotion"])
        events = list_review_events(self.connection, "c1")
        self.assertEqual(len(events), 4)
        self.assertIsNone(events[0]["previous_status"])
        self.assertEqual(events[1]["previous_status"], "pending")
        self.assertEqual(events[1]["new_gold_emotion"], "happy")
        self.assertEqual(events[2]["previous_gold_emotion"], "happy")

    def test_ai_prediction_is_separate_from_human_gold(self):
        insert_prediction(
            self.connection,
            PredictionRecord("c1", Emotion.ANGRY, "synthetic-model", "p1", 0.1),
        )
        record_review(
            self.connection,
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 2, "t1"),
        )
        prediction = self.connection.execute(
            "SELECT emotion FROM predictions WHERE candidate_id = 'c1'"
        ).fetchone()
        review = self.connection.execute(
            "SELECT gold_emotion FROM reviews WHERE candidate_id = 'c1'"
        ).fetchone()
        self.assertEqual(prediction["emotion"], "angry")
        self.assertEqual(review["gold_emotion"], "happy")

    def test_unknown_candidate_is_rejected_without_history(self):
        with self.assertRaises(CandidateNotFoundError):
            record_review(
                self.connection,
                ReviewRecord("missing", ReviewStatus.PENDING, None, 0, "t1"),
            )
        count = self.connection.execute("SELECT count(*) FROM review_events").fetchone()[0]
        self.assertEqual(count, 0)

    def test_zero_duration_without_explicit_measurement_is_not_measured(self):
        event_id = record_review(
            self.connection,
            ReviewRecord("c1", ReviewStatus.PENDING, None, 0, "t1"),
        )
        row = self.connection.execute(
            "SELECT * FROM review_measurements WHERE review_event_id=?", (event_id,)
        ).fetchone()
        self.assertEqual(row["measurement_status"], "not_measured")
        self.assertIsNone(row["elapsed_s"])

    def test_explicit_review_timestamps_compute_elapsed_time(self):
        event_id = record_review(
            self.connection,
            ReviewRecord("c1", ReviewStatus.PENDING, None, 0, "t1"),
            review_started_at="2026-11-23T10:00:00+09:00",
            review_ended_at="2026-11-23T10:00:08.500000+09:00",
        )
        row = self.connection.execute(
            "SELECT * FROM review_measurements WHERE review_event_id=?", (event_id,)
        ).fetchone()
        self.assertEqual(row["measurement_status"], "measured")
        self.assertEqual(row["measurement_source"], "explicit_timestamps")
        self.assertEqual(row["elapsed_s"], 8.5)


if __name__ == "__main__":
    unittest.main()
