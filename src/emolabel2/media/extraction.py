"""Exact, non-overwriting local clip extraction using FFmpeg."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from uuid import uuid4

from emolabel2.analytics.runtime import Stage, StageTimer
from emolabel2.domain import CandidateRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.media.probe import MediaInfo, MediaProbeError, find_media_tool, probe_media, save_media_info
from emolabel2.paths import resolve_inside_project, resolve_manifest_input


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ClipExtractionError(RuntimeError):
    pass


class InvalidClipRangeError(ClipExtractionError):
    pass


class DuplicateClipError(ClipExtractionError):
    pass


class CandidateDestinationExistsError(ClipExtractionError):
    pass


@dataclass(frozen=True)
class ExtractedClip:
    candidate_id: str
    path: Path
    file_hash: str
    media_info: MediaInfo


def _timer(connection: sqlite3.Connection, run_id: str | None, stage: Stage, version: str):
    if run_id is None:
        return nullcontext()
    return StageTimer(connection, run_id, stage, algorithm_version=version)


def analyze_registered_video(
    connection: sqlite3.Connection,
    project_root: Path,
    video_id: str,
    *,
    run_id: str | None = None,
    ffprobe: str | None = None,
) -> MediaInfo:
    row = connection.execute(
        "SELECT local_path FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"video not found: {video_id}")
    source = resolve_manifest_input(project_root, row["local_path"])
    with _timer(connection, run_id, Stage.PREPROCESS, "ffprobe-json-v1"):
        info = probe_media(source, ffprobe=ffprobe)
        save_media_info(connection, video_id, info)
    return info


def extract_registered_clip(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    video_id: str,
    candidate_id: str,
    start_s: float,
    end_s: float,
    run_id: str | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    duration_tolerance_s: float = 0.35,
) -> ExtractedClip:
    if not _SAFE_ID_RE.fullmatch(candidate_id):
        raise ClipExtractionError("candidate_id is unsafe for a filename")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (start_s, end_s)):
        raise InvalidClipRangeError("start_s and end_s must be finite numbers")
    start_ms, end_ms = round(start_s * 1000), round(end_s * 1000)
    try:
        candidate = CandidateRecord(candidate_id, video_id, start_ms, end_ms, algorithm_version="ffmpeg-mpeg4-v1")
    except ValueError as exc:
        raise InvalidClipRangeError(str(exc)) from exc

    duplicate = connection.execute(
        "SELECT candidate_id FROM candidates WHERE video_id = ? AND start_ms = ? AND end_ms = ?",
        (video_id, start_ms, end_ms),
    ).fetchone()
    if duplicate is not None:
        raise DuplicateClipError(f"segment already registered as {duplicate['candidate_id']}")

    info = analyze_registered_video(
        connection, project_root, video_id, run_id=run_id, ffprobe=ffprobe
    )
    if end_s > info.duration_s + 1e-6:
        raise InvalidClipRangeError(
            f"requested end {end_s:.3f}s exceeds video duration {info.duration_s:.3f}s"
        )

    root = Path(project_root).resolve(strict=True)
    source_row = connection.execute(
        "SELECT local_path FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    source = resolve_manifest_input(root, source_row["local_path"])
    candidates_dir = resolve_inside_project(root, "data/candidates", must_exist=True)
    if not candidates_dir.is_dir():
        raise ClipExtractionError("data/candidates is not a directory")
    destination = candidates_dir / f"{candidate_id}.mp4"
    if destination.exists():
        raise CandidateDestinationExistsError(f"candidate destination exists: {destination.name}")
    temporary = candidates_dir / f".{candidate_id}.partial.{uuid4().hex}.mp4"
    executable = ffmpeg or find_media_tool("ffmpeg")
    duration_s = (end_ms - start_ms) / 1000

    with _timer(connection, run_id, Stage.CLIP_SELECTION, "ffmpeg-mpeg4-v1"):
        command = [
            executable, "-v", "error", "-n", "-i", str(source),
            "-ss", f"{start_ms / 1000:.3f}", "-t", f"{duration_s:.3f}",
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "mpeg4", "-q:v", "3",
            "-c:a", "aac", "-movflags", "+faststart", str(temporary),
        ]
        destination_created = False
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=300)
            if completed.returncode != 0:
                detail = completed.stderr.strip()[-1000:]
                raise ClipExtractionError(f"ffmpeg extraction failed: {detail or 'unknown error'}")
            clip_info = probe_media(temporary, ffprobe=ffprobe)
            if abs(clip_info.duration_s - duration_s) > duration_tolerance_s:
                raise ClipExtractionError(
                    f"clip duration mismatch: requested={duration_s:.3f}, actual={clip_info.duration_s:.3f}"
                )
            clip_hash = sha256_file(temporary)
            os.link(temporary, destination)
            destination_created = True
            temporary.unlink()
            if sha256_file(destination) != clip_hash:
                raise ClipExtractionError("candidate hash changed after finalization")
            try:
                with connection:
                    connection.execute(
                        """INSERT INTO candidates (
                            candidate_id, video_id, start_ms, end_ms, status,
                            transcript, algorithm_version, clip_path, file_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            candidate.candidate_id, candidate.video_id,
                            candidate.start_ms, candidate.end_ms, candidate.status.value,
                            candidate.transcript, candidate.algorithm_version,
                            destination.relative_to(root).as_posix(), clip_hash,
                        ),
                    )
                    if run_id is not None:
                        connection.execute(
                            """INSERT INTO candidate_run_links(run_id, candidate_id)
                               VALUES (?, ?)""",
                            (run_id, candidate_id),
                        )
            except Exception:
                if destination.exists():
                    destination.unlink()
                raise
            return ExtractedClip(candidate_id, destination, clip_hash, clip_info)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            if destination_created and destination.exists():
                destination.unlink()
            raise
