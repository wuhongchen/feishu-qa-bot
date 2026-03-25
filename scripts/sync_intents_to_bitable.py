#!/usr/bin/env python3
"""Sync intents.json into Feishu Bitable table (upsert by 意图ID)."""

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
    list_field_names,
    list_records,
    resolve_app_and_table,
    update_record,
)
from feishu_app_auth import FeishuAppAuth  # noqa: E402
from intent_classifier_v5 import load_intents  # noqa: E402

load_project_env(__file__)


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _safe_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_links(raw_links: object) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(raw_links, list):
        return out
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if name and url:
            out.append({"name": name, "url": url})
    return out


def _intent_fields_for_intent_table(
    intent: Dict[str, object],
    now_ms: int,
    existing_fields: Dict[str, object],
) -> Dict[str, object]:
    intent_id = str(intent.get("id", "")).strip()
    name = str(intent.get("name", "")).strip()
    keywords = [str(x).strip() for x in intent.get("keywords", []) if str(x).strip()]
    patterns = [str(x).strip() for x in intent.get("patterns", []) if str(x).strip()]
    links = _normalize_links(intent.get("links", []))
    threshold = _safe_float(intent.get("threshold", 0.54), 0.54)
    enabled = _safe_bool(intent.get("enabled", True), True)

    created_time = existing_fields.get("创建时间")
    if created_time in (None, ""):
        created_time = now_ms

    hit_count = existing_fields.get("命中次数")
    if hit_count in (None, ""):
        hit_count = 0

    return {
        "意图ID": intent_id,
        "意图名称": name or intent_id,
        "关键词": ",".join(keywords),
        "正则模式": json.dumps(patterns, ensure_ascii=False),
        "回答模板": str(intent.get("answer", "")).strip(),
        "相关链接": json.dumps(links, ensure_ascii=False),
        "置信度阈值": threshold,
        "命中次数": _safe_int(hit_count, 0),
        "是否启用": enabled,
        "创建时间": created_time,
        "更新时间": now_ms,
    }


def _intent_fields_for_legacy_table(intent: Dict[str, object], now_ms: int) -> Dict[str, object]:
    intent_id = str(intent.get("id", "")).strip()
    name = str(intent.get("name", "")).strip()
    keywords = [str(x).strip() for x in intent.get("keywords", []) if str(x).strip()]
    patterns = [str(x).strip() for x in intent.get("patterns", []) if str(x).strip()]
    threshold = intent.get("threshold", 0.54)
    priority = intent.get("priority", 0)
    answer = str(intent.get("answer", "")).strip()
    links = _normalize_links(intent.get("links", []))

    meta_lines = [
        f"intent_id: {intent_id}",
        f"name: {name}",
        f"threshold: {threshold}",
        f"priority: {priority}",
        f"keywords: {', '.join(keywords[:20])}",
        f"patterns: {', '.join(patterns[:10])}",
        f"links_count: {len(links)}",
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
        "结束时间": now_ms,
        "知识来源": "知识库/intents.json",
        "置信度": 1.0,
        "意图分类": intent_id,
    }


def _load_existing_rows(
    token: str,
    app_token: str,
    table_id: str,
    key_field: str,
) -> Tuple[bool, Dict[str, Dict[str, object]], str]:
    ok, items, err = list_records(token=token, app_token=app_token, table_id=table_id)
    if not ok:
        return False, {}, err

    mapping: Dict[str, Dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("record_id", "")).strip()
        fields = item.get("fields", {})
        if not record_id or not isinstance(fields, dict):
            continue
        raw_key = fields.get(key_field)
        if raw_key is None:
            continue
        key = str(raw_key).strip()
        if not key:
            continue
        mapping[key] = {"record_id": record_id, "fields": fields}
    return True, mapping, ""


def _build_backfill_fields_for_intent_table(existing_fields: Dict[str, object], now_ms: int) -> Dict[str, object]:
    patch: Dict[str, object] = {}
    if existing_fields.get("命中次数") in (None, ""):
        patch["命中次数"] = 0
    if existing_fields.get("是否启用") is None:
        patch["是否启用"] = True
    if existing_fields.get("创建时间") in (None, ""):
        patch["创建时间"] = now_ms
    if existing_fields.get("更新时间") in (None, ""):
        patch["更新时间"] = now_ms
    return patch


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync intents.json to Feishu Bitable")
    parser.add_argument("--app-token", default=os.getenv("QA_INTENT_BITABLE_TOKEN", "").strip() or os.getenv("QA_BITABLE_TOKEN", "").strip())
    parser.add_argument("--table-id", default=os.getenv("QA_INTENT_TABLE_ID", "").strip())
    parser.add_argument("--base-url", default=os.getenv("QA_BITABLE_BASE_URL", "").strip())
    parser.add_argument("--table-name", default=os.getenv("QA_INTENT_TABLE_NAME", "").strip() or "意图库")
    parser.add_argument(
        "--enabled",
        default=os.getenv("QA_SYNC_INTENTS_TO_BITABLE", "true").strip().lower(),
        help="Whether sync is enabled: true/false",
    )
    parser.add_argument(
        "--backfill-existing",
        default=os.getenv("QA_INTENT_BACKFILL_EXISTING", "true").strip().lower(),
        help="Backfill missing fields on existing rows: true/false",
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

    ok, field_names = list_field_names(token=token, app_token=app_token, table_id=table_id)
    if not ok:
        print(dump_json({"synced": False, "error": "读取字段结构失败"}))
        return 1

    has_intent_table_fields = {"意图ID", "回答模板"}.issubset(field_names)
    key_field = "意图ID" if has_intent_table_fields else "会话ID"

    intents = load_intents()
    if not intents:
        print(dump_json({"synced": False, "error": "intents 为空"}))
        return 1

    ok, existing_rows, err = _load_existing_rows(token=token, app_token=app_token, table_id=table_id, key_field=key_field)
    if not ok:
        print(dump_json({"synced": False, "error": f"读取现有记录失败: {err}"}))
        return 1

    now_ms = int(datetime.now().timestamp() * 1000)
    created = 0
    updated = 0
    backfilled = 0
    errors: List[str] = []

    for intent in intents:
        intent_id = str(intent.get("id", "")).strip()
        if not intent_id:
            continue

        row_key = intent_id if has_intent_table_fields else f"intent::{intent_id}"
        existing = existing_rows.get(row_key, {})
        existing_fields = existing.get("fields", {}) if isinstance(existing.get("fields", {}), dict) else {}

        if has_intent_table_fields:
            fields = _intent_fields_for_intent_table(intent, now_ms=now_ms, existing_fields=existing_fields)
        else:
            fields = _intent_fields_for_legacy_table(intent, now_ms=now_ms)

        record_id = str(existing.get("record_id", "")).strip()
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
                errors.append(f"update {row_key}: {err_text}")
        else:
            ok, result = create_record(token=token, app_token=app_token, table_id=table_id, fields=fields)
            if ok:
                created += 1
            else:
                errors.append(f"create {row_key}: {result}")

    if has_intent_table_fields and args.backfill_existing in {"1", "true", "yes", "y", "on"}:
        for intent_id, row in existing_rows.items():
            record_id = str(row.get("record_id", "")).strip()
            fields = row.get("fields", {})
            if not record_id or not isinstance(fields, dict):
                continue
            patch = _build_backfill_fields_for_intent_table(fields, now_ms=now_ms)
            if not patch:
                continue
            ok, err_text = update_record(
                token=token,
                app_token=app_token,
                table_id=table_id,
                record_id=record_id,
                fields=patch,
            )
            if ok:
                backfilled += 1
            else:
                errors.append(f"backfill {intent_id}: {err_text}")

    result = {
        "synced": len(errors) == 0,
        "table_mode": "intent_table" if has_intent_table_fields else "legacy_qa_table",
        "app_token": app_token,
        "table_id": table_id,
        "intents_total": len(intents),
        "created": created,
        "updated": updated,
        "backfilled": backfilled,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
