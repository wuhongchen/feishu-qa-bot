#!/usr/bin/env python3
"""Poll Feishu group messages and route to QA handler."""

import base64
import json
import os
import re
import sys
import fcntl
import atexit
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, TextIO, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from env_bootstrap import load_project_env  # noqa: E402
from bitable_helper import create_record, resolve_app_and_table  # noqa: E402
from feishu_app_auth import FeishuAppAuth  # noqa: E402
from group_qa_handler import process_group_message  # noqa: E402
from ai_search_fallback import build_ai_image_rule  # noqa: E402

load_project_env(__file__)


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


PROCESS_WINDOW_MINUTES = max(1, int(os.getenv("QA_PROCESS_WINDOW_MINUTES", "5")))
FETCH_PAGE_SIZE = max(1, min(50, int(os.getenv("QA_FETCH_PAGE_SIZE", "20"))))
ENABLE_THREAD_MESSAGES = _parse_bool(os.getenv("QA_ENABLE_THREAD_MESSAGES", "true"), True)
THREAD_FETCH_MAX = max(1, min(50, int(os.getenv("QA_THREAD_FETCH_MAX", "20"))))
THREAD_PAGE_SIZE = max(1, min(50, int(os.getenv("QA_THREAD_PAGE_SIZE", "50"))))
APP_TOKEN = os.getenv("QA_BITABLE_TOKEN", "").strip()
TABLE_ID = os.getenv("QA_TABLE_ID", "").strip()
TABLE_NAME = os.getenv("QA_TABLE_NAME", "").strip()
BITABLE_BASE_URL = os.getenv("QA_BITABLE_BASE_URL", "").strip()
DEDUP_CACHE_FILE = Path(os.getenv("QA_MSG_DEDUP_CACHE_FILE", "/tmp/feishu_qa_processed_messages.json"))
DEDUP_TTL_MINUTES = max(PROCESS_WINDOW_MINUTES + 1, int(os.getenv("QA_MSG_DEDUP_TTL_MINUTES", "120")))
POLLER_LOCK_FILE = Path(os.getenv("QA_POLLER_LOCK_FILE", "/tmp/feishu_qa_poller.lock"))
ADMIN_ALERT_STATE_FILE = Path(os.getenv("QA_ADMIN_ALERT_STATE_FILE", "/tmp/feishu_qa_admin_alert_state.json"))
ADMIN_ALERT_CHAT_ID = str(os.getenv("QA_ADMIN_ALERT_CHAT_ID", "")).strip()
ADMIN_ALERT_ENABLED = _parse_bool(os.getenv("QA_ADMIN_ALERT_ENABLED", "true"), True)
ADMIN_ALERT_THRESHOLD = max(1, int(os.getenv("QA_ADMIN_ALERT_THRESHOLD", "3")))
ADMIN_ALERT_COOLDOWN_SECONDS = max(60, int(os.getenv("QA_ADMIN_ALERT_COOLDOWN_SECONDS", "1800")))
ENABLE_IMAGE_UNDERSTANDING = _parse_bool(os.getenv("QA_ENABLE_IMAGE_UNDERSTANDING", "true"), True)
IMAGE_MAX_BYTES = max(1024, int(os.getenv("QA_IMAGE_MAX_BYTES", "5000000")))
MESSAGE_TRIGGER_MODE = (os.getenv("QA_MESSAGE_TRIGGER_MODE", "all").strip().lower() or "all")
if MESSAGE_TRIGGER_MODE not in {"all", "mention"}:
    MESSAGE_TRIGGER_MODE = "all"

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


def _acquire_poller_lock() -> Optional[TextIO]:
    """Global poller lock across cron/manual/daemon runners."""
    try:
        POLLER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = POLLER_LOCK_FILE.open("a+", encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] acquire poller lock open failed: {exc}", file=sys.stderr)
        return None

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    except Exception as exc:
        print(f"[WARN] acquire poller lock flock failed: {exc}", file=sys.stderr)
        try:
            fh.close()
        except Exception:
            pass
        return None

    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except Exception:
        pass
    return fh


def _release_poller_lock(fh: Optional[TextIO]) -> None:
    if fh is None:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def _load_admin_alert_state() -> Dict[str, int]:
    try:
        if not ADMIN_ALERT_STATE_FILE.exists():
            return {"failure_streak": 0, "last_alert_ts": 0}
        payload = json.loads(ADMIN_ALERT_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"failure_streak": 0, "last_alert_ts": 0}
        return {
            "failure_streak": int(payload.get("failure_streak", 0) or 0),
            "last_alert_ts": int(payload.get("last_alert_ts", 0) or 0),
        }
    except Exception:
        return {"failure_streak": 0, "last_alert_ts": 0}


def _save_admin_alert_state(state: Dict[str, int]) -> None:
    try:
        ADMIN_ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADMIN_ALERT_STATE_FILE.write_text(
            json.dumps(
                {
                    "failure_streak": int(state.get("failure_streak", 0) or 0),
                    "last_alert_ts": int(state.get("last_alert_ts", 0) or 0),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[WARN] save admin alert state failed: {exc}", file=sys.stderr)


def _build_admin_alert_text(result: Dict[str, object]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    errors_raw = result.get("errors", [])
    errors = errors_raw if isinstance(errors_raw, list) else [str(errors_raw)]
    preview = "；".join([str(e).strip() for e in errors[:2] if str(e).strip()])[:260]
    total_processed = int(result.get("total_processed", 0) or 0)
    skipped_old = int(result.get("skipped_old", 0) or 0)
    return (
        f"QA Bot 告警（管理员）\n"
        f"时间：{now_str}\n"
        f"连续异常轮次已达阈值（{ADMIN_ALERT_THRESHOLD}）。\n"
        f"本轮处理：{total_processed}，跳过旧消息：{skipped_old}\n"
        f"错误摘要：{preview or '请查看 /tmp/feishu_qa_cycle.log'}"
    )


def _notify_admin_if_needed(token: str, result: Dict[str, object]) -> None:
    if not ADMIN_ALERT_ENABLED:
        return

    state = _load_admin_alert_state()
    now_ts = int(datetime.now().timestamp())
    error_count = len(result.get("errors", [])) if isinstance(result.get("errors", []), list) else 0

    if error_count <= 0:
        if state.get("failure_streak", 0) != 0:
            state["failure_streak"] = 0
            _save_admin_alert_state(state)
        return

    state["failure_streak"] = int(state.get("failure_streak", 0) or 0) + 1
    last_alert_ts = int(state.get("last_alert_ts", 0) or 0)
    cooldown_ok = now_ts - last_alert_ts >= ADMIN_ALERT_COOLDOWN_SECONDS
    threshold_ok = state["failure_streak"] >= ADMIN_ALERT_THRESHOLD

    if threshold_ok and cooldown_ok and ADMIN_ALERT_CHAT_ID:
        text = _build_admin_alert_text(result)
        success, mode = send_reply(token=token, chat_id=ADMIN_ALERT_CHAT_ID, content=text, reply_to_message_id="")
        if success:
            state["last_alert_ts"] = now_ts
            print(f"[INFO] admin alert sent via {mode} to {ADMIN_ALERT_CHAT_ID}", file=sys.stderr)
        else:
            print("[WARN] admin alert send failed", file=sys.stderr)

    _save_admin_alert_state(state)
    try:
        fh.close()
    except Exception:
        pass


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


def get_thread_messages(token: str, thread_id: str, limit: int = 50) -> List[dict]:
    if not thread_id:
        return []
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "container_id_type": "thread",
        "container_id": thread_id,
        "page_size": max(1, min(50, limit)),
        "sort_type": "ByCreateTimeDesc",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)
        payload = response.json()
        if payload.get("code") == 0:
            return payload.get("data", {}).get("items", [])
    except Exception as exc:
        print(f"[WARN] get_thread_messages failed for {thread_id}: {exc}", file=sys.stderr)
    return []


def _create_time_ms(msg: dict) -> int:
    try:
        return int(msg.get("create_time") or 0)
    except Exception:
        return 0


def fetch_chat_and_thread_messages(token: str, chat_id: str, limit: int = FETCH_PAGE_SIZE) -> List[dict]:
    chat_messages = get_messages(token, chat_id, limit=limit)
    if not chat_messages:
        return []

    merged: Dict[str, dict] = {}
    for msg in chat_messages:
        message_id = str(msg.get("message_id", "")).strip()
        if message_id:
            merged[message_id] = msg

    if ENABLE_THREAD_MESSAGES:
        thread_ids: List[str] = []
        seen_thread_ids = set()
        for msg in chat_messages:
            thread_id = str(msg.get("thread_id", "")).strip()
            if not thread_id or thread_id in seen_thread_ids:
                continue
            seen_thread_ids.add(thread_id)
            thread_ids.append(thread_id)
            if len(thread_ids) >= THREAD_FETCH_MAX:
                break

        for thread_id in thread_ids:
            for msg in get_thread_messages(token, thread_id, limit=THREAD_PAGE_SIZE):
                message_id = str(msg.get("message_id", "")).strip()
                if not message_id:
                    continue
                merged[message_id] = msg

    # Process old -> new so topic follow-up replies keep natural order.
    return sorted(merged.values(), key=_create_time_ms)


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


def _extract_post_text_and_mentions(body: Dict[str, object]) -> Tuple[str, List[dict], str]:
    lines: List[str] = []
    mentions: List[dict] = []
    image_key = ""

    title = str(body.get("title", "")).strip()
    if title:
        lines.append(title)

    blocks = body.get("content", [])
    if not isinstance(blocks, list):
        return "\n".join(lines).strip(), mentions, image_key

    for para in blocks:
        if not isinstance(para, list):
            continue
        segs: List[str] = []
        for node in para:
            if not isinstance(node, dict):
                continue
            tag = str(node.get("tag", "")).strip().lower()
            if tag == "text":
                segs.append(str(node.get("text", "")))
                continue
            if tag in {"a", "link"}:
                segs.append(str(node.get("text", "")).strip() or str(node.get("href", "")).strip())
                continue
            if tag == "at":
                mentions.append(node)
                segs.append(str(node.get("text", "")).strip() or "@用户")
                continue
            if tag == "img":
                if not image_key:
                    image_key = str(node.get("image_key") or node.get("file_key") or "").strip()
                continue
            # Keep fallback text if provided by other rich-text nodes.
            fallback = str(node.get("text", "")).strip()
            if fallback:
                segs.append(fallback)
        line = "".join(segs).strip()
        if line:
            lines.append(line)

    return "\n".join(lines).strip(), mentions, image_key


def parse_message_payload(msg: dict) -> Tuple[str, str, str, List[dict]]:
    """Return (msg_type, text, image_key, mentions)."""
    msg_type = str(msg.get("msg_type", "")).strip().lower()
    try:
        body = json.loads(msg.get("body", {}).get("content", "{}"))
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    text = str(body.get("text", "")).strip()
    image_key = ""
    mentions = body.get("mentions", [])
    if not isinstance(mentions, list):
        mentions = []
    if msg_type == "post":
        post_text, post_mentions, post_image_key = _extract_post_text_and_mentions(body)
        text = post_text
        if post_mentions:
            mentions = post_mentions
        if post_image_key:
            image_key = post_image_key
    if msg_type == "image":
        image_key = str(body.get("image_key") or body.get("file_key") or "").strip()
    if msg_type == "post" and image_key:
        # post 图文（或仅图片）统一按图片消息处理，优先走视觉兜底。
        # 某些客户端会附带无意义符号（如 "’"），这里清理掉避免污染提示词。
        if text and not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
            text = ""
        msg_type = "image"
    return msg_type, text, image_key, mentions


def _is_mention_message(text: str, mentions: List[dict]) -> bool:
    if mentions:
        return True
    lowered = str(text or "").lower()
    if "<at " in lowered:
        return True
    return str(text or "").strip().startswith("@")


def _should_handle_message(msg_type: str, text: str, mentions: List[dict]) -> bool:
    if MESSAGE_TRIGGER_MODE == "mention" and not _is_mention_message(text, mentions):
        return False
    if msg_type in {"text", "image", "post"}:
        return True
    return False


def _extract_sender(msg: dict) -> Tuple[str, str]:
    """Return (sender_type, sender_id). sender_id prefers open_id string."""
    sender = msg.get("sender", {})
    if not isinstance(sender, dict):
        return "", ""

    sender_type = str(sender.get("sender_type", "")).strip().lower()
    raw_id = sender.get("id", "")
    if isinstance(raw_id, dict):
        sender_id = (
            str(raw_id.get("open_id", "")).strip()
            or str(raw_id.get("user_id", "")).strip()
            or str(raw_id.get("union_id", "")).strip()
            or str(raw_id.get("app_id", "")).strip()
        )
    else:
        sender_id = str(raw_id).strip()
    return sender_type, sender_id


def fetch_image_attachment(
    token: str,
    message_id: str,
    image_key: str,
) -> Tuple[Optional[Dict[str, str]], str]:
    if not message_id or not image_key:
        return None, "图片消息缺少 message_id 或 image_key"

    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"type": "image"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
    except Exception as exc:
        return None, f"下载图片资源失败: {exc}"

    if response.status_code != 200:
        text = response.text[:200] if response.text else ""
        return None, f"下载图片资源失败: status={response.status_code} body={text}"

    raw = response.content or b""
    if not raw:
        return None, "图片资源为空"
    if len(raw) > IMAGE_MAX_BYTES:
        return None, f"图片过大（{len(raw)} bytes），超过限制 {IMAGE_MAX_BYTES} bytes"

    mime_type = (response.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip().lower()
    if not mime_type.startswith("image/"):
        mime_type = "image/jpeg"

    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
    }
    file_ext = ext_map.get(mime_type, "jpg")
    file_name = f"{image_key}.{file_ext}"

    attachment = {
        "type": "image",
        "name": file_name,
        "mimeType": mime_type,
        "mime_type": mime_type,
        "encoding": "base64",
        "fileName": file_name,
        "file_name": file_name,
        "content": base64.b64encode(raw).decode("ascii"),
    }
    return attachment, ""


def build_image_record_fields(
    chat_id: str,
    sender_id: str,
    image_key: str,
    answer_text: str,
    rule: Dict[str, object],
) -> dict:
    timestamp = int(datetime.now().timestamp() * 1000)
    safe_sender = sender_id[-8:] if sender_id else "unknown"
    safe_chat = chat_id[-8:] if chat_id else "unknown"
    return {
        "会话ID": f"img_{safe_chat}_{safe_sender}_{timestamp}",
        "提问者": sender_id,
        "提问时间": timestamp,
        "问题内容": f"[图片消息] image_key={image_key}",
        "回答内容": answer_text,
        "轮次": 1,
        "状态": "待补充意图",
        "是否解决": None,
        "结束时间": timestamp,
        "NPS状态": None,
        "知识来源": str(rule.get("source", "OpenClaw图片识别")),
        "置信度": float(rule.get("confidence", 0.65)),
        "意图分类": str(rule.get("intent", "ai_image_fallback")),
    }


def build_broadcast_text(
    window_minutes: int,
    total_processed: int,
    skipped_old: int,
    records_created_count: int,
    error_count: int,
) -> str:
    if error_count > 0:
        return f"本轮巡检已完成，发现 {error_count} 个异常，已记录待处理，请管理员关注。"

    if total_processed <= 0:
        if skipped_old > 0:
            return f"本轮巡检完成，最近 {window_minutes} 分钟没有新的问答消息。"
        return f"本轮巡检完成，最近 {window_minutes} 分钟暂无需要处理的消息。"

    if records_created_count > 0:
        return f"本轮共处理 {total_processed} 条问答消息，已新增 {records_created_count} 条记录。"
    return f"本轮共处理 {total_processed} 条问答消息，机器人已完成回复。"


def main() -> None:
    lock_fh = _acquire_poller_lock()
    if lock_fh is None:
        result = {
            "timestamp": int(datetime.now().timestamp() * 1000),
            "window_minutes": PROCESS_WINDOW_MINUTES,
            "total_processed": 0,
            "skipped_old": 0,
            "skipped_duplicates": 0,
            "records_created": [],
            "errors": [],
            "tasks": [],
            "broadcast": {
                "title": "QA 机器人巡检播报",
                "text": "上一轮仍在执行中，本轮跳过。",
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    atexit.register(_release_poller_lock, lock_fh)

    token = get_token()
    if not token:
        print(json.dumps({"error": "获取 tenant_access_token 失败"}, ensure_ascii=False))
        raise SystemExit(1)

    tasks = []
    records_created = []
    errors = []
    skipped_old = 0
    skipped_duplicates = 0
    skipped_already_replied = 0
    processed_cache = _prune_processed_cache(_load_processed_cache())

    for chat_id, config in CHAT_CONFIG.items():
        messages = fetch_chat_and_thread_messages(token, chat_id, limit=FETCH_PAGE_SIZE)
        if not messages:
            continue

        replied_message_ids = set()
        for row in messages:
            sender_type_row, _ = _extract_sender(row)
            if sender_type_row == "user":
                continue
            parent_id = str(row.get("parent_id") or row.get("root_id") or "").strip()
            if parent_id:
                replied_message_ids.add(parent_id)

        for msg in messages:
            if not is_recent_message(msg):
                skipped_old += 1
                continue

            message_id = str(msg.get("message_id", "")).strip()
            if message_id and message_id in processed_cache:
                skipped_duplicates += 1
                continue
            if message_id and message_id in replied_message_ids:
                skipped_already_replied += 1
                processed_cache[message_id] = int(datetime.now().timestamp() * 1000)
                continue

            sender_type, sender_id = _extract_sender(msg)
            if sender_type and sender_type != "user":
                continue
            # Ignore app-id style sender just in case sender_type is missing.
            if sender_id.startswith("cli_"):
                continue
            if not sender_id:
                continue

            msg_type, text, image_key, mentions = parse_message_payload(msg)
            if not _should_handle_message(msg_type, text, mentions):
                continue
            try:
                if msg_type == "image" and ENABLE_IMAGE_UNDERSTANDING:
                    attachment, fetch_err = fetch_image_attachment(
                        token=token,
                        message_id=message_id,
                        image_key=image_key,
                    )
                    if not attachment:
                        reply = "图片收到了，但暂时无法读取图片内容。请稍后重试，或补充文字描述我先帮你处理。"
                        record_fields = build_image_record_fields(
                            chat_id=chat_id,
                            sender_id=sender_id,
                            image_key=image_key or "unknown",
                            answer_text=reply,
                            rule={
                                "source": "OpenClaw图片识别/资源读取失败",
                                "confidence": 0.2,
                                "intent": "ai_image_unavailable",
                            },
                        )
                        if fetch_err:
                            errors.append(f"image fetch failed: {fetch_err}")
                    else:
                        image_rule = build_ai_image_rule(
                            question=text,
                            attachments=[attachment],
                            chat_id=chat_id,
                        )
                        if str(image_rule.get("intent", "")).strip() == "ai_image_unavailable":
                            debug_reason = str(image_rule.get("debug_reason", "")).strip()
                            if debug_reason:
                                print(
                                    f"[WARN] image unavailable[{message_id}]: {debug_reason}",
                                    file=sys.stderr,
                                )
                        reply = str(image_rule.get("answer", "")).strip()
                        record_fields = build_image_record_fields(
                            chat_id=chat_id,
                            sender_id=sender_id,
                            image_key=image_key or "unknown",
                            answer_text=reply,
                            rule=image_rule,
                        )
                else:
                    if not text:
                        continue
                    reply, record_fields = process_group_message(chat_id, sender_id, text, mentions=mentions)
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
                "msg_type": msg_type or "unknown",
                "question": (text if text else f"[{msg_type}]")[:80],
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
        "skipped_already_replied": skipped_already_replied,
        "records_created": records_created,
        "errors": errors,
        "tasks": tasks,
        "broadcast": {
            "title": "QA 机器人巡检播报",
            "text": build_broadcast_text(
                window_minutes=PROCESS_WINDOW_MINUTES,
                total_processed=len(tasks),
                skipped_old=skipped_old,
                records_created_count=len(records_created),
                error_count=len(errors),
            ),
        },
    }
    _notify_admin_if_needed(token, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
