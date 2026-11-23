"""Persist Silero VAD evidence and create Phase 2A clips from accepted proposals."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Callable

from emolabel2.analytics.runtime import Stage, StageTimer, link_candidate
from emolabel2.media.extraction import DuplicateClipError, extract_registered_clip
from emolabel2.paths import resolve_manifest_input
from emolabel2.scene.detect import SCENEDETECT_VERSION, SceneConfig, detect_scene_transitions
from emolabel2.scene.policy import SceneDecision, SceneTransition, evaluate_scene_policy
from emolabel2.vad.segments import (
    CandidateProposal, ShortSpeechPaddingConfig, SpeechSegment,
    build_proposals, expand_short_speech, merge_segments,
)
from emolabel2.vad.silero import SILERO_VERSION, VadConfig, detect_speech


def _candidate_id(video_id: str, proposal: CandidateProposal) -> str:
    token = f"{video_id}:{proposal.start_ms}:{proposal.end_ms}".encode()
    return f"vad-{sha256(token).hexdigest()[:20]}"


def run_vad_pipeline(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    run_id: str,
    video_id: str,
    config: VadConfig = VadConfig(),
    detector: Callable[[Path, VadConfig], list[SpeechSegment]] | None = None,
    scene_config: SceneConfig | None = None,
    scene_detector: Callable[[Path, SceneConfig], list[SceneTransition]] | None = None,
    short_padding_config: ShortSpeechPaddingConfig | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> int:
    video = connection.execute(
        "SELECT local_path FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    if video is None:
        raise LookupError(f"video not found: {video_id}")
    source = resolve_manifest_input(project_root, video["local_path"])
    if detector is None:
        raw = detect_speech(
            connection, project_root, source, run_id=run_id, config=config, ffmpeg=ffmpeg
        )
        model_version = SILERO_VERSION
    else:
        with StageTimer(
            connection, run_id, Stage.VAD,
            algorithm_version="injected-test-detector", model_version="synthetic",
        ):
            raw = detector(source, config)
        model_version = "synthetic"
    merged = merge_segments(raw, max_gap_s=config.merge_gap_s)
    proposals = build_proposals(merged)

    with connection:
        cursor = connection.execute(
            """INSERT INTO vad_analyses (
                run_id, video_id, model_name, model_version, config_json,
                raw_segment_count, merged_segment_count, candidate_count, created_at
            ) VALUES (?, ?, 'silero-vad', ?, ?, ?, ?, 0, ?)""",
            (
                run_id, video_id, model_version, config.to_json(), len(raw), len(merged),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        analysis_id = int(cursor.lastrowid)
        for kind, values in (("raw", raw), ("merged", merged)):
            connection.executemany(
                """INSERT INTO vad_segments (
                    analysis_id, segment_kind, sequence_no, start_ms, end_ms
                ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (analysis_id, kind, index, segment.start_ms, segment.end_ms)
                    for index, segment in enumerate(values)
                ],
            )

    proposal_rows: list[tuple[int, CandidateProposal]] = []
    with connection:
        for index, proposal in enumerate(proposals):
            cursor = connection.execute(
                """INSERT INTO vad_proposals (
                    analysis_id, sequence_no, start_ms, end_ms,
                    accepted, reject_reason, candidate_id
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (
                    analysis_id, index, proposal.start_ms, proposal.end_ms,
                    int(proposal.accepted), proposal.reject_reason,
                ),
            )
            proposal_rows.append((int(cursor.lastrowid), proposal))

    if scene_config is not None:
        effective_rows = proposal_rows
        if short_padding_config is not None:
            effective_rows = []
            video_end_ms = round(float(connection.execute(
                "SELECT duration_s FROM videos WHERE video_id=?", (video_id,)
            ).fetchone()[0]) * 1000)
            for proposal_id, proposal in proposal_rows:
                if proposal.accepted:
                    effective_rows.append((proposal_id, proposal))
                    continue
                if proposal.reject_reason != "speech_too_short":
                    continue
                original = SpeechSegment(proposal.start_ms, proposal.end_ms)
                padding = expand_short_speech(
                    original, merged, video_end_ms=video_end_ms,
                    config=short_padding_config,
                )
                with connection:
                    connection.execute(
                        """INSERT INTO vad_padding_decisions (
                            vad_proposal_id, original_start_ms, original_end_ms,
                            padded_start_ms, padded_end_ms, accepted, reason,
                            pad_before_ms, pad_after_ms, speech_ratio, candidate_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                        (
                            proposal_id, proposal.start_ms, proposal.end_ms,
                            padding.start_ms, padding.end_ms, int(padding.accepted),
                            padding.reason, padding.pad_before_ms, padding.pad_after_ms,
                            padding.speech_ratio,
                        ),
                    )
                if padding.accepted:
                    effective_rows.append((proposal_id, CandidateProposal(
                        padding.start_ms, padding.end_ms, True, padding.reason,
                    )))
        return _run_scene_aware_extraction(
            connection, project_root, run_id=run_id, video_id=video_id,
            source=source, vad_analysis_id=analysis_id, raw_speech=raw,
            proposal_rows=effective_rows, config=scene_config,
            detector=scene_detector, ffmpeg=ffmpeg, ffprobe=ffprobe,
        )

    created = 0
    for proposal_id, proposal in proposal_rows:
        candidate_id = None
        accepted = proposal.accepted
        reason = proposal.reject_reason
        if accepted:
            candidate_id = _candidate_id(video_id, proposal)
            try:
                extract_registered_clip(
                    connection, project_root, video_id=video_id, candidate_id=candidate_id,
                    start_s=proposal.start_ms / 1000, end_s=proposal.end_ms / 1000,
                    run_id=run_id, ffmpeg=ffmpeg, ffprobe=ffprobe,
                )
                created += 1
            except DuplicateClipError:
                existing = connection.execute(
                    """SELECT candidate_id FROM candidates
                       WHERE video_id=? AND start_ms=? AND end_ms=?""",
                    (video_id, proposal.start_ms, proposal.end_ms),
                ).fetchone()
                candidate_id = existing["candidate_id"]
                link_candidate(connection, run_id, candidate_id)
                created += 1
            except Exception as exc:
                accepted = False
                candidate_id = None
                reason = f"extraction_failed:{type(exc).__name__}"
        with connection:
            connection.execute(
                """UPDATE vad_proposals
                   SET accepted=?, reject_reason=?, candidate_id=? WHERE proposal_id=?""",
                (int(accepted), reason, candidate_id, proposal_id),
            )
    with connection:
        connection.execute(
            "UPDATE vad_analyses SET candidate_count=? WHERE analysis_id=?",
            (created, analysis_id),
        )
    return analysis_id


def _extract_scene_decision(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    run_id: str,
    video_id: str,
    decision: SceneDecision,
    ffmpeg: str | None,
    ffprobe: str | None,
) -> tuple[SceneDecision, str | None]:
    if decision.status == "rejected":
        return decision, None
    proposal = CandidateProposal(decision.start_ms, decision.end_ms, True, decision.reason)
    candidate_id = _candidate_id(video_id, proposal)
    try:
        extract_registered_clip(
            connection, project_root, video_id=video_id, candidate_id=candidate_id,
            start_s=decision.start_ms / 1000, end_s=decision.end_ms / 1000,
            run_id=run_id, ffmpeg=ffmpeg, ffprobe=ffprobe,
        )
    except DuplicateClipError:
        existing = connection.execute(
            """SELECT candidate_id FROM candidates
               WHERE video_id=? AND start_ms=? AND end_ms=?""",
            (video_id, decision.start_ms, decision.end_ms),
        ).fetchone()
        if existing is None:
            raise
        candidate_id = existing["candidate_id"]
        link_candidate(connection, run_id, candidate_id)
    except Exception as exc:
        return SceneDecision(
            decision.start_ms, decision.end_ms, "rejected",
            f"extraction_failed:{type(exc).__name__}", decision.transition_count,
        ), None
    return decision, candidate_id


def _run_scene_aware_extraction(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    run_id: str,
    video_id: str,
    source: Path,
    vad_analysis_id: int,
    raw_speech: list[SpeechSegment],
    proposal_rows: list[tuple[int, CandidateProposal]],
    config: SceneConfig,
    detector: Callable[[Path, SceneConfig], list[SceneTransition]] | None,
    ffmpeg: str | None,
    ffprobe: str | None,
) -> int:
    with StageTimer(
        connection, run_id, Stage.SCENE_DETECTION,
        algorithm_version="adaptive-plus-threshold-v1",
        model_version=f"pyscenedetect-{SCENEDETECT_VERSION}",
    ):
        transitions = (
            detector(source, config) if detector is not None
            else detect_scene_transitions(source, config)
        )
    hard_count = sum(item.transition_type == "hard_cut" for item in transitions)
    fade_count = sum(item.transition_type == "fade" for item in transitions)
    pre_count = sum(proposal.accepted for _, proposal in proposal_rows)
    with connection:
        cursor = connection.execute(
            """INSERT INTO scene_analyses (
                run_id, video_id, vad_analysis_id, library_version, policy_mode,
                config_json, hard_cut_count, fade_count, transition_count,
                pre_candidate_count, post_candidate_count, scene_rejected_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
            (
                run_id, video_id, vad_analysis_id, SCENEDETECT_VERSION,
                config.mode.value, config.to_json(), hard_count, fade_count,
                len(transitions), pre_count, datetime.now(timezone.utc).isoformat(),
            ),
        )
        scene_analysis_id = int(cursor.lastrowid)
        connection.executemany(
            """INSERT INTO scene_transitions (
                scene_analysis_id, sequence_no, time_ms, transition_type, detector_name
            ) VALUES (?, ?, ?, ?, ?)""",
            [
                (scene_analysis_id, index, item.time_ms, item.transition_type, item.detector_name)
                for index, item in enumerate(transitions)
            ],
        )

    post_count = 0
    scene_rejected = 0
    for proposal_id, proposal in proposal_rows:
        if not proposal.accepted:
            continue
        decisions = evaluate_scene_policy(
            proposal, raw_speech, transitions, mode=config.mode
        )
        rejected_by_scene = False
        for sequence_no, original_decision in enumerate(decisions):
            decision, candidate_id = _extract_scene_decision(
                connection, project_root, run_id=run_id, video_id=video_id,
                decision=original_decision, ffmpeg=ffmpeg, ffprobe=ffprobe,
            )
            if candidate_id is not None:
                post_count += 1
            if decision.status == "rejected" and decision.reason.startswith("scene_"):
                rejected_by_scene = True
            with connection:
                connection.execute(
                    """INSERT INTO scene_candidate_decisions (
                        scene_analysis_id, vad_proposal_id, sequence_no,
                        original_start_ms, original_end_ms,
                        adjusted_start_ms, adjusted_end_ms, decision_status,
                        reason, transition_count, candidate_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scene_analysis_id, proposal_id, sequence_no,
                        proposal.start_ms, proposal.end_ms,
                        decision.start_ms, decision.end_ms, decision.status,
                        decision.reason, decision.transition_count, candidate_id,
                    ),
                )
                if candidate_id is not None:
                    connection.execute(
                        """UPDATE vad_padding_decisions SET candidate_id=?
                           WHERE vad_proposal_id=?""",
                        (candidate_id, proposal_id),
                    )
        if rejected_by_scene:
            scene_rejected += 1
    with connection:
        connection.execute(
            """UPDATE scene_analyses
               SET post_candidate_count=?, scene_rejected_count=?
               WHERE scene_analysis_id=?""",
            (post_count, scene_rejected, scene_analysis_id),
        )
        connection.execute(
            "UPDATE vad_analyses SET candidate_count=? WHERE analysis_id=?",
            (post_count, vad_analysis_id),
        )
    return vad_analysis_id
