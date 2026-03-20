#!/usr/bin/env python3
"""Run one message through group_qa_handler and print JSON result."""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from group_qa_handler import process_group_message  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run single QA message")
    parser.add_argument("--chat-id", required=True, help="Feishu chat_id")
    parser.add_argument("--sender-id", required=True, help="Feishu sender_id")
    parser.add_argument("--message", required=True, help="Message text")
    parser.add_argument(
        "--mentions-json",
        default="[]",
        help="Mentions array in JSON format, default []",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        mentions = json.loads(args.mentions_json)
        if not isinstance(mentions, list):
            raise ValueError("mentions_json must be JSON array")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"invalid mentions_json: {exc}"}, ensure_ascii=False))
        return 1

    reply, record_fields = process_group_message(
        chat_id=args.chat_id,
        sender_id=args.sender_id,
        message=args.message,
        mentions=mentions,
    )

    output = {
        "ok": True,
        "matched": reply is not None,
        "reply_text": reply or "",
        "record_fields": record_fields or {},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
