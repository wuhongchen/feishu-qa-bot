#!/usr/bin/env python3
"""Helpers for Feishu Bitable app/table resolving and record CRUD."""

import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

_field_name_cache: Dict[str, set] = {}


def extract_app_token_from_base_url(base_url: str) -> str:
    text = str(base_url or "").strip()
    if not text:
        return ""
    matched = re.search(r"/base/([A-Za-z0-9]+)", text)
    if matched:
        return matched.group(1)
    return ""


def extract_table_id_from_base_url(base_url: str) -> str:
    text = str(base_url or "").strip()
    if not text:
        return ""

    try:
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        for key in ("table", "table_id", "tbl"):
            values = query.get(key, [])
            if values:
                val = str(values[0]).strip()
                if val.startswith("tbl"):
                    return val
    except Exception:
        pass

    matched = re.search(r"(tbl[A-Za-z0-9]+)", text)
    if matched:
        return matched.group(1)
    return ""


def list_tables(token: str, app_token: str, timeout: int = 8) -> Tuple[bool, List[dict], str]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"page_size": 100}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        body = resp.json()
        if body.get("code") != 0:
            return False, [], body.get("msg", "list tables failed")
        items = body.get("data", {}).get("items", [])
        if not isinstance(items, list):
            items = []
        return True, items, ""
    except Exception as exc:
        return False, [], str(exc)


def resolve_app_and_table(
    token: str,
    app_token: str,
    table_id: str,
    base_url: str = "",
    table_name: str = "",
) -> Tuple[bool, str, str, str]:
    app_token = str(app_token or "").strip()
    table_id = str(table_id or "").strip()
    base_url = str(base_url or "").strip()
    table_name = str(table_name or "").strip()

    if not app_token and base_url:
        app_token = extract_app_token_from_base_url(base_url)

    if not table_id and base_url:
        table_id = extract_table_id_from_base_url(base_url)

    if not app_token:
        return False, "", "", "缺少 QA_BITABLE_TOKEN 或 QA_BITABLE_BASE_URL"

    if table_id:
        return True, app_token, table_id, ""

    ok, tables, err = list_tables(token=token, app_token=app_token)
    if not ok:
        return False, app_token, "", f"自动解析 table_id 失败: {err}"
    if not tables:
        return False, app_token, "", "目标 base 下没有可用表格，请先创建数据表"

    if table_name:
        for tbl in tables:
            if str(tbl.get("name", "")).strip() == table_name:
                tid = str(tbl.get("table_id", "")).strip()
                if tid:
                    return True, app_token, tid, ""

    first_table_id = str(tables[0].get("table_id", "")).strip()
    if not first_table_id:
        return False, app_token, "", "无法从 tables 接口获取 table_id"
    return True, app_token, first_table_id, ""


def create_record(token: str, app_token: str, table_id: str, fields: Dict[str, object], timeout: int = 8) -> Tuple[bool, str]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload_fields = _filter_fields_by_table_schema(token, app_token, table_id, fields, timeout=timeout)
    payload = {"fields": payload_fields}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        body = resp.json()
        if body.get("code") == 0:
            record_id = body.get("data", {}).get("record", {}).get("record_id", "")
            return True, str(record_id or "")
        return False, body.get("msg", "create record failed")
    except Exception as exc:
        return False, str(exc)


def update_record(
    token: str,
    app_token: str,
    table_id: str,
    record_id: str,
    fields: Dict[str, object],
    timeout: int = 8,
) -> Tuple[bool, str]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload_fields = _filter_fields_by_table_schema(token, app_token, table_id, fields, timeout=timeout)
    payload = {"fields": payload_fields}
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=timeout)
        body = resp.json()
        if body.get("code") == 0:
            return True, str(record_id)
        return False, body.get("msg", "update record failed")
    except Exception as exc:
        return False, str(exc)


def list_records(token: str, app_token: str, table_id: str, timeout: int = 8, max_pages: int = 20) -> Tuple[bool, List[dict], str]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}
    page_token = ""
    out: List[dict] = []
    page_count = 0

    while page_count < max_pages:
        page_count += 1
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            body = resp.json()
        except Exception as exc:
            return False, out, str(exc)

        if body.get("code") != 0:
            return False, out, body.get("msg", "list records failed")

        data = body.get("data", {})
        items = data.get("items", [])
        if isinstance(items, list):
            out.extend(items)

        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token", "")).strip()
        if not page_token:
            break

    return True, out, ""


def dump_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _filter_fields_by_table_schema(
    token: str,
    app_token: str,
    table_id: str,
    fields: Dict[str, object],
    timeout: int = 8,
) -> Dict[str, object]:
    filtered = {k: v for k, v in fields.items() if v is not None}
    cache_key = f"{app_token}:{table_id}"
    allowed_names = _field_name_cache.get(cache_key)

    if allowed_names is None:
        ok, allowed_names = list_field_names(token=token, app_token=app_token, table_id=table_id, timeout=timeout)
        if ok and allowed_names:
            _field_name_cache[cache_key] = allowed_names
        else:
            _field_name_cache[cache_key] = set()
            return filtered

    if not allowed_names:
        return filtered

    matched = {k: v for k, v in filtered.items() if k in allowed_names}
    return matched or filtered


def list_field_names(token: str, app_token: str, table_id: str, timeout: int = 8) -> Tuple[bool, set]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"page_size": 500}
    names: set = set()
    page_token = ""
    has_more = True

    while has_more:
        req_params = dict(params)
        if page_token:
            req_params["page_token"] = page_token
        try:
            resp = requests.get(url, headers=headers, params=req_params, timeout=timeout)
            body = resp.json()
        except Exception:
            return False, set()

        if body.get("code") != 0:
            return False, set()

        data = body.get("data", {})
        items = data.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                field_name = str(item.get("field_name", "")).strip()
                if field_name:
                    names.add(field_name)

        has_more = bool(data.get("has_more"))
        page_token = str(data.get("page_token", "")).strip()
        if has_more and not page_token:
            break

    return True, names
