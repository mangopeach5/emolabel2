from __future__ import annotations

from datetime import datetime, timezone
import sqlite3


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def select_candidates(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    stt_analysis_id: int,
    boundary_tolerance_ms: int = 750,
) -> list[str]:
    if boundary_tolerance_ms < 0:
        raise ValueError("boundary_tolerance_ms must be nonnegative")
    candidates = connection.execute(
        """SELECT c.* FROM candidates c
           JOIN candidate_run_links l ON l.candidate_id=c.candidate_id
           WHERE l.run_id=? ORDER BY c.start_ms""", (run_id,)
    ).fetchall()
    segments = connection.execute(
        "SELECT * FROM stt_segments WHERE stt_analysis_id=? ORDER BY start_ms",
        (stt_analysis_id,),
    ).fetchall()
    selected: list[str] = []
    with connection:
        for candidate in candidates:
            matching = [
                item for item in segments
                if _overlap(candidate["start_ms"], candidate["end_ms"], item["start_ms"], item["end_ms"]) > 0
            ]
            duration = candidate["end_ms"] - candidate["start_ms"]
            transcript = " ".join(item["text"].strip() for item in matching if item["text"].strip()).strip()
            overlap_ms = sum(
                _overlap(candidate["start_ms"], candidate["end_ms"], item["start_ms"], item["end_ms"])
                for item in matching
            )
            overlap_ratio = min(1.0, overlap_ms / duration) if duration else 0.0
            uncertain = not matching or not transcript or any(item["uncertain"] for item in matching)
            if matching:
                first, last = matching[0], matching[-1]
                starts_cleanly = first["start_ms"] >= candidate["start_ms"] - boundary_tolerance_ms
                ends_cleanly = last["end_ms"] <= candidate["end_ms"] + boundary_tolerance_ms
                edge_gap_ok = (
                    abs(first["start_ms"] - candidate["start_ms"]) <= 1500
                    and abs(candidate["end_ms"] - last["end_ms"]) <= 1500
                )
                sentence_complete = starts_cleanly and ends_cleanly and edge_gap_ok
            else:
                sentence_complete = False
            punctuation_support = transcript.endswith((".", "?", "!", "。", "？", "！"))
            score = min(1.0, overlap_ratio * 0.55 + (0.35 if sentence_complete else 0) + (0.1 if punctuation_support else 0))
            warning = connection.execute(
                """SELECT 1 FROM scene_candidate_decisions
                   WHERE candidate_id=? AND decision_status='warning' LIMIT 1""",
                (candidate["candidate_id"],),
            ).fetchone()
            if warning is not None:
                score = max(0.0, score - 0.1)
            if uncertain:
                status, reason, complete_value = "deferred", "stt_uncertain", None
            elif not sentence_complete:
                status, reason, complete_value = "rejected", "sentence_boundary_incomplete", 0
            elif score < 0.65:
                status, reason, complete_value = "deferred", "low_selection_score", 1
            else:
                status, reason, complete_value = "selected", "vad_stt_scene_pass", 1
                selected.append(candidate["candidate_id"])
            connection.execute(
                """INSERT INTO clip_selection_decisions (
                    run_id, candidate_id, stt_analysis_id, decision_status, score,
                    reason, transcript, sentence_complete, vad_stt_overlap, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, candidate["candidate_id"], stt_analysis_id, status, score,
                    reason, transcript, complete_value, overlap_ratio,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.execute(
                "UPDATE candidates SET transcript=? WHERE candidate_id=?",
                (transcript, candidate["candidate_id"]),
            )
    return selected
