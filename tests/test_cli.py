import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from emolabel2.cli import main
from emolabel2.domain import RightsStatus, VideoRecord
from emolabel2.storage import insert_video, open_database


class CliTests(unittest.TestCase):
    def test_init_and_empty_stats_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/logs").mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "--project-root", str(root), "--database", "data/test.sqlite3", "init-db"
                ]), 0)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "--project-root", str(root), "--database", "data/test.sqlite3", "stats",
                    "--json-output", "data/logs/report.json",
                    "--csv-output", "data/logs/report.csv",
                ]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["runs"], [])
            self.assertIsNone(report["classification"])
            self.assertTrue((root / "data/logs/report.json").is_file())
            self.assertTrue((root / "data/logs/report.csv").is_file())

    def test_manual_baseline_records_only_explicit_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--project-root", str(root), "--database", "data/test.sqlite3", "init-db"])
            connection = open_database(root / "data/test.sqlite3")
            insert_video(connection, VideoRecord(
                "v1", "data/inbox/v.mp4", "a" * 64, "synthetic",
                RightsStatus.APPROVED_RESEARCH, "test", "test", 10, "now",
            ))
            connection.close()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "--project-root", str(root), "--database", "data/test.sqlite3",
                    "record-manual-baseline", "--run-id", "manual-1",
                    "--video-id", "v1", "--test-case-id", "case-1",
                    "--processing-s", "30", "--human-review-s", "20",
                    "--manual-interventions", "0",
                ]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["human_review_s"], 20)
            self.assertEqual(result["manual_interventions"], 0)

    def test_empty_batch_cli_writes_json_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/inbox/batch").mkdir(parents=True)
            (root / "data/logs").mkdir()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "--project-root", str(root), "--database", "data/test.sqlite3",
                    "batch", "--batch-id", "empty",
                    "--rights-basis", "approved", "--allowed-use", "tests",
                    "--json-output", "data/logs/empty.json",
                    "--csv-output", "data/logs/empty.csv",
                ]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["items"], [])
            self.assertTrue((root / "data/logs/empty.json").is_file())
            self.assertTrue((root / "data/logs/empty.csv").is_file())

    def test_youtube_intake_cli_registers_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/logs").mkdir(parents=True)
            (root / "urls.txt").write_text(
                "https://youtube.com/shorts/AbCdEf123_-\nhttps://youtu.be/AbCdEf123_-\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "--project-root", str(root), "--database", "data/test.sqlite3",
                    "youtube-intake", "--input", "urls.txt",
                    "--json-output", "data/logs/youtube.json",
                    "--csv-output", "data/logs/youtube.csv",
                ]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(len(report["items"]), 1)
            self.assertEqual(report["items"][0]["metadata_status"], "not_requested")
            self.assertEqual(report["items"][0]["rights_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
