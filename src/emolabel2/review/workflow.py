"""High-level Phase 1B workflow joining review persistence and safe export."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from emolabel2.domain import ReviewRecord, ReviewStatus
from emolabel2.review.export import ExportResult, export_accepted_candidate
from emolabel2.review.service import record_review


def save_review_and_export_if_accepted(
    connection: sqlite3.Connection,
    project_root: Path,
    review: ReviewRecord,
    *,
    run_id: str | None = None,
    review_started_at: str | None = None,
    review_ended_at: str | None = None,
    human_time_measured: bool | None = None,
) -> ExportResult | None:
    """Persist every review, exporting only an explicit accepted decision."""
    event_id = record_review(
        connection, review, run_id=run_id,
        review_started_at=review_started_at, review_ended_at=review_ended_at,
        human_time_measured=human_time_measured,
    )
    if review.status is ReviewStatus.ACCEPTED:
        result = export_accepted_candidate(connection, project_root, review.candidate_id)
        if run_id is not None:
            with connection:
                connection.execute(
                    """UPDATE run_candidate_outcomes SET export_valid=1
                       WHERE run_id=? AND review_event_id=?""",
                    (run_id, event_id),
                )
        return result
    return None
