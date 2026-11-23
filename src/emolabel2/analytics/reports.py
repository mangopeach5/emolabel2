"""Structured JSON/CSV reports for CLI and future dashboards."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

from emolabel2.analytics.evaluation import classification_report
from emolabel2.analytics.summary import build_run_summaries, compare_workflows, validate_final_dataset


def build_report(connection: sqlite3.Connection, project_root: Path) -> dict:
    validity = validate_final_dataset(connection, project_root)
    valid_ids = [item.candidate_id for item in validity if item.valid]
    summaries = build_run_summaries(connection, project_root)
    stages = [dict(row) for row in connection.execute(
        "SELECT * FROM stage_events ORDER BY run_id, event_id"
    ).fetchall()]

    pairs: list[tuple[str, str | None]] = []
    if valid_ids:
        placeholders = ",".join("?" for _ in valid_ids)
        rows = connection.execute(
            f"""SELECT r.gold_emotion,
                       (SELECT p.emotion FROM predictions p
                        WHERE p.candidate_id = r.candidate_id
                        ORDER BY p.prediction_id DESC LIMIT 1) AS predicted
                FROM reviews r
                WHERE r.candidate_id IN ({placeholders})
                  AND r.review_status = 'accepted' AND r.gold_emotion IS NOT NULL""",
            valid_ids,
        ).fetchall()
        pairs = [(row["gold_emotion"], row["predicted"]) for row in rows]
    classification = classification_report(pairs)
    observed_videos = connection.execute(
        """SELECT DISTINCT v.video_id, v.duration_s
           FROM videos v JOIN runs r ON r.video_id=v.video_id"""
    ).fetchall()
    observed_duration_s = sum(row["duration_s"] for row in observed_videos)
    valid_count = sum(item.valid for item in validity)
    return {
        "schema": "emolabel2.analytics.v2",
        "runs": [summary.as_dict() for summary in summaries],
        "stages": stages,
        "dataset": {
            "valid_count": valid_count,
            "invalid_count": sum(not item.valid for item in validity),
            "yield_observed_video_count": len(observed_videos),
            "yield_observed_duration_s": observed_duration_s,
            "valid_clips_per_video_hour": (
                valid_count / (observed_duration_s / 3600) if observed_duration_s else None
            ),
            "yield_stability": (
                "exploratory_single_video" if len(observed_videos) == 1
                else "multi_video_observation" if observed_videos else "not_measured"
            ),
            "items": [
                {"candidate_id": item.candidate_id, "valid": item.valid, "reasons": list(item.reasons)}
                for item in validity
            ],
        },
        "comparisons": compare_workflows(summaries),
        "classification": classification.as_dict() if classification else None,
    }


def write_json_report(report: dict, path: Path) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_csv_report(report: dict, path: Path) -> None:
    fields = [
        "run_id", "video_id", "test_case_id", "workflow_mode", "video_duration_s",
        "processing_s", "rtf", "candidate_links", "accepted", "rejected", "deferred",
        "pending", "adoption_rate", "valid_clips", "valid_clips_per_video_hour",
        "human_review_s", "manual_interventions",
        "candidate_count_semantics", "yield_sample_video_count", "yield_stability",
    ]
    with Path(path).open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {field: row.get(field) for field in fields}
            for row in report["runs"]
        )
