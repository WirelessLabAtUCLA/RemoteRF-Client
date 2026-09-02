#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python -m pytest -q tests/integration/test_global_v1_local.py
