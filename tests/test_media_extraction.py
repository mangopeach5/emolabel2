import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emolabel2.analytics.runtime import create_run, finish_run
from emolabel2.domain import Emotion, ReviewRecord, ReviewStatus, RightsStatus, VideoRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.media.extraction import (
    CandidateDestinationExistsError,
    DuplicateClipError,
    InvalidClipRangeError,
    analyze_registered_video,
    extract_registered_clip,
)
from emolabel2.media.probe import MediaProbeError, find_media_tool, probe_media
from emolabel2.review.workflow import save_review_and_export_if_accepted
from emolabel2.storage import initialize_schema, insert_video, open_database


class MediaExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = find_media_tool("ffmpeg")
            cls.ffprobe = find_media_tool("ffprobe")
        except MediaProbeError as exc:
            raise unittest.SkipTest(str(exc))
        cls.fixtures = tempfile.TemporaryDirectory()
        fixture_root = Path(cls.fixtures.name)
        cls.with_audio = fixture_root / "with-audio.mp4"
        cls.without_audio = fixture_root / "without-audio.mp4"
        commands = [
            [
                cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                "testsrc=size=160x120:rate=10", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=8000", "-t", "13",
                "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest",
                str(cls.with_audio),
            ],
            [
                cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                "testsrc=duration=5:size=160x120:rate=10", "-c:v", "mpeg4",
                "-q:v", "5", str(cls.without_audio),
            ],
        ]
        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
            if completed.returncode != 0:
                cls.fixtures.cleanup()
                raise unittest.SkipTest(f"synthetic FFmpeg fixture failed: {completed.stderr[-500:]}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "fixtures"):
            cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in ("data/inbox", "data/candidates", "data/final_dataset"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.source = self.root / "data/inbox/source.mp4"
        shutil.copyfile(self.with_audio, self.source)
        self.original_hash = sha256_file(self.source)
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        insert_video(self.connection, VideoRecord(
            "v1", "data/inbox/source.mp4", self.original_hash, "synthetic",
            RightsStatus.APPROVED_RESEARCH, "generated test fixture", "unit tests",
            probe_media(self.source, ffprobe=self.ffprobe).duration_s, "now",
        ))
        create_run(
            self.connection, run_id="run1", video_id="v1", test_case_id="media-case",
            workflow_mode="automated", started_at="start",
        )

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def extract(self, candidate_id="c1", start=1, end=4):
        return extract_registered_clip(
            self.connection, self.root, video_id="v1", candidate_id=candidate_id,
            start_s=start, end_s=end, run_id="run1",
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )

    def test_probe_and_normal_extraction_register_hash_and_runtime(self):
        info = analyze_registered_video(
            self.connection, self.root, "v1", run_id="run1", ffprobe=self.ffprobe
        )
        self.assertEqual((info.width, info.height), (160, 120))
        self.assertTrue(info.has_audio)
        clip = self.extract()
        self.assertTrue(clip.path.is_file())
        self.assertEqual(sha256_file(clip.path), clip.file_hash)
        row = self.connection.execute("SELECT * FROM candidates WHERE candidate_id='c1'").fetchone()
        self.assertEqual(row["file_hash"], clip.file_hash)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidate_run_links").fetchone()[0], 1)
        stages = {row[0] for row in self.connection.execute("SELECT stage FROM stage_events")}
        self.assertTrue({"preprocess", "clip_selection"} <= stages)

    def test_three_and_twelve_second_boundaries(self):
        three = self.extract("three", 0, 3)
        twelve = self.extract("twelve", 0.5, 12.5)
        self.assertAlmostEqual(three.media_info.duration_s, 3, delta=0.35)
        self.assertAlmostEqual(twelve.media_info.duration_s, 12, delta=0.35)

    def test_range_past_video_end_is_rejected(self):
        duration = probe_media(self.source, ffprobe=self.ffprobe).duration_s
        with self.assertRaises(InvalidClipRangeError):
            self.extract("too-late", duration - 2, duration + 1)
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM candidates WHERE candidate_id='too-late'"
        ).fetchone())

    def test_video_without_audio_is_supported(self):
        no_audio = self.root / "data/inbox/no-audio.mp4"
        shutil.copyfile(self.without_audio, no_audio)
        info = probe_media(no_audio, ffprobe=self.ffprobe)
        self.assertFalse(info.has_audio)
        insert_video(self.connection, VideoRecord(
            "v2", "data/inbox/no-audio.mp4", sha256_file(no_audio), "synthetic",
            RightsStatus.APPROVED_RESEARCH, "fixture", "unit tests", info.duration_s, "now",
        ))
        clip = extract_registered_clip(
            self.connection, self.root, video_id="v2", candidate_id="silent",
            start_s=0, end_s=3, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        self.assertFalse(clip.media_info.has_audio)

    def test_invalid_media_records_failed_preprocess_stage(self):
        bad = self.root / "data/inbox/bad.mp4"
        bad.write_bytes(b"not a video")
        insert_video(self.connection, VideoRecord(
            "bad", "data/inbox/bad.mp4", sha256_file(bad), "synthetic",
            RightsStatus.APPROVED_RESEARCH, "fixture", "unit tests", 5, "now",
        ))
        create_run(
            self.connection, run_id="bad-run", video_id="bad", test_case_id="bad",
            workflow_mode="automated", started_at="start",
        )
        with self.assertRaises(MediaProbeError):
            analyze_registered_video(
                self.connection, self.root, "bad", run_id="bad-run", ffprobe=self.ffprobe
            )
        event = self.connection.execute(
            "SELECT * FROM stage_events WHERE run_id='bad-run'"
        ).fetchone()
        self.assertEqual(event["success"], 0)
        self.assertEqual(event["error_type"], "MediaProbeError")

    def test_existing_destination_and_duplicate_segment_are_rejected(self):
        existing = self.root / "data/candidates/existing.mp4"
        existing.write_bytes(b"preserve me")
        with self.assertRaises(CandidateDestinationExistsError):
            self.extract("existing", 1, 4)
        self.assertEqual(existing.read_bytes(), b"preserve me")

        self.extract("first", 2, 5)
        with self.assertRaises(DuplicateClipError):
            self.extract("duplicate", 2, 5)
        self.assertFalse((self.root / "data/candidates/duplicate.mp4").exists())

    def test_original_is_preserved_and_only_accepted_clip_exports(self):
        clip = self.extract()
        self.assertEqual(sha256_file(self.source), self.original_hash)
        pending = save_review_and_export_if_accepted(
            self.connection, self.root,
            ReviewRecord("c1", ReviewStatus.PENDING, None, 1, "t1"),
        )
        self.assertIsNone(pending)
        exported = save_review_and_export_if_accepted(
            self.connection, self.root,
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 1, "t2"),
        )
        self.assertTrue(exported.destination.is_file())
        self.assertEqual(sha256_file(exported.destination), clip.file_hash)


if __name__ == "__main__":
    unittest.main()
