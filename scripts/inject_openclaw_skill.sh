#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKILL_NAME="feishu-qa-bot-skill"
MODE="${1:-link}" # link | copy

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

OC_RAW="$(openclaw skills list --json 2>/dev/null || true)"
OC_JSON="$(printf '%s' "${OC_RAW}" | extract_json)"
MANAGED_DIR="$(printf '%s' "${OC_JSON}" | python3 -c 'import json,sys
try:
    obj=json.loads(sys.stdin.read() or "{}")
except Exception:
    obj={}
print(obj.get("managedSkillsDir") or "")
')"
if [[ -z "${MANAGED_DIR}" ]]; then
  MANAGED_DIR="${HOME}/.openclaw/skills"
fi

mkdir -p "${MANAGED_DIR}"
TARGET="${MANAGED_DIR}/${SKILL_NAME}"

if [[ "${MODE}" == "copy" ]]; then
  rm -rf "${TARGET}"
  mkdir -p "${TARGET}"
  rsync -a --delete --exclude '.git' --exclude '__pycache__' "${SKILL_DIR}/" "${TARGET}/"
else
  ln -sfn "${SKILL_DIR}" "${TARGET}"
fi

CHECK_RAW="$(openclaw skills list --json 2>/dev/null || true)"
CHECK_JSON="$(printf '%s' "${CHECK_RAW}" | extract_json)"
FOUND="$(printf '%s' "${CHECK_JSON}" | python3 -c 'import json,sys
name="feishu-qa-bot-skill"
try:
    data=json.loads(sys.stdin.read() or "{}")
except Exception:
    data={}
skills=data.get("skills") or []
ok=any(str(s.get("name","")).strip()==name for s in skills if isinstance(s,dict))
print("true" if ok else "false")
')"

python3 - <<PY
import json
ok = str("${FOUND}").lower() == "true"
print(json.dumps({
  "ok": ok,
  "mode": "${MODE}",
  "skill_name": "${SKILL_NAME}",
  "managed_skills_dir": "${MANAGED_DIR}",
  "target_path": "${TARGET}"
}, ensure_ascii=False, indent=2))
PY
