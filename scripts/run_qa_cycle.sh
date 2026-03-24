#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Preserve caller-provided env overrides (e.g. cron inline vars) so .env load
# won't accidentally overwrite runtime behavior.
OVR_QA_NOTIFY_ON_IDLE="${QA_NOTIFY_ON_IDLE-__UNSET__}"
OVR_QA_SYNC_INTENTS_TO_BITABLE="${QA_SYNC_INTENTS_TO_BITABLE-__UNSET__}"
OVR_QA_KB_SOURCE="${QA_KB_SOURCE-__UNSET__}"
OVR_QA_CYCLE_LOG_FILE="${QA_CYCLE_LOG_FILE-__UNSET__}"
OVR_QA_IDLE_TEXT="${QA_IDLE_TEXT-__UNSET__}"

# Load project .env for non-interactive runners (cron/OpenClaw).
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/load_env.sh"

if [[ "${OVR_QA_NOTIFY_ON_IDLE}" != "__UNSET__" ]]; then
  export QA_NOTIFY_ON_IDLE="${OVR_QA_NOTIFY_ON_IDLE}"
fi
if [[ "${OVR_QA_SYNC_INTENTS_TO_BITABLE}" != "__UNSET__" ]]; then
  export QA_SYNC_INTENTS_TO_BITABLE="${OVR_QA_SYNC_INTENTS_TO_BITABLE}"
fi
if [[ "${OVR_QA_KB_SOURCE}" != "__UNSET__" ]]; then
  export QA_KB_SOURCE="${OVR_QA_KB_SOURCE}"
fi
if [[ "${OVR_QA_CYCLE_LOG_FILE}" != "__UNSET__" ]]; then
  export QA_CYCLE_LOG_FILE="${OVR_QA_CYCLE_LOG_FILE}"
fi
if [[ "${OVR_QA_IDLE_TEXT}" != "__UNSET__" ]]; then
  export QA_IDLE_TEXT="${OVR_QA_IDLE_TEXT}"
fi

QUIET_IDLE="${QA_NOTIFY_ON_IDLE:-false}"
CYCLE_LOG_FILE="${QA_CYCLE_LOG_FILE:-/tmp/feishu_qa_cycle.log}"
IDLE_TEXT="${QA_IDLE_TEXT:-本轮已执行完成，暂无需要处理的消息。}"
LOCK_DIR="${QA_CYCLE_LOCK_DIR:-/tmp/feishu_qa_cycle.lock}"

cd "${PROJECT_DIR}"

# Prevent overlapping cron runs, which can cause duplicate replies.
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "上一轮仍在执行中，本轮跳过。"
  exit 0
fi

cleanup_lock() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}
trap cleanup_lock EXIT

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

QUIET_IDLE_NORM="$(printf '%s' "${QUIET_IDLE}" | tr '[:upper:]' '[:lower:]')"
if [[ "${QUIET_IDLE_NORM}" =~ ^(1|true|yes|y|on)$ ]]; then
  echo "${BROADCAST_TEXT:-${IDLE_TEXT}}"
  exit 0
fi

# Keep one concise line for idle rounds to avoid platform "(no output)" hints.
echo "${IDLE_TEXT}"
