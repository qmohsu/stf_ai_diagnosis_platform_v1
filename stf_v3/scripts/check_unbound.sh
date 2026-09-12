#!/usr/bin/env bash
# D3 acceptance ②: with diagnostic_api/, obd_agent/ and obd-ui/ deleted, the
# V3 image still builds and its offline tests pass.  Uses a git worktree so
# the real checkout is untouched.  Needs docker or podman.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'git -C "$REPO" worktree remove --force "$TMP" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT
git -C "$REPO" worktree add --detach "$TMP" HEAD >/dev/null
rm -rf "$TMP/diagnostic_api" "$TMP/obd_agent" "$TMP/obd-ui"
echo "legacy dirs removed; remaining top-level:"; ls "$TMP"
ENGINE="${CONTAINER_ENGINE:-$(command -v docker || command -v podman)}"
"$ENGINE" build -q -t stf-v3:unbound-check "$TMP/stf_v3" >/dev/null
"$ENGINE" run --rm -e STF_V3_TEST_DATABASE_URL= stf-v3:unbound-check pytest -q -p no:cacheprovider
echo "UNBOUND CHECK PASS"
