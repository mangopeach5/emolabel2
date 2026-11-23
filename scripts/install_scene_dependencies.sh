#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p work
if [[ ! -x .venv-vad/bin/python ]]; then
    echo "ERROR: protected .venv-vad is missing; this script will not create it." >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is required." >&2
    exit 1
fi
version="$(.venv-vad/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$version" == "3.12" ]] || { echo "ERROR: expected Python 3.12, found $version" >&2; exit 1; }
uv pip freeze --python .venv-vad/bin/python > work/packages-before-scenedetect.txt
uv pip install --python .venv-vad/bin/python --dry-run -r requirements-scene.txt \
    2>&1 | tee work/scenedetect-install-plan.txt
if grep -Eiq '(^|[[:space:]])(torch|silero-vad)([[:space:]]|==).*(remove|uninstall|downgrade|upgrade)' work/scenedetect-install-plan.txt; then
    echo "ERROR: dry-run may change torch or silero-vad; installation stopped." >&2
    exit 1
fi
echo "Dry-run saved. Review work/scenedetect-install-plan.txt."
echo "Run with INSTALL_SCENEDETECT=1 only after confirming torch and silero-vad are unchanged."
if [[ "${INSTALL_SCENEDETECT:-0}" != "1" ]]; then
    exit 0
fi
uv pip install --python .venv-vad/bin/python -r requirements-scene.txt
uv pip freeze --python .venv-vad/bin/python > work/packages-after-scenedetect.txt
.venv-vad/bin/python - <<'PY'
import cv2
import scenedetect
import torch
from importlib.metadata import version

assert version("scenedetect-headless") == "0.7.3"
assert version("silero-vad") == "6.2.3"
assert torch.__version__.startswith("2.15.0")
print("scenedetect-headless:", version("scenedetect-headless"))
print("opencv:", cv2.__version__)
print("silero-vad:", version("silero-vad"))
print("torch:", torch.__version__)
PY
