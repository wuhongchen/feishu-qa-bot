#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

# Ensure non-interactive runners (cron/OpenClaw task worker) can locate CLI
# and Node runtime required by openclaw.
export PATH="${HOME}/.local/bin:${HOME}/.openclaw/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi
