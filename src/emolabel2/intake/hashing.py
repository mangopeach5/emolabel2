"""Streaming file hashing with no network or model dependencies."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"not a regular file: {source}")
    digest = sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
