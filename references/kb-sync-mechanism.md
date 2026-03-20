# Knowledge Base Sync Mechanism

Core implementation: `scripts/sync_knowledge_base.py`

## Source types

- Local file path
- HTTP/HTTPS endpoint returning JSON
- Feishu Wiki page URL (extract page text and merge into existing intents)

## Update guarantees

- Validate source payload before apply
- Strict mode by default (`QA_KB_STRICT=true`)
- Skip write if digest unchanged
- Backup current intents before update
- Atomic replace to avoid partial write
- Wiki mode uses incremental merge; it does not wipe all existing intents

## Runtime outputs

Script returns JSON summary including:

- `updated`
- `intents_count`
- `backup_path`
- `warnings`
- `error` (if failed)
