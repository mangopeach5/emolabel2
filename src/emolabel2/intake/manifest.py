"""CSV manifest parsing and validation for approved local videos."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path

from emolabel2.domain import RightsStatus
from emolabel2.paths import resolve_inside_project, resolve_manifest_input


REQUIRED_COLUMNS = (
    "video_id",
    "local_path",
    "source_name",
    "rights_status",
    "rights_basis",
    "allowed_use",
    "duration_s",
    "notes",
)


class ManifestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestRow:
    video_id: str
    local_path: str
    source_name: str
    rights_status: RightsStatus
    rights_basis: str
    allowed_use: str
    duration_s: float
    notes: str = ""


def _required_text(row: dict[str, str], name: str, row_number: int) -> str:
    value = (row.get(name) or "").strip()
    if not value:
        raise ManifestValidationError(f"row {row_number}: {name} is required")
    return value


def validate_manifest_row(row: dict[str, str], project_root: Path, row_number: int) -> ManifestRow:
    video_id = _required_text(row, "video_id", row_number)
    local_path = _required_text(row, "local_path", row_number)
    source_name = _required_text(row, "source_name", row_number)
    rights_basis = _required_text(row, "rights_basis", row_number)
    allowed_use = _required_text(row, "allowed_use", row_number)
    status_text = _required_text(row, "rights_status", row_number)
    try:
        rights_status = RightsStatus(status_text)
    except ValueError as exc:
        raise ManifestValidationError(f"row {row_number}: invalid rights_status {status_text!r}") from exc
    if rights_status is not RightsStatus.APPROVED_RESEARCH:
        raise ManifestValidationError(f"row {row_number}: rights_status must be approved_research")
    try:
        duration_s = float(_required_text(row, "duration_s", row_number))
    except ValueError as exc:
        raise ManifestValidationError(f"row {row_number}: duration_s must be numeric") from exc
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ManifestValidationError(f"row {row_number}: duration_s must be finite and positive")
    resolve_manifest_input(project_root, local_path)
    return ManifestRow(
        video_id=video_id,
        local_path=local_path,
        source_name=source_name,
        rights_status=rights_status,
        rights_basis=rights_basis,
        allowed_use=allowed_use,
        duration_s=duration_s,
        notes=(row.get("notes") or "").strip(),
    )


def read_video_manifest(path: Path, project_root: Path) -> list[ManifestRow]:
    manifest_path = resolve_inside_project(project_root, path, must_exist=True, require_file=True)
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ManifestValidationError("manifest has no header")
        missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise ManifestValidationError(f"manifest is missing columns: {', '.join(missing)}")
        rows = [validate_manifest_row(row, project_root, number) for number, row in enumerate(reader, 2)]
    seen: set[str] = set()
    for row in rows:
        if row.video_id in seen:
            raise ManifestValidationError(f"duplicate video_id: {row.video_id}")
        seen.add(row.video_id)
    return rows
