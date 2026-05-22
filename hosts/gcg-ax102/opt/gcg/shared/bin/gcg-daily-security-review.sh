#!/bin/bash
# gcg-daily-security-review.sh — Orchestrator for daily LLM security review
#
# Responsibilities:
#   1. Fetch DeepSeek API key from 1Password (same pattern as bootstrap)
#   2. Source DB env from /run/openclaw-nik/env (Nik's runtime env has DB creds)
#   3. Invoke gcg-daily-security-review.py
#   4. Exit cleanly even if LLM is unreachable (log but no noise)
#
# Called by: gcg-daily-security-review.service (systemd)
# Schedule:  gcg-daily-security-review.timer (02:00 UTC daily = 06:00 Dubai)
#
# Key 1Password item: GCG Agent Fleet / "Deepseek API" / credential field

set -uo pipefail

SCRIPT_DIR="/opt/gcg/shared/bin"
PYTHON="/opt/gcg/shared/venv/bin/python3"
REVIEW_PY="${SCRIPT_DIR}/gcg-daily-security-review.py"
LOG_FILE="/var/log/gcg/security-review-runner.log"
REPORT_DATE=$(date -u +%Y%m%d)

mkdir -p /var/log/gcg /var/log/gcg/security-alerts

log() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [gcg-security-review] $*" | tee -a "$LOG_FILE"
}

log "=== Starting daily security review for $REPORT_DATE ==="

# ── 1. Fetch DeepSeek API key from 1Password ──────────────────────────────
# $CREDENTIALS_DIRECTORY is set by systemd LoadCredentialEncrypted=
DEEPSEEK_API_KEY=""

if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -r "${CREDENTIALS_DIRECTORY}/op-sa-token" ]; then
    OP_TOKEN=$(cat "${CREDENTIALS_DIRECTORY}/op-sa-token")
    DEEPSEEK_API_KEY=$(
        OP_SERVICE_ACCOUNT_TOKEN="$OP_TOKEN" \
        op read "op://GCG Agent Fleet/Deepseek API/credential" 2>/dev/null || true
    )
    if [ -z "$DEEPSEEK_API_KEY" ]; then
        log "WARNING: Could not fetch DeepSeek API key from 1Password — LLM step will be skipped"
    else
        log "DeepSeek API key fetched from 1Password"
    fi
else
    log "WARNING: CREDENTIALS_DIRECTORY not set or op-sa-token missing — LLM step will be skipped"
fi

# ── 2. Source DB creds from a runtime env file ────────────────────────────
# Try Nik's runtime env first (always running critical agent), fall back to talos
DB_ENV_LOADED=0
for AGENT_ENV in /run/openclaw-nik/env /run/openclaw-talos/env /run/openclaw-marcus/env; do
    if [ -r "$AGENT_ENV" ]; then
        # shellcheck source=/dev/null
        set -a
        source "$AGENT_ENV"
        set +a
        DB_ENV_LOADED=1
        log "DB env sourced from $AGENT_ENV"
        break
    fi
done

if [ "$DB_ENV_LOADED" -eq 0 ]; then
    log "WARNING: No runtime env file found — DB audit_log write will be skipped"
fi

# ── 3. Run the Python reviewer ────────────────────────────────────────────
export DEEPSEEK_API_KEY
export REPORT_DATE

log "Launching gcg-daily-security-review.py..."

EXIT_CODE=0
"$PYTHON" "$REVIEW_PY" 2>&1 | tee -a "$LOG_FILE" || EXIT_CODE=$?

# Exit code 1 = alerts found (not an error, just informational)
# Exit code 0 = clean
# Exit code 2+ = actual error

if [ "$EXIT_CODE" -eq 0 ]; then
    log "Review complete — no security alerts."
elif [ "$EXIT_CODE" -eq 1 ]; then
    log "Review complete — SECURITY ALERTS were flagged. Check /var/log/gcg/security-alerts/"
else
    log "ERROR: Review script exited with code $EXIT_CODE. Check $LOG_FILE."
fi

log "=== Daily security review done (exit=$EXIT_CODE) ==="

# Always exit 0 from the orchestrator — we don't want systemd to mark the
# service as failed just because the LLM flagged alerts or was unreachable.
exit 0
