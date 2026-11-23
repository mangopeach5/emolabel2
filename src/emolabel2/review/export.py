"""Verified, non-overwriting export of explicitly accepted candidate clips."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from emolabel2.intake.hashing import sha256_file
from emolabel2.paths import resolve_candidate_clip, resolve_final_dataset, resolve_inside_project


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ExportError(RuntimeError):
    pass


class CandidateNotAcceptedError(ExportError):
    pass


class ExportIntegrityError(ExportError):
    pass


class DestinationExistsError(ExportError):
    pass


class DuplicateClipExportError(ExportError):
    pass


class ExportStateMismatchError(ExportError):
    pass


@dataclass(frozen=True)
class ExportResult:
    export_id: int
    destination: Path
    file_hash: str
    already_exported: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_failed(connection: sqlite3.Connection, export_id: int, error: Exception) -> None:
    with connection:
        connection.execute(
            """
            UPDATE exports
            SET export_status = 'failed', destination_hash = NULL,
                completed_at = NULL, error_message = ?
            WHERE export_id = ?
            """,
            (f"{type(error).__name__}: {error}", export_id),
        )


def export_accepted_candidate(
    connection: sqlite3.Connection,
    project_root: Path,
    candidate_id: str,
) -> ExportResult:
    """Copy one accepted clip after verifying source and destination hashes."""
    if not _SAFE_ID_RE.fullmatch(candidate_id):
        raise ExportError("candidate_id is unsafe for an export filename")

    record = connection.execute(
        """
        SELECT c.clip_path, c.file_hash, r.review_status, r.gold_emotion
        FROM candidates AS c
        LEFT JOIN reviews AS r ON r.candidate_id = c.candidate_id
        WHERE c.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if record is None:
        raise ExportError(f"candidate not found: {candidate_id}")
    if record["review_status"] != "accepted" or record["gold_emotion"] is None:
        raise CandidateNotAcceptedError(candidate_id)
    if not record["clip_path"] or not record["file_hash"]:
        raise ExportIntegrityError("candidate has no registered clip path or hash")

    root = Path(project_root).resolve(strict=True)
    source = resolve_candidate_clip(root, record["clip_path"])
    final_dir = resolve_final_dataset(root)
    suffix = source.suffix.lower()
    destination = final_dir / f"{candidate_id}{suffix}"
    destination_relative = destination.relative_to(root).as_posix()
    source_relative = source.relative_to(root).as_posix()

    existing = connection.execute(
        "SELECT * FROM exports WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    duplicate = connection.execute(
        """
        SELECT candidate_id FROM exports
        WHERE source_hash = ? AND candidate_id != ?
        """,
        (record["file_hash"], candidate_id),
    ).fetchone()
    if duplicate is not None:
        raise DuplicateClipExportError(
            f"same clip hash was already exported for candidate {duplicate['candidate_id']}"
        )
    if existing is not None and existing["export_status"] == "completed":
        if existing["exported_gold_emotion"] != record["gold_emotion"]:
            raise ExportStateMismatchError("gold emotion changed after export")
        saved_destination = resolve_inside_project(
            root, existing["destination_path"], must_exist=True, require_file=True
        )
        current_hash = sha256_file(saved_destination)
        if current_hash != existing["destination_hash"]:
            raise ExportIntegrityError("completed export file hash no longer matches")
        return ExportResult(existing["export_id"], saved_destination, current_hash, True)

    if destination.exists():
        raise DestinationExistsError(f"destination already exists: {destination_relative}")

    started_at = _utc_now()
    with connection:
        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO exports (
                    candidate_id, export_status, source_path, destination_path,
                    source_hash, exported_gold_emotion, started_at
                ) VALUES (?, 'in_progress', ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id, source_relative, destination_relative,
                    record["file_hash"], record["gold_emotion"], started_at,
                ),
            )
            export_id = int(cursor.lastrowid)
        else:
            export_id = int(existing["export_id"])
            connection.execute(
                """
                UPDATE exports SET
                    export_status = 'in_progress', source_path = ?, destination_path = ?,
                    source_hash = ?, destination_hash = NULL,
                    exported_gold_emotion = ?, started_at = ?, completed_at = NULL,
                    error_message = ''
                WHERE export_id = ?
                """,
                (
                    source_relative, destination_relative, record["file_hash"],
                    record["gold_emotion"], started_at, export_id,
                ),
            )

    temporary = final_dir / f".{candidate_id}.partial.{uuid4().hex}"
    destination_created = False
    try:
        current_source_hash = sha256_file(source)
        if current_source_hash != record["file_hash"]:
            raise ExportIntegrityError("candidate source hash changed before export")

        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())

        copied_hash = sha256_file(temporary)
        if copied_hash != current_source_hash:
            raise ExportIntegrityError("copied file hash does not match source")

        # Hard-link creation fails if destination already exists, so it cannot
        # silently overwrite a file created by another process.
        os.link(temporary, destination)
        destination_created = True
        temporary.unlink()

        destination_hash = sha256_file(destination)
        if destination_hash != current_source_hash:
            raise ExportIntegrityError("final destination hash does not match source")

        latest_review = connection.execute(
            """
            SELECT review_status, gold_emotion FROM reviews
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if (
            latest_review is None
            or latest_review["review_status"] != "accepted"
            or latest_review["gold_emotion"] != record["gold_emotion"]
        ):
            raise ExportStateMismatchError("review changed while export was running")

        with connection:
            connection.execute(
                """
                UPDATE exports SET export_status = 'completed', destination_hash = ?,
                    completed_at = ?, error_message = '' WHERE export_id = ?
                """,
                (destination_hash, _utc_now(), export_id),
            )
        return ExportResult(export_id, destination, destination_hash)
    except Exception as error:
        if temporary.exists():
            temporary.unlink()
        if destination_created and destination.exists():
            destination.unlink()
        _mark_failed(connection, export_id, error)
        raise
