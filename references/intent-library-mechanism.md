# Intent Library Mechanism

Core implementation: `intent_classifier_v5.py`

## What is optimized

- Schema normalization for each intent item
- Duplicate ID detection
- Optional controls per intent:
  - `enabled` (default true)
  - `priority` (default 0)
  - `exclude_keywords` (skip matching if these words appear)
  - `min_keyword_hits` (minimum keyword matches)
- Better cache strategy:
  - TTL cache (5 minutes)
  - Immediate reload when intents file mtime changes
- Deterministic tie-break:
  - score > priority > keyword_hits
- Atomic write for intent updates

## Backward compatibility

Old intent JSON still works without adding new optional fields.
