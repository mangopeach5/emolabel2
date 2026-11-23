import tempfile
import unittest
from pathlib import Path

from emolabel2.domain import CandidateRecord, Emotion, ReviewRecord, ReviewStatus, RightsStatus, VideoRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.review.export import (
    CandidateNotAcceptedError,
    DestinationExistsError,
    DuplicateClipExportError,
    ExportIntegrityError,
    ExportStateMismatchError,
    export_accepted_candidate,
)
from emolabel2.review.service import record_review
from emolabel2.review.workflow import save_review_and_export_if_accepted
from emolabel2.storage import initialize_schema, insert_candidate, insert_video, open_database


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in ("data/inbox", "data/candidates", "data/final_dataset"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.source = self.root / "data/candidates/c1.bin"
        self.original = b"synthetic-candidate-clip"
        self.source.write_bytes(self.original)

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
            file_hash=sha256_file(self.source),
        )

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def review(self, status, gold=None, when="t1", reason=""):
        record_review(
            self.connection,
            ReviewRecord("c1", status, gold, 1, when, reason),
        )

    def test_only_explicit_acceptance_exports_annotation_is_ignored(self):
        (self.root / "data/candidates/annotation.json").write_text("{}", encoding="utf-8")
        self.review(ReviewStatus.PENDING)
        with self.assertRaises(CandidateNotAcceptedError):
            export_accepted_candidate(self.connection, self.root, "c1")
        self.assertEqual(list((self.root / "data/final_dataset").iterdir()), [])

    def test_export_verifies_hash_and_preserves_source(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY)
        result = export_accepted_candidate(self.connection, self.root, "c1")
        self.assertFalse(result.already_exported)
        self.assertEqual(result.destination.read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(sha256_file(result.destination), sha256_file(self.source))
        row = self.connection.execute("SELECT * FROM exports").fetchone()
        self.assertEqual(row["export_status"], "completed")
        self.assertEqual(row["exported_gold_emotion"], "happy")

    def test_high_level_workflow_exports_only_after_acceptance(self):
        pending = save_review_and_export_if_accepted(
            self.connection,
            self.root,
            ReviewRecord("c1", ReviewStatus.PENDING, None, 1, "t1"),
        )
        self.assertIsNone(pending)
        self.assertEqual(list((self.root / "data/final_dataset").iterdir()), [])

        accepted = save_review_and_export_if_accepted(
            self.connection,
            self.root,
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 1, "t2"),
        )
        self.assertTrue(accepted.destination.exists())

    def test_repeated_export_is_idempotent(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY)
        first = export_accepted_candidate(self.connection, self.root, "c1")
        second = export_accepted_candidate(self.connection, self.root, "c1")
        self.assertTrue(second.already_exported)
        self.assertEqual(first.export_id, second.export_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM exports").fetchone()[0], 1)
        self.assertEqual(len(list((self.root / "data/final_dataset").iterdir())), 1)

    def test_existing_destination_is_never_overwritten(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY)
        destination = self.root / "data/final_dataset/c1.bin"
        destination.write_bytes(b"pre-existing")
        with self.assertRaises(DestinationExistsError):
            export_accepted_candidate(self.connection, self.root, "c1")
        self.assertEqual(destination.read_bytes(), b"pre-existing")

    def test_same_clip_hash_under_another_candidate_is_not_exported_twice(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY)
        export_accepted_candidate(self.connection, self.root, "c1")
        insert_candidate(
            self.connection,
            CandidateRecord("c2", "v1", 3_000, 6_000),
            clip_path="data/candidates/c1.bin",
            file_hash=sha256_file(self.source),
        )
        record_review(
            self.connection,
            ReviewRecord("c2", ReviewStatus.ACCEPTED, Emotion.HAPPY, 1, "t2"),
        )
        with self.assertRaises(DuplicateClipExportError):
            export_accepted_candidate(self.connection, self.root, "c2")
        self.assertFalse((self.root / "data/final_dataset/c2.bin").exists())

    def test_hash_failure_is_failed_not_completed_and_can_retry(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY)
        self.source.write_bytes(b"changed")
        with self.assertRaises(ExportIntegrityError):
            export_accepted_candidate(self.connection, self.root, "c1")
        failed = self.connection.execute("SELECT * FROM exports").fetchone()
        self.assertEqual(failed["export_status"], "failed")
        self.assertFalse((self.root / "data/final_dataset/c1.bin").exists())
        self.assertEqual(list((self.root / "data/final_dataset").glob("*.partial.*")), [])

        self.source.write_bytes(self.original)
        completed = export_accepted_candidate(self.connection, self.root, "c1")
        self.assertEqual(completed.export_id, failed["export_id"])
        state = self.connection.execute("SELECT export_status FROM exports").fetchone()[0]
        self.assertEqual(state, "completed")

    def test_review_cancellation_after_export_creates_issue(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY, "t1")
        export_accepted_candidate(self.connection, self.root, "c1")
        self.review(ReviewStatus.DEFERRED, None, "t2")
        issue = self.connection.execute(
            "SELECT * FROM export_issues WHERE resolved_at IS NULL"
        ).fetchone()
        self.assertEqual(issue["issue_type"], "review_status_changed")

        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY, "t3")
        unresolved = self.connection.execute(
            "SELECT count(*) FROM export_issues WHERE resolved_at IS NULL"
        ).fetchone()[0]
        self.assertEqual(unresolved, 0)

    def test_gold_change_after_export_is_tracked_and_not_reexported(self):
        self.review(ReviewStatus.ACCEPTED, Emotion.HAPPY, "t1")
        export_accepted_candidate(self.connection, self.root, "c1")
        self.review(ReviewStatus.ACCEPTED, Emotion.SAD, "t2")
        issue = self.connection.execute(
            "SELECT * FROM export_issues WHERE resolved_at IS NULL"
        ).fetchone()
        self.assertEqual(issue["issue_type"], "gold_emotion_changed")
        with self.assertRaises(ExportStateMismatchError):
            export_accepted_candidate(self.connection, self.root, "c1")
        self.assertEqual(len(list((self.root / "data/final_dataset").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
