#!/usr/bin/env bash
# D3 acceptance ①: stf_v3/ copied alone into an empty directory installs and
# passes its offline tests (+ import-linter).  Run from anywhere.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "copying $SRC -> $TMP/stf_v3"
mkdir -p "$TMP/stf_v3"
tar -C "$SRC" --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' -cf - . | tar -C "$TMP/stf_v3" -xf -
cd "$TMP/stf_v3"
python -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[dev]"
unset STF_V3_TEST_DATABASE_URL   # offline only: DB tests skip
pytest -q -p no:cacheprovider
lint-imports
echo "PORTABLE CHECK PASS"
