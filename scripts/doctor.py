"""Read-only diagnostic. Does not scan old EmoLabel folders or call network."""
from pathlib import Path
import os
import platform
import shutil
import sys

root = Path(__file__).resolve().parents[1]
print("EmoLabel 2.0 - local environment check")
print("project:", root)
print("python:", sys.version.split()[0])
print("WSL:", "yes" if os.getenv("WSL_DISTRO_NAME") or "microsoft" in platform.release().lower() else "not detected")
print("standalone directory:", "yes" if root.name == "emolabel2" else "CHECK FOLDER NAME")
for name in ("ffmpeg", "ffprobe", "uv", "codex", "ollama", "node", "npm"):
    print(name, ":", shutil.which(name) or "not installed in this shell")
print("No network, no model calls, no other folders scanned.")
