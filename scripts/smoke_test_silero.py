"""Offline smoke test for the model bundled in the installed silero-vad wheel."""
from importlib import metadata, resources
import hashlib

import torch
from silero_vad import get_speech_timestamps, load_silero_vad


def main() -> None:
    model_file = resources.files("silero_vad.data").joinpath("silero_vad.jit")
    model = load_silero_vad()
    silence = torch.zeros(16000, dtype=torch.float32)
    timestamps = get_speech_timestamps(
        silence, model, sampling_rate=16000, return_seconds=True
    )
    if timestamps:
        raise RuntimeError(f"one second of digital silence was detected as speech: {timestamps}")
    print("silero-vad:", metadata.version("silero-vad"))
    print("torch:", torch.__version__)
    print("model bytes:", model_file.stat().st_size)
    print("model sha256:", hashlib.sha256(model_file.read_bytes()).hexdigest())
    print("silence timestamps:", timestamps)
    print("offline smoke test: OK")


if __name__ == "__main__":
    main()
