"""Path containment checks that keep all work inside one project root."""
from __future__ import annotations

import os
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a path escapes the project or uses a symbolic link."""


class PathOutsideProjectError(UnsafePathError):
    """Raised when a path is lexically or physically outside the project."""


class SymlinkPathError(UnsafePathError):
    """Raised when any path component is a symbolic link."""


class MissingPathError(FileNotFoundError):
    """Raised when a safe in-project path does not exist."""


class NotRegularFileError(ValueError):
    """Raised when a safe existing path is not a regular file."""


def project_root_from_file() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_inside_project(
    project_root: Path,
    candidate: str | Path,
    *,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path:
    """Resolve *candidate* and reject paths outside *project_root* or symlinks."""
    root = Path(project_root).resolve(strict=True)
    raw = Path(candidate)
    joined = raw if raw.is_absolute() else root / raw

    # Normalize '.', '..', and absolute syntax without following symlinks. This
    # lets us classify an escape before asking whether the target exists.
    lexical = Path(os.path.abspath(joined))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise PathOutsideProjectError(f"path is outside the project: {candidate}") from exc

    # Reject every symlink component, including dangling links, before resolve()
    # can hide which component caused the redirection.
    current = lexical
    while current != root and current != current.parent:
        if current.is_symlink():
            raise SymlinkPathError(f"symbolic links are not allowed: {candidate}")
        current = current.parent

    # A second containment check protects against filesystem redirection or a
    # race that occurred after the lexical check.
    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathOutsideProjectError(f"path resolves outside the project: {candidate}") from exc
    if (must_exist or require_file) and not resolved.exists():
        raise MissingPathError(f"path does not exist inside the project: {candidate}")
    if require_file and not resolved.is_file():
        raise NotRegularFileError(f"path is not a regular file: {candidate}")
    return resolved


def resolve_manifest_input(project_root: Path, local_path: str) -> Path:
    """Validate a portable, relative manifest path under data/inbox."""
    raw = Path(local_path)
    if raw.is_absolute():
        raise UnsafePathError("manifest local_path must be relative")
    resolved = resolve_inside_project(project_root, raw, must_exist=True, require_file=True)
    inbox = resolve_inside_project(project_root, "data/inbox", must_exist=True)
    try:
        resolved.relative_to(inbox)
    except ValueError as exc:
        raise UnsafePathError("manifest input must be inside data/inbox") from exc
    return resolved


def resolve_candidate_clip(project_root: Path, clip_path: str) -> Path:
    """Validate a relative candidate clip path under data/candidates."""
    raw = Path(clip_path)
    if raw.is_absolute():
        raise UnsafePathError("candidate clip_path must be relative")
    resolved = resolve_inside_project(project_root, raw, must_exist=True, require_file=True)
    candidates = resolve_inside_project(project_root, "data/candidates", must_exist=True)
    try:
        resolved.relative_to(candidates)
    except ValueError as exc:
        raise UnsafePathError("candidate clip must be inside data/candidates") from exc
    return resolved


def resolve_final_dataset(project_root: Path) -> Path:
    """Return the existing, non-symlinked final dataset directory."""
    destination = resolve_inside_project(project_root, "data/final_dataset", must_exist=True)
    if not destination.is_dir():
        raise NotRegularFileError("data/final_dataset is not a directory")
    return destination
