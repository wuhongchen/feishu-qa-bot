#!/usr/bin/env python3
"""Core message handler for Feishu QA bot."""

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from env_bootstrap import load_project_env  # noqa: E402
from intent_classifier_v5 import (  # noqa: E402
    IntentResult,
    classify_intent,
    get_all_intents,
    get_answer,
    reload_intents,
)
from ai_search_fallback import build_ai_search_rule  # noqa: E402
from intent_backlog import record_unmatched_question  # noqa: E402

load_project_env(__file__)


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _should_append_meta(rule: dict) -> bool:
    if not _parse_bool(os.getenv("QA_REPLY_APPEND_META", "true"), True):
        return False
    intent = str((rule or {}).get("intent", "")).strip()
    if intent.startswith("ai_search_"):
        return _parse_bool(os.getenv("QA_AI_REPLY_APPEND_META", "false"), False)
    return True


QA_BITABLE_TOKEN = os.getenv("QA_BITABLE_TOKEN", "")
QA_TABLE_ID = os.getenv("QA_TABLE_ID", "")
_default_chats = "oc_839e988db57d1c706a89ba2bc3667dda,oc_004b8ddf9f113e547f4b13d814cd4619"
QA_CHAT_IDS = [
    cid.strip() for cid in os.getenv("QA_CHAT_ID", _default_chats).split(",") if cid.strip()
]
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")
MAX_ROUNDS = max(0, int(os.getenv("QA_MAX_ROUNDS", "0")))
SESSION_TTL_MINUTES = max(5, int(os.getenv("QA_SESSION_TTL_MINUTES", "30")))
ENABLE_NPS = _parse_bool(os.getenv("QA_ENABLE_NPS"), True)
APPEND_INTENT_NOTE = _parse_bool(os.getenv("QA_APPEND_INTENT_NOTE"), True)
APPEND_INTENT_NOTE_ON_UNMATCH = _parse_bool(os.getenv("QA_APPEND_INTENT_NOTE_ON_UNMATCH"), False)
NPS_EXCLUDE_INTENTS = {"thanks", "bot_status"}
NPS_EXCLUDE_INTENTS.update({"ai_search_fallback", "ai_search_no_result", "ai_search_blocked", "ai_search_unavailable"})

# Backward-compat symbol.
QA_RULES = get_all_intents()
_INTENT_NAME_MAP = {
    str(item.get("id", "")).strip(): str(item.get("name", "")).strip()
    for item in QA_RULES
    if str(item.get("id", "")).strip()
}

_sessions: Dict[str, dict] = {}


def _get_session_key(chat_id: str, user_id: str) -> str:
    return f"{chat_id}_{user_id}"


def _is_nps_score(text: str) -> Optional[int]:
    stripped = text.strip()
    if not re.fullmatch(r"\d{1,2}", stripped):
        return None
    score = int(stripped)
    if 0 <= score <= 10:
        return score
    return None


def _get_or_create_session(chat_id: str, user_id: str) -> Tuple[dict, bool]:
    key = _get_session_key(chat_id, user_id)

    if key in _sessions:
        session = _sessions[key]
        last_active = session.get("last_active", datetime.now())
        if isinstance(last_active, str):
            last_active = datetime.fromisoformat(last_active)

        if datetime.now() - last_active > timedelta(minutes=SESSION_TTL_MINUTES):
            del _sessions[key]
        else:
            session["last_active"] = datetime.now()
            return session, False

    session = {
        "session_id": f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}_{user_id[-8:]}",
        "chat_id": chat_id,
        "user_id": user_id,
        "start_time": datetime.now(),
        "last_active": datetime.now(),
        "rounds": 0,
        "questions": [],
        "answers": [],
        "status": "active",
        "nps_pending": False,
        "nps_score": None,
    }
    _sessions[key] = session
    return session, True


def match_question(question: str, intent_result: Optional[IntentResult] = None) -> Optional[dict]:
    result = intent_result or classify_intent(question)
    answer_data = get_answer(result)
    if answer_data is None:
        return None

    return {
        "answer": answer_data["text"],
        "source": f"知识库/{result.intent_id}",
        "confidence": answer_data["confidence"],
        "intent": result.intent_id,
        "links": answer_data.get("links", []),
    }


def match_question_with_fallback(
    question: str,
    intent_result: Optional[IntentResult] = None,
    chat_id: str = "",
) -> Optional[dict]:
    """Try local intent first, then optional scoped AI search fallback."""
    local_rule = match_question(question, intent_result=intent_result)
    if local_rule:
        return local_rule
    return build_ai_search_rule(question, chat_id=chat_id)


def generate_fallback() -> str:
    """Backward-compat fallback text."""
    return "我暂时没理解这个问题，可以换个说法，或补充更具体的关键词。"


def generate_reply(
    rule: dict,
    question: str = "",
    round_num: int = 1,
    is_new_session: bool = False,
    add_nps_prompt: bool = False,
) -> str:
    del round_num, is_new_session
    reply = f"{rule['answer']}"

    links = rule.get("links", [])
    if links:
        reply += "\n\n相关链接："
        for link in links:
            reply += f"\n- {link['name']}: {link['url']}"

    if _should_append_meta(rule):
        reply += f"\n\n---\n来源: {rule['source']}\n置信度: {int(rule['confidence'] * 100)}%"

    if add_nps_prompt and question:
        short_question = question[:40] + "..." if len(question) > 40 else question
        reply += (
            "\n\n请为本次回答打分（0-10）："
            f"\n问题: {short_question}"
            "\n直接回复数字即可，例如 8"
        )

    return reply


def _get_intent_name(intent_id: str) -> str:
    intent_id = str(intent_id or "").strip()
    if not intent_id:
        return ""
    return _INTENT_NAME_MAP.get(intent_id) or intent_id


def build_intent_note(intent_result: Optional[IntentResult]) -> str:
    """Append a concise intent hint without changing the main QA flow."""
    if not APPEND_INTENT_NOTE:
        return ""

    if not intent_result:
        if APPEND_INTENT_NOTE_ON_UNMATCH:
            return "补充：这条消息暂未命中明确意图，已按常规问答流程处理。"
        return ""

    intent_name = _get_intent_name(intent_result.intent_id)
    if intent_name:
        return f"补充：我理解你的问题更接近「{intent_name}」。"
    return ""


def build_record_fields(
    session: dict,
    question: str,
    answer: str,
    rule: Optional[dict] = None,
    matched: bool = True,
    nps_score: Optional[int] = None,
    nps_requested: bool = False,
) -> dict:
    timestamp = int(datetime.now().timestamp() * 1000)

    fields = {
        "会话ID": session["session_id"],
        "提问时间": timestamp,
        "问题内容": question,
        "回答内容": answer,
        "轮次": session["rounds"],
        "状态": "进行中",
        "是否解决": None,
        "结束时间": None,
        "NPS状态": "已评分" if nps_score is not None else "待评分" if nps_requested else None,
    }

    if nps_score is not None:
        fields["NPS评分"] = nps_score

    if rule:
        fields["知识来源"] = rule["source"]
        fields["置信度"] = rule["confidence"]
        fields["意图分类"] = rule.get("intent")

    return fields


def _handle_nps_message(session: dict, message: str) -> Tuple[Optional[str], Optional[dict]]:
    if not ENABLE_NPS or not session.get("nps_pending"):
        return None, None

    nps_score = _is_nps_score(message)
    if nps_score is None:
        return None, None

    session["nps_pending"] = False
    session["nps_score"] = nps_score

    if nps_score >= 9:
        follow_up = "感谢高分反馈，我们会继续保持。"
    elif nps_score >= 7:
        follow_up = "谢谢反馈，我们会继续优化答复质量。"
    else:
        follow_up = "收到你的反馈，我们会尽快改进，有需要也可以直接@助教。"

    record_fields = build_record_fields(
        session=session,
        question=f"NPS评分: {nps_score}",
        answer=follow_up,
        matched=False,
        nps_score=nps_score,
        nps_requested=False,
    )
    return follow_up, record_fields


def process_group_message(
    chat_id: str,
    sender_id: str,
    message: str,
    mentions: Optional[list] = None,
) -> Tuple[Optional[str], Optional[dict]]:
    """Main message processing function."""
    del mentions  # Reserved for future mention-only mode.

    if chat_id not in QA_CHAT_IDS:
        return None, None

    session, is_new_session = _get_or_create_session(chat_id, sender_id)

    nps_reply, nps_record = _handle_nps_message(session, message)
    if nps_reply:
        return nps_reply, nps_record

    # Simplified stable flow: local intent first, fallback second.
    intent_result = classify_intent(message)
    local_rule = match_question(message, intent_result=intent_result)
    if local_rule:
        rule = local_rule
    else:
        try:
            record_unmatched_question(
                question=message,
                chat_id=chat_id,
                sender_id=sender_id,
            )
        except Exception:
            pass
        rule = build_ai_search_rule(message, chat_id=chat_id)
        if not rule:
            session["questions"].append(message)
            unmatched_answer = "未命中意图，且当前群未开启 AI 兜底，已记录到问题库待补充。"
            record_fields = build_record_fields(
                session,
                question=message,
                answer=unmatched_answer,
                matched=False,
                nps_requested=False,
            )
            record_fields["状态"] = "未命中"
            record_fields["知识来源"] = "知识库/未命中"
            record_fields["置信度"] = 0.0
            record_fields["意图分类"] = "unmatched"
            return None, record_fields

    session["rounds"] += 1

    if MAX_ROUNDS > 0 and session["rounds"] > MAX_ROUNDS:
        session["status"] = "manual_required"
        transfer_reply = "当前问题轮次较多，已转人工，请稍等助教跟进。"
        if ADMIN_USER_ID:
            transfer_reply += f"\n<at id=\"{ADMIN_USER_ID}\"></at>"
        fields = build_record_fields(session, message, transfer_reply, matched=False)
        return transfer_reply, fields

    session["questions"].append(message)

    is_first_qa_round = session["rounds"] == 1
    add_nps_prompt = ENABLE_NPS and is_first_qa_round and rule.get("intent") not in NPS_EXCLUDE_INTENTS
    if add_nps_prompt:
        session["nps_pending"] = True

    reply = generate_reply(
        rule,
        question=message,
        round_num=session["rounds"],
        is_new_session=is_new_session,
        add_nps_prompt=add_nps_prompt,
    )
    intent_note = build_intent_note(intent_result)
    if intent_note:
        reply = f"{reply}\n\n{intent_note}"
    session["answers"].append(rule["answer"])

    record_fields = build_record_fields(
        session,
        question=message,
        answer=rule["answer"],
        rule=rule,
        matched=True,
        nps_requested=add_nps_prompt,
    )
    if rule.get("intent") in {"ai_search_fallback", "ai_search_no_result", "ai_search_unavailable"}:
        record_fields["状态"] = "待补充意图"
    return reply, record_fields


def end_session_manually(chat_id: str, user_id: str, resolved: bool = True) -> bool:
    del resolved
    key = _get_session_key(chat_id, user_id)
    if key in _sessions:
        del _sessions[key]
        return True
    return False


def process_group_mention(
    chat_id: str,
    sender_id: str,
    message: str,
    mentions: Optional[list] = None,
) -> Tuple[Optional[str], Optional[dict], str, str]:
    reply, record_fields = process_group_message(chat_id, sender_id, message, mentions)
    return reply, record_fields, QA_BITABLE_TOKEN, QA_TABLE_ID


def reload_intent_library() -> None:
    reload_intents()
    global QA_RULES
    global _INTENT_NAME_MAP
    QA_RULES = get_all_intents()
    _INTENT_NAME_MAP = {
        str(item.get("id", "")).strip(): str(item.get("name", "")).strip()
        for item in QA_RULES
        if str(item.get("id", "")).strip()
    }
    print("[Handler] Intent library reloaded")


if __name__ == "__main__":
    tests = [
        ("oc_839e988db57d1c706a89ba2bc3667dda", "作业什么时候截止？"),
        ("oc_004b8ddf9f113e547f4b13d814cd4619", "作业怎么交"),
        ("oc_004b8ddf9f113e547f4b13d814cd4619", "8"),
    ]
    print("=== Group QA Handler Smoke Test ===")
    for chat_id, question in tests:
        reply, fields = process_group_message(chat_id, "ou_test_user", question)
        print(f"Q: {question}")
        print(f"A: {reply}")
        print(f"Fields: {fields}\n")
