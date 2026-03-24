#!/usr/bin/env bash
set -euo pipefail

PID_FILE="${QA_DAEMON_PID_FILE:-/tmp/feishu_qa_poller.pid}"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "not_running"
  exit 0
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [[ -z "${PID}" ]]; then
  rm -f "${PID_FILE}"
  echo "not_running"
  exit 0
fi

if kill -0 "${PID}" 2>/dev/null; then
  kill "${PID}" 2>/dev/null || true
  sleep 1
  if kill -0 "${PID}" 2>/dev/null; then
    kill -9 "${PID}" 2>/dev/null || true
  fi
fi

rm -f "${PID_FILE}"
echo "stopped pid=${PID}"
