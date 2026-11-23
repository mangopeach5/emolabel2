#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p work
[[ -x .venv-vad/bin/python ]] || { echo "ERROR: protected .venv-vad is missing" >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "ERROR: uv is required" >&2; exit 1; }
version="$(.venv-vad/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$version" == "3.12" ]] || { echo "ERROR: expected Python 3.12, found $version" >&2; exit 1; }
uv pip freeze --python .venv-vad/bin/python > work/packages-before-whisper.txt
uv pip install --python .venv-vad/bin/python --dry-run \
    -c constraints-protected.txt -r requirements-whisper.txt \
    2>&1 | tee work/whisper-install-plan.txt
echo "Review work/whisper-install-plan.txt. Torch/Silero/PySceneDetect must not be replaced."
if [[ "${INSTALL_WHISPER:-0}" != "1" ]]; then
    exit 0
fi
if grep -Eiq '(torch|silero-vad|scenedetect-headless).*(remove|uninstall|downgrade|upgrade)' work/whisper-install-plan.txt; then
    echo "ERROR: protected dependency may change; installation stopped" >&2
    exit 1
fi
uv pip install --python .venv-vad/bin/python \
    -c constraints-protected.txt -r requirements-whisper.txt
uv pip freeze --python .venv-vad/bin/python > work/packages-after-whisper.txt
.venv-vad/bin/python - <<'PY'
from importlib.metadata import version
import torch
assert version("openai-whisper") == "20250625"
assert version("silero-vad") == "6.2.3"
assert version("scenedetect-headless") == "0.7.3"
assert torch.__version__.startswith("2.15.0")
print("openai-whisper:", version("openai-whisper"))
print("torch:", torch.__version__)
PY
