import sqlite3
import tempfile
import unittest
from pathlib import Path

from emolabel2.domain import RightsStatus, VideoRecord
from emolabel2.intake.manifest import ManifestRow
from emolabel2.storage import (
    find_video_by_hash,
    initialize_schema,
    insert_video,
    open_database,
    register_video_from_manifest,
)


def make_video(video_id="v1", digest="a" * 64, path="data/inbox/one.bin"):
    return VideoRecord(
        video_id, path, digest, "synthetic", RightsStatus.APPROVED_RESEARCH,
        "test fixture", "unit tests", 10, "2026-11-23T00:00:00+00:00",
    )


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_insert_and_find_video(self):
        insert_video(self.connection, make_video())
        row = find_video_by_hash(self.connection, "a" * 64)
        self.assertEqual(row["video_id"], "v1")

    def test_duplicate_hash_is_rejected(self):
        insert_video(self.connection, make_video())
        with self.assertRaises(sqlite3.IntegrityError):
            insert_video(self.connection, make_video("v2", "a" * 64, "data/inbox/two.bin"))

    def test_foreign_key_is_enabled(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.connection:
                self.connection.execute(
                    "INSERT INTO candidates(candidate_id, video_id, start_ms, end_ms, status) "
                    "VALUES ('c1', 'missing', 0, 3000, 'generated')"
                )

    def test_database_rejects_gold_without_acceptance(self):
        insert_video(self.connection, make_video())
        with self.connection:
            self.connection.execute(
                "INSERT INTO candidates(candidate_id, video_id, start_ms, end_ms, status) "
                "VALUES ('c1', 'v1', 0, 3000, 'generated')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.connection:
                self.connection.execute(
                    "INSERT INTO reviews(candidate_id, review_status, gold_emotion, human_time_s, reviewed_at) "
                    "VALUES ('c1', 'pending', 'happy', 1, 'now')"
                )

    def test_safe_registration_hashes_inbox_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "data" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "sample.bin").write_bytes(b"synthetic")
            row = ManifestRow(
                "v2", "data/inbox/sample.bin", "synthetic",
                RightsStatus.APPROVED_RESEARCH, "test fixture", "unit tests", 5,
            )
            video = register_video_from_manifest(
                self.connection, root, row, added_at="2026-11-23T00:00:00+00:00"
            )
            self.assertEqual(find_video_by_hash(self.connection, video.file_hash)["video_id"], "v2")

    def test_schema_version_one_is_migrated_without_losing_candidates(self):
        connection = open_database(":memory:")
        connection.executescript(
            """
            CREATE TABLE schema_info(version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (1);
            CREATE TABLE candidates (
                candidate_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                transcript TEXT NOT NULL DEFAULT '',
                algorithm_version TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO candidates(candidate_id, video_id, start_ms, end_ms, status)
            VALUES ('old-candidate', 'old-video', 0, 3000, 'generated');
            """
        )
        initialize_schema(connection)
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(candidates)")
        }
        self.assertIn("clip_path", columns)
        self.assertIn("file_hash", columns)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 1
        )
        self.assertEqual(
            connection.execute("SELECT version FROM schema_info").fetchone()[0], 11
        )
        connection.close()

    def test_schema_version_two_migrates_to_runtime_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            """
            CREATE TABLE schema_info(version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (2);
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY, local_path TEXT, file_hash TEXT,
                source_name TEXT, rights_status TEXT, rights_basis TEXT,
                allowed_use TEXT, duration_s REAL, added_at TEXT, notes TEXT
            );
            INSERT INTO videos VALUES (
                'preserved', 'data/inbox/x', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'synthetic', 'approved_research', 'test', 'test', 10, 'now', ''
            );
            """
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT count(*) FROM videos").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"runs", "stage_events", "candidate_run_links", "manual_interventions"} <= tables)
        connection.close()

    def test_schema_version_three_migrates_to_media_metadata(self):
        connection = open_database(":memory:")
        connection.executescript(
            """
            CREATE TABLE schema_info(version INTEGER NOT NULL);
            INSERT INTO schema_info(version) VALUES (3);
            """
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='media_info'"
        ).fetchone()
        self.assertIsNotNone(table)
        connection.close()

    def test_schema_version_four_migrates_to_vad_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (4);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"vad_analyses", "vad_segments", "vad_proposals"} <= tables)
        connection.close()

    def test_schema_version_five_migrates_to_scene_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (5);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"scene_analyses", "scene_transitions", "scene_candidate_decisions"} <= tables)
        connection.close()

    def test_schema_version_six_migrates_to_stt_and_selector_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (6);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({
            "stt_analyses", "stt_segments", "stt_words",
            "stt_vad_alignments", "clip_selection_decisions",
        } <= tables)
        connection.close()

    def test_schema_version_seven_migrates_to_padding_decisions(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (7);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vad_padding_decisions'"
        ).fetchone()
        self.assertIsNotNone(table)
        connection.close()

    def test_schema_version_eight_migrates_to_measurement_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (8);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"review_measurements", "run_candidate_outcomes", "metric_coverage"} <= tables)
        connection.close()

    def test_schema_version_nine_migrates_to_batch_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (9);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"batch_jobs", "batch_items"} <= tables)
        connection.close()

    def test_schema_version_ten_migrates_to_youtube_intake_tables(self):
        connection = open_database(":memory:")
        connection.executescript(
            "CREATE TABLE schema_info(version INTEGER NOT NULL);"
            "INSERT INTO schema_info(version) VALUES (10);"
        )
        initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT version FROM schema_info").fetchone()[0], 11)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"youtube_sources", "youtube_metadata_attempts"} <= tables)
        connection.close()


if __name__ == "__main__":
    unittest.main()
