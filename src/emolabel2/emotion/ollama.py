from __future__ import annotations

import json
import re
import sqlite3
from time import perf_counter
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from emolabel2.analytics.runtime import Stage, StageTimer
from emolabel2.domain import Emotion, PredictionRecord
from emolabel2.storage import insert_prediction


PROMPT_VERSION = "ko-emotion-json-v1"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


class OllamaUnavailableError(RuntimeError):
    pass


def classify_with_ollama(text: str, model: str) -> tuple[Emotion | None, str]:
    if not _MODEL_RE.fullmatch(model):
        raise ValueError("unsafe Ollama model name")
    prompt = (
        "다음 한국어 대사의 화자 감정을 사전 분류하라. "
        "허용값은 neutral, angry, sad, happy, uncertain뿐이다. "
        "문맥이 부족하거나 애매하면 uncertain을 선택하라. "
        "JSON 객체 {\"emotion\":\"...\"}만 출력하라.\n대사: " + text
    )
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False, "format": "json",
        "options": {"temperature": 0},
    }).encode("utf-8")
    request = Request(
        "http://127.0.0.1:11434/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            outer = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OllamaUnavailableError(
            "local Ollama is unavailable at 127.0.0.1:11434"
        ) from exc
    try:
        parsed = json.loads(outer["response"])
        label = str(parsed["emotion"]).lower().strip()
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OllamaUnavailableError("Ollama returned invalid JSON") from exc
    if label == "uncertain":
        return None, model
    try:
        return Emotion(label), model
    except ValueError:
        return None, model


def prelabel_candidates(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    candidate_ids: list[str],
    model: str,
    classifier: Callable[[str, str], tuple[Emotion | None, str]] = classify_with_ollama,
) -> list[int]:
    prediction_ids = []
    for candidate_id in candidate_ids:
        row = connection.execute(
            "SELECT transcript FROM candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"candidate not found: {candidate_id}")
        started = perf_counter()
        with StageTimer(
            connection, run_id, Stage.LLM_PRELABEL,
            algorithm_version=PROMPT_VERSION, model_version=model,
        ):
            emotion, model_version = classifier(row["transcript"], model)
        prediction_ids.append(insert_prediction(
            connection,
            PredictionRecord(
                candidate_id, emotion, model_version, PROMPT_VERSION,
                perf_counter() - started, needs_review=True,
            ),
        ))
        with connection:
            connection.execute(
                "UPDATE candidates SET status='prelabelled' WHERE candidate_id=?",
                (candidate_id,),
            )
    return prediction_ids
