#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PATH="${HOME}/.local/bin:${HOME}/.openclaw/bin:${PATH}"

FULLCYCLE_JOB_NAME="${FULLCYCLE_JOB_NAME:-qa_bot_full_cycle_openclaw}"
HEALTH_JOB_NAME="${HEALTH_JOB_NAME:-qa_bot_healthcheck_openclaw}"

if [[ -f "${SKILL_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${SKILL_DIR}/.env"
  set +a
fi

DEFAULT_CHAT_ID="${QA_CHAT_ID%%,*}"
if [[ -z "${DEFAULT_CHAT_ID}" ]]; then
  DEFAULT_CHAT_ID="oc_xxx"
fi
HEALTH_SENDER_ID="${QA_HEALTHCHECK_SENDER_ID:-${ADMIN_USER_ID:-ou_health}}"

extract_json() {
  python3 -c '
import json
import sys

raw = sys.stdin.read()
start = raw.find("{")
if start < 0:
    print("{}")
    sys.exit(0)
snippet = raw[start:]
try:
    obj = json.loads(snippet)
    print(json.dumps(obj, ensure_ascii=False))
except Exception:
    print("{}")
'
}

job_id_from_name() {
  local list_json="$1"
  local target_name="$2"
  printf '%s' "${list_json}" | python3 -c '
import json
import sys

target = sys.argv[1]
try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    data = {}
jobs = data.get("jobs") or []
for job in jobs:
    if isinstance(job, dict) and str(job.get("name", "")).strip() == target:
        print(str(job.get("id", "")).strip())
        break
' "${target_name}"
}

FULLCYCLE_MESSAGE="请执行命令：cd ${SKILL_DIR} && QA_NOTIFY_ON_IDLE=false bash scripts/run_qa_cycle.sh。回复要求：严格只回复命令标准输出的原文，不要补充执行过程、不要生成 JSON、不要二次总结；如果命令无输出则本轮不回复。"
HEALTH_MESSAGE="请执行命令：cd ${SKILL_DIR} && python3 scripts/run_single_message.py --chat-id ${DEFAULT_CHAT_ID} --sender-id ${HEALTH_SENDER_ID} --message \"bot 活着吗\" | python3 -c 'import json,sys;raw=sys.stdin.read();start=raw.find(\"{\");print(\"{}\" if start<0 else json.dumps(json.loads(raw[start:]), ensure_ascii=False))'。只输出该 JSON，不要补充说明。"

if ! LIST_RAW="$(openclaw cron list --all --json 2>&1)"; then
  echo "OpenClaw cron 网关不可用，请先启动 OpenClaw Desktop 或本地 Gateway 服务后重试。" >&2
  echo "${LIST_RAW}" >&2
  exit 1
fi
LIST_JSON="$(printf '%s' "${LIST_RAW}" | extract_json)"

EXISTING_FULLCYCLE_ID="$(job_id_from_name "${LIST_JSON}" "${FULLCYCLE_JOB_NAME}")"
EXISTING_HEALTH_ID="$(job_id_from_name "${LIST_JSON}" "${HEALTH_JOB_NAME}")"

if [[ -n "${EXISTING_FULLCYCLE_ID}" ]]; then
  openclaw cron rm "${EXISTING_FULLCYCLE_ID}" --json >/dev/null
fi
if [[ -n "${EXISTING_HEALTH_ID}" ]]; then
  openclaw cron rm "${EXISTING_HEALTH_ID}" --json >/dev/null
fi

if ! ADD_FULL_RAW="$(
  openclaw cron add \
    --name "${FULLCYCLE_JOB_NAME}" \
    --every "1m" \
    --session isolated \
    --expect-final \
    --message "${FULLCYCLE_MESSAGE}" \
    --json 2>&1
)"; then
  echo "创建 OpenClaw cron 任务失败：${FULLCYCLE_JOB_NAME}" >&2
  echo "${ADD_FULL_RAW}" >&2
  exit 1
fi

if ! ADD_HEALTH_RAW="$(
  openclaw cron add \
    --name "${HEALTH_JOB_NAME}" \
    --cron "30 8 * * *" \
    --tz "Asia/Shanghai" \
    --session isolated \
    --expect-final \
    --message "${HEALTH_MESSAGE}" \
    --json 2>&1
)"; then
  echo "创建 OpenClaw cron 任务失败：${HEALTH_JOB_NAME}" >&2
  echo "${ADD_HEALTH_RAW}" >&2
  exit 1
fi

ADD_FULL_JSON="$(printf '%s' "${ADD_FULL_RAW}" | extract_json)"
ADD_HEALTH_JSON="$(printf '%s' "${ADD_HEALTH_RAW}" | extract_json)"

export ADD_FULL_JSON ADD_HEALTH_JSON FULLCYCLE_JOB_NAME HEALTH_JOB_NAME
python3 - <<'PY'
import json
import os

def read_id(payload):
    if not isinstance(payload, dict):
        return ""
    if "id" in payload:
        return str(payload.get("id") or "")
    job = payload.get("job")
    if isinstance(job, dict):
        return str(job.get("id") or "")
    return ""

try:
    full = json.loads(os.environ.get("ADD_FULL_JSON", "{}") or "{}")
except Exception:
    full = {}
try:
    health = json.loads(os.environ.get("ADD_HEALTH_JSON", "{}") or "{}")
except Exception:
    health = {}

print(json.dumps({
  "ok": True,
  "jobs": [
    {"name": os.environ.get("FULLCYCLE_JOB_NAME", "qa_bot_full_cycle_openclaw"), "id": read_id(full)},
    {"name": os.environ.get("HEALTH_JOB_NAME", "qa_bot_healthcheck_openclaw"), "id": read_id(health)}
  ]
}, ensure_ascii=False, indent=2))
PY
