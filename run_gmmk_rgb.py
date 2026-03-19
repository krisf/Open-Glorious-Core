from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"

extra_paths: list[str] = []
for site_dir in sorted((PROJECT_DIR / ".venv" / "lib").glob("python*/site-packages"), reverse=True):
    extra_paths.append(str(site_dir))
extra_paths.append(str(SRC_DIR))

for extra in extra_paths:
    if extra not in sys.path:
        sys.path.insert(0, extra)

os.environ["PYTHONPATH"] = os.pathsep.join(extra_paths)

from gmmk_rgb_app.app import main

if __name__ == "__main__":
    main()
