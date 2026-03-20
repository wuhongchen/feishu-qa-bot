#!/usr/bin/env python3
"""Scoped AI search fallback for unmatched QA questions."""

import os
import re
import json
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests


DEFAULT_ALLOWED_DOMAINS = [
    "waytoagi.feishu.cn",
    "t0woxppdywz.feishu.cn",
    "docs.openclaw.ai",
    "openclaw.ai",
    "clawhub.com",
]
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


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: Optional[str], default_values: List[str]) -> List[str]:
    if not value:
        return list(default_values)
    return [x.strip() for x in value.split(",") if x.strip()]


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_allowed_domain(url: str, allowed_domains: List[str]) -> bool:
    domain = _extract_domain(url)
    if not domain:
        return False
    for allowed in allowed_domains:
        allowed = allowed.lower()
        if domain == allowed or domain.endswith(f".{allowed}"):
            return True
    return False


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


def _search_with_tavily(question: str, allowed_domains: List[str], max_results: int, timeout: int) -> List[SearchItem]:
    api_key = os.getenv("QA_TAVILY_API_KEY", "").strip()
    if not api_key:
        return []

    payload = {
        "api_key": api_key,
        "query": question,
        "search_depth": os.getenv("QA_AI_SEARCH_DEPTH", "basic"),
        "max_results": max(1, min(8, max_results)),
        "include_answer": False,
        "include_domains": allowed_domains,
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
        if not _is_allowed_domain(url, allowed_domains):
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


def _search_with_openclaw_agent(
    question: str,
    allowed_domains: List[str],
    max_results: int,
    timeout: int,
) -> Tuple[str, List[SearchItem], str]:
    agent_id = os.getenv("QA_OPENCLAW_SEARCH_AGENT", "main").strip() or "main"
    model = os.getenv("QA_OPENCLAW_SEARCH_MODEL", "").strip()
    domain_text = ", ".join(allowed_domains[:10])

    prompt = (
        "你是知识检索助手。请先检索再回答。\n"
        f"仅允许引用这些域名：{domain_text}\n"
        "输出必须是严格 JSON，不要输出任何额外文本：\n"
        "{\n"
        '  "answer": "给用户的简洁回答（120字内）",\n'
        '  "sources": [{"title":"标题","url":"https://...","snippet":"20-80字摘要"}]\n'
        "}\n"
        f"sources 最多 {max(1, min(6, max_results))} 条。\n"
        f"用户问题：{question}"
    )

    cmd = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--json",
        "--timeout",
        str(max(10, timeout)),
        "--message",
        prompt,
    ]
    if model:
        cmd.extend(["--model", model])

    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(15, timeout + 10),
        )
    except Exception as exc:
        return "", [], f"openclaw_exec_failed: {exc}"

    combined = "\n".join([completed.stdout or "", completed.stderr or ""]).strip()
    if completed.returncode != 0:
        return "", [], f"openclaw_non_zero_exit: {combined[:240]}"

    payload = _extract_first_json_block(combined)
    if not isinstance(payload, dict):
        return "", [], "openclaw_output_not_json"

    answer = str(payload.get("answer", "")).strip()
    sources_raw = payload.get("sources", [])
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
            if not _is_allowed_domain(url, allowed_domains):
                continue
            items.append(SearchItem(title=title, url=url, snippet=snippet))
            if len(items) >= max(1, min(8, max_results)):
                break

    return answer, items, ""


def _build_scoped_answer(items: List[SearchItem], allowed_domains: List[str]) -> str:
    lines = ["我没有命中本地知识库，但在限定范围内检索到这些信息："]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item.title}：{item.snippet}")

    domains = "、".join(allowed_domains[:5])
    lines.append("")
    lines.append(f"边界说明：仅基于白名单站点（如 {domains}）公开信息，不做范围外推断。")
    lines.append("如果你希望，我可以继续按这个问题给你缩小到 1-2 条最相关入口。")
    return "\n".join(lines)


def build_ai_search_rule(question: str) -> Optional[Dict[str, object]]:
    """Return a pseudo-intent rule for unmatched question, or None."""
    provider = os.getenv("QA_AI_SEARCH_PROVIDER", "openclaw").strip().lower()
    enabled_default = provider == "openclaw"
    enabled = _parse_bool(os.getenv("QA_ENABLE_AI_FALLBACK"), enabled_default)
    if not enabled:
        return None

    question = str(question or "").strip()
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

    allowed_domains = _parse_csv(os.getenv("QA_AI_SEARCH_ALLOWED_DOMAINS"), DEFAULT_ALLOWED_DOMAINS)
    max_results = max(1, int(os.getenv("QA_AI_SEARCH_MAX_RESULTS", "3")))
    timeout = max(3, int(os.getenv("QA_AI_SEARCH_TIMEOUT", "10")))

    if provider == "openclaw":
        answer_text, items, err = _search_with_openclaw_agent(
            question=question,
            allowed_domains=allowed_domains,
            max_results=max_results,
            timeout=timeout,
        )
        if err:
            return {
                "answer": (
                    "未命中本地知识库，且 OpenClaw 搜索当前不可用。"
                    "\n我会先进入普通问答兜底，并把该问题加入意图补充库。"
                ),
                "source": "OpenClaw搜索/不可用",
                "confidence": 0.2,
                "intent": "ai_search_unavailable",
                "links": [],
            }

        if not answer_text and not items:
            return {
                "answer": (
                    "我没有命中本地知识库，也未在限定搜索范围内找到足够可靠的信息。"
                    "\n我会先按普通问答给你可执行建议。"
                ),
                "source": "OpenClaw搜索/无结果",
                "confidence": 0.25,
                "intent": "ai_search_no_result",
                "links": [],
            }

        answer = answer_text or _build_scoped_answer(items, allowed_domains)
        links = [{"name": item.title[:32], "url": item.url} for item in items]
        confidence = min(0.78, 0.48 + len(items) * 0.08)
        return {
            "answer": answer,
            "source": "OpenClaw搜索/白名单站点",
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

    items = _search_with_tavily(
        question=question,
        allowed_domains=allowed_domains,
        max_results=max_results,
        timeout=timeout,
    )
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

    answer = _build_scoped_answer(items, allowed_domains)
    links = [{"name": item.title[:32], "url": item.url} for item in items]
    confidence = min(0.75, 0.45 + len(items) * 0.08)
    return {
        "answer": answer,
        "source": "AI搜索/白名单站点",
        "confidence": confidence,
        "intent": "ai_search_fallback",
        "links": links,
    }
