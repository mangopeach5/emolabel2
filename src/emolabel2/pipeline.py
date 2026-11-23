from __future__ import annotations

from pathlib import Path
import sqlite3
from uuid import uuid4

from emolabel2.analytics.runtime import RunTimer
from emolabel2.emotion.ollama import prelabel_candidates
from emolabel2.scene.detect import SceneConfig
from emolabel2.scene.policy import ScenePolicyMode
from emolabel2.selector.smart import select_candidates
from emolabel2.stt.whisper_local import WhisperConfig, run_stt_analysis
from emolabel2.vad.pipeline import run_vad_pipeline
from emolabel2.vad.segments import ShortSpeechPaddingConfig


def run_automatic_pipeline(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    video_id: str,
    run_id: str | None = None,
    whisper_model: str = "base",
    whisper_model_path: str = "models/whisper/base.pt",
    device: str = "cpu",
    scene_policy: str = "strict",
    ollama_model: str = "qwen2.5:7b",
    skip_emotion: bool = False,
) -> dict:
    run_id = run_id or f"auto-{uuid4().hex[:12]}"
    with RunTimer(
        connection, run_id=run_id, video_id=video_id,
        test_case_id=f"automatic:{video_id}", workflow_mode="automated",
        algorithm_version="emolabel2-mvp-v1",
        model_version=f"whisper-{whisper_model};ollama-{ollama_model}",
    ):
        vad_analysis_id = run_vad_pipeline(
            connection, project_root, run_id=run_id, video_id=video_id,
            scene_config=SceneConfig(mode=ScenePolicyMode(scene_policy)),
            short_padding_config=ShortSpeechPaddingConfig(),
        )
        stt_analysis_id = run_stt_analysis(
            connection, project_root, run_id=run_id, video_id=video_id,
            config=WhisperConfig(
                model_name=whisper_model, model_path=whisper_model_path, device=device,
            ),
        )
        selected = select_candidates(connection, run_id=run_id, stt_analysis_id=stt_analysis_id)
        prediction_ids = [] if skip_emotion else prelabel_candidates(
            connection, run_id=run_id, candidate_ids=selected, model=ollama_model,
        )
    counts = {
        row["decision_status"]: row["n"]
        for row in connection.execute(
            """SELECT decision_status, count(*) AS n FROM clip_selection_decisions
               WHERE run_id=? GROUP BY decision_status""", (run_id,)
        )
    }
    return {
        "run_id": run_id, "video_id": video_id,
        "vad_analysis_id": vad_analysis_id, "stt_analysis_id": stt_analysis_id,
        "selected_candidate_ids": selected, "selection_counts": counts,
        "prediction_ids": prediction_ids, "emotion_skipped": skip_emotion,
    }
