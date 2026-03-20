#!/usr/bin/env python3
"""Sync intents knowledge base from local file, JSON URL, or Feishu Wiki URL."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from intent_classifier_v5 import normalize_intents_payload  # noqa: E402

WIKI_MARKER = "【来自训练营Wiki的最新信息】"
WIKI_INTENT_RULES: Dict[str, List[str]] = {
    "course_schedule": [
        "课程日历",
        "第一课",
        "第二课",
        "第三课",
        "第四课",
        "第五课",
        "第六课",
        "第七课",
        "第八课",
        "周一",
        "周二",
        "周三",
        "周四",
        "周五",
        "周六",
        "周日",
    ],
    "course_material": ["课程讲义", "教学文档", "课程回放", "会议纪要", "思维导图", "课程大纲"],
    "homework_submit": ["作业提交", "作业指导", "提交", "表单", "作业提交保姆级指南"],
    "homework_deadline": ["延期", "结业证书", "作业", "入学申请"],
    "coding_plan": ["阿里云服务器", "9折优惠", "云端部署"],
}


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _digest_payload(payload: Any) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _extract_text_entries(body: str) -> List[str]:
    values: List[str] = []
    for match in re.finditer(r'"text":\{([^}]*)\}', body):
        text_obj = match.group(1)
        for item in re.finditer(r'"\d+":"((?:\\.|[^"\\])*)"', text_obj):
            raw = item.group(1)
            try:
                text = json.loads('"' + raw + '"')
            except Exception:
                text = raw
            text = " ".join(str(text).split())
            text = text.strip()
            if not text:
                continue
            values.append(text)
    return values


def _extract_feishu_wiki_lines(html_text: str) -> List[str]:
    raw_lines = _extract_text_entries(html_text)

    lines: List[str] = []
    seen = set()
    for line in raw_lines:
        if len(line) < 4 or len(line) > 220:
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", line):
            continue
        if line.startswith("http") and "feishu.cn/minutes" not in line:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    return lines


def _load_source_payload(source: str, timeout: int, headers: Dict[str, str]) -> Any:
    if source.startswith("http://") or source.startswith("https://"):
        resp = requests.get(source, headers=headers, timeout=timeout)
        resp.raise_for_status()

        content_type = (resp.headers.get("content-type") or "").lower()
        if "json" in content_type:
            return resp.json()

        text = resp.text
        if "feishu.cn/wiki/" in source and "<html" in text.lower():
            lines = _extract_feishu_wiki_lines(text)
            return {
                "_type": "feishu_wiki_lines",
                "wiki_url": source,
                "lines": lines,
            }

        try:
            return resp.json()
        except Exception:
            raise ValueError("URL source is not valid JSON and not supported wiki HTML")

    src_path = Path(source).expanduser()
    if not src_path.is_absolute():
        src_path = (BASE_DIR / src_path).resolve()
    return _read_json_file(src_path)


def _write_atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


def _backup_file(src: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"intents_{timestamp}.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _compact_intent_for_storage(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Drop default-only fields so stored JSON stays minimal and stable."""
    compact = dict(intent)
    if compact.get("enabled", True) is True:
        compact.pop("enabled", None)
    if int(compact.get("priority", 0)) == 0:
        compact.pop("priority", None)
    if int(compact.get("min_keyword_hits", 0)) == 0:
        compact.pop("min_keyword_hits", None)
    if not compact.get("exclude_keywords"):
        compact.pop("exclude_keywords", None)
    return compact


def _pick_snippets(lines: List[str], keywords: List[str], max_lines: int = 4) -> List[str]:
    picked: List[str] = []
    for line in lines:
        low = line.lower()
        if any(k.lower() in low for k in keywords):
            if line not in picked:
                picked.append(line)
        if len(picked) >= max_lines:
            break
    return picked


def _apply_wiki_snippet(answer: str, snippets: List[str]) -> str:
    base = answer
    if WIKI_MARKER in base:
        base = base.split(WIKI_MARKER, 1)[0].rstrip()

    if not snippets:
        return base

    block = WIKI_MARKER + "\n" + "\n".join(f"- {s}" for s in snippets)
    return f"{base}\n\n{block}".strip()


def _ensure_wiki_link(links: List[Dict[str, str]], wiki_url: str) -> List[Dict[str, str]]:
    out = [dict(x) for x in links if isinstance(x, dict)]
    for link in out:
        if link.get("url") == wiki_url:
            return out
    out.append({"name": "训练营Wiki（自动同步）", "url": wiki_url})
    return out


def _merge_wiki_lines_into_intents(existing_payload: Any, wiki_url: str, lines: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    intents, errors = normalize_intents_payload(existing_payload)
    if not intents:
        return [], {"updated_intents": [], "wiki_lines_count": len(lines)}, errors or ["existing intents invalid"]

    updated_ids: List[str] = []
    merged: List[Dict[str, Any]] = []

    for intent in intents:
        intent_id = str(intent.get("id", ""))
        rule_keywords = WIKI_INTENT_RULES.get(intent_id, [])
        if not rule_keywords:
            merged.append(_compact_intent_for_storage(intent))
            continue

        snippets = _pick_snippets(lines, rule_keywords, max_lines=4)
        if snippets:
            updated_ids.append(intent_id)

        new_intent = dict(intent)
        new_intent["answer"] = _apply_wiki_snippet(str(intent.get("answer", "")), snippets)
        new_intent["links"] = _ensure_wiki_link(list(intent.get("links", [])), wiki_url)
        merged.append(_compact_intent_for_storage(new_intent))

    meta = {
        "updated_intents": updated_ids,
        "wiki_lines_count": len(lines),
    }
    return merged, meta, errors


def sync_knowledge_base(
    source: str,
    target: Path,
    backup_dir: Path,
    timeout: int,
    strict: bool,
    dry_run: bool,
    force: bool,
    headers: Dict[str, str],
) -> Tuple[bool, Dict[str, Any]]:
    src_payload = _load_source_payload(source, timeout=timeout, headers=headers)

    merge_meta: Dict[str, Any] = {}
    if isinstance(src_payload, dict) and src_payload.get("_type") == "feishu_wiki_lines":
        if not target.exists():
            return False, {
                "updated": False,
                "error": "target_not_found",
                "message": "wiki merge mode requires existing intents target file",
            }
        existing_payload = _read_json_file(target)
        normalized_intents, merge_meta, errors = _merge_wiki_lines_into_intents(
            existing_payload,
            str(src_payload.get("wiki_url", source)),
            list(src_payload.get("lines", [])),
        )
    else:
        normalized_intents, errors = normalize_intents_payload(src_payload)

    if errors and strict:
        return False, {
            "updated": False,
            "error": "validation_failed",
            "validation_errors": errors,
            "valid_intents": len(normalized_intents),
            **merge_meta,
        }

    if not normalized_intents:
        return False, {
            "updated": False,
            "error": "no_valid_intents",
            "validation_errors": errors,
            **merge_meta,
        }

    new_payload = normalized_intents
    new_digest = _digest_payload(new_payload)

    current_payload = []
    if target.exists():
        current_payload = _read_json_file(target)
    current_digest = _digest_payload(current_payload)

    changed = current_digest != new_digest
    if not changed and not force:
        return True, {
            "updated": False,
            "reason": "no_change",
            "intents_count": len(new_payload),
            "digest": new_digest,
            "warnings": errors,
            **merge_meta,
        }

    backup_path = None
    if target.exists() and not dry_run:
        backup_path = _backup_file(target, backup_dir)

    if not dry_run:
        _write_atomic_json(target, new_payload)

    return True, {
        "updated": not dry_run,
        "dry_run": dry_run,
        "forced": force,
        "intents_count": len(new_payload),
        "digest": new_digest,
        "backup_path": str(backup_path) if backup_path else None,
        "warnings": errors,
        **merge_meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync intents.json from configured source")
    parser.add_argument("--source", default=os.getenv("QA_KB_SOURCE", ""), help="Source URL or file path")
    parser.add_argument(
        "--target",
        default=os.getenv("QA_KB_TARGET", str(BASE_DIR / "intents.json")),
        help="Target intents.json path",
    )
    parser.add_argument(
        "--backup-dir",
        default=os.getenv("QA_KB_BACKUP_DIR", str(BASE_DIR / "backups")),
        help="Backup directory",
    )
    parser.add_argument("--timeout", type=int, default=int(os.getenv("QA_KB_TIMEOUT", "15")))
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=_parse_bool(os.getenv("QA_KB_STRICT", "true"), True),
        help="Fail when source has validation errors",
    )
    parser.add_argument("--non-strict", dest="strict", action="store_false")
    parser.add_argument("--dry-run", action="store_true", help="Validate and diff only, no write")
    parser.add_argument("--force", action="store_true", help="Write even if no digest change")
    args = parser.parse_args()

    if not args.source:
        print(
            json.dumps(
                {
                    "updated": False,
                    "error": "missing_source",
                    "message": "Please provide --source or set QA_KB_SOURCE",
                },
                ensure_ascii=False,
            )
        )
        return 1

    headers: Dict[str, str] = {}
    headers_raw = os.getenv("QA_KB_SOURCE_HEADERS", "").strip()
    if headers_raw:
        try:
            parsed = json.loads(headers_raw)
            if isinstance(parsed, dict):
                headers = {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            pass

    target = Path(args.target).expanduser()
    if not target.is_absolute():
        target = (BASE_DIR / target).resolve()

    backup_dir = Path(args.backup_dir).expanduser()
    if not backup_dir.is_absolute():
        backup_dir = (BASE_DIR / backup_dir).resolve()

    try:
        ok, result = sync_knowledge_base(
            source=args.source,
            target=target,
            backup_dir=backup_dir,
            timeout=max(1, args.timeout),
            strict=args.strict,
            dry_run=args.dry_run,
            force=args.force,
            headers=headers,
        )
    except Exception as exc:
        print(json.dumps({"updated": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
