#!/usr/bin/env python3
"""Scoped AI search fallback for unmatched QA questions."""

import os
import re
import json
import time
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import requests


DEFAULT_BLOCKED_KEYWORDS = [
    "医疗",
    "诊断",
    "处方",
    "法律",
    "诉讼",
    "判刑",
    "股票",
    "买币",
    "投资建议",
    "政治立场",
    "密码",
    "token",
    "密钥",
    "越狱",
    "注入",
]
DEFAULT_SKIP_PATTERNS = [
    r"^\s*(你好|hello|hi|在吗)\s*[!！。.,，]?\s*$",
    r"^\s*(谢谢|感谢|辛苦了)\s*[!！。.,，]?\s*$",
]


@dataclass
class SearchItem:
    title: str
    url: str
    snippet: str


_OPENCLAW_COOLDOWN_UNTIL_TS = 0.0


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: Optional[str], default_values: List[str]) -> List[str]:
    if not value:
        return list(default_values)
    return [x.strip() for x in value.split(",") if x.strip()]


def is_gateway_enabled_for_chat(chat_id: str) -> bool:
    """Whether OpenClaw Gateway features are enabled for this chat."""
    allowlist = _parse_csv(os.getenv("QA_OPENCLAW_GATEWAY_CHAT_IDS"), [])
    if not allowlist:
        return True
    return str(chat_id or "").strip() in set(allowlist)


def _clean_snippet(text: str, max_len: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _is_smalltalk(question: str, patterns: List[str]) -> bool:
    text = str(question or "")
    for pattern in patterns:
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _is_blocked(question: str, blocked_keywords: List[str]) -> Tuple[bool, Optional[str]]:
    text = str(question or "").lower()
    for keyword in blocked_keywords:
        normalized = keyword.lower().strip()
        if normalized and normalized in text:
            return True, keyword
    return False, None


def _search_with_tavily(question: str, max_results: int, timeout: int) -> List[SearchItem]:
    api_key = os.getenv("QA_TAVILY_API_KEY", "").strip()
    if not api_key:
        return []

    payload = {
        "api_key": api_key,
        "query": question,
        "search_depth": os.getenv("QA_AI_SEARCH_DEPTH", "basic"),
        "max_results": max(1, min(8, max_results)),
        "include_answer": False,
    }

    try:
        resp = requests.post("https://api.tavily.com/search", json=payload, timeout=max(3, timeout))
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return []

    out: List[SearchItem] = []
    for row in body.get("results", []):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "")).strip()
        url = str(row.get("url", "")).strip()
        snippet = _clean_snippet(row.get("content", ""))
        if not title or not url or not snippet:
            continue
        out.append(SearchItem(title=title, url=url, snippet=snippet))
    return out


def _extract_first_json_block(text: str) -> Optional[Dict[str, object]]:
    raw = str(text or "")
    start = raw.find("{")
    if start < 0:
        return None
    snippet = raw[start:]
    try:
        return json.loads(snippet)
    except Exception:
        pass

    for end in range(len(snippet), start, -1):
        chunk = snippet[:end]
        try:
            return json.loads(chunk)
        except Exception:
            continue
    return None


def _extract_payload_text_from_openclaw_response(payload: Dict[str, object]) -> str:
    texts: List[str] = []

    def collect(obj: object) -> None:
        if not isinstance(obj, dict):
            return
        payloads = obj.get("payloads")
        if isinstance(payloads, list):
            for row in payloads:
                if not isinstance(row, dict):
                    continue
                text = str(row.get("text", "")).strip()
                if text:
                    texts.append(text)
        nested = obj.get("result")
        if isinstance(nested, dict):
            collect(nested)

    collect(payload)
    return "\n".join([x for x in texts if x]).strip()


def _extract_openclaw_plain_text(payload: Dict[str, object], raw_text: str = "") -> str:
    text = _extract_payload_text_from_openclaw_response(payload)
    if text:
        return text

    for key in ("answer", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    result = payload.get("result")
    if isinstance(result, dict):
        nested = _extract_openclaw_plain_text(result, "")
        if nested:
            return nested

    if raw_text:
        return raw_text.strip()
    return ""


def _openclaw_process_timeout_seconds(default_seconds: int = 20) -> int:
    """Hard timeout for openclaw subprocess to avoid cron-level SIGTERM."""
    try:
        return max(8, int(os.getenv("QA_OPENCLAW_PROCESS_TIMEOUT_SECONDS", str(default_seconds))))
    except Exception:
        return max(8, default_seconds)


def _openclaw_cooldown_seconds(default_seconds: int = 120) -> int:
    try:
        return max(10, int(os.getenv("QA_OPENCLAW_COOLDOWN_SECONDS", str(default_seconds))))
    except Exception:
        return max(10, default_seconds)


def _openclaw_cooldown_remaining_seconds() -> int:
    now = time.time()
    if _OPENCLAW_COOLDOWN_UNTIL_TS <= now:
        return 0
    return int(_OPENCLAW_COOLDOWN_UNTIL_TS - now)


def _trip_openclaw_cooldown() -> None:
    global _OPENCLAW_COOLDOWN_UNTIL_TS
    _OPENCLAW_COOLDOWN_UNTIL_TS = time.time() + _openclaw_cooldown_seconds()


def _clear_openclaw_cooldown() -> None:
    global _OPENCLAW_COOLDOWN_UNTIL_TS
    _OPENCLAW_COOLDOWN_UNTIL_TS = 0.0


def _search_with_openclaw_agent(
    question: str,
    max_results: int,
    timeout_seconds: int,
) -> Tuple[str, List[SearchItem], str]:
    prompt = (
        "你是飞书群答疑助手。请结合可用检索能力回答。\n"
        "若可提供来源，请输出严格 JSON：\n"
        "{\n"
        '  "answer":"给用户的简洁回答（120字内）",\n'
        '  "sources":[{"title":"标题","url":"https://...","snippet":"20-80字摘要"}]\n'
        "}\n"
        f"sources 最多 {max(1, min(6, max_results))} 条；若无来源也请先给可执行回答。\n"
        f"用户问题：{question}"
    )
    text, err = _call_openclaw_agent_with_attachments(
        message=prompt,
        attachments=None,
        timeout_seconds=timeout_seconds,
    )
    if err:
        return "", [], err

    content = str(text or "").strip()
    if not content:
        return "", [], "openclaw_gateway_empty_output"

    inner = _extract_first_json_block(content)
    if not isinstance(inner, dict):
        if _looks_like_openclaw_error_text(content):
            return "", [], f"openclaw_gateway_text_error: {content[:240]}"
        return content, [], ""

    answer = str(inner.get("answer", "")).strip()
    if not answer:
        answer = content
    if _looks_like_openclaw_error_text(answer):
        return "", [], f"openclaw_gateway_answer_error: {answer[:240]}"

    sources_raw = inner.get("sources", [])
    items: List[SearchItem] = []
    if isinstance(sources_raw, list):
        for row in sources_raw:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "")).strip()
            url = str(row.get("url", "")).strip()
            snippet = _clean_snippet(str(row.get("snippet", "")).strip())
            if not title or not url or not snippet:
                continue
            items.append(SearchItem(title=title, url=url, snippet=snippet))
            if len(items) >= max(1, min(8, max_results)):
                break
    return answer, items, ""


def _call_openclaw_agent_with_attachments(
    message: str,
    attachments: Optional[List[Dict[str, str]]],
    timeout_seconds: int,
) -> Tuple[str, str]:
    """Call OpenClaw gateway agent API with optional image attachments."""
    cooldown_left = _openclaw_cooldown_remaining_seconds()
    if cooldown_left > 0:
        return "", f"openclaw_cooldown_active: {cooldown_left}s"

    agent_id = os.getenv("QA_OPENCLAW_SEARCH_AGENT", "main").strip() or "main"
    params: Dict[str, object] = {
        "message": str(message or "").strip(),
        "agentId": agent_id,
        "timeout": max(30, timeout_seconds),
        "idempotencyKey": f"qa-gateway-{uuid4().hex}",
    }
    model = os.getenv("QA_OPENCLAW_SEARCH_MODEL", "").strip()
    if model:
        params["model"] = model
    if attachments:
        params["attachments"] = attachments

    gateway_timeout_ms = max(3000, int(os.getenv("QA_OPENCLAW_GATEWAY_TIMEOUT_MS", "12000")))
    cmd = [
        "openclaw",
        "gateway",
        "call",
        "agent",
        "--expect-final",
        "--json",
        "--timeout",
        str(gateway_timeout_ms),
        "--params",
        json.dumps(params, ensure_ascii=False),
    ]

    process_timeout = _openclaw_process_timeout_seconds(default_seconds=max(12, gateway_timeout_ms // 1000 + 4))
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
    except subprocess.TimeoutExpired:
        _trip_openclaw_cooldown()
        return "", f"openclaw_gateway_timeout: {process_timeout}s"
    except Exception as exc:
        _trip_openclaw_cooldown()
        return "", f"openclaw_gateway_exec_failed: {exc}"

    combined = "\n".join([completed.stdout or "", completed.stderr or ""]).strip()
    if completed.returncode != 0:
        _trip_openclaw_cooldown()
        return "", f"openclaw_gateway_non_zero_exit: {combined[:240]}"

    payload = _extract_first_json_block(combined)
    if not isinstance(payload, dict):
        text = combined.strip()
        if _looks_like_openclaw_error_text(text):
            _trip_openclaw_cooldown()
            return "", f"openclaw_gateway_text_error: {text[:240]}"
        return text, ""

    text = _extract_openclaw_plain_text(payload, combined)
    if _looks_like_openclaw_error_text(text):
        _trip_openclaw_cooldown()
        return "", f"openclaw_gateway_answer_error: {text[:240]}"
    _clear_openclaw_cooldown()
    return text, ""


def _looks_like_openclaw_error_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lower = raw.lower()
    patterns = [
        "request timed out before a response was generated",
        "please try again, or increase `agents.defaults.timeoutseconds`",
        "gateway agent failed",
        "gateway closed",
        "failovererror",
        "no api key found for provider",
        "error:",
    ]
    return any(p in lower for p in patterns)


def _build_scoped_answer(items: List[SearchItem]) -> str:
    lines = ["检索结果："]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item.title}：{item.snippet}")
    return "\n".join(lines)


def build_ai_image_rule(
    question: str,
    attachments: Optional[List[Dict[str, str]]],
    chat_id: str = "",
) -> Dict[str, object]:
    """Directly ask OpenClaw to understand image attachments and reply in Chinese."""
    if not is_gateway_enabled_for_chat(chat_id):
        return {
            "answer": "当前群暂未开通图片识别能力，请补充文字描述我先帮你处理。",
            "source": "OpenClaw图片识别/未开通",
            "confidence": 0.2,
            "intent": "ai_image_disabled",
            "links": [],
        }

    timeout_seconds = max(30, int(os.getenv("QA_OPENCLAW_TIMEOUT_SECONDS", "90")))
    user_question = str(question or "").strip()
    prompt = (
        "你是飞书群答疑助手，请结合用户问题和图片内容直接回复。\n"
        "要求：\n"
        "1) 使用简体中文，语气自然，不要输出技术细节\n"
        "2) 回答尽量简洁，优先给可执行建议\n"
        "3) 如果图片不清晰或无法判断，请明确说明并提示用户补充更清晰图片/上下文\n"
        f"用户问题：{user_question or '（未提供文字问题，请先概括图片关键信息后给建议）'}"
    )

    answer, err = _call_openclaw_agent_with_attachments(
        message=prompt,
        attachments=attachments,
        timeout_seconds=timeout_seconds,
    )
    if err or not answer.strip():
        return {
            "answer": "图片已收到，但当前识别服务暂不可用。请稍后再试，或补充文字描述我先帮你处理。",
            "source": "OpenClaw图片识别/不可用",
            "confidence": 0.2,
            "intent": "ai_image_unavailable",
            "links": [],
        }

    return {
        "answer": answer.strip(),
        "source": "OpenClaw Gateway图片识别",
        "confidence": 0.65,
        "intent": "ai_image_fallback",
        "links": [],
    }


def build_ai_search_rule(question: str, chat_id: str = "") -> Optional[Dict[str, object]]:
    """Return a pseudo-intent rule for unmatched question, or None."""
    provider = os.getenv("QA_AI_SEARCH_PROVIDER", "openclaw").strip().lower()
    # OpenClaw provider is the default unmatched path. Do not silently skip.
    enabled_default = True if provider == "openclaw" else False
    enabled = _parse_bool(os.getenv("QA_ENABLE_AI_FALLBACK"), enabled_default)
    if not enabled:
        return None

    question = str(question or "").strip()
    if not question:
        return None

    if provider != "openclaw":
        min_chars = max(2, int(os.getenv("QA_AI_FALLBACK_MIN_CHARS", "4")))
        if len(question) < min_chars:
            return None

        skip_patterns = _parse_csv(os.getenv("QA_AI_FALLBACK_SKIP_PATTERNS"), DEFAULT_SKIP_PATTERNS)
        if _is_smalltalk(question, skip_patterns):
            return None

    blocked_keywords = _parse_csv(os.getenv("QA_AI_FALLBACK_BLOCKED_KEYWORDS"), DEFAULT_BLOCKED_KEYWORDS)
    blocked, keyword = _is_blocked(question, blocked_keywords)
    if blocked:
        answer = (
            "这个问题超出了当前助教机器人的答复范围。"
            f"\n触发边界词：{keyword}"
            "\n建议在群内 @助教 或使用人工渠道获取支持。"
        )
        return {
            "answer": answer,
            "source": "AI搜索/边界拦截",
            "confidence": 0.35,
            "intent": "ai_search_blocked",
            "links": [],
        }

    max_results = max(1, int(os.getenv("QA_AI_SEARCH_MAX_RESULTS", "3")))
    tavily_timeout = max(3, int(os.getenv("QA_AI_SEARCH_TIMEOUT", "10")))
    openclaw_timeout = max(30, int(os.getenv("QA_OPENCLAW_TIMEOUT_SECONDS", "90")))

    if provider == "openclaw":
        if not is_gateway_enabled_for_chat(chat_id):
            return None
        answer_text, items, err = _search_with_openclaw_agent(
            question=question,
            max_results=max_results,
            timeout_seconds=openclaw_timeout,
        )
        if err:
            return {
                "answer": (
                    "OpenClaw Gateway 当前不可用，请稍后重试或 @助教。"
                ),
                "source": "OpenClaw Gateway/不可用",
                "confidence": 0.2,
                "intent": "ai_search_unavailable",
                "links": [],
            }

        if not answer_text and not items:
            return {
                "answer": (
                    "已执行 OpenClaw Gateway 检索，但暂未检索到可用结果。请补充关键词后再试。"
                ),
                "source": "OpenClaw Gateway/无结果",
                "confidence": 0.25,
                "intent": "ai_search_no_result",
                "links": [],
            }

        answer = answer_text or _build_scoped_answer(items)
        links = [{"name": item.title[:32], "url": item.url} for item in items]
        confidence = min(0.78, 0.48 + len(items) * 0.08)
        return {
            "answer": answer,
            "source": "OpenClaw Gateway",
            "confidence": confidence,
            "intent": "ai_search_fallback",
            "links": links,
        }

    if provider != "tavily":
        return {
            "answer": (
                "未命中本地知识库，且 AI 搜索提供方未正确配置。"
                "\n请联系管理员检查 QA_AI_SEARCH_PROVIDER。"
            ),
            "source": "AI搜索/配置异常",
            "confidence": 0.2,
            "intent": "ai_search_unavailable",
            "links": [],
        }

    items = _search_with_tavily(question=question, max_results=max_results, timeout=tavily_timeout)
    if not items:
        return {
            "answer": (
                "我没有命中本地知识库，也未在限定搜索范围内找到足够可靠的信息。"
                "\n你可以补充更具体的关键词，或直接 @助教。"
            ),
            "source": "AI搜索/无结果",
            "confidence": 0.25,
            "intent": "ai_search_no_result",
            "links": [],
        }

    answer = _build_scoped_answer(items)
    links = [{"name": item.title[:32], "url": item.url} for item in items]
    confidence = min(0.75, 0.45 + len(items) * 0.08)
    return {
        "answer": answer,
        "source": "AI搜索",
        "confidence": confidence,
        "intent": "ai_search_fallback",
        "links": links,
    }
