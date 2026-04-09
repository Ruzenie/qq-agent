# qq-agent

QQ Agent 项目（OneBot v11 + LLM）。


## 运行

```bash
cd /Users/ruzenie/code/agent/qq-agent
uv sync
uv run uvicorn qq_agent.qq_bot:app --host 0.0.0.0 --port 8000
```

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
```

## 回调地址

- Event webhook: `http://<host>:8000/onebot/v11/event`
- Health check: `http://<host>:8000/healthz`
