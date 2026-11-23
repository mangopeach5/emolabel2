"""Dependency-free VAD segment merging and 3-12 second proposal generation."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SpeechSegment:
    start_ms: int
    end_ms: int

    def __post_init__(self):
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("invalid speech segment")


@dataclass(frozen=True)
class CandidateProposal:
    start_ms: int
    end_ms: int
    accepted: bool
    reject_reason: str = ""


@dataclass(frozen=True)
class ShortSpeechPaddingConfig:
    target_duration_ms: int = 3200
    max_total_padding_ms: int = 800
    min_speech_ratio: float = 0.7

    def __post_init__(self):
        if not 3000 <= self.target_duration_ms <= 12000:
            raise ValueError("target_duration_ms must be between 3000 and 12000")
        if self.max_total_padding_ms < 0:
            raise ValueError("max_total_padding_ms must be nonnegative")
        if not math.isfinite(self.min_speech_ratio) or not 0 < self.min_speech_ratio <= 1:
            raise ValueError("min_speech_ratio must be in (0, 1]")


@dataclass(frozen=True)
class PaddingDecision:
    accepted: bool
    start_ms: int
    end_ms: int
    reason: str
    pad_before_ms: int
    pad_after_ms: int
    speech_ratio: float


def expand_short_speech(
    segment: SpeechSegment,
    all_speech: list[SpeechSegment],
    *,
    video_end_ms: int,
    config: ShortSpeechPaddingConfig = ShortSpeechPaddingConfig(),
) -> PaddingDecision:
    duration = segment.end_ms - segment.start_ms
    if duration >= 3000:
        return PaddingDecision(True, segment.start_ms, segment.end_ms, "not_short", 0, 0, 1.0)
    needed = config.target_duration_ms - duration
    if needed <= 0:
        needed = 3000 - duration
    if needed > config.max_total_padding_ms:
        return PaddingDecision(False, segment.start_ms, segment.end_ms, "padding_limit_exceeded", 0, 0, 1.0)
    before = needed // 2
    after = needed - before
    start = max(0, segment.start_ms - before)
    end = min(video_end_ms, segment.end_ms + after)
    missing = config.target_duration_ms - (end - start)
    if missing > 0:
        grow_before = min(missing, start)
        start -= grow_before
        missing -= grow_before
    if missing > 0:
        grow_after = min(missing, video_end_ms - end)
        end += grow_after
        missing -= grow_after
    actual_duration = end - start
    ratio = duration / actual_duration if actual_duration else 0.0
    if actual_duration < 3000:
        return PaddingDecision(False, start, end, "video_boundary_prevents_minimum", segment.start_ms - start, end - segment.end_ms, ratio)
    if ratio < config.min_speech_ratio:
        return PaddingDecision(False, start, end, "speech_ratio_too_low", segment.start_ms - start, end - segment.end_ms, ratio)
    for other in all_speech:
        if other == segment:
            continue
        if max(start, other.start_ms) < min(end, other.end_ms):
            return PaddingDecision(False, start, end, "padding_overlaps_other_speech", segment.start_ms - start, end - segment.end_ms, ratio)
    return PaddingDecision(
        True, start, end, "short_speech_padded",
        segment.start_ms - start, end - segment.end_ms, ratio,
    )


def merge_segments(segments: list[SpeechSegment], *, max_gap_s: float) -> list[SpeechSegment]:
    if not math.isfinite(max_gap_s) or max_gap_s < 0:
        raise ValueError("max_gap_s must be finite and nonnegative")
    gap_ms = round(max_gap_s * 1000)
    ordered = sorted(segments, key=lambda item: (item.start_ms, item.end_ms))
    merged: list[SpeechSegment] = []
    for segment in ordered:
        if not merged or segment.start_ms - merged[-1].end_ms > gap_ms:
            merged.append(segment)
        else:
            previous = merged[-1]
            merged[-1] = SpeechSegment(previous.start_ms, max(previous.end_ms, segment.end_ms))
    return merged


def build_proposals(
    segments: list[SpeechSegment], *, min_s: float = 3, max_s: float = 12
) -> list[CandidateProposal]:
    min_ms, max_ms = round(min_s * 1000), round(max_s * 1000)
    if min_ms <= 0 or max_ms < min_ms:
        raise ValueError("invalid candidate duration limits")
    proposals: list[CandidateProposal] = []
    for segment in segments:
        duration = segment.end_ms - segment.start_ms
        if duration < min_ms:
            proposals.append(CandidateProposal(segment.start_ms, segment.end_ms, False, "speech_too_short"))
            continue
        cursor = segment.start_ms
        while segment.end_ms - cursor > max_ms:
            proposals.append(CandidateProposal(cursor, cursor + max_ms, True, "long_speech_split"))
            cursor += max_ms
        remainder = segment.end_ms - cursor
        if remainder >= min_ms:
            proposals.append(CandidateProposal(cursor, segment.end_ms, True))
        elif remainder:
            proposals.append(CandidateProposal(cursor, segment.end_ms, False, "split_remainder_too_short"))
    return proposals
