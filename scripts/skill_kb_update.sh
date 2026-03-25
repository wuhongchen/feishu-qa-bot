#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/load_env.sh"

sync_intents="false"
declare -a kb_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sync-intents)
      sync_intents="true"
      shift
      ;;
    *)
      kb_args+=("$1")
      shift
      ;;
  esac
done

cd "${PROJECT_DIR}"

kb_stdout_file="$(mktemp)"
kb_stderr_file="$(mktemp)"
intent_stdout_file="$(mktemp)"
intent_stderr_file="$(mktemp)"

cleanup() {
  rm -f "${kb_stdout_file}" "${kb_stderr_file}" "${intent_stdout_file}" "${intent_stderr_file}"
}
trap cleanup EXIT

kb_status=0
if ! python3 scripts/sync_knowledge_base.py "${kb_args[@]}" >"${kb_stdout_file}" 2>"${kb_stderr_file}"; then
  kb_status=$?
fi

intent_status=-1
if [[ "${sync_intents}" == "true" && ${kb_status} -eq 0 ]]; then
  if ! python3 scripts/sync_intents_to_bitable.py >"${intent_stdout_file}" 2>"${intent_stderr_file}"; then
    intent_status=$?
  else
    intent_status=0
  fi
fi

export KB_STATUS="${kb_status}"
export INTENT_STATUS="${intent_status}"
export KB_STDOUT_FILE="${kb_stdout_file}"
export KB_STDERR_FILE="${kb_stderr_file}"
export INTENT_STDOUT_FILE="${intent_stdout_file}"
export INTENT_STDERR_FILE="${intent_stderr_file}"
export SYNC_INTENTS="${sync_intents}"

python3 - <<'PY'
import json
import os
from pathlib import Path


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def parse_json_or_text(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


kb_status = int(os.getenv("KB_STATUS", "1"))
intent_status = int(os.getenv("INTENT_STATUS", "-1"))
sync_intents = os.getenv("SYNC_INTENTS", "false").strip().lower() == "true"

kb_stdout = read_text(os.environ["KB_STDOUT_FILE"])
kb_stderr = read_text(os.environ["KB_STDERR_FILE"])
intent_stdout = read_text(os.environ["INTENT_STDOUT_FILE"])
intent_stderr = read_text(os.environ["INTENT_STDERR_FILE"])

payload = {
    "ok": kb_status == 0 and (not sync_intents or intent_status == 0),
    "kb_sync": {
        "status": "success" if kb_status == 0 else "failed",
        "code": kb_status,
        "result": parse_json_or_text(kb_stdout),
        "stderr": kb_stderr or None,
    },
}

if sync_intents:
    payload["intent_sync"] = {
        "status": "success" if intent_status == 0 else "failed",
        "code": intent_status if intent_status >= 0 else None,
        "result": parse_json_or_text(intent_stdout),
        "stderr": intent_stderr or None,
    }

print(json.dumps(payload, ensure_ascii=False, indent=2))
raise SystemExit(0 if payload["ok"] else 1)
PY
