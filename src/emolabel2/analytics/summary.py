"""Dataset validity, run summaries, and manual-vs-automated comparisons."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3

from emolabel2.intake.hashing import sha256_file
from emolabel2.paths import resolve_inside_project


@dataclass(frozen=True)
class DatasetItemValidity:
    candidate_id: str
    valid: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    video_id: str
    test_case_id: str
    workflow_mode: str
    video_duration_s: float
    processing_s: float | None
    rtf: float | None
    candidates: int
    accepted: int | None
    rejected: int | None
    deferred: int | None
    pending: int | None
    adoption_rate: float | None
    valid_clips: int | None
    valid_clips_per_video_hour: float | None
    human_review_s: float | None
    manual_interventions: int | None
    current_linked_state: dict[str, int]
    candidate_count_semantics: str = "linked_candidates_not_new_extractions"
    yield_sample_video_count: int = 1
    yield_stability: str = "exploratory_single_video"

    def as_dict(self) -> dict:
        values = asdict(self)
        values["candidate_links"] = values.pop("candidates")
        return values


def validate_final_dataset(connection: sqlite3.Connection, project_root: Path) -> list[DatasetItemValidity]:
    rows = connection.execute(
        """SELECT e.*, r.review_status, r.gold_emotion,
                  (SELECT count(*) FROM export_issues i
                   WHERE i.export_id = e.export_id AND i.resolved_at IS NULL) AS open_issues
           FROM exports e LEFT JOIN reviews r ON r.candidate_id = e.candidate_id
           WHERE e.export_status = 'completed' ORDER BY e.candidate_id"""
    ).fetchall()
    result = []
    for row in rows:
        reasons: list[str] = []
        if row["review_status"] != "accepted":
            reasons.append("current_review_not_accepted")
        if row["gold_emotion"] != row["exported_gold_emotion"]:
            reasons.append("gold_emotion_changed")
        if row["open_issues"]:
            reasons.append("unresolved_export_issue")
        try:
            destination = resolve_inside_project(
                project_root, row["destination_path"], must_exist=True, require_file=True
            )
            if sha256_file(destination) != row["destination_hash"]:
                reasons.append("destination_hash_mismatch")
        except (OSError, ValueError):
            reasons.append("destination_missing_or_unsafe")
        result.append(DatasetItemValidity(row["candidate_id"], not reasons, tuple(reasons)))
    return result


def build_run_summaries(connection: sqlite3.Connection, project_root: Path) -> list[RunSummary]:
    runs = connection.execute(
        "SELECT r.*, v.duration_s FROM runs r JOIN videos v ON v.video_id = r.video_id ORDER BY r.run_id"
    ).fetchall()
    summaries = []
    for run in runs:
        links = connection.execute(
            "SELECT candidate_id FROM candidate_run_links WHERE run_id = ?", (run["run_id"],)
        ).fetchall()
        ids = [row["candidate_id"] for row in links]
        current_counts = {status: 0 for status in ("accepted", "rejected", "deferred", "pending")}
        stage_measurement = connection.execute(
            """SELECT count(*) AS n, sum(elapsed_s) AS elapsed FROM stage_events
               WHERE run_id = ? AND stage = 'human_review'""",
            (run["run_id"],),
        ).fetchone()
        if stage_measurement["n"]:
            human_review_s = stage_measurement["elapsed"]
        else:
            measured = connection.execute(
                """SELECT count(*) AS n, sum(elapsed_s) AS elapsed
                   FROM review_measurements
                   WHERE run_id=? AND measurement_status='measured'""",
                (run["run_id"],),
            ).fetchone()
            human_review_s = measured["elapsed"] if measured["n"] else None
        if ids:
            placeholders = ",".join("?" for _ in ids)
            for row in connection.execute(
                f"SELECT review_status, count(*) AS n FROM reviews WHERE candidate_id IN ({placeholders}) GROUP BY review_status",
                ids,
            ):
                current_counts[row["review_status"]] = row["n"]
        latest_outcomes: dict[str, sqlite3.Row] = {}
        for row in connection.execute(
            """SELECT * FROM run_candidate_outcomes
               WHERE run_id=? ORDER BY outcome_id""", (run["run_id"],)
        ):
            latest_outcomes[row["candidate_id"]] = row
        if latest_outcomes:
            counts = {status: 0 for status in ("accepted", "rejected", "deferred", "pending")}
            for outcome in latest_outcomes.values():
                counts[outcome["review_status"]] += 1
            reviewed = sum(counts.values())
            valid_count = sum(outcome["export_valid"] for outcome in latest_outcomes.values())
        else:
            counts = {status: None for status in ("accepted", "rejected", "deferred", "pending")}
            reviewed = 0
            valid_count = None
        duration = run["duration_s"]
        processing = run["elapsed_s"]
        coverage = connection.execute(
            """SELECT measurement_status FROM metric_coverage
               WHERE run_id=? AND metric_name='manual_interventions'""",
            (run["run_id"],),
        ).fetchone()
        interventions = None if coverage is None or coverage["measurement_status"] != "measured" else connection.execute(
            "SELECT count(*) FROM manual_interventions WHERE run_id = ?", (run["run_id"],)
        ).fetchone()[0]
        summaries.append(RunSummary(
            run["run_id"], run["video_id"], run["test_case_id"], run["workflow_mode"],
            duration, processing, processing / duration if processing is not None and duration else None,
            len(ids), counts["accepted"], counts["rejected"], counts["deferred"], counts["pending"],
            counts["accepted"] / reviewed if reviewed and counts["accepted"] is not None else None,
            valid_count,
            valid_count / (duration / 3600) if valid_count is not None and duration else None,
            human_review_s, interventions, current_counts,
        ))
    return summaries


def compare_workflows(summaries: list[RunSummary]) -> list[dict]:
    by_case: dict[str, dict[str, RunSummary]] = {}
    for summary in summaries:
        by_case.setdefault(summary.test_case_id, {})[summary.workflow_mode] = summary
    comparisons = []
    for test_case_id, modes in sorted(by_case.items()):
        if "manual" not in modes or "automated" not in modes:
            continue
        manual, automated = modes["manual"], modes["automated"]
        if manual.human_review_s is None or automated.human_review_s is None:
            continue
        baseline = manual.human_review_s
        saved = baseline - automated.human_review_s
        comparisons.append({
            "test_case_id": test_case_id,
            "manual_run_id": manual.run_id,
            "automated_run_id": automated.run_id,
            "manual_human_review_s": baseline,
            "automated_human_review_s": automated.human_review_s,
            "saved_human_s": saved,
            "labor_reduction_rate": saved / baseline if baseline else None,
            "manual_valid_clips": manual.valid_clips,
            "automated_valid_clips": automated.valid_clips,
        })
    return comparisons
