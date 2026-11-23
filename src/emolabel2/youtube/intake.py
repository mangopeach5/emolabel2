from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from emolabel2.intake.hashing import sha256_file
from emolabel2.paths import resolve_inside_project, resolve_manifest_input


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
LINK_COLUMNS = ("youtube_video_id", "local_path", "rights_status", "rights_basis", "allowed_use")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_youtube_url(value: str) -> tuple[str, str]:
    raw = value.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parts[:1] == ["shorts"] and len(parts) >= 2:
            video_id = parts[1]
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError(f"unsupported or invalid YouTube URL: {value}")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def read_url_file(project_root: Path, path: str | Path) -> tuple[list[tuple[str, str]], list[dict]]:
    source = resolve_inside_project(project_root, path, must_exist=True, require_file=True)
    unique: dict[str, str] = {}
    failures: list[dict] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            video_id, canonical = normalize_youtube_url(value)
            unique.setdefault(video_id, canonical)
        except ValueError as exc:
            failures.append({"line": line_number, "input": value, "error": str(exc)})
    return list(unique.items()), failures


def register_urls(connection: sqlite3.Connection, entries: list[tuple[str, str]]) -> None:
    now = utc_now()
    with connection:
        for video_id, canonical in entries:
            connection.execute(
                """INSERT INTO youtube_sources(
                       youtube_video_id, canonical_url, metadata_status, rights_status,
                       file_status, created_at, updated_at
                   ) VALUES (?, ?, 'not_requested', 'unknown', 'missing', ?, ?)
                   ON CONFLICT(youtube_video_id) DO UPDATE SET updated_at=excluded.updated_at""",
                (video_id, canonical, now, now),
            )


def fetch_oembed(canonical_url: str, *, opener=urlopen, timeout: float = 10.0) -> dict:
    endpoint = "https://www.youtube.com/oembed?" + urlencode({"url": canonical_url, "format": "json"})
    request = Request(endpoint, headers={"User-Agent": "EmoLabel2/1.0 metadata-only"})
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {"title": payload.get("title"), "channel_name": payload.get("author_name"),
            "channel_url": payload.get("author_url"), "thumbnail_url": payload.get("thumbnail_url")}


def refresh_metadata(connection: sqlite3.Connection, *, failed_only: bool = False, fetcher=fetch_oembed) -> None:
    condition = "WHERE metadata_status='failed'" if failed_only else "WHERE metadata_status!='succeeded'"
    rows = connection.execute(
        f"SELECT youtube_video_id, canonical_url FROM youtube_sources {condition} ORDER BY youtube_video_id"
    ).fetchall()
    for row in rows:
        now = utc_now()
        try:
            metadata = fetcher(row["canonical_url"])
            with connection:
                connection.execute(
                    """UPDATE youtube_sources SET title=?, channel_name=?, channel_url=?, thumbnail_url=?,
                       metadata_status='succeeded', metadata_attempt_count=metadata_attempt_count+1,
                       metadata_error='', updated_at=? WHERE youtube_video_id=?""",
                    (metadata.get("title"), metadata.get("channel_name"), metadata.get("channel_url"),
                     metadata.get("thumbnail_url"), now, row["youtube_video_id"]),
                )
                connection.execute(
                    "INSERT INTO youtube_metadata_attempts(youtube_video_id, attempted_at, success) VALUES (?, ?, 1)",
                    (row["youtube_video_id"], now),
                )
        except Exception as exc:
            with connection:
                connection.execute(
                    """UPDATE youtube_sources SET metadata_status='failed',
                       metadata_attempt_count=metadata_attempt_count+1, metadata_error=?, updated_at=?
                       WHERE youtube_video_id=?""",
                    (str(exc)[:2000], now, row["youtube_video_id"]),
                )
                connection.execute(
                    """INSERT INTO youtube_metadata_attempts(
                       youtube_video_id, attempted_at, success, error_type, error_message
                       ) VALUES (?, ?, 0, ?, ?)""",
                    (row["youtube_video_id"], now, type(exc).__name__, str(exc)[:2000]),
                )


def read_link_manifest(project_root: Path, path: str | Path) -> list[dict]:
    source = resolve_inside_project(project_root, path, must_exist=True, require_file=True)
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [name for name in LINK_COLUMNS if not reader.fieldnames or name not in reader.fieldnames]
        if missing:
            raise ValueError(f"link manifest missing columns: {', '.join(missing)}")
        rows = [{key: (row.get(key) or "").strip() for key in LINK_COLUMNS} for row in reader]
    seen: set[str] = set()
    for number, row in enumerate(rows, 2):
        if not VIDEO_ID.fullmatch(row["youtube_video_id"]):
            raise ValueError(f"row {number}: invalid youtube_video_id")
        if row["youtube_video_id"] in seen:
            raise ValueError(f"row {number}: duplicate youtube_video_id")
        seen.add(row["youtube_video_id"])
        if row["rights_status"] != "approved":
            raise ValueError(f"row {number}: only approved files can be linked")
        if not row["rights_basis"] or not row["allowed_use"]:
            raise ValueError(f"row {number}: rights_basis and allowed_use are required")
        resolve_manifest_input(project_root, row["local_path"])
    return rows


def link_and_stage(connection: sqlite3.Connection, project_root: Path, rows: list[dict]) -> list[dict]:
    batch_dir = resolve_inside_project(project_root, "data/inbox/batch", must_exist=True)
    if not batch_dir.is_dir():
        raise ValueError("data/inbox/batch must be a directory")
    results = []
    for row in rows:
        video_id = row["youtube_video_id"]
        source_row = connection.execute(
            "SELECT * FROM youtube_sources WHERE youtube_video_id=?", (video_id,),
        ).fetchone()
        if source_row is None:
            raise LookupError(f"YouTube URL is not registered: {video_id}")
        if source_row["rights_status"] == "blocked":
            raise ValueError(f"YouTube ID {video_id} is blocked and cannot be staged")
        source = resolve_manifest_input(project_root, row["local_path"])
        if source.suffix.lower() != ".mp4":
            raise ValueError(f"linked file must be MP4: {row['local_path']}")
        digest = sha256_file(source)
        collision = connection.execute(
            "SELECT youtube_video_id FROM youtube_sources WHERE file_hash=? AND youtube_video_id!=?",
            (digest, video_id),
        ).fetchone()
        if collision is not None:
            raise ValueError(f"file is already linked to YouTube ID {collision['youtube_video_id']}")
        if source_row["file_hash"] is not None and source_row["file_hash"] != digest:
            raise ValueError(f"YouTube ID {video_id} is already linked to a different file")
        destination = batch_dir / f"youtube-{video_id}-{digest[:12]}.mp4"
        if destination.exists():
            if not destination.is_file() or sha256_file(destination) != digest:
                raise FileExistsError(f"staging destination conflicts: {destination.name}")
        else:
            temporary = batch_dir / f".{destination.name}.partial"
            if temporary.exists():
                raise FileExistsError(f"incomplete staging file exists: {temporary.name}")
            shutil.copyfile(source, temporary)
            if sha256_file(temporary) != digest:
                raise RuntimeError("staged file hash mismatch")
            temporary.replace(destination)
        relative = source.relative_to(project_root).as_posix()
        with connection:
            connection.execute(
                """UPDATE youtube_sources SET rights_status='approved', file_status='staged',
                   local_path=?, file_hash=?, updated_at=? WHERE youtube_video_id=?""",
                (relative, digest, utc_now(), video_id),
            )
        results.append({"youtube_video_id": video_id, "file_hash": digest,
                        "staged_path": destination.relative_to(project_root).as_posix()})
    return results


def set_rights_status(connection: sqlite3.Connection, video_id: str, status: str) -> None:
    if status not in {"unknown", "approved", "blocked"}:
        raise ValueError("invalid rights status")
    row = connection.execute(
        "SELECT file_status FROM youtube_sources WHERE youtube_video_id=?", (video_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"YouTube URL is not registered: {video_id}")
    if status == "blocked" and row["file_status"] != "missing":
        raise ValueError("a linked/staged file must be unlinked before blocking")
    with connection:
        connection.execute(
            "UPDATE youtube_sources SET rights_status=?, updated_at=? WHERE youtube_video_id=?",
            (status, utc_now(), video_id),
        )


def source_report(connection: sqlite3.Connection, failures: list[dict] | None = None) -> dict:
    return {"schema": "emolabel2.youtube-intake.v1",
            "items": [dict(row) for row in connection.execute(
                "SELECT * FROM youtube_sources ORDER BY youtube_video_id")],
            "invalid_inputs": failures or []}


def write_source_reports(report: dict, json_path: Path, csv_path: Path) -> None:
    with json_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    fields = list(report["items"][0].keys()) if report["items"] else [
        "youtube_video_id", "canonical_url", "metadata_status", "rights_status", "file_status"]
    with csv_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["items"])
