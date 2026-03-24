#!/usr/bin/env python3
"""Scoped AI search fallback for unmatched QA questions."""

import os
import re
import json
import time
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
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
QUESTION_HINT_PATTERNS = [
    r"[?？]\s*$",
    r"(怎么|如何|为什么|为啥|是否|能不能|可以吗|吗|么)",
    r"(哪里|哪个|哪种|哪位|几号|几点|多少)",
    r"\b(what|how|why|where|when|which|can i|could i|is it)\b",
]


@dataclass
class SearchItem:
    title: str
    url: str
    snippet: str


_OPENCLAW_COOLDOWN_UNTIL_TS = 0.0
_OPENCLAW_CONSECUTIVE_FAILURES = 0
_OPENCLAW_BIN_CACHE = ""


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: Optional[str], default_values: List[str]) -> List[str]:
    if not value:
        return list(default_values)
    return [x.strip() for x in value.split(",") if x.strip()]


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


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


def _looks_like_question(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    for pattern in QUESTION_HINT_PATTERNS:
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


def _ai_rate_cache_file() -> Path:
    return Path(os.getenv("QA_AI_FALLBACK_RATE_CACHE_FILE", "/tmp/feishu_qa_ai_fallback_rate.json"))


def _load_ai_rate_cache() -> Dict[str, List[int]]:
    path = _ai_rate_cache_file()
    try:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, List[int]] = {}
        for key, val in raw.items():
            if not isinstance(val, list):
                continue
            nums: List[int] = []
            for item in val:
                try:
                    nums.append(int(item))
                except Exception:
                    continue
            out[str(key)] = nums
        return out
    except Exception:
        return {}


def _save_ai_rate_cache(payload: Dict[str, List[int]]) -> None:
    path = _ai_rate_cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _prune_ts(values: List[int], now_s: int, window_s: int) -> List[int]:
    return [x for x in values if now_s - int(x) <= window_s]


def _reserve_ai_fallback_slot(chat_id: str) -> bool:
    max_calls = max(1, _parse_int(os.getenv("QA_AI_FALLBACK_MAX_CALLS_PER_WINDOW"), 3))
    window_s = max(30, _parse_int(os.getenv("QA_AI_FALLBACK_WINDOW_SECONDS"), 300))
    now_s = int(time.time())
    chat_key = f"chat:{str(chat_id or '_').strip()}"

    cache = _load_ai_rate_cache()
    global_hits = _prune_ts(cache.get("global", []), now_s, window_s)
    chat_hits = _prune_ts(cache.get(chat_key, []), now_s, window_s)

    if len(global_hits) >= max_calls or len(chat_hits) >= max_calls:
        cache["global"] = global_hits
        cache[chat_key] = chat_hits
        _save_ai_rate_cache(cache)
        return False

    global_hits.append(now_s)
    chat_hits.append(now_s)
    cache["global"] = global_hits
    cache[chat_key] = chat_hits
    _save_ai_rate_cache(cache)
    return True


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
    """Hard timeout for openclaw subprocess to avoid cron-level SIGTERM.

    `default_seconds` is derived from each method's intended timeout. Env
    override should only increase this floor, not reduce it and prematurely
    kill long-running waits (e.g. multimodal model inference).
    """
    try:
        configured = int(os.getenv("QA_OPENCLAW_PROCESS_TIMEOUT_SECONDS", str(default_seconds)))
    except Exception:
        configured = int(default_seconds)
    return max(8, int(default_seconds), configured)


def _resolve_openclaw_bin() -> str:
    """Resolve OpenClaw executable for non-interactive runners (cron)."""
    global _OPENCLAW_BIN_CACHE
    if _OPENCLAW_BIN_CACHE:
        return _OPENCLAW_BIN_CACHE

    candidates: List[str] = []
    env_bin = str(os.getenv("QA_OPENCLAW_BIN", "") or os.getenv("OPENCLAW_BIN", "")).strip()
    if env_bin:
        candidates.append(env_bin)

    which_bin = shutil.which("openclaw")
    if which_bin:
        candidates.append(which_bin)

    home = os.path.expanduser("~")
    candidates.extend(
        [
            os.path.join(home, ".local", "bin", "openclaw"),
            os.path.join(home, ".openclaw", "bin", "openclaw"),
            "/usr/local/bin/openclaw",
            "/opt/homebrew/bin/openclaw",
        ]
    )

    for path in candidates:
        candidate = str(path or "").strip()
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _OPENCLAW_BIN_CACHE = candidate
            return _OPENCLAW_BIN_CACHE

    _OPENCLAW_BIN_CACHE = "openclaw"
    return _OPENCLAW_BIN_CACHE


def _build_openclaw_subprocess_env() -> Dict[str, str]:
    """Build runtime env that can find both openclaw and node in cron."""
    env = dict(os.environ)
    path = env.get("PATH", "")
    prefixes = [
        os.path.join(os.path.expanduser("~"), ".local", "bin"),
        os.path.join(os.path.expanduser("~"), ".openclaw", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    for prefix in reversed(prefixes):
        if prefix and prefix not in path.split(":"):
            path = f"{prefix}:{path}" if path else prefix
    env["PATH"] = path
    return env


def _openclaw_cooldown_seconds(default_seconds: int = 30) -> int:
    try:
        seconds = int(os.getenv("QA_OPENCLAW_COOLDOWN_SECONDS", str(default_seconds)))
    except Exception:
        seconds = default_seconds
    if seconds <= 0:
        return 0
    return max(5, seconds)


def _openclaw_cooldown_failure_streak(default_streak: int = 3) -> int:
    try:
        return max(1, int(os.getenv("QA_OPENCLAW_COOLDOWN_FAILURE_STREAK", str(default_streak))))
    except Exception:
        return max(1, default_streak)


def _openclaw_cooldown_remaining_seconds() -> int:
    global _OPENCLAW_COOLDOWN_UNTIL_TS
    now = time.time()
    if _OPENCLAW_COOLDOWN_UNTIL_TS <= now:
        _OPENCLAW_COOLDOWN_UNTIL_TS = 0.0
        return 0
    return int(_OPENCLAW_COOLDOWN_UNTIL_TS - now)


def _record_openclaw_failure(enable_cooldown: bool = True) -> None:
    global _OPENCLAW_COOLDOWN_UNTIL_TS, _OPENCLAW_CONSECUTIVE_FAILURES
    _OPENCLAW_CONSECUTIVE_FAILURES += 1

    if not enable_cooldown:
        return

    cooldown_seconds = _openclaw_cooldown_seconds()
    if cooldown_seconds <= 0:
        return

    threshold = _openclaw_cooldown_failure_streak()
    if _OPENCLAW_CONSECUTIVE_FAILURES < threshold:
        return

    _OPENCLAW_COOLDOWN_UNTIL_TS = time.time() + cooldown_seconds


def _clear_openclaw_cooldown() -> None:
    global _OPENCLAW_COOLDOWN_UNTIL_TS, _OPENCLAW_CONSECUTIVE_FAILURES
    _OPENCLAW_COOLDOWN_UNTIL_TS = 0.0
    _OPENCLAW_CONSECUTIVE_FAILURES = 0


def _search_with_openclaw_agent(
    question: str,
    max_results: int,
    timeout_seconds: int,
) -> Tuple[str, List[SearchItem], str]:
    prompt = _build_openclaw_search_prompt(
        question=question,
        max_results=max_results,
        strict_mode=False,
    )
    text, err = _call_openclaw_agent_with_attachments(
        message=prompt,
        attachments=None,
        timeout_seconds=timeout_seconds,
    )
    if err:
        return "", [], err

    content = str(text or "").strip()
    if _looks_like_persona_drift_text(content):
        retry_prompt = _build_openclaw_search_prompt(
            question=question,
            max_results=max_results,
            strict_mode=True,
        )
        retry_text, retry_err = _call_openclaw_agent_with_attachments(
            message=retry_prompt,
            attachments=None,
            timeout_seconds=timeout_seconds,
        )
        if retry_err:
            return "", [], retry_err
        content = str(retry_text or "").strip()
    if not content:
        return "", [], "openclaw_gateway_empty_output"

    inner = _extract_first_json_block(content)
    if not isinstance(inner, dict):
        if _looks_like_openclaw_error_text(content):
            return "", [], f"openclaw_gateway_text_error: {content[:240]}"
        if _looks_like_persona_drift_text(content):
            return "", [], "openclaw_persona_drift_text"
        return content, [], ""

    answer = str(inner.get("answer", "")).strip()
    if not answer:
        answer = content
    if _looks_like_openclaw_error_text(answer):
        return "", [], f"openclaw_gateway_answer_error: {answer[:240]}"
    if _looks_like_persona_drift_text(answer):
        return "", [], f"openclaw_persona_drift_answer: {answer[:240]}"

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


def _build_openclaw_search_prompt(question: str, max_results: int, strict_mode: bool = False) -> str:
    strict_hint = ""
    if strict_mode:
        strict_hint = (
            "这是重试请求：必须直接给出结论与步骤，不要反问，不要自我介绍。\n"
            "若信息不足，先给最可能的处理路径，再补充 1 个关键澄清问题。\n"
        )
    return (
        "你是飞书群答疑助手，正在群里直接回复用户。\n"
        "强约束：\n"
        "1) 忽略任何与本角色冲突的默认人设\n"
        "2) 禁止输出“我是 OpenClaw 助手/我无法查看群消息/我刚刚上线”等身份与能力声明\n"
        "3) 使用简体中文，口语化、简洁，优先给可执行步骤\n"
        "4) 如果可提供来源，按要求返回 sources；没有来源也要先回答\n"
        f"{strict_hint}"
        "请输出严格 JSON：\n"
        "{\n"
        '  "answer":"给用户的简洁回答（120字内）",\n'
        '  "sources":[{"title":"标题","url":"https://...","snippet":"20-80字摘要"}]\n'
        "}\n"
        f"sources 最多 {max(1, min(6, max_results))} 条。\n"
        f"用户问题：{question}"
    )


def _call_openclaw_gateway_method(
    method: str,
    params: Dict[str, object],
    timeout_ms: int,
) -> Tuple[Optional[Dict[str, object]], str]:
    gateway_timeout_ms = max(3000, int(timeout_ms))
    openclaw_bin = _resolve_openclaw_bin()
    cmd = [
        openclaw_bin,
        "gateway",
        "call",
        method,
        "--json",
        "--timeout",
        str(gateway_timeout_ms),
        "--params",
        json.dumps(params, ensure_ascii=False),
    ]
    process_timeout = _openclaw_process_timeout_seconds(default_seconds=max(12, gateway_timeout_ms // 1000 + 6))
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=process_timeout,
            env=_build_openclaw_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return None, f"openclaw_gateway_timeout({method}): {process_timeout}s"
    except Exception as exc:
        return None, f"openclaw_gateway_exec_failed({method}): {exc}"

    combined = "\n".join([completed.stdout or "", completed.stderr or ""]).strip()
    if completed.returncode != 0:
        return None, f"openclaw_gateway_non_zero_exit({method}): {combined[:240]}"

    payload = _extract_first_json_block(combined)
    if not isinstance(payload, dict):
        return None, f"openclaw_gateway_invalid_json({method}): {combined[:240]}"
    return payload, ""


def _extract_assistant_text_from_preview(payload: Dict[str, object]) -> str:
    previews = payload.get("previews")
    if not isinstance(previews, list):
        return ""
    for preview in previews:
        if not isinstance(preview, dict):
            continue
        items = preview.get("items")
        if not isinstance(items, list):
            continue
        for row in reversed(items):
            if not isinstance(row, dict):
                continue
            if str(row.get("role", "")).strip() != "assistant":
                continue
            text = str(row.get("text", "")).strip()
            if text:
                return text
    return ""


def _call_openclaw_agent_with_attachments(
    message: str,
    attachments: Optional[List[Dict[str, str]]],
    timeout_seconds: int,
    bypass_cooldown: bool = False,
    use_parent_session: bool = True,
    model_override: str = "",
) -> Tuple[str, str]:
    """Use OpenClaw spawned child session flow (sessions.create/sessions.send/agent.wait)."""
    if not bypass_cooldown:
        cooldown_left = _openclaw_cooldown_remaining_seconds()
        if cooldown_left > 0:
            return "", f"openclaw_cooldown_active: {cooldown_left}s"

    agent_id = os.getenv("QA_OPENCLAW_SEARCH_AGENT", "main").strip() or "main"
    parent_session_key = os.getenv("QA_OPENCLAW_PARENT_SESSION_KEY", f"agent:{agent_id}:main").strip()
    model = str(model_override or "").strip() or os.getenv("QA_OPENCLAW_SEARCH_MODEL", "").strip()
    base_timeout_ms = max(6000, int(os.getenv("QA_OPENCLAW_GATEWAY_TIMEOUT_MS", "12000")))
    wait_timeout_ms = max(30000, int(timeout_seconds) * 1000)

    create_params: Dict[str, object] = {
        "agentId": agent_id,
        "label": f"qa-spawn-{uuid4().hex[:8]}",
    }
    if use_parent_session and parent_session_key:
        create_params["parentSessionKey"] = parent_session_key
    if model:
        create_params["model"] = model

    # Text-only path can start directly on create; multimodal path sends with attachments after create.
    if not attachments:
        create_params["message"] = str(message or "").strip()

    created, err = _call_openclaw_gateway_method("sessions.create", create_params, timeout_ms=base_timeout_ms)
    if err and use_parent_session and parent_session_key and "unknown parent session" in err.lower():
        create_params.pop("parentSessionKey", None)
        created, err = _call_openclaw_gateway_method("sessions.create", create_params, timeout_ms=base_timeout_ms)
    if err or not isinstance(created, dict):
        _record_openclaw_failure(enable_cooldown=True)
        return "", err or "openclaw_sessions_create_failed"

    session_key = str(created.get("key", "")).strip()
    if not session_key:
        _record_openclaw_failure(enable_cooldown=True)
        return "", f"openclaw_sessions_create_no_key: {str(created)[:240]}"

    run_id = str(created.get("runId", "")).strip() if created.get("runStarted") else ""
    if attachments:
        send_params: Dict[str, object] = {
            "key": session_key,
            "message": str(message or "").strip(),
            "attachments": attachments,
            "timeoutMs": wait_timeout_ms,
            "idempotencyKey": f"qa-spawn-send-{uuid4().hex}",
        }
        sent, send_err = _call_openclaw_gateway_method("sessions.send", send_params, timeout_ms=base_timeout_ms)
        if send_err or not isinstance(sent, dict):
            _record_openclaw_failure(enable_cooldown=True)
            return "", send_err or "openclaw_sessions_send_failed"
        run_id = str(sent.get("runId", "")).strip()

    if run_id:
        waited, wait_err = _call_openclaw_gateway_method(
            "agent.wait",
            {"runId": run_id, "timeoutMs": wait_timeout_ms},
            timeout_ms=wait_timeout_ms + 5000,
        )
        if wait_err:
            _record_openclaw_failure(enable_cooldown=True)
            return "", wait_err
        if isinstance(waited, dict):
            status = str(waited.get("status", "")).strip().lower()
            if status in {"timeout", "error"}:
                _record_openclaw_failure(enable_cooldown=True)
                return "", f"openclaw_spawn_wait_{status}: {str(waited)[:240]}"

    preview_payload, preview_err = _call_openclaw_gateway_method(
        "sessions.preview",
        {"keys": [session_key], "limit": 20, "maxChars": 8000},
        timeout_ms=base_timeout_ms,
    )
    if preview_err or not isinstance(preview_payload, dict):
        _record_openclaw_failure(enable_cooldown=True)
        return "", preview_err or "openclaw_sessions_preview_failed"

    text = _extract_assistant_text_from_preview(preview_payload).strip()
    if not text:
        text = _extract_openclaw_plain_text(preview_payload, "").strip()
    if not text:
        _record_openclaw_failure(enable_cooldown=True)
        return "", "openclaw_spawn_empty_output"
    if _looks_like_openclaw_error_text(text):
        _record_openclaw_failure(enable_cooldown=False)
        return "", f"openclaw_spawn_answer_error: {text[:240]}"

    _clear_openclaw_cooldown()
    return text, ""


def _looks_like_openclaw_error_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if _is_rate_limit_text(raw):
        return True
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


def _looks_like_persona_drift_text(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    patterns = [
        "我是openclaw",
        "openclaw助手",
        "刚刚上线",
        "不是飞书群答疑助手",
        "无法查看或回复群消息",
        "无法查看群消息",
    ]
    return any(p in lower for p in patterns)


def _looks_like_no_image_seen_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lower = raw.lower()
    patterns = [
        "没有看到您上传的图片",
        "没有收到任何图片",
        "没有包含图片",
        "没有附带图片",
        "没有接收到图片",
        "图片内容无法直接显示",
        "图片内容无法直接识别",
        "图片无法直接显示",
        "图片无法识别",
        "无法查看图片",
        "需要先查看您发送的图片",
        "请您上传一张图片",
        "请重新发送图片",
        "i don't see the image",
        "i cannot see the image",
        "no image provided",
    ]
    if any(p in lower for p in patterns):
        return True
    if re.search(r"没有.*(图片|图像)", raw):
        return True
    if re.search(r"请.*(上传|发送).*(图片|图像)", raw):
        return True
    return False


def _is_rate_limit_text(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    patterns = [
        "rate limit",
        "api rate limit reached",
        "too many requests",
        "http 429",
        "status code 429",
        "quota exceeded",
        "exceeded your current quota",
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
    image_model = os.getenv("QA_OPENCLAW_IMAGE_MODEL", "").strip()
    fallback_model = os.getenv("QA_OPENCLAW_IMAGE_FALLBACK_MODEL", "").strip()
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
        # Image understanding should not be blocked by text fallback cooldown.
        bypass_cooldown=_parse_bool(os.getenv("QA_IMAGE_IGNORE_OPENCLAW_COOLDOWN"), True),
        # Avoid inheriting long parent context to reduce multimodal hallucination.
        use_parent_session=False,
        model_override=image_model,
    )
    fallback_attempted = False

    def _try_fallback_image_model() -> Tuple[str, str]:
        nonlocal fallback_attempted
        if not fallback_model or fallback_model == image_model:
            return "", ""
        fallback_attempted = True
        return _call_openclaw_agent_with_attachments(
            message=prompt,
            attachments=attachments,
            timeout_seconds=timeout_seconds,
            bypass_cooldown=True,
            use_parent_session=False,
            model_override=fallback_model,
        )

    if err and not _is_rate_limit_text(err):
        # Retry once for transient gateway jitter in multimodal path.
        retry_answer, retry_err = _call_openclaw_agent_with_attachments(
            message=prompt,
            attachments=attachments,
            timeout_seconds=timeout_seconds,
            bypass_cooldown=True,
            use_parent_session=False,
            model_override=image_model,
        )
        if retry_answer.strip():
            answer, err = retry_answer, ""
        else:
            err = retry_err or err

    if err or not answer.strip():
        fb_answer, fb_err = _try_fallback_image_model()
        if fb_answer.strip() and not _looks_like_no_image_seen_text(fb_answer):
            return {
                "answer": fb_answer.strip(),
                "source": f"OpenClaw Gateway图片识别(回退:{fallback_model})",
                "confidence": 0.62,
                "intent": "ai_image_fallback",
                "links": [],
            }
        if fb_err and _is_rate_limit_text(fb_err):
            return {
                "answer": "图片识别服务当前请求较多，触发限流。请稍后再试，或先补充文字描述我来处理。",
                "source": "OpenClaw图片识别/限流",
                "confidence": 0.2,
                "intent": "ai_image_unavailable",
                "debug_reason": fb_err,
                "links": [],
            }
        if fb_answer.strip() and _looks_like_no_image_seen_text(fb_answer):
            return {
                "answer": "图片我收到了，但模型没有成功读取到图像内容。请重新发送原图（不要转发压缩图），我再为你识别。",
                "source": "OpenClaw图片识别/未读取到图像",
                "confidence": 0.2,
                "intent": "ai_image_unavailable",
                "debug_reason": "openclaw_spawn_no_image_context(fallback)",
                "links": [],
            }
        if _is_rate_limit_text(err):
            return {
                "answer": "图片识别服务当前请求较多，触发限流。请稍后再试，或先补充文字描述我来处理。",
                "source": "OpenClaw图片识别/限流",
                "confidence": 0.2,
                "intent": "ai_image_unavailable",
                "debug_reason": err,
                "links": [],
            }
        return {
            "answer": "图片已收到，但当前识别服务暂不可用。请稍后再试，或补充文字描述我先帮你处理。",
            "source": "OpenClaw图片识别/不可用",
            "confidence": 0.2,
            "intent": "ai_image_unavailable",
            "debug_reason": err or fb_err or "unknown",
            "links": [],
        }

    if _looks_like_no_image_seen_text(answer):
        if not fallback_attempted:
            fb_answer, fb_err = _try_fallback_image_model()
            if fb_answer.strip() and not _looks_like_no_image_seen_text(fb_answer):
                return {
                    "answer": fb_answer.strip(),
                    "source": f"OpenClaw Gateway图片识别(回退:{fallback_model})",
                    "confidence": 0.62,
                    "intent": "ai_image_fallback",
                    "links": [],
                }
            if fb_err and _is_rate_limit_text(fb_err):
                return {
                    "answer": "图片识别服务当前请求较多，触发限流。请稍后再试，或先补充文字描述我来处理。",
                    "source": "OpenClaw图片识别/限流",
                    "confidence": 0.2,
                    "intent": "ai_image_unavailable",
                    "debug_reason": fb_err,
                    "links": [],
                }
        return {
            "answer": "图片我收到了，但模型没有成功读取到图像内容。请重新发送原图（不要转发压缩图），我再为你识别。",
            "source": "OpenClaw图片识别/未读取到图像",
            "confidence": 0.2,
            "intent": "ai_image_unavailable",
            "debug_reason": "openclaw_spawn_no_image_context(primary)",
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

    min_chars = max(2, int(os.getenv("QA_AI_FALLBACK_MIN_CHARS", "4")))
    if len(question) < min_chars:
        return None

    skip_patterns = _parse_csv(os.getenv("QA_AI_FALLBACK_SKIP_PATTERNS"), DEFAULT_SKIP_PATTERNS)
    if _is_smalltalk(question, skip_patterns):
        return None

    require_question = _parse_bool(
        os.getenv("QA_AI_FALLBACK_REQUIRE_QUESTION"),
        provider == "openclaw",
    )
    if require_question and not _looks_like_question(question):
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
            return {
                "answer": "我先记下这个问题了。当前群还没开通 AI 检索兜底，建议先 @助教，我这边会继续补充到意图库。",
                "source": "OpenClaw/未开通",
                "confidence": 0.2,
                "intent": "ai_search_unavailable",
                "links": [],
            }
        enable_rate_guard = _parse_bool(
            os.getenv("QA_AI_FALLBACK_ENABLE_RATE_GUARD"),
            True,
        )
        if enable_rate_guard and not _reserve_ai_fallback_slot(chat_id):
            wait_seconds = max(30, _parse_int(os.getenv("QA_AI_FALLBACK_WINDOW_SECONDS"), 300))
            return {
                "answer": f"我正在处理其他检索请求，避免拥堵请约 {wait_seconds} 秒后再问一次；紧急问题可直接 @助教。",
                "source": "OpenClaw/排队中",
                "confidence": 0.22,
                "intent": "ai_search_unavailable",
                "links": [],
            }
        answer_text, items, err = _search_with_openclaw_agent(
            question=question,
            max_results=max_results,
            timeout_seconds=openclaw_timeout,
        )
        if err:
            if _is_rate_limit_text(err):
                answer = "当前检索请求较多，服务触发限流。请 30-60 秒后再试；紧急问题可直接 @助教。"
                source = "OpenClaw/限流"
            elif err.startswith("openclaw_cooldown_active:"):
                matched = re.search(r"(\d+)s", err)
                left = matched.group(1) if matched else "几十"
                answer = f"检索服务正在自动恢复中，预计约 {left} 秒后可用。你可以稍后再问一次，或 @助教。"
                source = "OpenClaw/恢复中"
            else:
                answer = "我这边检索服务刚刚波动，暂时没拿到结果。请稍后重试，或 @助教。"
                source = "OpenClaw/不可用"
            return {
                "answer": answer,
                "source": source,
                "confidence": 0.2,
                "intent": "ai_search_unavailable",
                "links": [],
            }

        if not answer_text and not items:
            return {
                "answer": (
                    "已执行 OpenClaw 检索，但暂未检索到可用结果。请补充关键词后再试。"
                ),
                "source": "OpenClaw/无结果",
                "confidence": 0.25,
                "intent": "ai_search_no_result",
                "links": [],
            }

        answer = answer_text or _build_scoped_answer(items)
        links = [{"name": item.title[:32], "url": item.url} for item in items]
        confidence = min(0.78, 0.48 + len(items) * 0.08)
        return {
            "answer": answer,
            "source": "OpenClaw",
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
