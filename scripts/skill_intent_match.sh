#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/load_env.sh"

cd "${PROJECT_DIR}"

python3 - "$@" <<'PY'
import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

BASE_DIR = Path.cwd()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from intent_classifier_v5 import classify_intent, load_intents, reload_intents  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match one question against intents.json")
    parser.add_argument("--question", required=True, help="Question text")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Force reload intents from file before matching",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logs_buf = io.StringIO()
    with contextlib.redirect_stderr(logs_buf):
        intents = reload_intents() if args.reload else load_intents()
        result = classify_intent(args.question)
    logs = [line for line in logs_buf.getvalue().splitlines() if line.strip()]

    payload = {
        "ok": True,
        "question": args.question,
        "matched": result is not None,
        "intents_count": len(intents),
        "logs": logs,
    }

    if result is not None:
        payload.update(
            {
                "intent_id": result.intent_id,
                "confidence": round(float(result.confidence), 4),
                "matched_keywords": result.matched_keywords,
                "answer": result.answer,
                "links": result.links,
            }
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
