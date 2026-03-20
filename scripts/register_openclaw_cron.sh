#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

FULLCYCLE_JOB_NAME="${FULLCYCLE_JOB_NAME:-qa_bot_full_cycle_openclaw}"
HEALTH_JOB_NAME="${HEALTH_JOB_NAME:-qa_bot_healthcheck_openclaw}"
DEFAULT_CHAT_ID="${QA_CHAT_ID%%,*}"
if [[ -z "${DEFAULT_CHAT_ID}" ]]; then
  DEFAULT_CHAT_ID="oc_xxx"
fi

if [[ -f "${SKILL_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${SKILL_DIR}/.env"
  set +a
fi

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

FULLCYCLE_MESSAGE="请执行命令：cd ${SKILL_DIR} && bash scripts/run_qa_cycle.sh。读取输出 JSON 中的 broadcast.text 作为播报文案。回复要求：只输出一段自然中文播报，不要展示 JSON、字段名、代码块、技术指标。"
HEALTH_MESSAGE="请执行命令：cd ${SKILL_DIR} && python3 scripts/run_single_message.py --chat-id ${DEFAULT_CHAT_ID} --sender-id ou_health --message \"bot 活着吗\"。返回 JSON 并标记是否 matched。"

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

python3 - <<PY
import json

def read_id(payload):
    if not isinstance(payload, dict):
        return ""
    if "id" in payload:
        return str(payload.get("id") or "")
    job = payload.get("job")
    if isinstance(job, dict):
        return str(job.get("id") or "")
    return ""

full = json.loads("""${ADD_FULL_JSON}""")
health = json.loads("""${ADD_HEALTH_JSON}""")

print(json.dumps({
  "ok": True,
  "jobs": [
    {"name": "${FULLCYCLE_JOB_NAME}", "id": read_id(full)},
    {"name": "${HEALTH_JOB_NAME}", "id": read_id(health)}
  ]
}, ensure_ascii=False, indent=2))
PY
