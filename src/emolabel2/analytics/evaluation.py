"""Classification metrics computed only from supplied human gold labels."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from emolabel2.domain import Emotion


LABELS = tuple(emotion.value for emotion in Emotion)


@dataclass(frozen=True)
class ClassMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class ClassificationReport:
    labels: tuple[str, ...]
    confusion_matrix: tuple[tuple[int, ...], ...]
    per_class: dict[str, ClassMetrics]
    macro_f1: float
    evaluated: int
    abstained: int

    def as_dict(self) -> dict:
        result = asdict(self)
        result["labels"] = list(self.labels)
        result["confusion_matrix"] = [list(row) for row in self.confusion_matrix]
        return result


def classification_report(pairs: list[tuple[str, str | None]]) -> ClassificationReport | None:
    """Return None when no gold labels exist; never invent evaluation values."""
    if not pairs:
        return None
    index = {label: position for position, label in enumerate(LABELS)}
    matrix = [[0 for _ in LABELS] for _ in LABELS]
    abstained = 0
    for gold, predicted in pairs:
        if gold not in index:
            raise ValueError(f"invalid gold label: {gold}")
        if predicted is None:
            abstained += 1
            continue
        if predicted not in index:
            raise ValueError(f"invalid predicted label: {predicted}")
        matrix[index[gold]][index[predicted]] += 1

    per_class: dict[str, ClassMetrics] = {}
    for label, position in index.items():
        tp = matrix[position][position]
        fp = sum(matrix[row][position] for row in range(len(LABELS)) if row != position)
        fn = sum(matrix[position][column] for column in range(len(LABELS)) if column != position)
        # Abstentions for this gold class are false negatives.
        fn += sum(1 for gold, predicted in pairs if gold == label and predicted is None)
        support = sum(1 for gold, _ in pairs if gold == label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = ClassMetrics(precision, recall, f1, support)

    macro_f1 = sum(value.f1 for value in per_class.values()) / len(LABELS)
    return ClassificationReport(
        LABELS, tuple(tuple(row) for row in matrix), per_class,
        macro_f1, len(pairs), abstained,
    )
