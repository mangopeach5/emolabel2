"""Core records and state rules for the local EmoLabel 2.0 pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Emotion(StrEnum):
    NEUTRAL = "neutral"
    ANGRY = "angry"
    SAD = "sad"
    HAPPY = "happy"


class RightsStatus(StrEnum):
    PENDING = "pending"
    APPROVED_RESEARCH = "approved_research"
    RESTRICTED = "restricted"
    EXPIRED = "expired"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class CandidateStatus(StrEnum):
    GENERATED = "generated"
    PRELABELLED = "prelabelled"
    REVIEWED = "reviewed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_nonnegative_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    local_path: str
    file_hash: str
    source_name: str
    rights_status: RightsStatus
    rights_basis: str
    allowed_use: str
    duration_s: float
    added_at: str
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rights_status, RightsStatus):
            raise ValueError("rights_status must be a RightsStatus")
        for name in ("video_id", "local_path", "source_name", "rights_basis", "allowed_use", "added_at"):
            _require_text(name, getattr(self, name))
        if not _SHA256_RE.fullmatch(self.file_hash):
            raise ValueError("file_hash must be a lowercase SHA-256 hex digest")
        _require_nonnegative_finite("duration_s", self.duration_s)
        if self.duration_s == 0:
            raise ValueError("duration_s must be greater than zero")


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    video_id: str
    start_ms: int
    end_ms: int
    status: CandidateStatus = CandidateStatus.GENERATED
    transcript: str = ""
    algorithm_version: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, CandidateStatus):
            raise ValueError("status must be a CandidateStatus")
        _require_text("candidate_id", self.candidate_id)
        _require_text("video_id", self.video_id)
        if isinstance(self.start_ms, bool) or not isinstance(self.start_ms, int) or self.start_ms < 0:
            raise ValueError("start_ms must be a nonnegative integer")
        if isinstance(self.end_ms, bool) or not isinstance(self.end_ms, int) or self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be an integer greater than start_ms")
        duration_ms = self.end_ms - self.start_ms
        if not 3_000 <= duration_ms <= 12_000:
            raise ValueError("candidate duration must be between 3 and 12 seconds")


@dataclass(frozen=True)
class PredictionRecord:
    candidate_id: str
    emotion: Emotion | None
    model_name: str
    prompt_version: str
    elapsed_s: float
    needs_review: bool = True

    def __post_init__(self) -> None:
        if self.emotion is not None and not isinstance(self.emotion, Emotion):
            raise ValueError("emotion must be an Emotion or None")
        if not isinstance(self.needs_review, bool):
            raise ValueError("needs_review must be a boolean")
        _require_text("candidate_id", self.candidate_id)
        _require_text("model_name", self.model_name)
        _require_text("prompt_version", self.prompt_version)
        _require_nonnegative_finite("elapsed_s", self.elapsed_s)
        if self.emotion is None and not self.needs_review:
            raise ValueError("a null prediction must require review")


@dataclass(frozen=True)
class ReviewRecord:
    candidate_id: str
    status: ReviewStatus
    gold_emotion: Emotion | None
    human_time_s: float
    reviewed_at: str
    reject_reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReviewStatus):
            raise ValueError("status must be a ReviewStatus")
        if self.gold_emotion is not None and not isinstance(self.gold_emotion, Emotion):
            raise ValueError("gold_emotion must be an Emotion or None")
        _require_text("candidate_id", self.candidate_id)
        _require_text("reviewed_at", self.reviewed_at)
        _require_nonnegative_finite("human_time_s", self.human_time_s)
        if self.status is ReviewStatus.ACCEPTED and self.gold_emotion is None:
            raise ValueError("accepted reviews require a human gold emotion")
        if self.status is not ReviewStatus.ACCEPTED and self.gold_emotion is not None:
            raise ValueError("only accepted reviews may contain a gold emotion")
        if self.status is ReviewStatus.REJECTED and not self.reject_reason.strip():
            raise ValueError("rejected reviews require a reason")
