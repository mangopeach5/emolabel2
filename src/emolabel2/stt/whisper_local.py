from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
import math
from pathlib import Path
import sqlite3
from typing import Callable

from emolabel2.analytics.runtime import Stage, StageTimer
from emolabel2.intake.hashing import sha256_file
from emolabel2.paths import resolve_inside_project, resolve_manifest_input


WHISPER_PACKAGE_VERSION = "20250625"


class SttDependencyError(RuntimeError):
    pass


class SttModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class SttWord:
    start_ms: int
    end_ms: int
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class SttSegment:
    start_ms: int
    end_ms: int
    text: str
    words: tuple[SttWord, ...] = field(default_factory=tuple)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    uncertain: bool = False
    uncertainty_reason: str = ""


@dataclass(frozen=True)
class SttResult:
    segments: tuple[SttSegment, ...]
    language: str = "ko"


@dataclass(frozen=True)
class WhisperConfig:
    model_name: str = "base"
    model_path: str = "models/whisper/base.pt"
    device: str = "cpu"
    language: str = "ko"
    task: str = "transcribe"

    def __post_init__(self):
        if self.model_name not in ("tiny", "base", "small"):
            raise ValueError("model_name must be tiny, base, or small")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        if self.language != "ko" or self.task != "transcribe":
            raise ValueError("Korean transcription requires language=ko and task=transcribe")


def _finite_or_none(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_result(raw: dict) -> SttResult:
    segments: list[SttSegment] = []
    for item in raw.get("segments", []):
        words = tuple(
            SttWord(
                round(float(word["start"]) * 1000),
                round(float(word["end"]) * 1000),
                str(word.get("word", "")),
                _finite_or_none(word.get("probability")),
            )
            for word in item.get("words", [])
            if "start" in word and "end" in word
        )
        text = str(item.get("text", "")).strip()
        avg = _finite_or_none(item.get("avg_logprob"))
        no_speech = _finite_or_none(item.get("no_speech_prob"))
        compression = _finite_or_none(item.get("compression_ratio"))
        reasons = []
        if not text:
            reasons.append("empty_text")
        if avg is not None and avg < -1.0:
            reasons.append("low_logprob")
        if no_speech is not None and no_speech > 0.6:
            reasons.append("high_no_speech_probability")
        if compression is not None and compression > 2.4:
            reasons.append("high_compression_ratio")
        segments.append(SttSegment(
            round(float(item["start"]) * 1000),
            round(float(item["end"]) * 1000), text, words, avg, no_speech,
            compression, bool(reasons), ",".join(reasons),
        ))
    return SttResult(tuple(segments), str(raw.get("language", "ko")))


def transcribe_with_whisper(source: Path, project_root: Path, config: WhisperConfig) -> SttResult:
    try:
        import whisper
    except ImportError as exc:
        raise SttDependencyError(
            "openai-whisper==20250625 is not installed; no model was downloaded automatically"
        ) from exc
    try:
        version = metadata.version("openai-whisper")
    except metadata.PackageNotFoundError as exc:
        raise SttDependencyError("openai-whisper package metadata is missing") from exc
    if version != WHISPER_PACKAGE_VERSION:
        raise SttDependencyError(f"expected openai-whisper {WHISPER_PACKAGE_VERSION}, found {version}")
    model_path = resolve_inside_project(project_root, config.model_path, must_exist=True, require_file=True)
    model = whisper.load_model(str(model_path), device=config.device)
    raw = model.transcribe(
        str(source), language="ko", task="transcribe", word_timestamps=True,
        verbose=None, fp16=config.device == "cuda", condition_on_previous_text=False,
        hallucination_silence_threshold=1.0,
    )
    return _parse_result(raw)


def run_stt_analysis(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    run_id: str,
    video_id: str,
    config: WhisperConfig = WhisperConfig(),
    transcriber: Callable[[Path, Path, WhisperConfig], SttResult] | None = None,
) -> int:
    video = connection.execute("SELECT local_path FROM videos WHERE video_id=?", (video_id,)).fetchone()
    if video is None:
        raise LookupError(f"video not found: {video_id}")
    source = resolve_manifest_input(project_root, video["local_path"])
    model_path = "synthetic"
    model_hash = "synthetic"
    with StageTimer(
        connection, run_id, Stage.STT,
        algorithm_version="whisper-word-timestamps-v1",
        model_version=f"openai-whisper-{WHISPER_PACKAGE_VERSION}:{config.model_name}",
    ):
        if transcriber is None:
            resolved_model = resolve_inside_project(
                project_root, config.model_path, must_exist=True, require_file=True
            )
            model_path = resolved_model.relative_to(Path(project_root).resolve()).as_posix()
            model_hash = sha256_file(resolved_model)
            result = transcribe_with_whisper(source, project_root, config)
        else:
            result = transcriber(source, project_root, config)
    uncertain_reasons = []
    if result.language != "ko":
        uncertain_reasons.append("unexpected_language")
    if not result.segments:
        uncertain_reasons.append("no_segments")
    if any(item.uncertain for item in result.segments):
        uncertain_reasons.append("uncertain_segments")
    with connection:
        cursor = connection.execute(
            """INSERT INTO stt_analyses (
                run_id, video_id, engine_name, engine_version, model_name,
                model_path, model_hash, language, task, segment_count, word_count,
                uncertain, uncertainty_reason, created_at
            ) VALUES (?, ?, 'openai-whisper', ?, ?, ?, ?, 'ko', 'transcribe', ?, ?, ?, ?, ?)""",
            (
                run_id, video_id, WHISPER_PACKAGE_VERSION, config.model_name,
                model_path, model_hash, len(result.segments),
                sum(len(item.words) for item in result.segments), int(bool(uncertain_reasons)),
                ",".join(uncertain_reasons), datetime.now(timezone.utc).isoformat(),
            ),
        )
        analysis_id = int(cursor.lastrowid)
        segment_ids = []
        for index, segment in enumerate(result.segments):
            segment_cursor = connection.execute(
                """INSERT INTO stt_segments (
                    stt_analysis_id, sequence_no, start_ms, end_ms, text,
                    avg_logprob, no_speech_prob, compression_ratio, uncertain, uncertainty_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    analysis_id, index, segment.start_ms, segment.end_ms, segment.text,
                    segment.avg_logprob, segment.no_speech_prob, segment.compression_ratio,
                    int(segment.uncertain), segment.uncertainty_reason,
                ),
            )
            segment_id = int(segment_cursor.lastrowid)
            segment_ids.append((segment_id, segment))
            connection.executemany(
                """INSERT INTO stt_words (
                    stt_segment_id, sequence_no, start_ms, end_ms, text, probability
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (segment_id, word_index, word.start_ms, word.end_ms, word.text, word.probability)
                    for word_index, word in enumerate(segment.words)
                ],
            )
        vad_rows = connection.execute(
            """SELECT s.segment_id, s.start_ms, s.end_ms
               FROM vad_segments s JOIN vad_analyses a ON a.analysis_id=s.analysis_id
               WHERE a.run_id=? AND s.segment_kind='raw'""", (run_id,)
        ).fetchall()
        for vad in vad_rows:
            for segment_id, segment in segment_ids:
                overlap = max(0, min(vad["end_ms"], segment.end_ms) - max(vad["start_ms"], segment.start_ms))
                if overlap == 0:
                    continue
                connection.execute(
                    """INSERT INTO stt_vad_alignments (
                        stt_analysis_id, vad_segment_id, stt_segment_id, overlap_ms,
                        vad_coverage, stt_coverage, start_delta_ms, end_delta_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        analysis_id, vad["segment_id"], segment_id, overlap,
                        overlap / (vad["end_ms"] - vad["start_ms"]),
                        overlap / max(1, segment.end_ms - segment.start_ms),
                        segment.start_ms - vad["start_ms"], segment.end_ms - vad["end_ms"],
                    ),
                )
    return analysis_id
