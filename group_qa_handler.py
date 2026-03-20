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
NPS_EXCLUDE_INTENTS = {"thanks", "bot_status"}
NPS_EXCLUDE_INTENTS.update({"ai_search_fallback", "ai_search_no_result", "ai_search_blocked", "ai_search_unavailable"})
NPS_EXCLUDE_INTENTS.update({"general_qa_fallback", "unmatched_pending"})

# Backward-compat symbol.
QA_RULES = get_all_intents()

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


def match_question(question: str) -> Optional[dict]:
    result = classify_intent(question)
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


def match_question_with_fallback(question: str) -> Optional[dict]:
    """Try local intent first, then optional scoped AI search fallback."""
    local_rule = match_question(question)
    if local_rule:
        return local_rule
    return build_ai_search_rule(question)


def generate_fallback() -> str:
    """Backward-compat fallback text."""
    return "我暂时没理解这个问题，可以换个说法，或补充更具体的关键词。"


def build_general_qa_rule(question: str, backlog_id: str = "", backlog_count: int = 0) -> dict:
    """Build always-respond fallback when no intent/search answer is available."""
    question = str(question or "").strip()
    short_question = question[:60] + "..." if len(question) > 60 else question
    backlog_tip = ""
    if backlog_id:
        backlog_tip = f"\n我已将该问题加入意图补充库（{backlog_id}，累计 {max(1, backlog_count)} 次）。"

    answer = (
        f"我先按普通问答处理：你问的是「{short_question}」。\n"
        "当前意图库还没有这条标准答案，我先给你通用建议：\n"
        "1) 补充具体场景（课程名/任务名/时间点）\n"
        "2) 给出你卡住的步骤或报错信息\n"
        "3) 我会继续基于你补充的信息给出更精确答复"
        f"{backlog_tip}\n"
        "如果你愿意，我可以下一条直接按“可执行步骤清单”格式回答。"
    )
    return {
        "answer": answer,
        "source": "普通问答/待补充意图",
        "confidence": 0.42,
        "intent": "general_qa_fallback",
        "links": [],
    }


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

    reply += f"\n\n---\n来源: {rule['source']}\n置信度: {int(rule['confidence'] * 100)}%"

    if add_nps_prompt and question:
        short_question = question[:40] + "..." if len(question) > 40 else question
        reply += (
            "\n\n请为本次回答打分（0-10）："
            f"\n问题: {short_question}"
            "\n直接回复数字即可，例如 8"
        )

    return reply


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

    # 1) Local intent first.
    local_rule = match_question(message)
    if local_rule:
        rule = local_rule
    else:
        # 2) Record unmatched question for intent-library evolution.
        backlog_id = ""
        backlog_count = 0
        try:
            backlog_id, backlog_count, _ = record_unmatched_question(
                question=message,
                chat_id=chat_id,
                sender_id=sender_id,
            )
        except Exception:
            backlog_id, backlog_count = "", 0
        # 3) Try scoped AI search fallback.
        rule = build_ai_search_rule(message)
        # 4) Always respond even if search has no answer.
        if not rule:
            rule = build_general_qa_rule(message, backlog_id=backlog_id, backlog_count=backlog_count)

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
    session["answers"].append(rule["answer"])

    record_fields = build_record_fields(
        session,
        question=message,
        answer=rule["answer"],
        rule=rule,
        matched=True,
        nps_requested=add_nps_prompt,
    )
    if rule.get("intent") in {"general_qa_fallback", "ai_search_fallback", "ai_search_no_result"}:
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
    QA_RULES = get_all_intents()
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
