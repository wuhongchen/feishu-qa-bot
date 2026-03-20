#!/usr/bin/env python3
"""Poll Feishu group messages and route to QA handler."""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from env_bootstrap import load_project_env  # noqa: E402
from bitable_helper import create_record, resolve_app_and_table  # noqa: E402
from feishu_app_auth import FeishuAppAuth  # noqa: E402
from group_qa_handler import process_group_message  # noqa: E402

load_project_env(__file__)

PROCESS_WINDOW_MINUTES = max(1, int(os.getenv("QA_PROCESS_WINDOW_MINUTES", "5")))
APP_TOKEN = os.getenv("QA_BITABLE_TOKEN", "").strip()
TABLE_ID = os.getenv("QA_TABLE_ID", "").strip()
TABLE_NAME = os.getenv("QA_TABLE_NAME", "").strip()
BITABLE_BASE_URL = os.getenv("QA_BITABLE_BASE_URL", "").strip()
DEDUP_CACHE_FILE = Path(os.getenv("QA_MSG_DEDUP_CACHE_FILE", "/tmp/feishu_qa_processed_messages.json"))
DEDUP_TTL_MINUTES = max(PROCESS_WINDOW_MINUTES + 1, int(os.getenv("QA_MSG_DEDUP_TTL_MINUTES", "120")))

_resolved_app_token: Optional[str] = None
_resolved_table_id: Optional[str] = None

DEFAULT_CHAT_CONFIG = {
    "oc_839e988db57d1c706a89ba2bc3667dda": {"name": "工具组", "trigger": "auto", "bot_app_id": None},
    "oc_004b8ddf9f113e547f4b13d814cd4619": {
        "name": "学员交流群",
        "trigger": "auto",
        "bot_app_id": os.getenv("FEISHU_APP_ID", ""),
    },
}


def load_chat_config() -> Dict[str, Dict[str, str]]:
    """Load chat config from env fallback to defaults."""
    chat_ids_env = os.getenv("QA_CHAT_ID", "").strip()
    names_env = os.getenv("QA_CHAT_NAMES", "").strip()

    if not chat_ids_env:
        return DEFAULT_CHAT_CONFIG

    chat_ids = [i.strip() for i in chat_ids_env.split(",") if i.strip()]
    name_map = {}
    if names_env:
        for pair in [p.strip() for p in names_env.split(",") if p.strip()]:
            if ":" not in pair:
                continue
            cid, name = pair.split(":", 1)
            name_map[cid.strip()] = name.strip()

    return {
        cid: {"name": name_map.get(cid, cid), "trigger": "auto", "bot_app_id": os.getenv("FEISHU_APP_ID", "")}
        for cid in chat_ids
    }


CHAT_CONFIG = load_chat_config()


def get_token() -> str:
    auth = FeishuAppAuth()
    return auth.get_token() or ""


def _load_processed_cache() -> Dict[str, int]:
    try:
        if not DEDUP_CACHE_FILE.exists():
            return {}
        payload = json.loads(DEDUP_CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        out: Dict[str, int] = {}
        for key, value in payload.items():
            try:
                out[str(key)] = int(value)
            except Exception:
                continue
        return out
    except Exception:
        return {}


def _save_processed_cache(cache: Dict[str, int]) -> None:
    try:
        DEDUP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] save dedup cache failed: {exc}", file=sys.stderr)


def _prune_processed_cache(cache: Dict[str, int]) -> Dict[str, int]:
    now_ms = int(datetime.now().timestamp() * 1000)
    ttl_ms = DEDUP_TTL_MINUTES * 60 * 1000
    out = {k: v for k, v in cache.items() if now_ms - int(v) <= ttl_ms}
    return out


def _resolve_bitable_target(token: str) -> Tuple[bool, str, str, str]:
    global _resolved_app_token, _resolved_table_id
    if _resolved_app_token and _resolved_table_id:
        return True, _resolved_app_token, _resolved_table_id, ""

    ok, app_token, table_id, err = resolve_app_and_table(
        token=token,
        app_token=APP_TOKEN,
        table_id=TABLE_ID,
        base_url=BITABLE_BASE_URL,
        table_name=TABLE_NAME,
    )
    if not ok:
        return False, "", "", err

    _resolved_app_token = app_token
    _resolved_table_id = table_id
    return True, app_token, table_id, ""


def get_messages(token: str, chat_id: str, limit: int = 10) -> List[dict]:
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "page_size": limit,
        "sort_type": "ByCreateTimeDesc",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)
        payload = response.json()
        if payload.get("code") == 0:
            return payload.get("data", {}).get("items", [])
    except Exception as exc:
        print(f"[ERROR] get_messages failed for {chat_id}: {exc}", file=sys.stderr)
    return []


def is_recent_message(msg: dict, minutes: int = PROCESS_WINDOW_MINUTES) -> bool:
    try:
        create_time = msg.get("create_time")
        if not create_time:
            return False
        msg_time = datetime.fromtimestamp(int(create_time) / 1000)
        return msg_time >= datetime.now() - timedelta(minutes=minutes)
    except Exception:
        return False


def send_reply(token: str, chat_id: str, content: str, reply_to_message_id: str = "") -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"msg_type": "text", "content": json.dumps({"text": content})}

    # Prefer replying to the original message for better context threading.
    if reply_to_message_id:
        reply_url = f"https://open.feishu.cn/open-apis/im/v1/messages/{reply_to_message_id}/reply"
        try:
            response = requests.post(reply_url, headers=headers, json=payload, timeout=8)
            body = response.json()
            if body.get("code") == 0:
                return True, "reply"
            print(
                f"[WARN] reply-to-message failed for {chat_id}/{reply_to_message_id}: "
                f"{body.get('msg', body)}; fallback to chat send",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"[WARN] reply-to-message exception for {chat_id}/{reply_to_message_id}: {exc}; "
                "fallback to chat send",
                file=sys.stderr,
            )

    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    payload["receive_id"] = chat_id

    try:
        response = requests.post(url, headers=headers, params=params, json=payload, timeout=8)
        body = response.json()
        return body.get("code") == 0, "chat"
    except Exception as exc:
        print(f"[ERROR] send_reply failed for {chat_id}: {exc}", file=sys.stderr)
        return False, "failed"


def write_record(token: str, fields: dict) -> Tuple[bool, str]:
    ok, app_token, table_id, err = _resolve_bitable_target(token)
    if not ok:
        return False, err

    if not app_token or not table_id:
        return False, "未解析到 QA_BITABLE_TOKEN / QA_TABLE_ID"
    return create_record(token=token, app_token=app_token, table_id=table_id, fields=fields)


def extract_message_text(msg: dict) -> str:
    try:
        body = json.loads(msg.get("body", {}).get("content", "{}"))
        return str(body.get("text", "")).strip()
    except Exception:
        return ""


def main() -> None:
    token = get_token()
    if not token:
        print(json.dumps({"error": "获取 tenant_access_token 失败"}, ensure_ascii=False))
        raise SystemExit(1)

    tasks = []
    records_created = []
    errors = []
    skipped_old = 0
    skipped_duplicates = 0
    processed_cache = _prune_processed_cache(_load_processed_cache())

    for chat_id, config in CHAT_CONFIG.items():
        messages = get_messages(token, chat_id, limit=10)
        if not messages:
            continue

        for msg in messages:
            if not is_recent_message(msg):
                skipped_old += 1
                continue

            message_id = str(msg.get("message_id", "")).strip()
            if message_id and message_id in processed_cache:
                skipped_duplicates += 1
                continue

            sender = msg.get("sender", {})
            if sender.get("sender_type") == "app":
                continue

            sender_id = sender.get("id", "")
            if not sender_id:
                continue

            text = extract_message_text(msg)
            if not text:
                continue

            try:
                reply, record_fields = process_group_message(chat_id, sender_id, text)
            except Exception as exc:
                errors.append(f"process failed: {exc}")
                continue

            send_success: Optional[bool] = None
            reply_mode = ""
            write_success: Optional[bool] = None
            write_result = ""

            if not reply and not record_fields:
                if message_id:
                    processed_cache[message_id] = int(datetime.now().timestamp() * 1000)
                continue

            if reply:
                send_success, reply_mode = send_reply(
                    token=token,
                    chat_id=chat_id,
                    content=reply,
                    reply_to_message_id=message_id,
                )
            if record_fields:
                write_success, write_result = write_record(token, record_fields)

            task_info = {
                "chat_id": chat_id,
                "chat_name": config.get("name", chat_id),
                "sender_id": sender_id,
                "message_id": message_id,
                "question": text[:80],
                "matched": bool(reply),
                "send_success": send_success,
                "reply_mode": reply_mode,
                "write_success": write_success,
            }

            if write_success is True:
                task_info["record_id"] = write_result
                records_created.append(write_result)
            elif record_fields:
                task_info["write_error"] = write_result
                errors.append(f"write failed: {write_result}")

            tasks.append(task_info)
            if message_id:
                processed_cache[message_id] = int(datetime.now().timestamp() * 1000)

    _save_processed_cache(processed_cache)
    result = {
        "timestamp": int(datetime.now().timestamp() * 1000),
        "window_minutes": PROCESS_WINDOW_MINUTES,
        "total_processed": len(tasks),
        "skipped_old": skipped_old,
        "skipped_duplicates": skipped_duplicates,
        "records_created": records_created,
        "errors": errors,
        "tasks": tasks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
