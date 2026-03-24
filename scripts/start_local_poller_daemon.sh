#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${QA_DAEMON_PID_FILE:-/tmp/feishu_qa_poller.pid}"
LOG_FILE="${QA_DAEMON_LOG_FILE:-/tmp/feishu_qa_poller_daemon.log}"
INTERVAL_SECONDS="${QA_DAEMON_INTERVAL_SECONDS:-30}"
ALLOW_WITH_CRON="${QA_ALLOW_DAEMON_WITH_CRON:-false}"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/load_env.sh"

ALLOW_WITH_CRON_NORM="$(printf '%s' "${ALLOW_WITH_CRON}" | tr '[:upper:]' '[:lower:]')"
if [[ ! "${ALLOW_WITH_CRON_NORM}" =~ ^(1|true|yes|y|on)$ ]]; then
  if crontab -l 2>/dev/null | grep -q "FEISHU_QA_BOT_START"; then
    echo "blocked: cron 已启用 QA 轮询。为避免重复回复，默认不再启动本地 daemon。"
    echo "如确需并行，请显式设置 QA_ALLOW_DAEMON_WITH_CRON=true 后重试。"
    exit 1
  fi
fi

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "already_running pid=${OLD_PID} pid_file=${PID_FILE}"
    exit 0
  fi
fi

cd "${PROJECT_DIR}"
nohup bash -c '
  set -euo pipefail
  LOG_FILE="$1"
  INTERVAL_SECONDS="$2"
  while true; do
    python3 group_qa_poller_v3.py >>"${LOG_FILE}" 2>&1 || true
    sleep "${INTERVAL_SECONDS}"
  done
' _ "${LOG_FILE}" "${INTERVAL_SECONDS}" >>"${LOG_FILE}" 2>&1 &

echo $! >"${PID_FILE}"
echo "started pid=$(cat "${PID_FILE}") interval=${INTERVAL_SECONDS}s log=${LOG_FILE}"
