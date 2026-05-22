#!/bin/bash
# gcg-openai-oauth-refresh.sh — wrapper for the OAuth refresh daemon
# Calls the Python implementation. Exits non-zero on any failure.
set -euo pipefail
exec python3 /opt/gcg/shared/bin/gcg-openai-oauth-refresh.py "$@"
