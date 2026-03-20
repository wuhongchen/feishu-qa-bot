# AI Search Fallback Mechanism

Core implementation: `ai_search_fallback.py`

## Trigger

- Only runs when local intent does not match.
- Controlled by `QA_ENABLE_AI_FALLBACK`.
- With `QA_AI_SEARCH_PROVIDER=openclaw` (default), fallback is enabled by default.

## Scope boundary

- Out-of-scope topics are blocked by `QA_AI_FALLBACK_BLOCKED_KEYWORDS`.
- Small-talk is skipped by `QA_AI_FALLBACK_SKIP_PATTERNS`.

## Output boundary

- Reply is built only from retrieved snippets and links.
- Includes explicit boundary note: no out-of-scope inference.
- If no reliable results: returns "no result in scoped sources" guidance.

## Safety defaults

- If provider misconfigured, returns config warning instead of hallucinated answer.
- If OpenClaw execution fails, returns a Chinese availability notice instead of raw technical error.

## Image understanding path

- `group_qa_poller_v3.py` routes `msg_type=image` directly to OpenClaw multimodal capability.
- Image is downloaded from Feishu resource API, converted to base64 attachment, then sent via `openclaw gateway call agent`.
- This path replies in natural language directly and does not append technical meta template.
