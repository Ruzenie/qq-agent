# qq-agent

QQ Agent 项目（OneBot v11 + LLM）。


## 运行

```bash
cd /Users/ruzenie/code/agent/qq-agent
uv sync
uv run uvicorn qq_agent.qq_bot:app --host 0.0.0.0 --port 8000
```

## 导入问题排查（hello_agents）

如果编辑器提示 `无法解析导入 "hello_agents"`：

1. 确认 IDE Python 解释器选择为 `qq-agent/.venv/bin/python`
2. 在项目根目录执行一次 `uv sync`
3. 重启语言服务（Pylance/Pyright）

项目已提供 `pyrightconfig.json`，并显式绑定 `.venv` 与 `src` 路径。

## 环境变量（.env）

```env
# LLM
LLM_MODEL_ID=your-model
LLM_API_KEY=your-key
LLM_BASE_URL=https://your-gateway/v1
LLM_TIMEOUT=60
LLM_USER_AGENT=Mozilla/5.0

# OneBot
ONEBOT_API_BASE=http://127.0.0.1:3000
ONEBOT_ACCESS_TOKEN=your-onebot-token
ONEBOT_EVENT_SECRET=your-onebot-secret
QQ_BOT_SELF_ID=123456789
QQ_USER_WHITELIST=123456789,987654321
QQ_SUPER_ADMINS=123456789
QQ_WHITELIST_FILE=data/whitelist_users.txt

# Reply policy
BOT_MAX_REPLY_CHARS=50
BOT_FALLBACK_REPLY=收到，稍后回复你。
BOT_CMD_DELAY_MIN=0.5
BOT_CMD_DELAY_MAX=1.5

# Layered memory
QQ_MEMORY_FILE=data/memory/sessions.json
QQ_MEMORY_MAX_TURNS=8

# Audit log
QQ_AUDIT_LOG_ENABLED=1
QQ_AUDIT_LOG_FILE=data/logs/audit.jsonl
```

## 回调地址

- Event webhook: `http://<host>:8000/onebot/v11/event`
- Health check: `http://<host>:8000/healthz`

## 白名单管理命令（仅超级管理员）

可用命令：

- `help`（查询所有一级指令）
- `<一级指令> --help/-h/help/帮助`（查询该一级指令全部子命令）
- `wl add 123456789`（添加白名单）
- `wl del 123456789`（移除白名单）
- `wl list`（查看白名单）

中文别名：

- `添加白名单123456789`
- `删除白名单123456789`
- `白名单列表`

## 分层记忆与审计日志

- 分层记忆：
  - 短期记忆：进程内会话上下文；
  - 长期记忆：`QQ_MEMORY_FILE` 持久化最近若干轮（`QQ_MEMORY_MAX_TURNS`）。
- 审计日志：
  - 记录收消息、命令/LLM 路由、回包、忽略原因、失败原因；
  - 默认输出到 `QQ_AUDIT_LOG_FILE`（JSONL 格式）。
