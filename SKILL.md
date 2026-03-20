---
name: feishu-qa-bot-skill
version: 1.4.0
description: 飞书群聊问答机器人，支持动态意图分类、受控 AI 搜索兜底、NPS 收集、多维表格记录和可调度的知识库同步。
author: hongchen
license: MIT
capabilities:
  - id: classify-intent
    description: 基于 intents.json 的动态意图识别（支持去重、阈值、优先级、排除词）
  - id: reply-with-template
    description: 按统一文案模板生成结构化回复并附带来源与链接
  - id: append-intent-note
    description: 保持原始问答主流程不变，在会话回复中补充意图识别说明
  - id: ai-search-fallback
    description: 本地意图未命中时执行 AI 搜索兜底
  - id: collect-nps
    description: 首轮有效答复后触发 0-10 分评分并记录 NPS 状态
  - id: poll-feishu-groups
    description: 定时轮询多个飞书群消息并自动触发问答流程
  - id: write-bitable-record
    description: 将问答结果、置信度、意图分类、NPS 状态写入飞书多维表格
  - id: sync-knowledge-base
    description: 从本地/远程 JSON 或飞书 Wiki 增量同步知识库并校验、备份、原子写入
  - id: sync-intents-to-bitable
    description: 将 intents.json 以 upsert 方式同步到飞书表格，确保意图库可追踪
  - id: schedule-full-cycle
    description: 支持 poll、kb-sync、full-cycle 三类调度，满足生产持续运行
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
    - QA_MAX_ROUNDS
    - QA_SESSION_TTL_MINUTES
    - QA_PROCESS_WINDOW_MINUTES
    - QA_MSG_DEDUP_CACHE_FILE
    - QA_MSG_DEDUP_TTL_MINUTES
    - QA_KB_SOURCE
    - QA_KB_TARGET
    - QA_KB_BACKUP_DIR
    - QA_KB_TIMEOUT
    - QA_KB_STRICT
    - QA_KB_SOURCE_HEADERS
    - QA_SYNC_INTENTS_TO_BITABLE
    - QA_ENABLE_AI_FALLBACK
    - QA_AI_SEARCH_PROVIDER
    - QA_TAVILY_API_KEY
    - QA_AI_SEARCH_MAX_RESULTS
    - QA_AI_SEARCH_TIMEOUT
    - QA_AI_SEARCH_DEPTH
    - QA_AI_FALLBACK_MIN_CHARS
    - QA_AI_FALLBACK_SKIP_PATTERNS
    - QA_AI_FALLBACK_BLOCKED_KEYWORDS
inputs:
  - name: mode
    type: string
    required: false
    default: poll
    description: 运行模式，支持 poll、single、kb-sync、full-cycle
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
outputs:
  - name: reply_text
    type: string
    description: 机器人最终回复文本
  - name: record_fields
    type: json
    description: 写入多维表格的结构化字段
  - name: poll_result
    type: json
    description: 轮询结果摘要（处理条数、错误、记录 ID）
  - name: kb_sync_result
    type: json
    description: 知识库同步结果（是否更新、备份位置、校验告警）
  - name: intent_sync_result
    type: json
    description: 意图库同步结果（created/updated/errors）
  - name: debug_result
    type: json
    description: 单条调试模式输出（matched、reply、record）
tags:
  - feishu
  - qa-bot
  - group-chat
  - intent-classification
  - ai-search-fallback
  - nps
  - bitable
  - scheduler
minOpenClawVersion: 0.1.0
---

# Feishu QA Bot Skill

这是一个可直接落地的飞书群答疑技能，覆盖从配置、运行、智能边界到调度运维的完整链路。

## 1. 端到端机制

1. `group_qa_poller_v3.py` 轮询目标群消息并过滤旧消息。
2. `group_qa_handler.py` 做会话管理、意图匹配、回复生成、NPS 状态管理。
3. 所有问题（含未命中）都会写入 bitable 记录，保证问题池完整。
4. 未命中时（可选）进入 `ai_search_fallback.py` 进行受控搜索兜底。
5. `scripts/sync_knowledge_base.py` 定时把外部知识同步到 `intents.json`。
6. `scripts/sync_intents_to_bitable.py` 把意图库内容同步到同一张表。
7. `scripts/run_qa_cycle.sh` 可实现“同步知识 -> 同步意图库 -> 轮询答复”的全周期调度。

## 2. 使用配置（按优先级）

### 2.1 必填配置

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `QA_CHAT_ID`
- `QA_BITABLE_TOKEN`（或 `QA_BITABLE_BASE_URL`）
- `QA_TABLE_ID`（可选，未填时自动按 `QA_TABLE_NAME` 或首表解析）

### 2.2 行为配置

- `QA_ENABLE_NPS=true|false`：是否开启 NPS。
- `QA_APPEND_INTENT_NOTE=true|false`：命中意图时是否附加简短识别说明。
- `QA_APPEND_INTENT_NOTE_ON_UNMATCH=true|false`：未命中时是否也附加说明。
- `QA_MAX_ROUNDS=0|N`：单会话最大轮次，`0` 表示不限制。
- `QA_SESSION_TTL_MINUTES=30`：会话超时重建阈值。
- `QA_PROCESS_WINDOW_MINUTES=5`：轮询时消息时间窗口。
- `QA_MSG_DEDUP_CACHE_FILE`：消息去重缓存文件。
- `QA_MSG_DEDUP_TTL_MINUTES`：去重缓存过期分钟数。
- `ADMIN_USER_ID=ou_xxx`：轮次过多时 @ 人工。

### 2.3 知识库同步配置

- `QA_KB_SOURCE`：本地 JSON、JSON URL 或飞书 Wiki URL。
- `QA_KB_TARGET`：目标 `intents.json`。
- `QA_KB_BACKUP_DIR`：备份目录。
- `QA_KB_TIMEOUT`：拉取超时秒数。
- `QA_KB_STRICT=true|false`：是否严格校验。
- `QA_KB_SOURCE_HEADERS`：可选请求头 JSON。
- `QA_SYNC_INTENTS_TO_BITABLE=true|false`：是否在周期任务中同步意图库到表格。

### 2.4 AI 搜索兜底配置（默认 OpenClaw）

- `QA_ENABLE_AI_FALLBACK=true|false`
- `QA_AI_SEARCH_PROVIDER=openclaw`
- `QA_OPENCLAW_SEARCH_AGENT=main`
- `QA_TAVILY_API_KEY=tvly-...`
- `QA_AI_SEARCH_MAX_RESULTS=3`
- `QA_AI_SEARCH_TIMEOUT=10`
- `QA_AI_SEARCH_DEPTH=basic`
- `QA_AI_FALLBACK_MIN_CHARS=4`
- `QA_AI_FALLBACK_SKIP_PATTERNS=...`
- `QA_AI_FALLBACK_BLOCKED_KEYWORDS=...`

## 3. 运行方式

### 3.1 轮询模式（生产主入口）

```bash
python3 group_qa_poller_v3.py
```

### 3.2 单条调试（问题复现）

```bash
python3 scripts/run_single_message.py \
  --chat-id oc_xxx \
  --sender-id ou_xxx \
  --message "作业什么时候截止？"
```

### 3.3 知识库同步

```bash
python3 scripts/sync_knowledge_base.py --source "$QA_KB_SOURCE"
```

### 3.4 全周期执行（推荐）

```bash
bash scripts/run_qa_cycle.sh
```

### 3.5 注入 OpenClaw 技能目录

```bash
bash scripts/inject_openclaw_skill.sh
```

## 4. 调度方案（生产建议）

### 4.1 单任务全周期（推荐中小规模群）

- 模板：`templates/crontab.fullcycle.example`
- 策略：每分钟执行一次 `run_qa_cycle.sh`。
- 作用：每次先同步知识，再处理消息，保证回答尽量使用最新知识。

### 4.2 拆分调度（推荐中大型群）

- 模板：`templates/crontab.example` + `templates/crontab.kb-sync.example`
- 策略：消息轮询每分钟，知识同步每 10 分钟。
- 作用：降低每次轮询耗时，让消息处理更稳定。

### 4.3 运营增强调度（推荐）

- 模板：`templates/crontab.ops.example`
- 策略：轮询 + 知识同步 + 每日冒烟检查。
- 作用：兼顾可用性、知识新鲜度和日常健康检查。

### 4.4 OpenClaw Cron 调度

```bash
bash scripts/register_openclaw_cron.sh
```

## 5. 智能能力边界与方向

### 5.1 当前智能能力边界

- 本地意图优先，兜底搜索只在未命中时触发。
- 命中禁用词直接拒答并引导人工，不做高风险建议。
- 回答以证据摘要为主，不做无来源推断。

### 5.2 后续智能方向（建议迭代）

1. 增加“意图置信度低但非零”的二次检索策略。
2. 用 NPS 低分样本反哺 `intents.json` 优化关键词和文案。
3. 增加来源质量分层（官方文档 > 群公告 > 其他站点）。
4. 增加“转人工原因码”统计，形成知识缺口看板。

## 6. 回复文案与模板治理

- 回复模板遵循：结论先行 -> 操作步骤 -> 下一步引导 -> 可选链接。
- 模板规范：`references/reply-template-guidelines.md`
- 机制文档：
  - `references/intent-library-mechanism.md`
  - `references/kb-sync-mechanism.md`
  - `references/intent-to-bitable-sync-mechanism.md`
  - `references/ai-search-fallback-mechanism.md`

## 7. 运维检查清单

1. 环境变量完成且敏感值有效。
2. `python3 -m py_compile *.py scripts/*.py` 通过。
3. `python3 scripts/run_single_message.py ...` 单条调试通过。
4. `python3 scripts/sync_knowledge_base.py --source "$QA_KB_SOURCE" --dry-run` 通过。
5. 轮询日志和同步日志可持续写入且无连续错误。

## 8. 失败处理约定

- Token 获取失败：轮询退出并输出错误 JSON。
- 意图未命中：不静默，直接走 OpenClaw 搜索；无结果时返回搜索状态提示，并沉淀到意图补充库。
- AI 兜底无结果：给出“限定范围无可靠信息”提示并引导人工。
- 多维表格配置缺失：允许回复，记录写入失败信息。
- 超轮次：自动转人工并可 @ `ADMIN_USER_ID`。
