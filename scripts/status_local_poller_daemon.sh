#!/usr/bin/env bash
set -euo pipefail

PID_FILE="${QA_DAEMON_PID_FILE:-/tmp/feishu_qa_poller.pid}"
LOG_FILE="${QA_DAEMON_LOG_FILE:-/tmp/feishu_qa_poller_daemon.log}"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "status=stopped pid_file=${PID_FILE}"
  exit 0
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
  echo "status=running pid=${PID} pid_file=${PID_FILE} log=${LOG_FILE}"
  exit 0
fi

echo "status=stopped(stale_pid_file) pid_file=${PID_FILE} log=${LOG_FILE}"
