#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

mode=""
question=""
source_override=""
reload_intents="false"
sync_intents="false"
dry_run="false"
force_sync="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      mode="${2:-}"
      shift 2
      ;;
    --question)
      question="${2:-}"
      shift 2
      ;;
    --source)
      source_override="${2:-}"
      shift 2
      ;;
    --reload)
      reload_intents="true"
      shift
      ;;
    --sync-intents)
      sync_intents="true"
      shift
      ;;
    --dry-run)
      dry_run="true"
      shift
      ;;
    --force)
      force_sync="true"
      shift
      ;;
    *)
      echo "{\"ok\":false,\"error\":\"unknown_arg\",\"arg\":\"$1\"}"
      exit 1
      ;;
  esac
done

cd "${PROJECT_DIR}"

if [[ -z "${mode}" ]]; then
  echo '{"ok":false,"error":"missing_mode","message":"use --mode kb-sync|intent-match"}'
  exit 1
fi

if [[ "${mode}" == "kb-sync" ]]; then
  declare -a args=()
  if [[ -n "${source_override}" ]]; then
    args+=(--source "${source_override}")
  fi
  if [[ "${dry_run}" == "true" ]]; then
    args+=(--dry-run)
  fi
  if [[ "${force_sync}" == "true" ]]; then
    args+=(--force)
  fi
  if [[ "${sync_intents}" == "true" ]]; then
    args+=(--sync-intents)
  fi
  exec bash "${SCRIPT_DIR}/skill_kb_update.sh" "${args[@]}"
fi

if [[ "${mode}" == "intent-match" ]]; then
  if [[ -z "${question}" ]]; then
    echo '{"ok":false,"error":"missing_question","message":"intent-match requires --question"}'
    exit 1
  fi
  declare -a args=(--question "${question}")
  if [[ "${reload_intents}" == "true" ]]; then
    args+=(--reload)
  fi
  exec bash "${SCRIPT_DIR}/skill_intent_match.sh" "${args[@]}"
fi

echo "{\"ok\":false,\"error\":\"invalid_mode\",\"mode\":\"${mode}\"}"
exit 1
