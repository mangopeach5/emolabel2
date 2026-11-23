import math
import unittest

from emolabel2.analytics.runtime import (
    Stage,
    StageTimer,
    RunTimer,
    create_run,
    finish_run,
    record_intervention,
    record_stage,
)
from emolabel2.domain import RightsStatus, VideoRecord
from emolabel2.storage import initialize_schema, insert_video, open_database


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.connection = open_database(":memory:")
        initialize_schema(self.connection)
        insert_video(self.connection, VideoRecord(
            "v1", "data/inbox/v.bin", "a" * 64, "synthetic",
            RightsStatus.APPROVED_RESEARCH, "test", "unit tests", 10, "now",
        ))
        create_run(
            self.connection, run_id="r1", video_id="v1", test_case_id="case1",
            workflow_mode="automated", started_at="start", algorithm_version="a1",
            model_version="m1",
        )

    def tearDown(self):
        self.connection.close()

    def test_records_all_required_stages(self):
        for stage in Stage:
            record_stage(
                self.connection, run_id="r1", stage=stage, started_at="s", ended_at="e",
                elapsed_s=1, success=True, algorithm_version="a1", model_version="m1",
            )
        rows = self.connection.execute("SELECT * FROM stage_events ORDER BY event_id").fetchall()
        self.assertEqual([row["stage"] for row in rows], [stage.value for stage in Stage])
        self.assertTrue(all(row["success"] == 1 for row in rows))

    def test_timer_records_failure_and_reraises(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with StageTimer(self.connection, "r1", Stage.STT, algorithm_version="stt-v1"):
                raise RuntimeError("synthetic failure")
        row = self.connection.execute("SELECT * FROM stage_events").fetchone()
        self.assertEqual(row["success"], 0)
        self.assertEqual(row["error_type"], "RuntimeError")
        self.assertGreaterEqual(row["elapsed_s"], 0)

    def test_finish_and_intervention(self):
        record_intervention(self.connection, "r1", "boundary_edit", occurred_at="now")
        finish_run(self.connection, "r1", elapsed_s=5, success=True, ended_at="end")
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = 'r1'").fetchone()
        self.assertEqual(row["elapsed_s"], 5)
        self.assertEqual(row["success"], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM manual_interventions").fetchone()[0], 1)

    def test_invalid_runtime_values_are_rejected(self):
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                finish_run(self.connection, "r1", elapsed_s=value, success=True)

    def test_whole_run_timer_records_failure(self):
        with self.assertRaises(ValueError):
            with RunTimer(
                self.connection, run_id="r2", video_id="v1", test_case_id="case2",
                workflow_mode="automated",
            ):
                raise ValueError("failed run")
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = 'r2'").fetchone()
        self.assertEqual(row["success"], 0)
        self.assertEqual(row["error_type"], "ValueError")
        self.assertGreaterEqual(row["elapsed_s"], 0)


if __name__ == "__main__":
    unittest.main()
