import json
import tempfile
import unittest
from pathlib import Path

from emolabel2.analytics.reports import build_report, write_csv_report, write_json_report
from emolabel2.analytics.runtime import Stage, create_run, finish_run, link_candidate, record_intervention, record_stage
from emolabel2.analytics.summary import build_run_summaries, compare_workflows, validate_final_dataset
from emolabel2.domain import CandidateRecord, Emotion, PredictionRecord, ReviewRecord, ReviewStatus, RightsStatus, VideoRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.review.export import export_accepted_candidate
from emolabel2.review.service import record_review
from emolabel2.storage import initialize_schema, insert_candidate, insert_prediction, insert_video, open_database


class AnalyticsReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in ("data/inbox", "data/candidates", "data/final_dataset", "data/logs"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        clip = self.root / "data/candidates/c1.bin"
        clip.write_bytes(b"synthetic")
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        insert_video(self.connection, VideoRecord(
            "v1", "data/inbox/v.bin", "a" * 64, "synthetic",
            RightsStatus.APPROVED_RESEARCH, "test", "unit tests", 100, "now",
        ))
        insert_candidate(
            self.connection, CandidateRecord("c1", "v1", 0, 3_000),
            clip_path="data/candidates/c1.bin", file_hash=sha256_file(clip),
        )
        insert_prediction(
            self.connection, PredictionRecord("c1", Emotion.HAPPY, "synthetic", "p1", 0.1)
        )
        record_review(
            self.connection, ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 1, "t1")
        )
        export_accepted_candidate(self.connection, self.root, "c1")
        self._create_run("manual", "manual", 30, 20)
        self._create_run("auto", "automated", 10, 5)
        link_candidate(self.connection, "auto", "c1")
        record_intervention(self.connection, "auto", "emotion_correction", occurred_at="now")

    def _create_run(self, run_id, mode, total, human):
        create_run(
            self.connection, run_id=run_id, video_id="v1", test_case_id="same-case",
            workflow_mode=mode, started_at="start",
        )
        record_stage(
            self.connection, run_id=run_id, stage=Stage.HUMAN_REVIEW,
            started_at="s", ended_at="e", elapsed_s=human, success=True,
        )
        finish_run(self.connection, run_id, elapsed_s=total, success=True, ended_at="end")

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_validity_requires_database_review_and_file_hash(self):
        (self.root / "data/final_dataset/rogue.bin").write_bytes(b"not registered")
        valid = validate_final_dataset(self.connection, self.root)
        self.assertEqual(len(valid), 1)
        self.assertTrue(valid[0].valid)
        record_review(
            self.connection, ReviewRecord("c1", ReviewStatus.DEFERRED, None, 1, "t2")
        )
        invalid = validate_final_dataset(self.connection, self.root)
        self.assertFalse(invalid[0].valid)
        self.assertIn("current_review_not_accepted", invalid[0].reasons)

    def test_run_summary_rtf_counts_yield_and_interventions(self):
        summaries = {item.run_id: item for item in build_run_summaries(self.connection, self.root)}
        auto = summaries["auto"]
        self.assertAlmostEqual(auto.rtf, 0.1)
        self.assertEqual(auto.candidates, 1)
        self.assertIsNone(auto.accepted)
        self.assertEqual(auto.current_linked_state["accepted"], 1)
        self.assertIsNone(auto.valid_clips)
        self.assertIsNone(auto.valid_clips_per_video_hour)
        self.assertEqual(auto.yield_sample_video_count, 1)
        self.assertEqual(auto.yield_stability, "exploratory_single_video")
        self.assertEqual(auto.human_review_s, 5)
        self.assertEqual(auto.manual_interventions, 1)

    def test_manual_and_automated_comparison_uses_same_case(self):
        comparisons = compare_workflows(build_run_summaries(self.connection, self.root))
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0]["saved_human_s"], 15)
        self.assertEqual(comparisons[0]["labor_reduction_rate"], 0.75)

    def test_report_contains_real_classification_and_structured_stages(self):
        report = build_report(self.connection, self.root)
        self.assertEqual(report["dataset"]["valid_count"], 1)
        self.assertEqual(report["classification"]["evaluated"], 1)
        self.assertEqual(report["classification"]["macro_f1"], 0.25)
        self.assertEqual(len(report["stages"]), 2)

    def test_json_csv_are_exclusive_and_parseable(self):
        report = build_report(self.connection, self.root)
        json_path = self.root / "data/logs/report.json"
        csv_path = self.root / "data/logs/report.csv"
        write_json_report(report, json_path)
        write_csv_report(report, csv_path)
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["schema"], "emolabel2.analytics.v2")
        self.assertIn("run_id", csv_path.read_text(encoding="utf-8").splitlines()[0])
        with self.assertRaises(FileExistsError):
            write_json_report(report, json_path)

    def test_reused_candidate_current_state_is_not_counted_as_each_run_outcome(self):
        link_candidate(self.connection, "manual", "c1")
        summaries = {item.run_id: item for item in build_run_summaries(self.connection, self.root)}
        self.assertEqual(summaries["manual"].current_linked_state["accepted"], 1)
        self.assertEqual(summaries["auto"].current_linked_state["accepted"], 1)
        self.assertIsNone(summaries["manual"].accepted)
        self.assertIsNone(summaries["auto"].accepted)
        self.assertEqual(summaries["auto"].candidate_count_semantics, "linked_candidates_not_new_extractions")

    def test_review_outcome_is_attributed_only_to_explicit_run(self):
        record_review(
            self.connection,
            ReviewRecord("c1", ReviewStatus.ACCEPTED, Emotion.HAPPY, 2, "t3"),
            run_id="auto", human_time_measured=True,
        )
        summaries = {item.run_id: item for item in build_run_summaries(self.connection, self.root)}
        self.assertEqual(summaries["auto"].accepted, 1)
        self.assertIsNone(summaries["manual"].accepted)

    def test_unmeasured_zero_is_null_and_comparison_is_omitted(self):
        create_run(
            self.connection, run_id="unknown-auto", video_id="v1",
            test_case_id="unknown-case", workflow_mode="automated", started_at="s",
        )
        finish_run(self.connection, "unknown-auto", elapsed_s=1, success=True, ended_at="e")
        summaries = {item.run_id: item for item in build_run_summaries(self.connection, self.root)}
        self.assertIsNone(summaries["unknown-auto"].human_review_s)
        self.assertIsNone(summaries["unknown-auto"].manual_interventions)
        self.assertFalse(any(item["test_case_id"] == "unknown-case" for item in compare_workflows(list(summaries.values()))))


if __name__ == "__main__":
    unittest.main()
