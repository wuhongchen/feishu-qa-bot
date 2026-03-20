#!/usr/bin/env python3
"""Dynamic intent classifier backed by intents.json."""

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

INTENTS_FILE = os.path.join(os.path.dirname(__file__), "intents.json")
CACHE_TTL_MINUTES = 5

_intent_cache: Dict[str, object] = {}
_cache_time: Optional[datetime] = None
_cache_source_mtime: Optional[float] = None


@dataclass
class IntentResult:
    """Intent match result."""

    intent_id: str
    confidence: float
    matched_keywords: List[str]
    answer: str
    links: List[Dict[str, str]]


def _safe_list_strings(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _parse_bool(value: object, default: bool = True) -> bool:
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


def _normalize_text(text: str) -> str:
    text = str(text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_text_compact(text: str) -> str:
    text = _normalize_text(text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _normalize_intent(raw: Dict[str, object]) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    """Normalize and validate one raw intent record."""
    intent_id = str(raw.get("id", "")).strip()
    name = str(raw.get("name", "")).strip()
    if not intent_id:
        return None, "missing id"
    if not name:
        return None, f"{intent_id}: missing name"

    keywords = _safe_list_strings(raw.get("keywords", []))
    patterns = _safe_list_strings(raw.get("patterns", []))
    exclude_keywords = _safe_list_strings(raw.get("exclude_keywords", []))

    links: List[Dict[str, str]] = []
    for link in raw.get("links", []):
        if not isinstance(link, dict):
            continue
        link_name = str(link.get("name", "")).strip()
        link_url = str(link.get("url", "")).strip()
        if link_name and link_url:
            links.append({"name": link_name, "url": link_url})

    try:
        threshold = float(raw.get("threshold", 0.54))
    except (TypeError, ValueError):
        threshold = 0.54
    threshold = max(0.0, min(2.0, threshold))

    try:
        min_keyword_hits = int(raw.get("min_keyword_hits", 0))
    except (TypeError, ValueError):
        min_keyword_hits = 0
    min_keyword_hits = max(0, min_keyword_hits)

    try:
        priority = int(raw.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0

    normalized = {
        "id": intent_id,
        "name": name,
        "keywords": keywords,
        "patterns": patterns,
        "exclude_keywords": exclude_keywords,
        "answer": str(raw.get("answer", "")).strip(),
        "links": links,
        "threshold": threshold,
        "enabled": _parse_bool(raw.get("enabled", True), True),
        "priority": priority,
        "min_keyword_hits": min_keyword_hits,
    }

    if not normalized["answer"]:
        return None, f"{intent_id}: missing answer"

    return normalized, None


def normalize_intents_payload(raw_payload: object) -> Tuple[List[Dict[str, object]], List[str]]:
    """Normalize payload from file/remote source and return (intents, errors)."""
    if isinstance(raw_payload, dict) and isinstance(raw_payload.get("intents"), list):
        rows = raw_payload.get("intents", [])
    elif isinstance(raw_payload, list):
        rows = raw_payload
    else:
        return [], ["payload must be a list or object containing intents list"]

    intents: List[Dict[str, object]] = []
    errors: List[str] = []
    seen_ids = set()

    for row in rows:
        if not isinstance(row, dict):
            errors.append("intent item must be an object")
            continue
        normalized, err = _normalize_intent(row)
        if err:
            errors.append(err)
            continue
        intent_id = normalized["id"]
        if intent_id in seen_ids:
            errors.append(f"duplicate id: {intent_id}")
            continue
        seen_ids.add(intent_id)
        intents.append(normalized)

    if not intents:
        errors.append("no valid intents")

    return intents, errors


def load_intents() -> List[Dict[str, object]]:
    """Load and cache intents from JSON file."""
    global _cache_time, _intent_cache, _cache_source_mtime

    if _cache_time and _intent_cache:
        elapsed_min = (datetime.now() - _cache_time).total_seconds() / 60
        source_mtime = None
        try:
            source_mtime = Path(INTENTS_FILE).stat().st_mtime
        except OSError:
            pass
        if elapsed_min < CACHE_TTL_MINUTES and source_mtime == _cache_source_mtime:
            return _intent_cache.get("intents", [])

    try:
        with open(INTENTS_FILE, "r", encoding="utf-8") as f:
            raw_payload = json.load(f)

        intents, errors = normalize_intents_payload(raw_payload)
        if errors:
            print(f"[IntentLoader] normalized with warnings: {len(errors)}", file=sys.stderr)
            for err in errors[:8]:
                print(f"[IntentLoader] warning: {err}", file=sys.stderr)

        _intent_cache = {"intents": intents}
        _cache_time = datetime.now()
        try:
            _cache_source_mtime = Path(INTENTS_FILE).stat().st_mtime
        except OSError:
            _cache_source_mtime = None
        print(f"[IntentLoader] loaded {len(intents)} intents from file", file=sys.stderr)
        return intents
    except Exception as exc:  # pragma: no cover
        print(f"[IntentLoader] failed to load intents: {exc}", file=sys.stderr)
        return _intent_cache.get("intents", []) if _intent_cache else []


def reload_intents() -> List[Dict[str, object]]:
    """Force reload intents from disk."""
    global _cache_time
    _cache_time = None
    return load_intents()


def classify_intent(question: str) -> Optional[IntentResult]:
    """Classify one question and return best matched intent."""
    intents = load_intents()
    if not intents:
        return None

    original_text = str(question or "").strip()
    if not original_text:
        return None

    text = _normalize_text(original_text)
    compact_text = _normalize_text_compact(original_text)

    best_intent: Optional[Dict[str, object]] = None
    best_score = -1.0
    best_priority = -10**9
    best_keyword_hits = -1
    best_keywords: List[str] = []

    for intent in intents:
        if not intent.get("enabled", True):
            continue

        excluded = False
        for bad_kw in intent.get("exclude_keywords", []):
            if _normalize_text(bad_kw) in text or _normalize_text_compact(bad_kw) in compact_text:
                excluded = True
                break
        if excluded:
            continue

        score = 0.0
        matched_keywords: List[str] = []

        for keyword in intent.get("keywords", []):
            kw_norm = _normalize_text(keyword)
            kw_compact = _normalize_text_compact(keyword)
            if not kw_norm:
                continue

            if kw_norm in text or (kw_compact and kw_compact in compact_text):
                score += 1.0
                if text == kw_norm or compact_text == kw_compact:
                    score += 0.2
                matched_keywords.append(keyword)

        for pattern in intent.get("patterns", []):
            try:
                if re.search(pattern, original_text):
                    score += 2.0
            except re.error:
                continue

        keyword_hits = len(matched_keywords)
        min_keyword_hits = int(intent.get("min_keyword_hits", 0))
        if keyword_hits < min_keyword_hits:
            continue

        keyword_count = max(1, len(intent.get("keywords", [])))
        normalized_score = score / (keyword_count ** 0.5)
        threshold = float(intent.get("threshold", 0.54))
        priority = int(intent.get("priority", 0))

        if normalized_score < threshold:
            continue

        better = False
        if normalized_score > best_score:
            better = True
        elif normalized_score == best_score and priority > best_priority:
            better = True
        elif (
            normalized_score == best_score
            and priority == best_priority
            and keyword_hits > best_keyword_hits
        ):
            better = True

        if better:
            best_score = normalized_score
            best_priority = priority
            best_keyword_hits = keyword_hits
            best_intent = intent
            best_keywords = matched_keywords

    if not best_intent:
        return None

    return IntentResult(
        intent_id=str(best_intent["id"]),
        confidence=min(best_score, 1.0),
        matched_keywords=best_keywords,
        answer=str(best_intent.get("answer", "")),
        links=list(best_intent.get("links", [])),
    )


def get_answer(intent_result: Optional[IntentResult]) -> Optional[Dict[str, object]]:
    """Convert result into answer payload."""
    if not intent_result:
        return None
    return {
        "text": intent_result.answer,
        "confidence": intent_result.confidence,
        "links": intent_result.links,
    }


def get_all_intents() -> List[Dict[str, object]]:
    """Return lightweight intent summary."""
    return [
        {
            "id": i["id"],
            "name": i["name"],
            "enabled": i.get("enabled", True),
            "priority": i.get("priority", 0),
            "keywords_count": len(i.get("keywords", [])),
            "threshold": i.get("threshold", 0.54),
        }
        for i in load_intents()
    ]


def _write_intents(intents: List[Dict[str, object]]) -> None:
    target = Path(INTENTS_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8") as tmp:
        json.dump(intents, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name

    os.replace(temp_name, str(target))


def add_intent(intent_data: Dict[str, object]) -> bool:
    """Append one new intent into intents.json."""
    intents = load_intents()
    normalized, err = _normalize_intent(intent_data)
    if err:
        print(f"[IntentManager] invalid intent payload: {err}", file=sys.stderr)
        return False

    if any(i["id"] == normalized["id"] for i in intents):
        print(f"[IntentManager] intent exists: {normalized['id']}", file=sys.stderr)
        return False

    intents.append(normalized)
    try:
        _write_intents(intents)
        reload_intents()
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[IntentManager] add failed: {exc}", file=sys.stderr)
        return False


def update_intent(intent_id: str, updates: Dict[str, object]) -> bool:
    """Update one intent by id."""
    intents = load_intents()
    updated = False

    for idx, intent in enumerate(intents):
        if intent["id"] != intent_id:
            continue
        merged = dict(intent)
        merged.update(updates)
        normalized, err = _normalize_intent(merged)
        if err:
            print(f"[IntentManager] invalid updates: {err}", file=sys.stderr)
            return False
        intents[idx] = normalized
        updated = True
        break

    if not updated:
        print(f"[IntentManager] intent not found: {intent_id}", file=sys.stderr)
        return False

    try:
        _write_intents(intents)
        reload_intents()
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[IntentManager] update failed: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    test_questions = [
        "作业什么时候截止？",
        "Coding Plan 怎么买？",
        "课件在哪里？",
        "这个 skill 找不到",
        "百炼模型怎么添加？",
        "配对码过期了",
        "你好",
        "谢谢，解决了",
    ]

    print("=== Intent Classifier v5 Smoke Test ===")
    for question in test_questions:
        result = classify_intent(question)
        if not result:
            print(f"Q: {question} -> [no match]")
            continue
        print(
            f"Q: {question} -> {result.intent_id} "
            f"(confidence={result.confidence:.2f}, matched={result.matched_keywords})"
        )
