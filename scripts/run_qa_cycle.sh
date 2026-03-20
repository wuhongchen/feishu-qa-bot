#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load project .env for non-interactive runners (cron/OpenClaw).
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/load_env.sh"

cd "${PROJECT_DIR}"

# Optional: sync KB before polling when QA_KB_SOURCE is configured.
if [[ -n "${QA_KB_SOURCE:-}" ]]; then
  python3 scripts/sync_knowledge_base.py --source "${QA_KB_SOURCE}"
fi

# Optional: sync intent library snapshot into Bitable table.
if [[ "${QA_SYNC_INTENTS_TO_BITABLE:-true}" =~ ^(1|true|yes|y|on)$ ]]; then
  if ! python3 scripts/sync_intents_to_bitable.py; then
    echo "[WARN] sync_intents_to_bitable failed, continue polling..."
  fi
fi

python3 group_qa_poller_v3.py
