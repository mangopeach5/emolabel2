from __future__ import annotations


def _edit_distance(expected: str, predicted: str) -> int:
    previous = list(range(len(predicted) + 1))
    for row, expected_char in enumerate(expected, 1):
        current = [row]
        for column, predicted_char in enumerate(predicted, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected_char != predicted_char),
            ))
        previous = current
    return previous[-1]


def character_error_rate(expected: str | None, predicted: str) -> float | None:
    """Return CER only when a non-empty human reference exists."""
    if expected is None or not expected:
        return None
    return _edit_distance(expected, predicted) / len(expected)


def timestamp_mean_absolute_error(
    gold: list[tuple[int, int]] | None,
    predicted: list[tuple[int, int]],
) -> dict | None:
    """Compare paired human-verified word boundaries; never invent missing gold."""
    if gold is None or not gold or len(gold) != len(predicted):
        return None
    start_mae = sum(abs(a[0] - b[0]) for a, b in zip(gold, predicted)) / len(gold)
    end_mae = sum(abs(a[1] - b[1]) for a, b in zip(gold, predicted)) / len(gold)
    return {"start_mae_ms": start_mae, "end_mae_ms": end_mae}
