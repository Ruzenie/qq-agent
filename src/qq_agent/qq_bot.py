"""QQ 机器人 Webhook 服务模块（OneBot v11）。

职责：
1. 接收并校验 OneBot 事件回调。
2. 将消息分发给 AgentRuntime 生成回复。
3. 调用 OneBot HTTP API 回发群聊/私聊消息。
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

from .agent_runtime import AgentRuntime

load_dotenv()

app = FastAPI(title="HelloAgents QQ Bot")
_runtime: Optional[AgentRuntime] = None
_runtime_error: Optional[str] = None

ONEBOT_API_BASE = os.getenv("ONEBOT_API_BASE", "http://127.0.0.1:3000")
ONEBOT_ACCESS_TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "")
ONEBOT_EVENT_SECRET = os.getenv("ONEBOT_EVENT_SECRET", "")
QQ_BOT_SELF_ID = os.getenv("QQ_BOT_SELF_ID", "")


def _verify_signature(body: bytes, x_signature: Optional[str]) -> None:
    """校验 OneBot 请求签名。

    当配置了 `ONEBOT_EVENT_SECRET` 时启用 HMAC-SHA1 校验。
    """
    if not ONEBOT_EVENT_SECRET:
        return
    if not x_signature:
        raise HTTPException(status_code=401, detail="Missing signature.")
    digest = hmac.new(ONEBOT_EVENT_SECRET.encode("utf-8"), body, hashlib.sha1).hexdigest()
    expected = f"sha1={digest}"
    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature.")


def _session_id(event: Dict[str, Any]) -> str:
    """按消息范围构造稳定会话 ID。"""
    if event.get("message_type") == "group":
        return f"qq:group:{event.get('group_id')}"
    return f"qq:private:{event.get('user_id')}"


def _extract_text(event: Dict[str, Any]) -> str:
    """从 OneBot 事件中提取纯文本消息。"""
    text = event.get("raw_message")
    if isinstance(text, str) and text.strip():
        return text.strip()
    message = event.get("message")
    if isinstance(message, str):
        return message.strip()
    return ""


def _is_self_message(event: Dict[str, Any]) -> bool:
    """判断是否为机器人自身发出的消息，避免自回环。"""
    user_id = str(event.get("user_id", ""))
    self_id = str(event.get("self_id", ""))
    if QQ_BOT_SELF_ID and user_id == QQ_BOT_SELF_ID:
        return True
    return bool(user_id and self_id and user_id == self_id)


async def _send_group_msg(group_id: int, text: str) -> None:
    """调用 OneBot 群消息接口发送文本。"""
    url = f"{ONEBOT_API_BASE.rstrip('/')}/send_group_msg"
    headers: Dict[str, str] = {}
    if ONEBOT_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ONEBOT_ACCESS_TOKEN}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json={"group_id": group_id, "message": text}, headers=headers)
        resp.raise_for_status()


async def _send_private_msg(user_id: int, text: str) -> None:
    """调用 OneBot 私聊接口发送文本。"""
    url = f"{ONEBOT_API_BASE.rstrip('/')}/send_private_msg"
    headers: Dict[str, str] = {}
    if ONEBOT_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ONEBOT_ACCESS_TOKEN}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json={"user_id": user_id, "message": text}, headers=headers)
        resp.raise_for_status()


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    """健康检查接口。"""
    if _runtime_error:
        return {"status": "degraded"}
    return {"status": "ok"}


@app.post("/onebot/v11/event")
async def onebot_event(request: Request, x_signature: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """处理 OneBot v11 事件并回发回复消息。"""
    body = await request.body()
    _verify_signature(body, x_signature)
    event = await request.json()

    if event.get("post_type") != "message":
        return {"ok": True, "ignored": "non-message"}
    if _is_self_message(event):
        return {"ok": True, "ignored": "self-message"}

    text = _extract_text(event)
    if not text:
        return {"ok": True, "ignored": "empty-message"}

    session_id = _session_id(event)
    runtime = _get_runtime()
    answer, _ = runtime.reply(session_id=session_id, user_text=text)

    if event.get("message_type") == "group":
        group_id = event.get("group_id")
        if not group_id:
            return {"ok": False, "error": "missing group_id"}
        await _send_group_msg(int(group_id), answer)
    else:
        user_id = event.get("user_id")
        if not user_id:
            return {"ok": False, "error": "missing user_id"}
        await _send_private_msg(int(user_id), answer)

    return {"ok": True, "session_id": session_id}


def _get_runtime() -> AgentRuntime:
    """懒加载运行时实例。

    这样即使 LLM 环境变量尚未配置，服务本身也可先启动。
    """
    global _runtime
    global _runtime_error
    if _runtime is not None:
        return _runtime
    try:
        _runtime = AgentRuntime()
        _runtime_error = None
        return _runtime
    except Exception as exc:  # keep service alive even if LLM env is missing
        _runtime_error = str(exc)
        raise HTTPException(status_code=500, detail=f"Runtime init failed: {_runtime_error}")
