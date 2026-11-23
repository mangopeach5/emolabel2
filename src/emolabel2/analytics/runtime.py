"""Deterministic storage and context timers for pipeline runtimes."""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from enum import StrEnum
import math
from time import perf_counter
import sqlite3


class Stage(StrEnum):
    PREPROCESS = "preprocess"
    VAD = "vad"
    STT = "stt"
    SCENE_DETECTION = "scene_detection"
    CLIP_SELECTION = "clip_selection"
    LLM_PRELABEL = "llm_prelabel"
    HUMAN_REVIEW = "human_review"
    EXPORT = "export"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite_nonnegative(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def create_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    video_id: str,
    test_case_id: str,
    workflow_mode: str,
    started_at: str | None = None,
    algorithm_version: str = "",
    model_version: str = "",
) -> None:
    if workflow_mode not in ("manual", "automated"):
        raise ValueError("workflow_mode must be manual or automated")
    if not all(isinstance(value, str) and value.strip() for value in (run_id, video_id, test_case_id)):
        raise ValueError("run_id, video_id, and test_case_id are required")
    with connection:
        connection.execute(
            """INSERT INTO runs (
                run_id, video_id, test_case_id, workflow_mode, started_at,
                algorithm_version, model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, video_id, test_case_id, workflow_mode, started_at or utc_now(), algorithm_version, model_version),
        )


def finish_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    elapsed_s: float,
    success: bool,
    ended_at: str | None = None,
    error_type: str = "",
) -> None:
    _finite_nonnegative("elapsed_s", elapsed_s)
    if not isinstance(success, bool):
        raise ValueError("success must be boolean")
    with connection:
        cursor = connection.execute(
            """UPDATE runs SET ended_at = ?, elapsed_s = ?, success = ?, error_type = ?
               WHERE run_id = ?""",
            (ended_at or utc_now(), elapsed_s, int(success), error_type, run_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"run not found: {run_id}")


def record_stage(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    stage: Stage,
    started_at: str,
    ended_at: str,
    elapsed_s: float,
    success: bool,
    error_type: str = "",
    error_message: str = "",
    algorithm_version: str = "",
    model_version: str = "",
) -> int:
    _finite_nonnegative("elapsed_s", elapsed_s)
    if not isinstance(stage, Stage) or not isinstance(success, bool):
        raise ValueError("stage and success have invalid types")
    with connection:
        cursor = connection.execute(
            """INSERT INTO stage_events (
                run_id, stage, started_at, ended_at, elapsed_s, success,
                error_type, error_message, algorithm_version, model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, stage.value, started_at, ended_at, elapsed_s, int(success), error_type, error_message, algorithm_version, model_version),
        )
    return int(cursor.lastrowid)


def link_candidate(connection: sqlite3.Connection, run_id: str, candidate_id: str) -> None:
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO candidate_run_links(run_id, candidate_id) VALUES (?, ?)",
            (run_id, candidate_id),
        )


def record_intervention(connection: sqlite3.Connection, run_id: str, intervention_type: str, *, details: str = "", occurred_at: str | None = None) -> int:
    if not intervention_type.strip():
        raise ValueError("intervention_type is required")
    with connection:
        cursor = connection.execute(
            """INSERT INTO manual_interventions(run_id, intervention_type, occurred_at, details)
               VALUES (?, ?, ?, ?)""",
            (run_id, intervention_type, occurred_at or utc_now(), details),
        )
        connection.execute(
            """INSERT INTO metric_coverage(run_id, metric_name, measurement_status)
               VALUES (?, 'manual_interventions', 'measured')
               ON CONFLICT(run_id, metric_name) DO UPDATE SET
                   measurement_status='measured'""",
            (run_id,),
        )
    return int(cursor.lastrowid)


def mark_manual_interventions_measured(connection: sqlite3.Connection, run_id: str) -> None:
    """Explicitly declare that zero or more interventions were tracked for a run."""
    with connection:
        connection.execute(
            """INSERT INTO metric_coverage(run_id, metric_name, measurement_status)
               VALUES (?, 'manual_interventions', 'measured')
               ON CONFLICT(run_id, metric_name) DO UPDATE SET
                   measurement_status='measured'""",
            (run_id,),
        )


class StageTimer(AbstractContextManager["StageTimer"]):
    def __init__(self, connection: sqlite3.Connection, run_id: str, stage: Stage, *, algorithm_version: str = "", model_version: str = ""):
        self.connection = connection
        self.run_id = run_id
        self.stage = stage
        self.algorithm_version = algorithm_version
        self.model_version = model_version
        self.started_at = ""
        self._start = 0.0

    def __enter__(self) -> "StageTimer":
        self.started_at = utc_now()
        self._start = perf_counter()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        elapsed = perf_counter() - self._start
        record_stage(
            self.connection, run_id=self.run_id, stage=self.stage,
            started_at=self.started_at, ended_at=utc_now(), elapsed_s=elapsed,
            success=exc is None,
            error_type=exc_type.__name__ if exc_type else "",
            error_message=str(exc) if exc else "",
            algorithm_version=self.algorithm_version, model_version=self.model_version,
        )
        return False


class RunTimer(AbstractContextManager["RunTimer"]):
    """Create and finish one whole-video run using a monotonic clock."""

    def __init__(self, connection: sqlite3.Connection, *, run_id: str, video_id: str, test_case_id: str, workflow_mode: str, algorithm_version: str = "", model_version: str = ""):
        self.connection = connection
        self.run_id = run_id
        self.video_id = video_id
        self.test_case_id = test_case_id
        self.workflow_mode = workflow_mode
        self.algorithm_version = algorithm_version
        self.model_version = model_version
        self._start = 0.0

    def __enter__(self) -> "RunTimer":
        create_run(
            self.connection, run_id=self.run_id, video_id=self.video_id,
            test_case_id=self.test_case_id, workflow_mode=self.workflow_mode,
            algorithm_version=self.algorithm_version, model_version=self.model_version,
        )
        self._start = perf_counter()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        finish_run(
            self.connection, self.run_id, elapsed_s=perf_counter() - self._start,
            success=exc is None, error_type=exc_type.__name__ if exc_type else "",
        )
        return False
