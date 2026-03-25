---
name: feishu-qa-bot-skill
version: 1.5.1
description: 飞书群聊问答机器人技能，支持意图优先、OpenClaw 文本/图片兜底、Bitable 全量记录、知识库同步与生产调度。
author: hongchen
license: MIT
capabilities:
  - id: intent-priority-qa
    description: 本地意图库优先匹配，命中后直接回复标准答案
  - id: openclaw-text-fallback
    description: 意图未命中时走 OpenClaw Gateway 会话链路进行文本兜底
  - id: openclaw-image-understanding
    description: 图片消息直接走 OpenClaw 多模态识别并回复
  - id: poller-dedup-and-lock
    description: 轮询消息去重 + 全局进程锁，避免并发重复回复
  - id: skip-already-replied
    description: 检测话题中已存在机器人回复时跳过二次补答
  - id: bitable-recording
    description: 问答结果写入飞书多维表格，沉淀问题池与命中信息
  - id: nps-feedback
    description: 首轮有效答复后可触发 NPS 评分采集
  - id: kb-sync
    description: 从本地/URL/飞书 Wiki 同步知识库到 intents.json
  - id: kb-sync-callable
    description: 提供可直接调用的知识库更新方法（支持可选同步到意图库）
  - id: intent-sync-bitable
    description: 将 intents.json upsert 到飞书多维表格“意图库”
  - id: intent-match-callable
    description: 提供可直接调用的单条意图识别方法（返回命中结果 JSON）
  - id: admin-alert
    description: 连续异常达到阈值时发送管理员告警（可配置冷却）
  - id: scheduler-ready
    description: 支持系统 cron 与 OpenClaw cron 的稳定调度
permissions:
  network: true
  filesystem: true
  shell: true
  clipboard: false
  env:
    - FEISHU_APP_ID
    - FEISHU_APP_SECRET
    - FEISHU_TOKEN_CACHE_FILE
    - QA_BITABLE_TOKEN
    - QA_BITABLE_BASE_URL
    - QA_TABLE_ID
    - QA_TABLE_NAME
    - QA_CHAT_ID
    - QA_CHAT_NAMES
    - ADMIN_USER_ID
    - QA_ENABLE_NPS
    - QA_APPEND_INTENT_NOTE
    - QA_APPEND_INTENT_NOTE_ON_UNMATCH
    - QA_REPLY_APPEND_META
    - QA_REPLY_REQUIRE_MENTION
    - QA_MAX_ROUNDS
    - QA_SESSION_TTL_MINUTES
    - QA_PROCESS_WINDOW_MINUTES
    - QA_FETCH_PAGE_SIZE
    - QA_ENABLE_THREAD_MESSAGES
    - QA_THREAD_FETCH_MAX
    - QA_THREAD_PAGE_SIZE
    - QA_MESSAGE_TRIGGER_MODE
    - QA_MSG_DEDUP_CACHE_FILE
    - QA_MSG_DEDUP_TTL_MINUTES
    - QA_POLLER_LOCK_FILE
    - QA_KB_SOURCE
    - QA_KB_TARGET
    - QA_KB_BACKUP_DIR
    - QA_KB_TIMEOUT
    - QA_KB_STRICT
    - QA_KB_SOURCE_HEADERS
    - QA_SYNC_INTENTS_TO_BITABLE
    - QA_INTENT_TABLE_ID
    - QA_INTENT_TABLE_NAME
    - QA_INTENT_BACKFILL_EXISTING
    - QA_INTENT_BACKLOG_FILE
    - QA_ENABLE_AI_FALLBACK
    - QA_AI_SEARCH_PROVIDER
    - QA_OPENCLAW_SEARCH_AGENT
    - QA_OPENCLAW_SEARCH_MODEL
    - QA_OPENCLAW_IMAGE_MODEL
    - QA_OPENCLAW_IMAGE_FALLBACK_MODEL
    - QA_OPENCLAW_GATEWAY_CHAT_IDS
    - QA_OPENCLAW_TIMEOUT_SECONDS
    - QA_OPENCLAW_PROCESS_TIMEOUT_SECONDS
    - QA_OPENCLAW_GATEWAY_TIMEOUT_MS
    - QA_OPENCLAW_COOLDOWN_SECONDS
    - QA_OPENCLAW_COOLDOWN_FAILURE_STREAK
    - QA_OPENCLAW_PARENT_SESSION_KEY
    - QA_OPENCLAW_BIN
    - OPENCLAW_BIN
    - QA_ENABLE_IMAGE_UNDERSTANDING
    - QA_IMAGE_MAX_BYTES
    - QA_IMAGE_IGNORE_OPENCLAW_COOLDOWN
    - QA_TAVILY_API_KEY
    - QA_AI_SEARCH_MAX_RESULTS
    - QA_AI_SEARCH_TIMEOUT
    - QA_AI_SEARCH_DEPTH
    - QA_AI_REPLY_APPEND_META
    - QA_AI_FALLBACK_MIN_CHARS
    - QA_AI_FALLBACK_REQUIRE_QUESTION
    - QA_AI_FALLBACK_ENABLE_RATE_GUARD
    - QA_AI_FALLBACK_MAX_CALLS_PER_WINDOW
    - QA_AI_FALLBACK_WINDOW_SECONDS
    - QA_AI_FALLBACK_RATE_CACHE_FILE
    - QA_AI_FALLBACK_SKIP_PATTERNS
    - QA_AI_FALLBACK_BLOCKED_KEYWORDS
    - QA_NOTIFY_ON_IDLE
    - QA_CYCLE_LOG_FILE
    - QA_CYCLE_LOCK_DIR
    - QA_ADMIN_ALERT_ENABLED
    - QA_ADMIN_ALERT_CHAT_ID
    - QA_ADMIN_ALERT_THRESHOLD
    - QA_ADMIN_ALERT_COOLDOWN_SECONDS
    - QA_ADMIN_ALERT_STATE_FILE
    - QA_ALLOW_DAEMON_WITH_CRON
inputs:
  - name: mode
    type: string
    required: false
    default: poll
    description: 运行模式，支持 poll、single、kb-sync、intent-match、full-cycle
  - name: chat_id
    type: string
    required: false
    default: ""
    description: single 模式下的群聊 ID
  - name: sender_id
    type: string
    required: false
    default: ""
    description: single 模式下的发送者 ID
  - name: message
    type: string
    required: false
    default: ""
    description: single 模式下的问题文本
  - name: mentions_json
    type: string
    required: false
    default: "[]"
    description: single 模式下 @ 信息 JSON 数组
  - name: kb_source_override
    type: string
    required: false
    default: ""
    description: 可选覆盖知识源（未提供则使用 QA_KB_SOURCE）
  - name: question
    type: string
    required: false
    default: ""
    description: intent-match 模式下用于识别的单条问题文本
  - name: sync_intents_after_kb
    type: boolean
    required: false
    default: false
    description: kb-sync 模式完成后，是否继续把 intents.json 同步到飞书“意图库”
  - name: force_reload_intents
    type: boolean
    required: false
    default: false
    description: intent-match 模式下是否先强制重载 intents.json 再识别
outputs:
  - name: reply_text
    type: string
    description: 机器人最终回复文本
  - name: poll_result
    type: json
    description: 轮询结果摘要（处理条数、跳过条数、错误、记录 ID）
  - name: broadcast_text
    type: string
    description: 本轮播报文案（用于 cron 回显）
  - name: kb_sync_result
    type: json
    description: 知识库同步结果（是否更新、备份、校验）
  - name: intent_sync_result
    type: json
    description: 意图库同步结果（created/updated/errors）
  - name: intent_match_result
    type: json
    description: 单条意图识别结果（matched/intent_id/confidence/keywords）
  - name: debug_result
    type: json
    description: 单条调试输出（matched、reply、record）
  - name: admin_alert_result
    type: json
    description: 管理员告警发送结果（是否触发、是否发送成功）
tags:
  - feishu
  - qa-bot
  - openclaw
  - image-understanding
  - bitable
  - scheduler
  - intent-classification
minOpenClawVersion: 0.1.0
---

# Feishu QA Bot Skill

## 1. 技能定位

这是一个用于飞书群聊的生产型问答技能：

- 群消息自动处理（支持非 @）
- 意图优先、AI 兜底
- 图片消息优先走多模态识别
- 所有问答入库并可回溯
- 支持知识库与意图库持续同步

## 2. 完整运行链路

1. `group_qa_poller_v3.py` 轮询配置群消息（含话题消息）。
2. 先做去重与锁控：
   - 消息去重缓存（`QA_MSG_DEDUP_CACHE_FILE`）
   - 全局进程锁（`QA_POLLER_LOCK_FILE`）
3. 对每条消息执行处理：
   - 文本：`group_qa_handler.py`（意图优先）
   - 图片：`build_ai_image_rule(...)`（OpenClaw 多模态）
4. 文本未命中时触发 `ai_search_fallback.py`：
   - `sessions.create -> sessions.send -> agent.wait -> sessions.preview`
5. 回复后写入 Bitable（问答主表）。
6. 未命中问题写入 backlog（用于后续补意图）。
7. 连续异常达到阈值时，仅向管理员会话告警。

## 3. 关键机制（已稳定）

### 3.1 防重复回复

- 轮询全局锁：避免 cron/手动/daemon 并发重复消费。
- 已回复跳过：如果同话题里该消息已有 `app` 回复，不再补答。
- daemon 与 cron 互斥：默认禁止同时运行。

### 3.2 图片识别策略

- `msg_type=image` 或带图片的 `post` 优先走图片识别。
- 识别失败时才回退到“暂不可用”提示。
- 图片大小上限：`QA_IMAGE_MAX_BYTES`（默认 5MB）。

### 3.3 OpenClaw 调用稳定性

- 自动解析 `openclaw` 可执行路径（支持 cron 环境）。
- 子进程 PATH 自动补全（含 `node` 常见路径）。
- 超时 + 冷却 + 连续失败阈值控制。

### 3.4 管理员告警（不打扰业务群）

- `QA_ADMIN_ALERT_ENABLED=true`
- `QA_ADMIN_ALERT_CHAT_ID=...`
- `QA_ADMIN_ALERT_THRESHOLD=3`
- `QA_ADMIN_ALERT_COOLDOWN_SECONDS=1800`

## 4. 推荐配置（最小可用）

必填：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `QA_CHAT_ID`
- `QA_BITABLE_BASE_URL`（推荐）或 `QA_BITABLE_TOKEN + QA_TABLE_ID`

推荐打开：

- `QA_MESSAGE_TRIGGER_MODE=all`
- `QA_ENABLE_AI_FALLBACK=true`
- `QA_AI_SEARCH_PROVIDER=openclaw`
- `QA_ENABLE_IMAGE_UNDERSTANDING=true`
- `QA_SYNC_INTENTS_TO_BITABLE=true`

## 5. 运行与调度

### 5.1 单次轮询

```bash
python3 group_qa_poller_v3.py
```

### 5.2 全周期入口（推荐生产）

```bash
bash scripts/run_qa_cycle.sh
```

全周期默认：知识同步（可选）-> 意图库同步（可选）-> 群消息轮询。

### 5.3 OpenClaw 注入与调度

```bash
bash scripts/inject_openclaw_skill.sh
bash scripts/register_openclaw_cron.sh
openclaw cron list --all --json
```

### 5.4 OpenClaw 可调用方法

统一入口（推荐）：

```bash
# 1) 知识库更新（可选：同步到意图库）
bash scripts/skill_call.sh --mode kb-sync --source "https://waytoagi.feishu.cn/wiki/WewVwPVkyipyaCka7twcwPZMnJf" --dry-run
bash scripts/skill_call.sh --mode kb-sync --source "https://waytoagi.feishu.cn/wiki/WewVwPVkyipyaCka7twcwPZMnJf" --force --sync-intents

# 2) 单条意图识别
bash scripts/skill_call.sh --mode intent-match --question "coding plan 是什么" --reload
```

分离入口（兼容旧调用）：

1. 知识库更新（可选带意图库同步）

```bash
# 基础更新：按 QA_KB_SOURCE 更新 intents.json
bash scripts/skill_kb_update.sh

# 指定源并强制写入；完成后同步到飞书“意图库”
bash scripts/skill_kb_update.sh \
  --source "https://waytoagi.feishu.cn/wiki/WewVwPVkyipyaCka7twcwPZMnJf" \
  --force \
  --sync-intents
```

2. 单条意图识别（用于 OpenClaw 调用/排障）

```bash
# 识别一条问题，输出结构化 JSON
bash scripts/skill_intent_match.sh --question "coding plan 是什么"

# 先强制重载 intents.json 再识别
bash scripts/skill_intent_match.sh --question "第4课回放在哪" --reload
```

说明：
- 两个方法都先执行 `scripts/load_env.sh`，保证在 OpenClaw/Cron 环境下也能读取 `.env`。
- 输出均为 JSON，便于被 OpenClaw gateway/cron/task 直接消费。
- 新增统一调度脚本：`scripts/skill_call.sh`，可通过 `--mode` 路由到知识库更新或意图识别。

## 6. 运维准则

- 只保留一个消息入口：
  - 推荐 `cron + run_qa_cycle.sh`
  - 不建议再开 `start_local_poller_daemon.sh`
- 如必须使用 daemon，需显式允许：
  - `QA_ALLOW_DAEMON_WITH_CRON=true`

## 7. 自检命令

```bash
python3 -m py_compile *.py scripts/*.py
python3 scripts/run_single_message.py --chat-id oc_xxx --sender-id ou_xxx --message "测试一下"
openclaw gateway call sessions.create --json --timeout 12000 --params '{"agentId":"main","label":"qa-health"}'
```

## 8. 常见问题

1. 图片偶发“已收到但无法识别”
- 优先检查 `QA_OPENCLAW_IMAGE_MODEL` 是否支持视觉。
- 再查 `gateway.log` 是否有 `sessions.send/agent.wait` 超时。

2. 出现重复回复
- 检查是否同时启用了 cron 和 daemon。
- 检查锁文件与去重缓存路径是否被改动。

3. Cron 下 OpenClaw 不可用
- 设置 `QA_OPENCLAW_BIN` 或确认 `~/.local/bin/openclaw` 可用。
- 确认系统可找到 `node`。

4. 能回复但不能写表
- 检查 Bitable 权限和 `QA_TABLE_ID / QA_TABLE_NAME`。

## 9. 参考文档

- `references/intent-library-mechanism.md`
- `references/kb-sync-mechanism.md`
- `references/intent-to-bitable-sync-mechanism.md`
- `references/ai-search-fallback-mechanism.md`
- `references/reply-template-guidelines.md`
