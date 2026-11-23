import tempfile
import unittest
from pathlib import Path

from emolabel2.analytics.runtime import create_run
from emolabel2.domain import CandidateRecord, Emotion, RightsStatus, VideoRecord
from emolabel2.emotion.ollama import prelabel_candidates
from emolabel2.selector.smart import select_candidates
from emolabel2.storage import initialize_schema, insert_candidate, insert_video, open_database
from emolabel2.stt.whisper_local import (
    SttResult, SttSegment, SttWord, WhisperConfig, run_stt_analysis,
)


class SttSelectorEmotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data/inbox").mkdir(parents=True)
        source = self.root / "data/inbox/video.bin"
        source.write_bytes(b"synthetic local fixture")
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        insert_video(self.connection, VideoRecord(
            "v1", "data/inbox/video.bin", "a" * 64, "synthetic",
            RightsStatus.APPROVED_RESEARCH, "unit test", "unit test", 10, "now",
        ))
        create_run(
            self.connection, run_id="r1", video_id="v1", test_case_id="mvp",
            workflow_mode="automated", started_at="now",
        )
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO vad_analyses (
                    run_id, video_id, model_name, model_version, config_json,
                    raw_segment_count, merged_segment_count, candidate_count, created_at
                ) VALUES ('r1','v1','synthetic','1','{}',1,1,1,'now')"""
            )
            analysis_id = cursor.lastrowid
            self.connection.execute(
                """INSERT INTO vad_segments (
                    analysis_id, segment_kind, sequence_no, start_ms, end_ms
                ) VALUES (?, 'raw', 0, 1000, 5000)""", (analysis_id,)
            )
        insert_candidate(
            self.connection, CandidateRecord("c1", "v1", 1000, 5000),
            clip_path="data/candidates/c1.mp4", file_hash="b" * 64,
        )
        with self.connection:
            self.connection.execute(
                "INSERT INTO candidate_run_links(run_id,candidate_id) VALUES ('r1','c1')"
            )

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def mock_transcriber(self, source, root, config):
        return SttResult((SttSegment(
            1000, 5000, "오늘 정말 기분이 좋아요.",
            (SttWord(1000, 1700, "오늘", 0.9), SttWord(4000, 5000, "좋아요.", 0.9)),
            -0.2, 0.01, 1.0,
        ),))

    def test_stt_timestamps_alignment_and_selection_are_saved(self):
        analysis_id = run_stt_analysis(
            self.connection, self.root, run_id="r1", video_id="v1",
            config=WhisperConfig(), transcriber=self.mock_transcriber,
        )
        self.assertEqual(self.connection.execute("SELECT count(*) FROM stt_words").fetchone()[0], 2)
        alignment = self.connection.execute("SELECT * FROM stt_vad_alignments").fetchone()
        self.assertEqual(alignment["overlap_ms"], 4000)
        selected = select_candidates(
            self.connection, run_id="r1", stt_analysis_id=analysis_id,
        )
        self.assertEqual(selected, ["c1"])
        decision = self.connection.execute("SELECT * FROM clip_selection_decisions").fetchone()
        self.assertEqual(decision["decision_status"], "selected")

    def test_uncertain_stt_is_deferred_not_fabricated(self):
        def uncertain(source, root, config):
            return SttResult((SttSegment(
                1000, 5000, "", (), -2.0, 0.9, 3.0, True, "low_confidence",
            ),))
        analysis_id = run_stt_analysis(
            self.connection, self.root, run_id="r1", video_id="v1", transcriber=uncertain,
        )
        self.assertEqual(select_candidates(
            self.connection, run_id="r1", stt_analysis_id=analysis_id,
        ), [])
        self.assertEqual(
            self.connection.execute("SELECT decision_status FROM clip_selection_decisions").fetchone()[0],
            "deferred",
        )

    def test_stt_segment_crossing_clip_boundary_is_rejected(self):
        def crossing(source, root, config):
            return SttResult((SttSegment(
                0, 7000, "아직 문장이 계속되고 있습니다", (), -0.2, 0.01, 1.0,
            ),))
        analysis_id = run_stt_analysis(
            self.connection, self.root, run_id="r1", video_id="v1", transcriber=crossing,
        )
        self.assertEqual(select_candidates(
            self.connection, run_id="r1", stt_analysis_id=analysis_id,
        ), [])
        row = self.connection.execute("SELECT * FROM clip_selection_decisions").fetchone()
        self.assertEqual(row["decision_status"], "rejected")
        self.assertEqual(row["reason"], "sentence_boundary_incomplete")

    def test_emotion_prediction_is_separate_from_human_gold(self):
        with self.connection:
            self.connection.execute("UPDATE candidates SET transcript='기분이 좋아요' WHERE candidate_id='c1'")
        ids = prelabel_candidates(
            self.connection, run_id="r1", candidate_ids=["c1"], model="qwen-test",
            classifier=lambda text, model: (Emotion.HAPPY, model),
        )
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.connection.execute("SELECT emotion FROM predictions").fetchone()[0], "happy")
        self.assertIsNone(self.connection.execute("SELECT * FROM reviews").fetchone())

    def test_uncertain_emotion_is_stored_as_null_prediction(self):
        ids = prelabel_candidates(
            self.connection, run_id="r1", candidate_ids=["c1"], model="llama-test",
            classifier=lambda text, model: (None, model),
        )
        self.assertEqual(len(ids), 1)
        row = self.connection.execute("SELECT emotion, needs_review FROM predictions").fetchone()
        self.assertIsNone(row["emotion"])
        self.assertEqual(row["needs_review"], 1)


if __name__ == "__main__":
    unittest.main()
