from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from emolabel2.scene.policy import ScenePolicyMode, SceneTransition


SCENEDETECT_VERSION = "0.7.3"


class SceneDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneConfig:
    mode: ScenePolicyMode = ScenePolicyMode.STRICT
    adaptive_threshold: float = 3.0
    content_val: float = 15.0
    fade_threshold: float = 12.0
    min_scene_len_frames: int = 15

    def __post_init__(self):
        if not isinstance(self.mode, ScenePolicyMode):
            raise ValueError("mode must be ScenePolicyMode")
        for name in ("adaptive_threshold", "content_val", "fade_threshold"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if isinstance(self.min_scene_len_frames, bool) or not isinstance(self.min_scene_len_frames, int) or self.min_scene_len_frames < 1:
            raise ValueError("min_scene_len_frames must be a positive integer")

    def to_json(self) -> str:
        values = asdict(self)
        values["mode"] = self.mode.value
        return json.dumps(values, sort_keys=True)


def _internal_boundaries(scene_list, transition_type: str, detector_name: str):
    return [
        SceneTransition(round(scene[0].seconds * 1000), transition_type, detector_name)
        for scene in scene_list[1:]
    ]


def detect_scene_transitions(source: Path, config: SceneConfig) -> list[SceneTransition]:
    try:
        from scenedetect import AdaptiveDetector, ThresholdDetector, detect
    except ImportError as exc:
        raise SceneDependencyError(
            "scenedetect-headless==0.7.3 is not installed in the active environment"
        ) from exc
    hard_scenes = detect(
        str(source),
        AdaptiveDetector(
            adaptive_threshold=config.adaptive_threshold,
            min_scene_len=config.min_scene_len_frames,
            min_content_val=config.content_val,
        ),
        show_progress=False,
    )
    fade_scenes = detect(
        str(source),
        ThresholdDetector(
            threshold=config.fade_threshold,
            min_scene_len=config.min_scene_len_frames,
            # A video may legitimately end after fading to black.  Without this,
            # ThresholdDetector only emits a cut after a subsequent fade-in.
            add_final_scene=True,
        ),
        show_progress=False,
    )
    return (
        _internal_boundaries(hard_scenes, "hard_cut", "AdaptiveDetector")
        + _internal_boundaries(fade_scenes, "fade", "ThresholdDetector")
    )
