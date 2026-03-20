#!/usr/bin/env python3
"""Unmatched question backlog for intent library improvement."""

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BACKLOG_FILE = BASE_DIR / "backups" / "intent_backlog.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_question(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _backlog_path() -> Path:
    raw = os.getenv("QA_INTENT_BACKLOG_FILE", "").strip()
    if not raw:
        return DEFAULT_BACKLOG_FILE
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (BASE_DIR / p).resolve()


def _load_backlog(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"version": 1, "updated_at": _now_iso(), "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"version": 1, "updated_at": _now_iso(), "items": []}
        items = payload.get("items", [])
        if not isinstance(items, list):
            items = []
        return {"version": 1, "updated_at": payload.get("updated_at", _now_iso()), "items": items}
    except Exception:
        return {"version": 1, "updated_at": _now_iso(), "items": []}


def _write_atomic(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


def record_unmatched_question(question: str, chat_id: str, sender_id: str) -> Tuple[str, int, str]:
    """
    Record unmatched question into local backlog.

    Returns:
      (candidate_id, hit_count, backlog_path)
    """
    normalized = _normalize_question(question)
    if not normalized:
        return "", 0, str(_backlog_path())

    path = _backlog_path()
    payload = _load_backlog(path)
    items: List[Dict[str, object]] = payload.get("items", [])

    now = _now_iso()
    candidate_id = ""
    hit_count = 1

    for row in items:
        if not isinstance(row, dict):
            continue
        if str(row.get("normalized_question", "")).strip() != normalized:
            continue

        candidate_id = str(row.get("id", "")).strip() or f"intent_cand_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]}"
        row["id"] = candidate_id
        row["question"] = str(question).strip()
        row["last_seen"] = now
        row["count"] = int(row.get("count", 0)) + 1
        hit_count = int(row["count"])

        chats = row.get("chat_ids", [])
        users = row.get("sender_ids", [])
        if not isinstance(chats, list):
            chats = []
        if not isinstance(users, list):
            users = []
        if chat_id and chat_id not in chats:
            chats.append(chat_id)
        if sender_id and sender_id not in users:
            users.append(sender_id)
        row["chat_ids"] = chats[:20]
        row["sender_ids"] = users[:20]
        break

    if not candidate_id:
        candidate_id = f"intent_cand_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]}"
        items.append(
            {
                "id": candidate_id,
                "question": str(question).strip(),
                "normalized_question": normalized,
                "status": "pending",
                "count": 1,
                "first_seen": now,
                "last_seen": now,
                "chat_ids": [chat_id] if chat_id else [],
                "sender_ids": [sender_id] if sender_id else [],
                "suggested_answer": "",
            }
        )

    payload["items"] = items
    payload["updated_at"] = now
    _write_atomic(path, payload)
    return candidate_id, hit_count, str(path)
