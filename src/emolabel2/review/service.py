"""Transactional human-review state changes with append-only history."""
from __future__ import annotations

from datetime import datetime
import sqlite3

from emolabel2.domain import ReviewRecord, ReviewStatus


class CandidateNotFoundError(LookupError):
    pass


def _add_issue_if_missing(
    connection: sqlite3.Connection,
    *,
    export_id: int,
    candidate_id: str,
    issue_type: str,
    details: str,
    detected_at: str,
) -> None:
    existing = connection.execute(
        """
        SELECT 1 FROM export_issues
        WHERE export_id = ? AND issue_type = ? AND resolved_at IS NULL
        """,
        (export_id, issue_type),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO export_issues (
                export_id, candidate_id, issue_type, details, detected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (export_id, candidate_id, issue_type, details, detected_at),
        )


def _sync_export_issues(connection: sqlite3.Connection, review: ReviewRecord) -> None:
    exported = connection.execute(
        """
        SELECT export_id, exported_gold_emotion FROM exports
        WHERE candidate_id = ? AND export_status = 'completed'
        """,
        (review.candidate_id,),
    ).fetchone()
    if exported is None:
        return

    # Close prior mismatch episodes first. If the current review still differs,
    # a new unresolved issue below represents the current state precisely while
    # the resolved rows preserve the full history.
    connection.execute(
        """
        UPDATE export_issues SET resolved_at = ?
        WHERE export_id = ? AND resolved_at IS NULL
        """,
        (review.reviewed_at, exported["export_id"]),
    )

    matches = (
        review.status is ReviewStatus.ACCEPTED
        and review.gold_emotion is not None
        and review.gold_emotion.value == exported["exported_gold_emotion"]
    )
    if matches:
        return

    if review.status is not ReviewStatus.ACCEPTED:
        _add_issue_if_missing(
            connection,
            export_id=exported["export_id"],
            candidate_id=review.candidate_id,
            issue_type="review_status_changed",
            details=f"exported candidate is now {review.status.value}",
            detected_at=review.reviewed_at,
        )
    else:
        _add_issue_if_missing(
            connection,
            export_id=exported["export_id"],
            candidate_id=review.candidate_id,
            issue_type="gold_emotion_changed",
            details=(
                f"exported gold={exported['exported_gold_emotion']}; "
                f"current gold={review.gold_emotion.value}"
            ),
            detected_at=review.reviewed_at,
        )


def _elapsed_from_timestamps(started_at: str, ended_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at)
        ended = datetime.fromisoformat(ended_at)
    except ValueError as exc:
        raise ValueError("review timestamps must be ISO-8601") from exc
    elapsed = (ended - started).total_seconds()
    if elapsed < 0:
        raise ValueError("review ended_at must not precede started_at")
    return elapsed


def record_review(
    connection: sqlite3.Connection,
    review: ReviewRecord,
    *,
    run_id: str | None = None,
    review_started_at: str | None = None,
    review_ended_at: str | None = None,
    human_time_measured: bool | None = None,
) -> int:
    """Save current review state and an immutable state-change event."""
    with connection:
        candidate = connection.execute(
            "SELECT candidate_id FROM candidates WHERE candidate_id = ?",
            (review.candidate_id,),
        ).fetchone()
        if candidate is None:
            raise CandidateNotFoundError(review.candidate_id)
        if run_id is not None:
            linked = connection.execute(
                """SELECT 1 FROM candidate_run_links
                   WHERE run_id=? AND candidate_id=?""",
                (run_id, review.candidate_id),
            ).fetchone()
            if linked is None:
                raise ValueError("candidate is not linked to the specified run")
        if (review_started_at is None) != (review_ended_at is None):
            raise ValueError("review start and end timestamps must be provided together")

        previous = connection.execute(
            "SELECT review_status, gold_emotion FROM reviews WHERE candidate_id = ?",
            (review.candidate_id,),
        ).fetchone()
        cursor = connection.execute(
            """
            INSERT INTO review_events (
                candidate_id, previous_status, new_status,
                previous_gold_emotion, new_gold_emotion,
                human_time_s, reject_reason, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.candidate_id,
                previous["review_status"] if previous else None,
                review.status.value,
                previous["gold_emotion"] if previous else None,
                review.gold_emotion.value if review.gold_emotion else None,
                review.human_time_s,
                review.reject_reason,
                review.reviewed_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO reviews (
                candidate_id, review_status, gold_emotion,
                human_time_s, reviewed_at, reject_reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                review_status = excluded.review_status,
                gold_emotion = excluded.gold_emotion,
                human_time_s = excluded.human_time_s,
                reviewed_at = excluded.reviewed_at,
                reject_reason = excluded.reject_reason
            """,
            (
                review.candidate_id,
                review.status.value,
                review.gold_emotion.value if review.gold_emotion else None,
                review.human_time_s,
                review.reviewed_at,
                review.reject_reason,
            ),
        )
        connection.execute(
            "UPDATE candidates SET status = 'reviewed' WHERE candidate_id = ?",
            (review.candidate_id,),
        )
        _sync_export_issues(connection, review)
        if review_started_at is not None:
            elapsed_s = _elapsed_from_timestamps(review_started_at, review_ended_at)
            measurement_status = "measured"
            measurement_source = "explicit_timestamps"
        else:
            measured = review.human_time_s > 0 if human_time_measured is None else human_time_measured
            elapsed_s = review.human_time_s if measured else None
            measurement_status = "measured" if measured else "not_measured"
            measurement_source = "explicit_duration" if measured else "not_provided"
        connection.execute(
            """INSERT INTO review_measurements (
                review_event_id, run_id, started_at, ended_at, elapsed_s,
                measurement_status, measurement_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                cursor.lastrowid, run_id, review_started_at, review_ended_at,
                elapsed_s, measurement_status, measurement_source,
            ),
        )
        if run_id is not None:
            connection.execute(
                """INSERT INTO run_candidate_outcomes (
                    run_id, candidate_id, review_event_id, review_status,
                    gold_emotion, export_valid, recorded_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (
                    run_id, review.candidate_id, cursor.lastrowid, review.status.value,
                    review.gold_emotion.value if review.gold_emotion else None,
                    review.reviewed_at,
                ),
            )
    return int(cursor.lastrowid)


def list_review_events(connection: sqlite3.Connection, candidate_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM review_events WHERE candidate_id = ? ORDER BY event_id",
        (candidate_id,),
    ).fetchall()
