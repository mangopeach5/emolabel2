import csv
import tempfile
import unittest
from pathlib import Path

from emolabel2.storage import initialize_schema, open_database
from emolabel2.youtube.intake import (
    link_and_stage, normalize_youtube_url, read_link_manifest, read_url_file,
    refresh_metadata, register_urls, set_rights_status, source_report,
)


class YouTubeIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data/inbox/batch").mkdir(parents=True)
        (self.root / "data/logs").mkdir(parents=True)
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_supported_urls_normalize_to_one_video_id(self):
        expected = "AbCdEf123_-"
        values = [
            f"https://www.youtube.com/shorts/{expected}?feature=share",
            f"https://youtube.com/watch?v={expected}&t=2",
            f"https://youtu.be/{expected}?si=x",
        ]
        self.assertEqual({normalize_youtube_url(value)[0] for value in values}, {expected})
        self.assertEqual(normalize_youtube_url(values[0])[1], f"https://www.youtube.com/watch?v={expected}")

    def test_invalid_hosts_and_ids_are_rejected(self):
        for value in ("https://example.com/watch?v=AbCdEf123_-", "https://youtu.be/short"):
            with self.assertRaises(ValueError):
                normalize_youtube_url(value)

    def test_txt_deduplicates_and_preserves_invalid_lines(self):
        path = self.root / "urls.txt"
        path.write_text(
            "https://youtu.be/AbCdEf123_-\nhttps://youtube.com/shorts/AbCdEf123_-\ninvalid\n",
            encoding="utf-8",
        )
        entries, failures = read_url_file(self.root, "urls.txt")
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(failures), 1)

    def test_metadata_failure_keeps_registered_url_and_can_retry_failed_only(self):
        register_urls(self.connection, [("AbCdEf123_-", "https://www.youtube.com/watch?v=AbCdEf123_-")])

        def failing(_url):
            raise OSError("offline")

        refresh_metadata(self.connection, fetcher=failing)
        row = self.connection.execute("SELECT * FROM youtube_sources").fetchone()
        self.assertEqual(row["metadata_status"], "failed")
        self.assertEqual(row["rights_status"], "unknown")

        def succeeding(_url):
            return {"title": "Synthetic", "channel_name": "Tester", "channel_url": None, "thumbnail_url": None}

        refresh_metadata(self.connection, failed_only=True, fetcher=succeeding)
        row = self.connection.execute("SELECT * FROM youtube_sources").fetchone()
        self.assertEqual(row["metadata_status"], "succeeded")
        self.assertEqual(row["metadata_attempt_count"], 2)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM youtube_metadata_attempts").fetchone()[0], 2)

    def _manifest(self, video_id="AbCdEf123_-", path="data/inbox/local.mp4"):
        manifest = self.root / "links.csv"
        with manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=(
                "youtube_video_id", "local_path", "rights_status", "rights_basis", "allowed_use"
            ))
            writer.writeheader()
            writer.writerow({"youtube_video_id": video_id, "local_path": path,
                             "rights_status": "approved", "rights_basis": "owner permission",
                             "allowed_use": "research"})
        return manifest

    def test_approved_local_mp4_is_hash_verified_and_staged_idempotently(self):
        local = self.root / "data/inbox/local.mp4"
        local.write_bytes(b"synthetic-mp4")
        register_urls(self.connection, [("AbCdEf123_-", "https://www.youtube.com/watch?v=AbCdEf123_-")])
        rows = read_link_manifest(self.root, self._manifest())
        first = link_and_stage(self.connection, self.root, rows)
        second = link_and_stage(self.connection, self.root, rows)
        self.assertEqual(first, second)
        staged = self.root / first[0]["staged_path"]
        self.assertEqual(staged.read_bytes(), b"synthetic-mp4")
        row = self.connection.execute("SELECT * FROM youtube_sources").fetchone()
        self.assertEqual((row["rights_status"], row["file_status"]), ("approved", "staged"))

    def test_same_file_cannot_be_linked_to_two_video_ids(self):
        local = self.root / "data/inbox/local.mp4"
        local.write_bytes(b"same-file")
        entries = [
            ("AbCdEf123_-", "https://www.youtube.com/watch?v=AbCdEf123_-"),
            ("ZyXwVu987_-", "https://www.youtube.com/watch?v=ZyXwVu987_-"),
        ]
        register_urls(self.connection, entries)
        link_and_stage(self.connection, self.root, read_link_manifest(self.root, self._manifest()))
        with self.assertRaises(ValueError):
            link_and_stage(self.connection, self.root, read_link_manifest(
                self.root, self._manifest("ZyXwVu987_-")
            ))

    def test_unapproved_manifest_is_rejected(self):
        local = self.root / "data/inbox/local.mp4"
        local.write_bytes(b"synthetic")
        manifest = self._manifest()
        text = manifest.read_text(encoding="utf-8").replace("approved", "unknown")
        manifest.write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError):
            read_link_manifest(self.root, manifest)

    def test_blocked_url_remains_without_file(self):
        register_urls(self.connection, [("AbCdEf123_-", "https://www.youtube.com/watch?v=AbCdEf123_-")])
        set_rights_status(self.connection, "AbCdEf123_-", "blocked")
        item = source_report(self.connection)["items"][0]
        self.assertEqual(item["rights_status"], "blocked")
        self.assertEqual(item["file_status"], "missing")
        local = self.root / "data/inbox/local.mp4"
        local.write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            link_and_stage(self.connection, self.root, read_link_manifest(self.root, self._manifest()))


if __name__ == "__main__":
    unittest.main()
