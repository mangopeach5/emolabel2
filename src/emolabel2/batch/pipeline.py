from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from time import perf_counter
from typing import Callable

from emolabel2.domain import RightsStatus, VideoRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.media.probe import probe_media
from emolabel2.paths import resolve_inside_project
from emolabel2.pipeline import run_automatic_pipeline
from emolabel2.storage import find_video_by_hash, insert_video


_SAFE_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class BatchConfig:
    rights_basis: str
    allowed_use: str
    whisper_model: str = "base"
    whisper_model_path: str = "models/whisper/base.pt"
    device: str = "cpu"
    scene_policy: str = "strict"
    ollama_model: str = "qwen2.5:1.5b"
    skip_emotion: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _video_id_for_hash(connection: sqlite3.Connection, digest: str) -> str:
    for length in (16, 24, 32, 64):
        value = f"batch-{digest[:length]}"
        row = connection.execute("SELECT file_hash FROM videos WHERE video_id=?", (value,)).fetchone()
        if row is None or row["file_hash"] == digest:
            return value
    raise RuntimeError("unable to allocate collision-free video_id")


def _register_or_reuse_video(
    connection: sqlite3.Connection,
    project_root: Path,
    source: Path,
    digest: str,
    config: BatchConfig,
    media_probe: Callable[[Path], object],
) -> str:
    existing = find_video_by_hash(connection, digest)
    if existing is not None:
        return existing["video_id"]
    info = media_probe(source)
    video_id = _video_id_for_hash(connection, digest)
    relative = source.relative_to(project_root).as_posix()
    insert_video(connection, VideoRecord(
        video_id, relative, digest, "local-approved-batch",
        RightsStatus.APPROVED_RESEARCH, config.rights_basis, config.allowed_use,
        float(info.duration_s), _utc_now(), "registered by batch pipeline",
    ))
    return video_id


def _discover(
    connection: sqlite3.Connection, project_root: Path, batch_id: str
) -> list[sqlite3.Row]:
    directory = resolve_inside_project(project_root, "data/inbox/batch", must_exist=True)
    if not directory.is_dir():
        raise ValueError("data/inbox/batch must be a directory")
    canonical_by_hash = {
        row["file_hash"]: row["item_id"]
        for row in connection.execute(
            """SELECT item_id, file_hash FROM batch_items
               WHERE batch_id=? AND duplicate_of_item_id IS NULL ORDER BY item_id""",
            (batch_id,),
        )
    }
    for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if entry.suffix.lower() != ".mp4":
            continue
        source = resolve_inside_project(project_root, entry.relative_to(project_root), must_exist=True, require_file=True)
        relative = source.relative_to(project_root).as_posix()
        exists = connection.execute(
            "SELECT item_id FROM batch_items WHERE batch_id=? AND source_path=?",
            (batch_id, relative),
        ).fetchone()
        if exists is not None:
            continue
        digest = sha256_file(source)
        duplicate_of = canonical_by_hash.get(digest)
        status = "skipped_duplicate" if duplicate_of is not None else "pending"
        with connection:
            cursor = connection.execute(
                """INSERT INTO batch_items (
                    batch_id, source_path, file_hash, status, duplicate_of_item_id
                ) VALUES (?, ?, ?, ?, ?)""",
                (batch_id, relative, digest, status, duplicate_of),
            )
        if duplicate_of is None:
            canonical_by_hash[digest] = int(cursor.lastrowid)
    return connection.execute(
        "SELECT * FROM batch_items WHERE batch_id=? ORDER BY item_id", (batch_id,)
    ).fetchall()


def run_batch(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    batch_id: str,
    config: BatchConfig,
    pipeline_runner: Callable[..., dict] = run_automatic_pipeline,
    media_probe: Callable[[Path], object] = probe_media,
) -> dict:
    if not _SAFE_BATCH_ID.fullmatch(batch_id):
        raise ValueError("unsafe batch_id")
    root = Path(project_root).resolve(strict=True)
    now = _utc_now()
    existing = connection.execute("SELECT * FROM batch_jobs WHERE batch_id=?", (batch_id,)).fetchone()
    if existing is None:
        with connection:
            connection.execute(
                """INSERT INTO batch_jobs(batch_id, config_json, status, created_at, updated_at)
                   VALUES (?, ?, 'running', ?, ?)""",
                (batch_id, config.to_json(), now, now),
            )
    elif existing["config_json"] != config.to_json():
        raise ValueError("batch configuration changed; use a new batch_id")
    else:
        with connection:
            connection.execute(
                "UPDATE batch_jobs SET status='running', updated_at=? WHERE batch_id=?",
                (now, batch_id),
            )
    items = _discover(connection, root, batch_id)
    for item in items:
        if item["status"] == "skipped_duplicate":
            continue
        if item["status"] == "completed":
            try:
                completed_source = resolve_inside_project(
                    root, item["source_path"], must_exist=True, require_file=True,
                )
                if sha256_file(completed_source) == item["file_hash"]:
                    continue
                completed_error = "batch input changed after completion; use a new batch_id"
            except Exception as exc:
                completed_error = f"completed batch input is unavailable: {exc}"
            with connection:
                connection.execute(
                    """UPDATE batch_items SET status='failed', error_type='InputChangedError',
                       error_message=?, completed_at=? WHERE item_id=?""",
                    (completed_error[:2000], _utc_now(), item["item_id"]),
                )
            continue
        started = perf_counter()
        attempt = item["attempt_count"] + 1
        run_id = f"batch-{batch_id}-{item['file_hash'][:10]}-a{attempt}"
        with connection:
            connection.execute(
                """UPDATE batch_items SET status='running', attempt_count=?, started_at=?,
                   completed_at=NULL, error_type='', error_message=''
                   WHERE item_id=?""",
                (attempt, _utc_now(), item["item_id"]),
            )
        try:
            source = resolve_inside_project(root, item["source_path"], must_exist=True, require_file=True)
            if sha256_file(source) != item["file_hash"]:
                raise RuntimeError("batch input changed after discovery")
            video_id = _register_or_reuse_video(
                connection, root, source, item["file_hash"], config, media_probe,
            )
            result = pipeline_runner(
                connection, root, video_id=video_id, run_id=run_id,
                whisper_model=config.whisper_model,
                whisper_model_path=config.whisper_model_path,
                device=config.device, scene_policy=config.scene_policy,
                ollama_model=config.ollama_model, skip_emotion=config.skip_emotion,
            )
            candidate_count = connection.execute(
                "SELECT count(*) FROM candidate_run_links WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            with connection:
                connection.execute(
                    """UPDATE batch_items SET status='completed', video_id=?, run_id=?,
                       candidate_count=?, selected_count=?, prediction_count=?, elapsed_s=?,
                       completed_at=? WHERE item_id=?""",
                    (
                        video_id, run_id, candidate_count,
                        len(result.get("selected_candidate_ids", [])),
                        len(result.get("prediction_ids", [])), perf_counter() - started,
                        _utc_now(), item["item_id"],
                    ),
                )
        except Exception as exc:
            run_exists = connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
            with connection:
                connection.execute(
                    """UPDATE batch_items SET status='failed', run_id=?, elapsed_s=?,
                       error_type=?, error_message=?, completed_at=? WHERE item_id=?""",
                    (
                        run_id if run_exists else None, perf_counter() - started,
                        type(exc).__name__, str(exc)[:2000], _utc_now(), item["item_id"],
                    ),
                )
    failed = connection.execute(
        "SELECT count(*) FROM batch_items WHERE batch_id=? AND status='failed'", (batch_id,)
    ).fetchone()[0]
    final_status = "completed_with_errors" if failed else "completed"
    with connection:
        connection.execute(
            "UPDATE batch_jobs SET status=?, updated_at=? WHERE batch_id=?",
            (final_status, _utc_now(), batch_id),
        )
    return batch_report(connection, batch_id)


def batch_report(connection: sqlite3.Connection, batch_id: str) -> dict:
    job = connection.execute("SELECT * FROM batch_jobs WHERE batch_id=?", (batch_id,)).fetchone()
    if job is None:
        raise LookupError(f"batch not found: {batch_id}")
    items = [dict(row) for row in connection.execute(
        "SELECT * FROM batch_items WHERE batch_id=? ORDER BY item_id", (batch_id,)
    )]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "schema": "emolabel2.batch.v1", "batch_id": batch_id,
        "status": job["status"], "counts": counts, "items": items,
    }


def write_batch_reports(report: dict, json_path: Path, csv_path: Path) -> None:
    with Path(json_path).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    fields = [
        "item_id", "source_path", "file_hash", "video_id", "run_id", "status",
        "attempt_count", "candidate_count", "selected_count", "prediction_count",
        "elapsed_s", "error_type", "error_message", "started_at", "completed_at",
    ]
    with Path(csv_path).open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: item.get(field) for field in fields} for item in report["items"])
