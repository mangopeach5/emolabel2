"""Small deterministic metric helpers (NOT a finished analytics engine)."""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RunMetrics:
    media_duration_s: float
    processing_s: float
    candidates: int = 0
    reviewed: int = 0
    accepted: int = 0
    manual_review_s: float = 0.0

    def check(self) -> None:
        for field in ("media_duration_s", "processing_s", "manual_review_s"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid metric: {field}")
        for field in ("candidates", "reviewed", "accepted"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Invalid count: {field}")
        if self.accepted > self.reviewed or self.reviewed > self.candidates:
            raise ValueError("accepted <= reviewed <= candidates must hold")

    @property
    def rtf(self) -> float | None:
        self.check()
        return self.processing_s / self.media_duration_s if self.media_duration_s else None

    @property
    def accepted_per_reviewed(self) -> float | None:
        self.check()
        return self.accepted / self.reviewed if self.reviewed else None

    @property
    def human_s_per_gold(self) -> float | None:
        self.check()
        return self.manual_review_s / self.accepted if self.accepted else None


def labor_time_reduction(baseline_s: float, new_s: float) -> float | None:
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in (baseline_s, new_s)):
        raise ValueError("Times must be finite and nonnegative")
    return (baseline_s - new_s) / baseline_s if baseline_s else None
