#!/usr/bin/env bash
#
# run_eval.sh — codified HARNESS eval invocation (HARNESS-23 T16, issue #154).
#
# Runs ON the PolyU GPU server (ssh polyu-gpu), from any directory.  Encodes
# the corrected podman procedure from
# docs/harness_14_phase6_followups.md -> "Running the FULL eval":
#
#   1. Capture env from the RUNNING stf-diagnostic-api container (NOT
#      infra/.env — the app reads DB_*/LLM_* keys that compose maps from
#      differently-named ones).
#   2. podman run the eval image with tests/ mounted RW (reports/ lives
#      there — RO would lose the JSON at teardown), scripts/ RO, and the
#      REAL infra_diagnostic_api_manuals volume RO (APP-61 backfilled
#      factory_code, so the real volume works).
#   3. pytest --run-eval -p no:cacheprovider over both lane test files.
#   4. Print the newest report path + the scp/aggregate follow-up.
#
# A NON-ZERO pytest exit is EXPECTED whenever any entry scores below its
# lane's _PASS_THRESHOLD — the report is still written at session teardown.
# This script therefore does NOT treat pytest failure as a script failure.
#
# Usage:
#   ./run_eval.sh [--lane manual|rag|both] [--smoke | -k EXPR] [--dry-run]
#
#   --lane manual|rag|both   Which lane(s) to run (default: both, one
#                            shared report — the comparable configuration).
#   --smoke                  Plumbing check: one entry per question_type
#                            (both lanes unless --lane narrows it).  The
#                            fixed subset, chosen as the first locked
#                            golden of each type:
#                              lookup-001       (lookup)
#                              dtc-001          (procedural)
#                              cross-001        (cross-section)
#                              image-001        (image-required)
#                              adversarial-001  (adversarial)
#   -k EXPR                  Raw pytest -k passthrough (mutually exclusive
#                            with --smoke), e.g. -k lookup-002.
#   --dry-run                Print every command instead of executing
#                            (testable off-server; no podman required).
#
# Environment:
#   STF_REPO_DIR             Repo checkout on the server
#                            (default: ~/stf_ai_diagnosis_platform_v1).
#
# Full both-lane run is long (~65 min at the 2026-07-12 re-baseline) —
# launch detached (nohup/tmux) and poll for the report file.
#
# Author: Li-Ta Hsu

set -u -o pipefail

# --- Constants ---------------------------------------------------------------

REPO_DIR="${STF_REPO_DIR:-$HOME/stf_ai_diagnosis_platform_v1}"
API_DIR="$REPO_DIR/diagnostic_api"
CONTAINER="stf-diagnostic-api"
IMAGE="localhost/stf-diagnostic-api:0.1.0"
MANUALS_VOLUME="infra_diagnostic_api_manuals"
REPORTS_DIR="$API_DIR/tests/harness/evals/reports"

MANUAL_TEST="/app/tests/harness/evals/test_manual_agent_eval.py"
RAG_TEST="/app/tests/harness/evals/test_rag_eval.py"

# Env keys the app actually reads — captured from the running container.
ENV_KEY_REGEX='^(DB_|LLM_|EMBEDDING_|VISION_|PREMIUM_LLM_|MANUAL_|JWT_|STRICT_MODE|LOG_|OBD_LOG_|AUDIO_|ALLOW_EXTERNAL)'

# One entry per question_type (see header for the id -> type mapping).
SMOKE_K='lookup-001 or dtc-001 or cross-001 or image-001 or adversarial-001'

# --- Helpers -----------------------------------------------------------------

log() { printf '[run_eval] %s\n' "$*" >&2; }
die() { printf '[run_eval] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'
}

# Execute the given command, or echo it (shell-quoted) under --dry-run.
run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# --- Argument parsing --------------------------------------------------------

LANE="both"
K_EXPR=""
SMOKE=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --lane)
      [ $# -ge 2 ] || die "--lane requires an argument: manual|rag|both"
      LANE="$2"; shift 2 ;;
    --lane=*)
      LANE="${1#*=}"; shift ;;
    --smoke)
      SMOKE=1; shift ;;
    -k)
      [ $# -ge 2 ] || die "-k requires a pytest keyword expression"
      K_EXPR="$2"; shift 2 ;;
    -k=*)
      K_EXPR="${1#*=}"; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      die "unknown argument: $1 (see --help)" ;;
  esac
done

case "$LANE" in
  manual|rag|both) ;;
  *) die "invalid --lane '$LANE' (expected manual, rag, or both)" ;;
esac

if [ "$SMOKE" -eq 1 ] && [ -n "$K_EXPR" ]; then
  die "--smoke and -k are mutually exclusive (--smoke IS a -k preset)"
fi
if [ "$SMOKE" -eq 1 ]; then
  K_EXPR="$SMOKE_K"
fi

# --- Preflight ---------------------------------------------------------------

if [ "$DRY_RUN" -eq 0 ]; then
  command -v podman >/dev/null 2>&1 \
    || die "podman not found — this script runs ON the PolyU server"

  [ -d "$API_DIR/tests" ] \
    || die "no tests/ at $API_DIR — set STF_REPO_DIR to the repo checkout"

  if ! podman ps --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
    die "container '$CONTAINER' is not running — the eval captures its \
env and needs the stack (Postgres/Ollama) up. Start it per the CLAUDE.md \
deploy procedure, then re-run."
  fi

  if ! podman image exists "$IMAGE"; then
    die "image '$IMAGE' not found — build it first: cd $REPO_DIR/infra && \
podman-compose -f docker-compose.yml -f docker-compose.polyu.yml \
build diagnostic-api"
  fi
else
  log "dry-run: skipping preflight (podman/container/image checks)"
fi

# --- Step 1: capture env from the running container --------------------------

TS="$(date +%Y%m%d_%H%M%S)"
ENV_FILE="/tmp/eval_${TS}.env"
RUN_LOG="/tmp/eval_run_${TS}.log"

if [ "$DRY_RUN" -eq 1 ]; then
  printf '[dry-run] podman exec %s env | grep -E %q > %s\n' \
    "$CONTAINER" "$ENV_KEY_REGEX" "$ENV_FILE"
else
  podman exec "$CONTAINER" env | grep -E "$ENV_KEY_REGEX" > "$ENV_FILE" \
    || die "failed to capture env from '$CONTAINER'"
  [ -s "$ENV_FILE" ] \
    || die "captured env file is empty — is '$CONTAINER' healthy?"
  # The env file holds credentials (DB, JWT, OpenRouter) — remove on exit.
  trap 'rm -f "$ENV_FILE"' EXIT
  log "captured $(wc -l < "$ENV_FILE") env vars from $CONTAINER -> $ENV_FILE"
fi

# --- Step 2+3: assemble and run the podman/pytest invocation -----------------

TEST_FILES=()
case "$LANE" in
  manual) TEST_FILES=("$MANUAL_TEST") ;;
  rag)    TEST_FILES=("$RAG_TEST") ;;
  both)   TEST_FILES=("$MANUAL_TEST" "$RAG_TEST") ;;
esac

PYTEST_ARGS=(pytest --run-eval -p no:cacheprovider
  "${TEST_FILES[@]}" --tb=short)
if [ -n "$K_EXPR" ]; then
  PYTEST_ARGS+=(-k "$K_EXPR")
fi

PODMAN_CMD=(podman run --rm
  -v "$API_DIR/tests:/app/tests"
  -v "$API_DIR/scripts:/app/scripts:ro"
  -v "$MANUALS_VOLUME:/app/data/manuals:ro"
  --env-file "$ENV_FILE"
  -e PYTHONPATH=/app
  -e LOG_FILE=/tmp/diag.log
  --network host
  "$IMAGE"
  "${PYTEST_ARGS[@]}")

log "lane=$LANE smoke=$SMOKE k='${K_EXPR:-<none>}'"
log "run log: $RUN_LOG"

PYTEST_RC=0
if [ "$DRY_RUN" -eq 1 ]; then
  run_cmd "${PODMAN_CMD[@]}"
else
  "${PODMAN_CMD[@]}" 2>&1 | tee "$RUN_LOG" || PYTEST_RC=$?
fi

if [ "$PYTEST_RC" -ne 0 ]; then
  log "pytest exited $PYTEST_RC — EXPECTED when entries score below the \
lane threshold; the report is still written at teardown."
fi

# --- Step 4: locate the newest report + next steps ---------------------------

NEWEST=""
for f in "$REPORTS_DIR"/eval_*.json; do
  [ -e "$f" ] || continue
  if [ -z "$NEWEST" ] || [ "$f" -nt "$NEWEST" ]; then
    NEWEST="$f"
  fi
done

if [ -n "$NEWEST" ]; then
  log "newest report: $NEWEST"
  log "next: scp polyu-gpu:$NEWEST docs/eval-reports/<name>.json && python docs/eval-reports/aggregate_phase6.py docs/eval-reports/<name>.json"
else
  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: reports land in $REPORTS_DIR/eval_<ts>.json"
  else
    die "no report found under $REPORTS_DIR — check $RUN_LOG (a report \
should be written even on below-threshold failures)"
  fi
fi

exit 0
