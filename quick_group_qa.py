#!/usr/bin/env python3
"""Backward-compatible wrapper for group_qa_handler."""

from group_qa_handler import (
    ADMIN_USER_ID,
    MAX_ROUNDS,
    QA_BITABLE_TOKEN,
    QA_CHAT_IDS,
    QA_RULES,
    QA_TABLE_ID,
    generate_fallback,
    generate_reply,
    match_question,
    process_group_mention,
    process_group_message,
)


def handle_group_message(chat_id: str, sender_id: str, message: str) -> dict:
    """Legacy function signature used by older workflows."""
    reply, record_fields = process_group_message(chat_id, sender_id, message)
    return {
        "reply": reply,
        "should_record": record_fields is not None,
        "record_fields": record_fields,
        "app_token": QA_BITABLE_TOKEN,
        "table_id": QA_TABLE_ID,
        "matched": reply is not None,
    }


if __name__ == "__main__":
    print("群聊 QA Bot 兼容入口")
    print(f"支持群聊: {QA_CHAT_IDS}")
    print(f"最大轮次限制: {MAX_ROUNDS if MAX_ROUNDS > 0 else '不限制'}")
    print(f"管理员: {ADMIN_USER_ID or '未配置'}")
    print(f"规则数量: {len(QA_RULES)}")

    chat_id = QA_CHAT_IDS[0] if QA_CHAT_IDS else "test"
    user_id = "ou_test"
    tests = ["作业什么时候截止？", "8", "谢谢"]
    for q in tests:
        result = handle_group_message(chat_id, user_id, q)
        print(f"\nQ: {q}")
        print(f"matched: {result['matched']}")
        print(f"reply: {result['reply']}")
