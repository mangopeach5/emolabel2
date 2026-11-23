#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p work
if [[ ! -x .venv-vad/bin/python ]]; then
    echo "ERROR: .venv-vad does not exist. Create it explicitly with Python 3.12 and uv." >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is required; the existing environment will not be modified." >&2
    exit 1
fi
python_version="$(.venv-vad/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$python_version" != "3.12" ]]; then
    echo "ERROR: protected .venv-vad must use Python 3.12; found $python_version" >&2
    exit 1
fi
uv pip freeze --python .venv-vad/bin/python > work/packages-before-vad.txt
uv pip install --python .venv-vad/bin/python --dry-run -r requirements-vad.txt \
    | tee work/silero-install-plan.txt
uv pip install --python .venv-vad/bin/python -r requirements-vad.txt
uv pip freeze --python .venv-vad/bin/python > work/packages-after-vad.txt
source .venv-vad/bin/activate
python - <<'PY'
import importlib.metadata
from importlib import resources
import hashlib
import torch

model = resources.files("silero_vad.data").joinpath("silero_vad.jit")
digest = hashlib.sha256(model.read_bytes()).hexdigest()
print("silero-vad:", importlib.metadata.version("silero-vad"))
print("torch:", torch.__version__)
print("model:", model)
print("model bytes:", model.stat().st_size)
print("model sha256:", digest)
PY
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B scripts/smoke_test_silero.py
