from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from emolabel2.vad.segments import CandidateProposal, SpeechSegment


class ScenePolicyMode(StrEnum):
    STRICT = "strict"
    WARN = "warn"


@dataclass(frozen=True)
class SceneTransition:
    time_ms: int
    transition_type: str
    detector_name: str

    def __post_init__(self):
        if self.time_ms < 0:
            raise ValueError("transition time must be nonnegative")
        if self.transition_type not in ("hard_cut", "fade"):
            raise ValueError("unsupported transition type")
        if not self.detector_name.strip():
            raise ValueError("detector_name is required")


@dataclass(frozen=True)
class SceneDecision:
    start_ms: int
    end_ms: int
    status: str
    reason: str = ""
    transition_count: int = 0


def _overlaps_speech(start_ms: int, end_ms: int, speech: list[SpeechSegment]) -> bool:
    return any(max(start_ms, item.start_ms) < min(end_ms, item.end_ms) for item in speech)


def evaluate_scene_policy(
    proposal: CandidateProposal,
    speech: list[SpeechSegment],
    transitions: list[SceneTransition],
    *,
    mode: ScenePolicyMode,
    min_s: float = 3,
    max_s: float = 12,
) -> list[SceneDecision]:
    if not isinstance(mode, ScenePolicyMode):
        raise ValueError("mode must be ScenePolicyMode")
    if not all(math.isfinite(value) for value in (min_s, max_s)) or min_s <= 0 or max_s < min_s:
        raise ValueError("invalid duration limits")
    if not proposal.accepted:
        return [SceneDecision(
            proposal.start_ms, proposal.end_ms, "rejected", proposal.reject_reason, 0
        )]
    inside = sorted({
        item.time_ms for item in transitions
        if proposal.start_ms < item.time_ms < proposal.end_ms
    })
    if not inside:
        return [SceneDecision(proposal.start_ms, proposal.end_ms, "accepted")]
    inside_speech = any(
        segment.start_ms < point < segment.end_ms
        for point in inside for segment in speech
    )
    if mode is ScenePolicyMode.WARN:
        reason = "scene_transition_inside_speech" if inside_speech else "scene_transition_inside_candidate"
        return [SceneDecision(
            proposal.start_ms, proposal.end_ms, "warning", reason, len(inside)
        )]
    if inside_speech:
        return [SceneDecision(
            proposal.start_ms, proposal.end_ms, "rejected",
            "scene_cut_inside_speech", len(inside),
        )]

    min_ms, max_ms = round(min_s * 1000), round(max_s * 1000)
    boundaries = [proposal.start_ms, *inside, proposal.end_ms]
    decisions: list[SceneDecision] = []
    for start_ms, end_ms in zip(boundaries, boundaries[1:]):
        duration = end_ms - start_ms
        if not _overlaps_speech(start_ms, end_ms, speech):
            status, reason = "rejected", "scene_adjusted_no_speech"
        elif duration < min_ms:
            status, reason = "rejected", "scene_adjusted_too_short"
        elif duration > max_ms:
            status, reason = "rejected", "scene_adjusted_too_long"
        else:
            status, reason = "accepted", "scene_split_at_silence"
        decisions.append(SceneDecision(start_ms, end_ms, status, reason, len(inside)))
    return decisions
