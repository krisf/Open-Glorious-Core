#!/usr/bin/env sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
python3 -m pip install --upgrade build
python3 -m build

echo "Built release artifacts in dist/"
