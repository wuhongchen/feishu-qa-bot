# Intent To Bitable Sync Mechanism

Core implementation: `scripts/sync_intents_to_bitable.py`

## Goal

- Keep `intents.json` continuously synchronized to the same Bitable table as QA records.
- Use upsert key `会话ID = intent::<intent_id>` to avoid duplicate records.

## How it works

1. Resolve Bitable target from:
   - `QA_BITABLE_TOKEN` / `QA_TABLE_ID`, or
   - `QA_BITABLE_BASE_URL` (+ optional `QA_TABLE_NAME`)
2. Load normalized intents from `intent_classifier_v5.load_intents()`.
3. List existing records and build map for `intent::*`.
4. Upsert each intent:
   - existing key -> update
   - missing key -> create

## Scheduling

- Included in `scripts/run_qa_cycle.sh` when `QA_SYNC_INTENTS_TO_BITABLE=true`.
- Can run separately via `scripts/run_intent_sync_once.sh`.
