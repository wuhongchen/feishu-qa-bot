#!/usr/bin/env python3
"""Sync intents.json into Feishu Bitable table (upsert by 会话ID=intent::<id>)."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from env_bootstrap import load_project_env  # noqa: E402
from bitable_helper import (  # noqa: E402
    create_record,
    dump_json,
    list_records,
    resolve_app_and_table,
    update_record,
)
from feishu_app_auth import FeishuAppAuth  # noqa: E402
from intent_classifier_v5 import load_intents  # noqa: E402

load_project_env(__file__)


def _intent_fields(intent: Dict[str, object], now_ms: int) -> Dict[str, object]:
    intent_id = str(intent.get("id", "")).strip()
    name = str(intent.get("name", "")).strip()
    keywords = [str(x).strip() for x in intent.get("keywords", []) if str(x).strip()]
    patterns = [str(x).strip() for x in intent.get("patterns", []) if str(x).strip()]
    threshold = intent.get("threshold", 0.54)
    priority = intent.get("priority", 0)
    answer = str(intent.get("answer", "")).strip()
    links = intent.get("links", [])

    meta_lines = [
        f"intent_id: {intent_id}",
        f"name: {name}",
        f"threshold: {threshold}",
        f"priority: {priority}",
        f"keywords: {', '.join(keywords[:20])}",
        f"patterns: {', '.join(patterns[:10])}",
        f"links_count: {len(links) if isinstance(links, list) else 0}",
    ]
    meta_text = "\n".join(meta_lines)
    answer_text = f"[意图配置]\n{meta_text}\n\n[回复模板]\n{answer}"

    return {
        "会话ID": f"intent::{intent_id}",
        "提问时间": now_ms,
        "问题内容": f"意图库同步：{name or intent_id}",
        "回答内容": answer_text,
        "轮次": 0,
        "状态": "意图库",
        "是否解决": None,
        "结束时间": None,
        "NPS状态": None,
        "NPS评分": None,
        "知识来源": "知识库/intents.json",
        "置信度": 1.0,
        "意图分类": intent_id,
    }


def _load_existing_intent_records(token: str, app_token: str, table_id: str) -> Tuple[bool, Dict[str, str], str]:
    ok, items, err = list_records(token=token, app_token=app_token, table_id=table_id)
    if not ok:
        return False, {}, err

    mapping: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("record_id", "")).strip()
        fields = item.get("fields", {})
        if not record_id or not isinstance(fields, dict):
            continue
        session_id = str(fields.get("会话ID", "")).strip()
        if not session_id.startswith("intent::"):
            continue
        mapping[session_id] = record_id
    return True, mapping, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync intents.json to Feishu Bitable")
    parser.add_argument("--app-token", default=os.getenv("QA_BITABLE_TOKEN", "").strip())
    parser.add_argument("--table-id", default=os.getenv("QA_TABLE_ID", "").strip())
    parser.add_argument("--base-url", default=os.getenv("QA_BITABLE_BASE_URL", "").strip())
    parser.add_argument("--table-name", default=os.getenv("QA_TABLE_NAME", "").strip())
    parser.add_argument(
        "--enabled",
        default=os.getenv("QA_SYNC_INTENTS_TO_BITABLE", "true").strip().lower(),
        help="Whether sync is enabled: true/false",
    )
    args = parser.parse_args()

    if args.enabled not in {"1", "true", "yes", "y", "on"}:
        print(dump_json({"synced": False, "reason": "disabled"}))
        return 0

    auth = FeishuAppAuth()
    token = auth.get_token() or ""
    if not token:
        print(dump_json({"synced": False, "error": "获取 tenant_access_token 失败"}))
        return 1

    ok, app_token, table_id, err = resolve_app_and_table(
        token=token,
        app_token=args.app_token,
        table_id=args.table_id,
        base_url=args.base_url,
        table_name=args.table_name,
    )
    if not ok:
        print(dump_json({"synced": False, "error": err}))
        return 1

    intents = load_intents()
    if not intents:
        print(dump_json({"synced": False, "error": "intents 为空"}))
        return 1

    ok, existing_map, err = _load_existing_intent_records(token=token, app_token=app_token, table_id=table_id)
    if not ok:
        print(dump_json({"synced": False, "error": f"读取现有记录失败: {err}"}))
        return 1

    now_ms = int(datetime.now().timestamp() * 1000)
    created = 0
    updated = 0
    errors: List[str] = []

    for intent in intents:
        fields = _intent_fields(intent, now_ms=now_ms)
        session_id = str(fields.get("会话ID", "")).strip()
        if not session_id:
            continue

        record_id = existing_map.get(session_id, "")
        if record_id:
            ok, err_text = update_record(
                token=token,
                app_token=app_token,
                table_id=table_id,
                record_id=record_id,
                fields=fields,
            )
            if ok:
                updated += 1
            else:
                errors.append(f"update {session_id}: {err_text}")
        else:
            ok, result = create_record(token=token, app_token=app_token, table_id=table_id, fields=fields)
            if ok:
                created += 1
            else:
                errors.append(f"create {session_id}: {result}")

    result = {
        "synced": len(errors) == 0,
        "app_token": app_token,
        "table_id": table_id,
        "intents_total": len(intents),
        "created": created,
        "updated": updated,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
