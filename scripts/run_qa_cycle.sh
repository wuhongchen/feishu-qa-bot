#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
QUIET_IDLE="${QA_NOTIFY_ON_IDLE:-false}"
CYCLE_LOG_FILE="${QA_CYCLE_LOG_FILE:-/tmp/feishu_qa_cycle.log}"

# Load project .env for non-interactive runners (cron/OpenClaw).
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/load_env.sh"

cd "${PROJECT_DIR}"

# Optional: sync KB before polling when QA_KB_SOURCE is configured.
if [[ -n "${QA_KB_SOURCE:-}" ]]; then
  python3 scripts/sync_knowledge_base.py --source "${QA_KB_SOURCE}" >>"${CYCLE_LOG_FILE}" 2>&1 || true
fi

# Optional: sync intent library snapshot into Bitable table.
if [[ "${QA_SYNC_INTENTS_TO_BITABLE:-true}" =~ ^(1|true|yes|y|on)$ ]]; then
  python3 scripts/sync_intents_to_bitable.py >>"${CYCLE_LOG_FILE}" 2>&1 || true
fi

if ! POLLER_RAW="$(python3 group_qa_poller_v3.py 2>>"${CYCLE_LOG_FILE}")"; then
  echo "本轮巡检执行异常，请稍后重试。"
  exit 1
fi

SUMMARY_LINE="$(
  printf '%s' "${POLLER_RAW}" | python3 -c '
import json
import sys

raw = sys.stdin.read()
start = raw.find("{")
if start < 0:
    print("-1\t1\t")
    sys.exit(0)

snippet = raw[start:]
obj = None
for end in range(len(snippet), 0, -1):
    chunk = snippet[:end]
    try:
        obj = json.loads(chunk)
        break
    except Exception:
        continue

if not isinstance(obj, dict):
    print("-1\t1\t")
    sys.exit(0)

total_processed = int(obj.get("total_processed", 0) or 0)
errors_raw = obj.get("errors", [])
if isinstance(errors_raw, list):
    err_count = len(errors_raw)
else:
    try:
        err_count = int(errors_raw or 0)
    except Exception:
        err_count = 0
broadcast = str((obj.get("broadcast") or {}).get("text", "")).strip()
print(f"{total_processed}\t{err_count}\t{broadcast}")
'
)"

IFS=$'\t' read -r TOTAL_PROCESSED ERROR_COUNT BROADCAST_TEXT <<<"${SUMMARY_LINE}"

if [[ "${TOTAL_PROCESSED}" == "-1" ]]; then
  echo "本轮巡检完成。"
  exit 0
fi

if (( ERROR_COUNT > 0 )); then
  echo "${BROADCAST_TEXT:-本轮巡检发现异常，请查看日志。}"
  exit 0
fi

if (( TOTAL_PROCESSED > 0 )); then
  echo "${BROADCAST_TEXT:-本轮已处理问答消息。}"
  exit 0
fi

if [[ "${QUIET_IDLE,,}" =~ ^(1|true|yes|y|on)$ ]]; then
  echo "${BROADCAST_TEXT:-本轮无新消息。}"
fi
