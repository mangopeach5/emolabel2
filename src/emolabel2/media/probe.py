"""Read-only media metadata inspection through local FFprobe."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess


class MediaProbeError(RuntimeError):
    pass


class MediaToolNotFoundError(MediaProbeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int
    height: int
    frame_rate: float
    has_audio: bool
    video_codec: str
    audio_codec: str | None


def find_media_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise MediaToolNotFoundError(f"required local executable not found: {name}")
    return executable


def _positive_finite(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise MediaProbeError(f"invalid {name}: {value}")
    return value


def _frame_rate(stream: dict) -> float:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    try:
        return _positive_finite("frame rate", float(Fraction(raw)))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise MediaProbeError(f"invalid frame rate: {raw!r}") from exc


def probe_media(path: Path, *, ffprobe: str | None = None, timeout_s: float = 60) -> MediaInfo:
    source = Path(path)
    if not source.is_file():
        raise MediaProbeError(f"media file does not exist: {source}")
    executable = ffprobe or find_media_tool("ffprobe")
    command = [
        executable, "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(source),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaProbeError(f"ffprobe execution failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-1000:]
        raise MediaProbeError(f"ffprobe rejected media: {detail or 'unknown error'}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") or []
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not video_streams:
        raise MediaProbeError("media has no video stream")
    video = video_streams[0]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    raw_duration = (payload.get("format") or {}).get("duration") or video.get("duration")
    try:
        duration = _positive_finite("duration", float(raw_duration))
        width = int(video.get("width"))
        height = int(video.get("height"))
    except (TypeError, ValueError) as exc:
        raise MediaProbeError("ffprobe returned incomplete dimensions or duration") from exc
    if width <= 0 or height <= 0:
        raise MediaProbeError("video dimensions must be positive")
    codec = video.get("codec_name")
    if not codec:
        raise MediaProbeError("video codec is missing")
    return MediaInfo(
        duration, width, height, _frame_rate(video), bool(audio_streams), str(codec),
        str(audio_streams[0].get("codec_name")) if audio_streams else None,
    )


def save_media_info(connection: sqlite3.Connection, video_id: str, info: MediaInfo, *, probed_at: str | None = None) -> None:
    with connection:
        connection.execute(
            """INSERT INTO media_info (
                video_id, duration_s, width, height, frame_rate, has_audio,
                video_codec, audio_codec, probed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                duration_s=excluded.duration_s, width=excluded.width, height=excluded.height,
                frame_rate=excluded.frame_rate, has_audio=excluded.has_audio,
                video_codec=excluded.video_codec, audio_codec=excluded.audio_codec,
                probed_at=excluded.probed_at""",
            (
                video_id, info.duration_s, info.width, info.height, info.frame_rate,
                int(info.has_audio), info.video_codec, info.audio_codec,
                probed_at or datetime.now(timezone.utc).isoformat(),
            ),
        )
