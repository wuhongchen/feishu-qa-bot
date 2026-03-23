# Feishu QA Bot Skill

飞书群聊智能问答机器人项目。该项目通过轮询飞书群消息，使用意图分类匹配知识库答案，并自动回复与入库。

## 当前能力

- 基于 `intents.json` 的动态意图分类（支持热重载）
- 意图库条目校验（去重、字段归一化、可启停、优先级与排除词）
- 自动群聊问答（配置群内消息都会处理，不再静默未命中）
- 保持原始问答主流程（意图优先），并在会话内补充意图识别说明（可开关）
- 所有问题入库（命中与未命中都会写入 bitable）
- 未命中兜底 AI 搜索（默认走 OpenClaw 搜索）
- 图片消息直连 OpenClaw 多模态识别（识别后直接自然语言回复）
- 未命中问题自动沉淀到本地意图补充库（`backups/intent_backlog.json`）
- 首轮 NPS 评分收集（0-10 分）
- 问答记录写入飞书多维表格
- 意图库快照同步到飞书多维表格（upsert）
- 知识库定时同步（支持本地文件或远程 URL 源）
- 兼容旧接口 `quick_group_qa.py`

## 核心文件

- `group_qa_poller_v3.py`: 定时轮询入口
- `group_qa_handler.py`: 核心消息处理、会话管理、NPS
- `intent_classifier_v5.py`: 动态意图分类器
- `ai_search_fallback.py`: 未命中后的受控 AI 搜索兜底
- `intent_backlog.py`: 未命中问题沉淀到意图补充库
- `bitable_helper.py`: base 链接解析、自动选表、记录读写辅助
- `intents.json`: 业务知识库 + 回复文案模板
- `scripts/sync_knowledge_base.py`: 知识库同步脚本（校验、备份、原子更新）
- `scripts/sync_intents_to_bitable.py`: 意图库同步脚本（upsert 到表格）
- `feishu_app_auth.py`: 飞书 token 获取与缓存
- `quick_group_qa.py`: 向后兼容包装层

轮询输出里新增 `broadcast.text`，用于对外播报（非技术化描述）。

## 目录结构

```text
feishu-qa-bot/
├── SKILL.md
├── README.md
├── INSTALL.md
├── .env.example
├── requirements.txt
├── feishu_app_auth.py
├── group_qa_handler.py
├── group_qa_poller_v3.py
├── intent_classifier_v5.py
├── ai_search_fallback.py
├── intent_backlog.py
├── intents.json
├── quick_group_qa.py
├── scripts/
│   ├── run_poller_once.sh
│   ├── run_qa_cycle.sh
│   ├── run_single_message.py
│   ├── run_kb_sync_once.sh
│   ├── run_intent_sync_once.sh
│   ├── load_env.sh
│   ├── inject_openclaw_skill.sh
│   ├── register_openclaw_cron.sh
│   ├── sync_knowledge_base.py
│   └── sync_intents_to_bitable.py
├── references/
│   ├── reply-template-guidelines.md
│   ├── intent-library-mechanism.md
│   ├── kb-sync-mechanism.md
│   ├── intent-to-bitable-sync-mechanism.md
│   └── ai-search-fallback-mechanism.md
└── templates/
    ├── crontab.example
    ├── crontab.kb-sync.example
    ├── crontab.fullcycle.example
    ├── crontab.intent-sync.example
    └── crontab.ops.example
```

## 快速开始

```bash
cd feishu-qa-bot
pip install -r requirements.txt
cp .env.example .env
python3 group_qa_handler.py
python3 group_qa_poller_v3.py
```

如果你希望“先同步知识库再轮询消息”，可以执行：

```bash
QA_KB_SOURCE=/path/to/intents-source.json bash scripts/run_qa_cycle.sh
```

全周期降噪默认行为：

- `scripts/run_qa_cycle.sh` 只输出最终播报一句。
- 空闲轮次默认静默（`QA_NOTIFY_ON_IDLE=false`）。
- 同步过程日志写入 `QA_CYCLE_LOG_FILE`（默认 `/tmp/feishu_qa_cycle.log`）。

如果你希望注入到 OpenClaw 原生运行（推荐）：

```bash
bash scripts/inject_openclaw_skill.sh
bash scripts/register_openclaw_cron.sh
openclaw cron list --all --json
```

如果你希望直接使用你给的 base 链接配置表格，可以设置：

```bash
QA_BITABLE_BASE_URL="https://t0woxppdywz.feishu.cn/base/RQLIbiG3VaIg0rsZxezcKTaMnwf?from=from_copylink"
# 可选：如果不填 QA_TABLE_ID，则按 QA_TABLE_NAME 精确匹配，否则回退到第一个表
QA_TABLE_NAME="问答主表"
```

## 知识库同步机制（新增）

支持把外部知识源定时同步到 `intents.json`：

```bash
python3 scripts/sync_knowledge_base.py \
  --source /path/to/intents-source.json
```

也支持 URL 源：

```bash
python3 scripts/sync_knowledge_base.py \
  --source https://example.com/intents.json
```

也支持飞书 Wiki 链接（会从页面抽取文本并合并到现有意图文案）：

```bash
python3 scripts/sync_knowledge_base.py \
  --source https://waytoagi.feishu.cn/wiki/WewVwPVkyipyaCka7twcwPZMnJf
```

脚本特性：

- 自动校验意图结构（默认 strict）
- 变更检测（无变化不写入）
- 自动备份旧版本
- 原子写入（避免写坏文件）
- Feishu Wiki 模式下按意图规则增量合并（不直接覆盖全部意图）

## 未命中 AI 搜索兜底（新增）

默认启用（`QA_AI_SEARCH_PROVIDER=openclaw`），在本地意图未命中时走 OpenClaw 文本兜底链路。

触发条件：

- 本地意图未命中
- 问题长度达到 `QA_AI_FALLBACK_MIN_CHARS`
- 不匹配 `QA_AI_FALLBACK_SKIP_PATTERNS`（例如“你好/谢谢”）

边界控制：

- `QA_AI_FALLBACK_BLOCKED_KEYWORDS`：命中后直接拒答并提示人工
- 仅输出检索证据摘要，不做范围外推断

配置示例：

```bash
QA_ENABLE_AI_FALLBACK=true
QA_AI_SEARCH_PROVIDER=openclaw
QA_OPENCLAW_SEARCH_AGENT=main
QA_OPENCLAW_GATEWAY_CHAT_IDS=oc_xxx,oc_yyy
QA_OPENCLAW_TIMEOUT_SECONDS=90
QA_OPENCLAW_PROCESS_TIMEOUT_SECONDS=20
QA_OPENCLAW_GATEWAY_TIMEOUT_MS=12000
QA_OPENCLAW_COOLDOWN_SECONDS=120
QA_OPENCLAW_PARENT_SESSION_KEY=agent:main:main
QA_APPEND_INTENT_NOTE=true
QA_APPEND_INTENT_NOTE_ON_UNMATCH=false
QA_ENABLE_IMAGE_UNDERSTANDING=true
QA_IMAGE_MAX_BYTES=5000000
```

说明：`openclaw` provider 文本与图片统一走会话子代理链路：`sessions.create` → `sessions.send`（可带 `attachments`）→ `agent.wait` → `sessions.preview`。默认使用 `QA_OPENCLAW_PARENT_SESSION_KEY=agent:main:main` 作为父会话；若父会话不存在会自动降级为无父会话创建。
默认不附加技术尾注（来源/置信度），可通过 `QA_AI_REPLY_APPEND_META=true` 打开。

图片消息说明：
- `msg_type=image` 会优先走 OpenClaw 图片识别链路，不经过本地意图匹配。
- 回复内容直接使用自然语言，不附加技术模板。
- 图片体积默认限制 5MB（`QA_IMAGE_MAX_BYTES`）。

定向群开放说明：
- `QA_OPENCLAW_GATEWAY_CHAT_IDS` 为空时：默认对 `QA_CHAT_ID` 内所有群开放 Gateway 能力。
- 配置为逗号分隔 chat_id 时：仅这些群启用 Gateway 文本兜底与图片识别；其他群仅走本地意图。

会话内意图补充说明：
- 默认沿用原始流程：先本地意图，未命中再 AI 搜索兜底。
- `QA_APPEND_INTENT_NOTE=true`：命中意图时在回复末尾补充一行识别说明。
- `QA_APPEND_INTENT_NOTE_ON_UNMATCH=false`：未命中时默认不加提示，降低噪音。

如需改为 Tavily：

```bash
QA_AI_SEARCH_PROVIDER=tavily
QA_TAVILY_API_KEY=tvly-xxxx
```

若 OpenClaw 搜索无结果或不可用，系统会直接返回搜索状态提示，并把该问题写入意图补充库。

## 意图库同步到表格（新增）

将 `intents.json` 同步到同一张表（`会话ID=intent::<id>`）：

```bash
python3 scripts/sync_intents_to_bitable.py
```

建议在全周期中开启：

```bash
QA_SYNC_INTENTS_TO_BITABLE=true
bash scripts/run_qa_cycle.sh
```

## 回复文案模板规范（已应用）

`intents.json` 中每条 answer 建议遵循统一结构：

1. 结论先行：先回答“是什么/能不能”。
2. 操作步骤：给出 2-4 条可执行步骤。
3. 下一步引导：告诉用户还可以继续问什么。
4. 链接可选：仅放最相关的 1-2 个链接。

你可以查看：`references/reply-template-guidelines.md`

## 兼容说明

`quick_group_qa.py` 保留了旧工作流接口：

- `handle_group_message(chat_id, sender_id, message)`
- `process_group_mention(...)`

## 验证建议

```bash
python3 -m py_compile *.py
python3 quick_group_qa.py
python3 group_qa_handler.py
python3 scripts/sync_knowledge_base.py --source intents.json --dry-run
python3 scripts/sync_intents_to_bitable.py --enabled false
```

如果要校验 OpenClaw skill 格式：

```bash
python3 /Users/hongchen/.codex/skills/openclaw-skill-creator/scripts/validate_openclaw_skill.py \
  /path/to/feishu-qa-bot
```

如果遇到 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 未配置：

```bash
cp -n .env.example .env
# 编辑 .env 填入真实值后执行
bash scripts/run_poller_once.sh
```
