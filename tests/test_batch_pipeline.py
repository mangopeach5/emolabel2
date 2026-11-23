import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from emolabel2.analytics.runtime import create_run, finish_run, link_candidate
from emolabel2.batch.pipeline import BatchConfig, run_batch, write_batch_reports
from emolabel2.domain import CandidateRecord
from emolabel2.storage import initialize_schema, insert_candidate, open_database


class BatchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in (
            "data/inbox/batch", "data/candidates", "data/final_dataset", "data/logs"
        ):
            (self.root / relative).mkdir(parents=True)
        self.a = self.root / "data/inbox/batch/a.mp4"
        self.b = self.root / "data/inbox/batch/b.mp4"
        self.a.write_bytes(b"synthetic-a")
        self.b.write_bytes(b"synthetic-b")
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        self.config = BatchConfig("approved fixture", "unit tests")
        self.calls = []

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def probe(path):
        return SimpleNamespace(duration_s=10.0)

    def successful_runner(self, connection, root, **kwargs):
        video_id, run_id = kwargs["video_id"], kwargs["run_id"]
        self.calls.append(video_id)
        create_run(
            connection, run_id=run_id, video_id=video_id,
            test_case_id=f"automatic:{video_id}", workflow_mode="automated", started_at="s",
        )
        candidate_id = f"c-{video_id[-16:]}"
        video_hash = connection.execute(
            "SELECT file_hash FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()[0]
        insert_candidate(
            connection, CandidateRecord(candidate_id, video_id, 0, 3200),
            clip_path=f"data/candidates/{candidate_id}.mp4", file_hash=video_hash,
        )
        link_candidate(connection, run_id, candidate_id)
        finish_run(connection, run_id, elapsed_s=1, success=True, ended_at="e")
        return {"selected_candidate_ids": [candidate_id], "prediction_ids": [1]}

    def test_batch_processes_each_unique_file_and_keeps_final_dataset_empty(self):
        report = run_batch(
            self.connection, self.root, batch_id="batch-one", config=self.config,
            pipeline_runner=self.successful_runner, media_probe=self.probe,
        )
        self.assertEqual(report["counts"], {"completed": 2})
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(item["candidate_count"] == 1 for item in report["items"]))
        self.assertTrue(all(item["selected_count"] == 1 for item in report["items"]))
        self.assertTrue(all(item["prediction_count"] == 1 for item in report["items"]))
        self.assertEqual(list((self.root / "data/final_dataset").iterdir()), [])

    def test_duplicate_hash_is_recorded_and_not_processed_twice(self):
        (self.root / "data/inbox/batch/a-copy.mp4").write_bytes(self.a.read_bytes())
        report = run_batch(
            self.connection, self.root, batch_id="duplicates", config=self.config,
            pipeline_runner=self.successful_runner, media_probe=self.probe,
        )
        self.assertEqual(report["counts"], {"completed": 2, "skipped_duplicate": 1})
        duplicate = next(item for item in report["items"] if item["status"] == "skipped_duplicate")
        self.assertIsNotNone(duplicate["duplicate_of_item_id"])
        self.assertEqual(len(self.calls), 2)

    def test_one_failure_does_not_stop_other_video(self):
        def runner(connection, root, **kwargs):
            local_path = connection.execute(
                "SELECT local_path FROM videos WHERE video_id=?", (kwargs["video_id"],)
            ).fetchone()[0]
            if local_path.endswith("a.mp4"):
                raise RuntimeError("synthetic failure")
            return self.successful_runner(connection, root, **kwargs)
        report = run_batch(
            self.connection, self.root, batch_id="continue", config=self.config,
            pipeline_runner=runner, media_probe=self.probe,
        )
        self.assertEqual(report["status"], "completed_with_errors")
        self.assertEqual(report["counts"], {"failed": 1, "completed": 1})
        failure = next(item for item in report["items"] if item["status"] == "failed")
        self.assertEqual(failure["error_type"], "RuntimeError")

    def test_restart_skips_completed_and_retries_failed(self):
        failed_once = set()

        def runner(connection, root, **kwargs):
            video_id = kwargs["video_id"]
            if video_id not in failed_once:
                failed_once.add(video_id)
                raise RuntimeError("first attempt")
            return self.successful_runner(connection, root, **kwargs)

        first = run_batch(
            self.connection, self.root, batch_id="restart", config=self.config,
            pipeline_runner=runner, media_probe=self.probe,
        )
        self.assertEqual(first["counts"], {"failed": 2})
        second = run_batch(
            self.connection, self.root, batch_id="restart", config=self.config,
            pipeline_runner=runner, media_probe=self.probe,
        )
        self.assertEqual(second["counts"], {"completed": 2})
        self.assertTrue(all(item["attempt_count"] == 2 for item in second["items"]))
        calls_after_success = len(self.calls)
        third = run_batch(
            self.connection, self.root, batch_id="restart", config=self.config,
            pipeline_runner=runner, media_probe=self.probe,
        )
        self.assertEqual(third["counts"], {"completed": 2})
        self.assertEqual(len(self.calls), calls_after_success)

    def test_changed_config_requires_new_batch_id(self):
        run_batch(
            self.connection, self.root, batch_id="fixed-config", config=self.config,
            pipeline_runner=self.successful_runner, media_probe=self.probe,
        )
        with self.assertRaises(ValueError):
            run_batch(
                self.connection, self.root, batch_id="fixed-config",
                config=BatchConfig("different", "unit tests"),
                pipeline_runner=self.successful_runner, media_probe=self.probe,
            )

    def test_changed_completed_input_is_failed_without_reprocessing_other_items(self):
        run_batch(
            self.connection, self.root, batch_id="changed-input", config=self.config,
            pipeline_runner=self.successful_runner, media_probe=self.probe,
        )
        calls_after_first_run = len(self.calls)
        self.a.write_bytes(b"changed-after-completion")
        report = run_batch(
            self.connection, self.root, batch_id="changed-input", config=self.config,
            pipeline_runner=self.successful_runner, media_probe=self.probe,
        )
        self.assertEqual(report["status"], "completed_with_errors")
        self.assertEqual(report["counts"], {"failed": 1, "completed": 1})
        changed = next(item for item in report["items"] if item["status"] == "failed")
        self.assertEqual(changed["error_type"], "InputChangedError")
        self.assertIn("use a new batch_id", changed["error_message"])
        self.assertEqual(len(self.calls), calls_after_first_run)

    def test_json_and_csv_reports_are_exclusive(self):
        report = run_batch(
            self.connection, self.root, batch_id="reports", config=self.config,
            pipeline_runner=self.successful_runner, media_probe=self.probe,
        )
        json_path = self.root / "data/logs/batch.json"
        csv_path = self.root / "data/logs/batch.csv"
        write_batch_reports(report, json_path, csv_path)
        self.assertTrue(json_path.is_file())
        self.assertTrue(csv_path.is_file())
        with self.assertRaises(FileExistsError):
            write_batch_reports(report, json_path, self.root / "data/logs/other.csv")


if __name__ == "__main__":
    unittest.main()
