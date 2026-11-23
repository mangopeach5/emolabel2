"""Explicit, checksum-verified download from OpenAI's official model host."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4


MODELS = {
    "tiny": ("65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9", "tiny.pt"),
    "base": ("ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e", "base.pt"),
    "small": ("9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794", "small.pt"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=MODELS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    directory = root / "models/whisper"
    directory.mkdir(parents=True, exist_ok=True)
    expected, filename = MODELS[args.model]
    destination = directory / filename
    if destination.exists():
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"existing {filename} has wrong SHA-256; it was not overwritten")
        print(f"already verified: {destination}")
        return 0
    temporary = directory / f".{filename}.partial.{uuid4().hex}"
    url = f"https://openaipublic.azureedge.net/main/whisper/models/{expected}/{filename}"
    digest = hashlib.sha256()
    try:
        with urlopen(url, timeout=60) as source, temporary.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError("downloaded model SHA-256 does not match the official URL")
        os.link(temporary, destination)
        temporary.unlink()
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    print(f"downloaded and verified: {destination}")
    print(f"sha256: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
