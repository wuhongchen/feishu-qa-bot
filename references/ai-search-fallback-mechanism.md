# AI Search Fallback Mechanism

Core implementation: `ai_search_fallback.py`

## Trigger

- Only runs when local intent does not match.
- Controlled by `QA_ENABLE_AI_FALLBACK` (default false).

## Scope boundary

- Search source is restricted by `QA_AI_SEARCH_ALLOWED_DOMAINS`.
- Out-of-scope topics are blocked by `QA_AI_FALLBACK_BLOCKED_KEYWORDS`.
- Small-talk is skipped by `QA_AI_FALLBACK_SKIP_PATTERNS`.

## Output boundary

- Reply is built only from retrieved snippets and links.
- Includes explicit boundary note: no out-of-scope inference.
- If no reliable results: returns "no result in scoped sources" guidance.

## Safety defaults

- Fallback is off by default.
- If provider misconfigured, returns config warning instead of hallucinated answer.
