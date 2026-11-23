import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from emolabel2.analytics.runtime import create_run
from emolabel2.domain import RightsStatus, VideoRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.media.probe import MediaProbeError, find_media_tool, probe_media
from emolabel2.scene.detect import SceneConfig
from emolabel2.scene.policy import ScenePolicyMode, SceneTransition
from emolabel2.scene.reporting import scene_analysis_summary
from emolabel2.storage import initialize_schema, insert_video, open_database
from emolabel2.vad.pipeline import run_vad_pipeline
from emolabel2.vad.segments import SpeechSegment
from emolabel2.vad.segments import ShortSpeechPaddingConfig
from emolabel2.vad.silero import VadConfig, convert_to_pcm16k_mono


class VadPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = find_media_tool("ffmpeg")
            cls.ffprobe = find_media_tool("ffprobe")
        except MediaProbeError as exc:
            raise unittest.SkipTest(str(exc))
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixtures.name) / "synthetic.mp4"
        command = [
            cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
            "testsrc=size=160x120:rate=10", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=8000", "-t", "13",
            "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest",
            str(cls.fixture),
        ]
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
        for relative in ("data/inbox", "data/candidates", "data/final_dataset", "data/work"):
            (self.root / relative).mkdir(parents=True)
        self.source = self.root / "data/inbox/source.mp4"
        shutil.copyfile(self.fixture, self.source)
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        info = probe_media(self.source, ffprobe=self.ffprobe)
        insert_video(self.connection, VideoRecord(
            "v1", "data/inbox/source.mp4", sha256_file(self.source), "synthetic",
            RightsStatus.APPROVED_RESEARCH, "generated fixture", "unit tests",
            info.duration_s, "now",
        ))
        create_run(
            self.connection, run_id="run1", video_id="v1", test_case_id="vad-case",
            workflow_mode="automated", started_at="start",
        )

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def run_pipeline(self, detector):
        return run_vad_pipeline(
            self.connection, self.root, run_id="run1", video_id="v1",
            detector=detector, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )

    def test_pcm_conversion_is_16khz_mono_16bit(self):
        destination = self.root / "data/work/audio.wav"
        convert_to_pcm16k_mono(self.source, destination, ffmpeg=self.ffmpeg)
        with wave.open(str(destination), "rb") as stream:
            self.assertEqual(stream.getframerate(), 16000)
            self.assertEqual(stream.getnchannels(), 1)
            self.assertEqual(stream.getsampwidth(), 2)

    def test_segments_candidates_rejections_and_runtime_are_persisted(self):
        original_hash = sha256_file(self.source)
        analysis_id = self.run_pipeline(
            lambda source, config: [
                SpeechSegment(0, 1000),
                SpeechSegment(2000, 6000),
                SpeechSegment(7000, 10000),
            ]
        )
        analysis = self.connection.execute(
            "SELECT * FROM vad_analyses WHERE analysis_id=?", (analysis_id,)
        ).fetchone()
        self.assertEqual(analysis["raw_segment_count"], 3)
        self.assertEqual(analysis["merged_segment_count"], 3)
        self.assertEqual(analysis["candidate_count"], 2)
        proposals = self.connection.execute(
            "SELECT * FROM vad_proposals WHERE analysis_id=? ORDER BY sequence_no", (analysis_id,)
        ).fetchall()
        self.assertEqual([row["accepted"] for row in proposals], [0, 1, 1])
        self.assertEqual(proposals[0]["reject_reason"], "speech_too_short")
        self.assertTrue(all(row["candidate_id"] for row in proposals[1:]))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 2)
        event = self.connection.execute(
            "SELECT * FROM stage_events WHERE run_id='run1' AND stage='vad'"
        ).fetchone()
        self.assertEqual(event["success"], 1)
        self.assertEqual(event["model_version"], "synthetic")
        self.assertEqual(sha256_file(self.source), original_hash)

    def test_rerun_reuses_registered_segments_without_duplicate_candidates(self):
        detector = lambda source, config: [SpeechSegment(1000, 4000)]
        first = self.run_pipeline(detector)
        second = self.run_pipeline(detector)
        self.assertNotEqual(first, second)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM vad_proposals").fetchone()[0], 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT candidate_count FROM vad_analyses WHERE analysis_id=?", (second,)
            ).fetchone()[0], 1,
        )

    def test_extraction_failure_is_not_registered_as_candidate(self):
        analysis_id = self.run_pipeline(lambda source, config: [SpeechSegment(12000, 16000)])
        proposal = self.connection.execute(
            "SELECT * FROM vad_proposals WHERE analysis_id=?", (analysis_id,)
        ).fetchone()
        self.assertEqual(proposal["accepted"], 0)
        self.assertTrue(proposal["reject_reason"].startswith("extraction_failed:"))
        self.assertIsNone(proposal["candidate_id"])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 0)

    def test_strict_scene_policy_rejects_cut_inside_speech_and_records_types(self):
        analysis_id = run_vad_pipeline(
            self.connection, self.root, run_id="run1", video_id="v1",
            detector=lambda source, config: [SpeechSegment(0, 8000)],
            scene_config=SceneConfig(mode=ScenePolicyMode.STRICT),
            scene_detector=lambda source, config: [
                SceneTransition(4000, "hard_cut", "AdaptiveDetector"),
                SceneTransition(6000, "fade", "ThresholdDetector"),
            ],
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        scene = self.connection.execute(
            "SELECT * FROM scene_analyses WHERE vad_analysis_id=?", (analysis_id,)
        ).fetchone()
        self.assertEqual((scene["hard_cut_count"], scene["fade_count"]), (1, 1))
        self.assertEqual((scene["pre_candidate_count"], scene["post_candidate_count"]), (1, 0))
        self.assertEqual(scene["scene_rejected_count"], 1)
        decision = self.connection.execute("SELECT * FROM scene_candidate_decisions").fetchone()
        self.assertEqual(decision["reason"], "scene_cut_inside_speech")
        self.assertEqual((decision["original_start_ms"], decision["original_end_ms"]), (0, 8000))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 0)
        event = self.connection.execute(
            "SELECT * FROM stage_events WHERE stage='scene_detection'"
        ).fetchone()
        self.assertEqual(event["success"], 1)

    def test_warn_scene_policy_keeps_candidate_for_later_human_review(self):
        run_vad_pipeline(
            self.connection, self.root, run_id="run1", video_id="v1",
            detector=lambda source, config: [SpeechSegment(0, 8000)],
            scene_config=SceneConfig(mode=ScenePolicyMode.WARN),
            scene_detector=lambda source, config: [
                SceneTransition(4000, "hard_cut", "AdaptiveDetector")
            ],
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        decision = self.connection.execute("SELECT * FROM scene_candidate_decisions").fetchone()
        self.assertEqual(decision["decision_status"], "warning")
        self.assertEqual(decision["reason"], "scene_transition_inside_speech")
        self.assertIsNotNone(decision["candidate_id"])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 1)
        scene_id = self.connection.execute("SELECT scene_analysis_id FROM scene_analyses").fetchone()[0]
        summary = scene_analysis_summary(self.connection, scene_id)
        self.assertEqual(summary["post_candidate_count"], 1)
        self.assertIsNone(summary["human_adoption_rate"])

    def test_scene_rerun_reuses_existing_candidate_safely(self):
        kwargs = dict(
            detector=lambda source, config: [SpeechSegment(1000, 5000)],
            scene_config=SceneConfig(mode=ScenePolicyMode.STRICT),
            scene_detector=lambda source, config: [],
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        run_vad_pipeline(self.connection, self.root, run_id="run1", video_id="v1", **kwargs)
        run_vad_pipeline(self.connection, self.root, run_id="run1", video_id="v1", **kwargs)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM scene_analyses").fetchone()[0], 2)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM scene_candidate_decisions").fetchone()[0], 2)

    def test_short_speech_padding_preserves_original_rejection_and_creates_candidate(self):
        run_vad_pipeline(
            self.connection, self.root, run_id="run1", video_id="v1",
            detector=lambda source, config: [SpeechSegment(10000, 12900)],
            scene_config=SceneConfig(mode=ScenePolicyMode.STRICT),
            scene_detector=lambda source, config: [],
            short_padding_config=ShortSpeechPaddingConfig(),
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        proposal = self.connection.execute("SELECT * FROM vad_proposals").fetchone()
        self.assertEqual((proposal["accepted"], proposal["reject_reason"]), (0, "speech_too_short"))
        padding = self.connection.execute("SELECT * FROM vad_padding_decisions").fetchone()
        self.assertEqual(padding["accepted"], 1)
        self.assertEqual(padding["padded_end_ms"] - padding["padded_start_ms"], 3200)
        self.assertIsNotNone(padding["candidate_id"])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 1)

    def test_short_speech_padding_rerun_reuses_candidate(self):
        kwargs = dict(
            detector=lambda source, config: [SpeechSegment(10000, 12900)],
            scene_config=SceneConfig(mode=ScenePolicyMode.STRICT),
            scene_detector=lambda source, config: [],
            short_padding_config=ShortSpeechPaddingConfig(),
            ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
        )
        run_vad_pipeline(self.connection, self.root, run_id="run1", video_id="v1", **kwargs)
        run_vad_pipeline(self.connection, self.root, run_id="run1", video_id="v1", **kwargs)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM candidates").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM vad_padding_decisions").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
