#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for d in data/inbox data/work data/candidates data/final_dataset data/logs data/manifests data/test_cases; do
    mkdir -p "$d"
done
echo "Prepared new local project: $(pwd)"
echo 'No GitHub, no Hugging Face, no downloads, no external original touched.'
