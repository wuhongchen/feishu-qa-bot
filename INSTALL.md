# 安装指南

## 1. 安装依赖

```bash
cd feishu-qa-bot
pip install -r requirements.txt
```

## 2. 配置环境变量

```bash
cp .env.example .env
# 然后把 .env 里所有必填项替换成你的真实值
```

至少需要配置：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `QA_BITABLE_TOKEN` + `QA_TABLE_ID`（二选一）
  或 `QA_BITABLE_BASE_URL`（推荐，支持自动解析）
- `QA_CHAT_ID`

如果你只有 base 链接，也可以不用手填 `QA_BITABLE_TOKEN`：

- 配置 `QA_BITABLE_BASE_URL`（例如你提供的 `RQLIbiG3VaIg0rsZxezcKTaMnwf` 这个 base）
- 可选配置 `QA_TABLE_NAME`，用于自动选表

## 3. 本地冒烟测试

```bash
python3 group_qa_handler.py
python3 quick_group_qa.py
```

## 4. 手动执行轮询

```bash
python3 group_qa_poller_v3.py
```

如果你想先同步知识库再执行轮询：

```bash
QA_KB_SOURCE=/path/to/intents-source.json bash scripts/run_qa_cycle.sh
```

## 5. 注入 OpenClaw 并注册原生调度

先把技能注入到 OpenClaw skills 目录（软链模式）：

```bash
bash scripts/inject_openclaw_skill.sh
```

再注册 OpenClaw 原生 cron（agent job，不是系统 shell cron）：

```bash
bash scripts/register_openclaw_cron.sh
```

查看调度状态：

```bash
openclaw cron list --all --json
```

## 6. 配置知识库定时同步（新增）

先配置知识源：

- `QA_KB_SOURCE`: 本地 JSON 文件路径、JSON URL，或飞书 Wiki 链接
- `QA_KB_TARGET`: 本项目的 `intents.json` 路径

手动执行一次同步：

```bash
python3 scripts/sync_knowledge_base.py --source "$QA_KB_SOURCE"
```

飞书 Wiki 示例：

```bash
python3 scripts/sync_knowledge_base.py \
  --source "https://waytoagi.feishu.cn/wiki/WewVwPVkyipyaCka7twcwPZMnJf"
```

加定时任务（每 10 分钟）：

```bash
*/10 * * * * cd /path/to/feishu-qa-bot && /usr/bin/env bash scripts/run_kb_sync_once.sh >> /tmp/feishu_qa_kb_sync.log 2>&1
```

说明：同步脚本默认严格校验（`QA_KB_STRICT=true`），校验失败会拒绝覆盖线上知识库。

也可以直接使用运维模板（轮询 + 同步 + 每日冒烟）：

```bash
cat templates/crontab.ops.example
```

## 7. 配置意图库同步到表格（新增）

手动同步一次：

```bash
python3 scripts/sync_intents_to_bitable.py
```

独立定时任务（每 15 分钟）：

```bash
*/15 * * * * cd /path/to/feishu-qa-bot && /usr/bin/env bash scripts/run_intent_sync_once.sh >> /tmp/feishu_qa_intent_sync.log 2>&1
```

## 8. 配置未命中 AI 搜索兜底（可选）

系统默认在“意图未命中”时也会回复普通问答，不会静默。  
如果你希望未命中时优先给出带来源的检索答复，可以开启受控 AI 搜索兜底：

- `QA_ENABLE_AI_FALLBACK=true`
- `QA_AI_SEARCH_PROVIDER=openclaw`
- `QA_OPENCLAW_SEARCH_AGENT=main`
- `QA_AI_SEARCH_ALLOWED_DOMAINS=...`（强烈建议只放业务域名）
- 需要本机 OpenClaw gateway 可用（`openclaw health` 可检查）

如果你要改为 Tavily，再补：

- `QA_AI_SEARCH_PROVIDER=tavily`
- `QA_TAVILY_API_KEY=...`

边界机制：

- 仅在本地知识库未命中时触发
- 仅引用白名单域名内容
- 命中禁答词（医疗、法律、投资等）直接拒答并提示人工

补充机制：

- 所有未命中问题会自动写入 `QA_INTENT_BACKLOG_FILE`（默认 `backups/intent_backlog.json`）
- 可定期根据 backlog 把高频问题补充到 `intents.json`

## 常见问题

### 1) Token 获取失败

- 检查 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 是否正确。
- 检查飞书应用是否为企业自建应用，且密钥未过期。

### 2) 能读消息但发不出去

- 检查是否开通 `im:message:send_as_bot` 或等价发送权限。
- 检查机器人是否在目标群中。

### 3) 能回复但写不了表格

- 检查 `QA_BITABLE_TOKEN` 和 `QA_TABLE_ID`。
- 检查应用权限 `bitable:app`、`base:record:create`。

### 4) 为什么还是像“独立 cron”在跑

- 先确认技能已注入：`openclaw skills list --json | rg feishu-qa-bot-skill`
- 再确认是 OpenClaw cron job：`openclaw cron list --all`
- 如果只看到系统 crontab，没有 OpenClaw cron，请重新执行 `bash scripts/register_openclaw_cron.sh`

### 5) 提示 FEISHU_APP_ID / FEISHU_APP_SECRET 未配置

- 检查项目根目录是否存在 `.env`（可从 `.env.example` 复制）。
- 确认 `.env` 中已填写真实 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。
- 重新执行：`bash scripts/run_poller_once.sh`
- 可先验证鉴权：`python3 feishu_app_auth.py`
