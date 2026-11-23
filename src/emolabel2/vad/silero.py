"""Offline Silero VAD adapter; model weights are loaded from the installed wheel."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import subprocess
import wave
from uuid import uuid4

from emolabel2.analytics.runtime import Stage, StageTimer
from emolabel2.media.probe import find_media_tool
from emolabel2.paths import resolve_inside_project
from emolabel2.vad.segments import SpeechSegment


SILERO_VERSION = "6.2.3"


class VadDependencyError(RuntimeError):
    pass


class VadProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class VadConfig:
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100
    speech_pad_ms: int = 30
    merge_gap_s: float = 0.8

    def __post_init__(self):
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be between 0 and 1")
        for name in ("min_speech_duration_ms", "min_silence_duration_ms", "speech_pad_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not math.isfinite(self.merge_gap_s) or self.merge_gap_s < 0:
            raise ValueError("merge_gap_s must be finite and nonnegative")

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def convert_to_pcm16k_mono(source: Path, destination: Path, *, ffmpeg: str | None = None) -> None:
    executable = ffmpeg or find_media_tool("ffmpeg")
    command = [
        executable, "-v", "error", "-n", "-i", str(source), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=300)
    if completed.returncode != 0:
        raise VadProcessingError(f"FFmpeg audio conversion failed: {completed.stderr.strip()[-1000:]}")


def _load_audio_tensor(wav_path: Path):
    try:
        import torch
    except ImportError as exc:
        raise VadDependencyError("torch is not installed in the active environment") from exc
    with wave.open(str(wav_path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
            raise VadProcessingError("PCM input must be 16 kHz mono signed 16-bit")
        frames = bytearray(stream.readframes(stream.getnframes()))
    return torch.frombuffer(frames, dtype=torch.int16).to(torch.float32) / 32768.0


def detect_speech(
    connection,
    project_root: Path,
    source: Path,
    *,
    run_id: str,
    config: VadConfig = VadConfig(),
    ffmpeg: str | None = None,
) -> list[SpeechSegment]:
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad
    except ImportError as exc:
        raise VadDependencyError("silero-vad==6.2.3 is not installed") from exc
    root = Path(project_root).resolve(strict=True)
    work = resolve_inside_project(root, "data/work", must_exist=True)
    temporary = work / f"vad-{uuid4().hex}.wav"
    with StageTimer(
        connection, run_id, Stage.VAD,
        algorithm_version="silero-get-speech-timestamps-v1",
        model_version=f"silero-vad-{SILERO_VERSION}",
    ):
        try:
            convert_to_pcm16k_mono(source, temporary, ffmpeg=ffmpeg)
            audio = _load_audio_tensor(temporary)
            model = load_silero_vad()
            timestamps = get_speech_timestamps(
                audio, model, sampling_rate=16000, threshold=config.threshold,
                min_speech_duration_ms=config.min_speech_duration_ms,
                min_silence_duration_ms=config.min_silence_duration_ms,
                speech_pad_ms=config.speech_pad_ms, return_seconds=True,
            )
            return [
                SpeechSegment(round(float(item["start"]) * 1000), round(float(item["end"]) * 1000))
                for item in timestamps
            ]
        finally:
            if temporary.exists():
                temporary.unlink()
