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
from pathlib import Path
import re
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

from .anti_risk import load_anti_risk_config_from_env, random_command_delay, sanitize_for_config
from .agent_runtime import AgentRuntime
from .audit_logger import AuditLogger

load_dotenv()

app = FastAPI(title="HelloAgents QQ Bot")
_runtime: Optional[AgentRuntime] = None
_runtime_error: Optional[str] = None

ONEBOT_API_BASE = os.getenv("ONEBOT_API_BASE", "http://127.0.0.1:3000")
ONEBOT_ACCESS_TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "")
ONEBOT_EVENT_SECRET = os.getenv("ONEBOT_EVENT_SECRET", "")
QQ_BOT_SELF_ID = os.getenv("QQ_BOT_SELF_ID", "")
QQ_USER_WHITELIST = {
    item.strip()
    for item in os.getenv("QQ_USER_WHITELIST", "").replace(";", ",").split(",")
    if item.strip()
}
QQ_SUPER_ADMINS = {
    item.strip()
    for item in os.getenv("QQ_SUPER_ADMINS", "").replace(";", ",").split(",")
    if item.strip()
}
QQ_WHITELIST_FILE = Path(os.getenv("QQ_WHITELIST_FILE", "data/whitelist_users.txt"))
_ANTI_RISK_CONFIG = load_anti_risk_config_from_env()
_AUDIT_LOGGER = AuditLogger()
_COMMAND_HELP_FLAGS = {"--help", "-h", "help", "帮助"}
_COMMAND_ALIASES = {
    "帮助": "help",
    "菜单": "help",
}
_COMMAND_REGISTRY: Dict[str, Dict[str, Any]] = {
    "help": {
        "summary": "查询所有指令",
        "subcommands": {},
    },
    "wl": {
        "summary": "白名单类指令",
        "subcommands": {
            "add": "添加白名单",
            "del": "删除白名单",
            "list": "查看白名单",
        },
    },
}


def _load_whitelist_file() -> set[str]:
    """从白名单文件加载账号。"""
    if not QQ_WHITELIST_FILE.exists():
        return set()
    users: set[str] = set()
    for line in QQ_WHITELIST_FILE.read_text(encoding="utf-8").splitlines():
        uid = line.strip()
        if uid and not uid.startswith("#"):
            users.add(uid)
    return users


def _save_whitelist_file(users: set[str]) -> None:
    """将白名单写回文件，保证重启后仍生效。"""
    QQ_WHITELIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(sorted(users))
    if content:
        content += "\n"
    QQ_WHITELIST_FILE.write_text(content, encoding="utf-8")


_RUNTIME_WHITELIST = set(QQ_USER_WHITELIST) | _load_whitelist_file()


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


def _is_super_admin(event: Dict[str, Any]) -> bool:
    """判断是否为超级管理员账号。"""
    if not QQ_SUPER_ADMINS:
        return False
    user_id = str(event.get("user_id", ""))
    return bool(user_id and user_id in QQ_SUPER_ADMINS)


def _is_whitelisted_user(event: Dict[str, Any]) -> bool:
    """判断发送者是否在账号白名单中。

    当 `QQ_USER_WHITELIST` 为空时，视为不启用白名单限制。
    """
    if _is_super_admin(event):
        return True
    if not _RUNTIME_WHITELIST:
        return True
    user_id = str(event.get("user_id", ""))
    return bool(user_id and user_id in _RUNTIME_WHITELIST)


def _parse_admin_command(text: str) -> tuple[str, str]:
    """解析白名单管理命令，返回 (action, arg)。"""
    raw = text.strip()
    m = re.match(r"^/?wl\s+(add|del|list)\s*(.*)$", raw, flags=re.IGNORECASE)
    if m:
        action = m.group(1).lower()
        arg = m.group(2).strip()
        return action, arg

    for prefix in ("添加白名单", "白名单添加"):
        if raw.startswith(prefix):
            return "add", raw[len(prefix) :].strip()
    for prefix in ("删除白名单", "移除白名单", "白名单删除"):
        if raw.startswith(prefix):
            return "del", raw[len(prefix) :].strip()
    if raw in ("白名单列表", "查看白名单"):
        return "list", ""

    return "", ""


def _parse_root_command(text: str) -> tuple[str, list[str]]:
    """解析命令文本，返回 (一级指令, 参数列表)。"""
    raw = text.strip()
    if raw.startswith("/"):
        raw = raw[1:].strip()
    if not raw:
        return "", []
    tokens = raw.split()
    root_raw = tokens[0]
    root_cmd = _COMMAND_ALIASES.get(root_raw, root_raw.lower())
    if root_cmd not in _COMMAND_REGISTRY:
        return "", []
    return root_cmd, tokens[1:]


def _is_help_query_text(text: str) -> bool:
    """判断是否为指令查询类请求（可放宽字数限制）。"""
    root_cmd, args = _parse_root_command(text)
    if root_cmd == "help" and not args:
        return True
    return bool(args and args[0].lower() in _COMMAND_HELP_FLAGS)


def _root_help_text() -> str:
    """返回一级命令帮助文本。"""
    lines = ["指令帮助:"]
    for root_cmd, meta in _COMMAND_REGISTRY.items():
        lines.append(f"{root_cmd} {meta['summary']}")
    lines.append("可使用 --help、-h、help 或“帮助”查看详细子命令。")
    return "\n".join(lines)


def _command_help_text(root_cmd: str) -> str:
    """返回指定一级命令的子命令帮助文本。"""
    subcommands: Dict[str, str] = _COMMAND_REGISTRY[root_cmd]["subcommands"]
    if not subcommands:
        return f"{root_cmd} 无子命令。"
    lines = [f"{root_cmd} 子命令:"]
    for sub_cmd, desc in subcommands.items():
        lines.append(f"{sub_cmd} {desc}")
    return "\n".join(lines)


def _handle_command(event: Dict[str, Any], text: str) -> Optional[str]:
    """处理命令管线（优先于白名单与 LLM）。"""
    root_cmd, args = _parse_root_command(text)
    if not root_cmd:
        return _handle_admin_command(event, text)

    if root_cmd == "help" and not args:
        return _root_help_text()

    if args and args[0].lower() in _COMMAND_HELP_FLAGS:
        return _command_help_text(root_cmd)

    return _handle_admin_command(event, text)


def _handle_admin_command(event: Dict[str, Any], text: str) -> Optional[str]:
    """处理超级管理员的白名单命令；非命令返回 None。"""
    action, arg = _parse_admin_command(text)
    if not action:
        return None
    if not _is_super_admin(event):
        return "无权限：仅超级管理员可管理白名单。"

    if action == "list":
        if not _RUNTIME_WHITELIST:
            return "白名单为空。"
        return f"白名单账号：{'、'.join(sorted(_RUNTIME_WHITELIST))}"

    uid = "".join(ch for ch in arg if ch.isdigit())
    if not uid:
        return "参数错误：请提供有效QQ号。"

    if action == "add":
        if uid in _RUNTIME_WHITELIST:
            return f"账号 {uid} 已在白名单中。"
        _RUNTIME_WHITELIST.add(uid)
        _save_whitelist_file(_RUNTIME_WHITELIST)
        return f"已添加白名单账号：{uid}"

    if action == "del":
        if uid not in _RUNTIME_WHITELIST:
            return f"账号 {uid} 不在白名单中。"
        _RUNTIME_WHITELIST.remove(uid)
        _save_whitelist_file(_RUNTIME_WHITELIST)
        return f"已移除白名单账号：{uid}"

    return "未知命令。"


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
    msg_type = str(event.get("message_type", ""))
    user_id = str(event.get("user_id", ""))
    group_id = str(event.get("group_id", ""))

    if event.get("post_type") != "message":
        _AUDIT_LOGGER.log(
            "event_ignored",
            {"reason": "non-message", "post_type": str(event.get("post_type", ""))},
        )
        return {"ok": True, "ignored": "non-message"}
    if _is_self_message(event):
        _AUDIT_LOGGER.log("event_ignored", {"reason": "self-message", "message_type": msg_type, "user_id": user_id})
        return {"ok": True, "ignored": "self-message"}

    text = _extract_text(event)
    if not text:
        _AUDIT_LOGGER.log("event_ignored", {"reason": "empty-message", "message_type": msg_type, "user_id": user_id})
        return {"ok": True, "ignored": "empty-message"}

    _AUDIT_LOGGER.log(
        "message_received",
        {
            "message_type": msg_type,
            "user_id": user_id,
            "group_id": group_id,
            "text": text,
        },
    )

    command_reply = _handle_command(event, text)
    if command_reply is not None:
        command_reply = sanitize_for_config(
            command_reply,
            _ANTI_RISK_CONFIG,
            keep_newlines=True,
            skip_length_limit=_is_help_query_text(text),
        )
        await random_command_delay(_ANTI_RISK_CONFIG)
        if event.get("message_type") == "group":
            group_id = event.get("group_id")
            if not group_id:
                _AUDIT_LOGGER.log("message_failed", {"pipeline": "command", "error": "missing group_id", "user_id": user_id})
                return {"ok": False, "error": "missing group_id"}
            await _send_group_msg(int(group_id), command_reply)
        else:
            user_id = event.get("user_id")
            if not user_id:
                _AUDIT_LOGGER.log("message_failed", {"pipeline": "command", "error": "missing user_id"})
                return {"ok": False, "error": "missing user_id"}
            await _send_private_msg(int(user_id), command_reply)
        _AUDIT_LOGGER.log(
            "message_sent",
            {
                "pipeline": "command",
                "message_type": msg_type,
                "user_id": user_id,
                "group_id": group_id,
                "reply": command_reply,
            },
        )
        return {"ok": True, "command": True}

    if not _is_whitelisted_user(event):
        _AUDIT_LOGGER.log(
            "event_ignored",
            {"reason": "not-in-whitelist", "message_type": msg_type, "user_id": user_id},
        )
        return {"ok": True, "ignored": "not-in-whitelist"}

    session_id = _session_id(event)
    runtime = _get_runtime()
    answer, _ = runtime.reply(session_id=session_id, user_text=text)
    answer = sanitize_for_config(answer, _ANTI_RISK_CONFIG)

    if event.get("message_type") == "group":
        group_id = event.get("group_id")
        if not group_id:
            _AUDIT_LOGGER.log("message_failed", {"pipeline": "llm", "error": "missing group_id", "session_id": session_id})
            return {"ok": False, "error": "missing group_id"}
        await _send_group_msg(int(group_id), answer)
    else:
        user_id = event.get("user_id")
        if not user_id:
            _AUDIT_LOGGER.log("message_failed", {"pipeline": "llm", "error": "missing user_id", "session_id": session_id})
            return {"ok": False, "error": "missing user_id"}
        await _send_private_msg(int(user_id), answer)
    _AUDIT_LOGGER.log(
        "message_sent",
        {
            "pipeline": "llm",
            "session_id": session_id,
            "message_type": msg_type,
            "user_id": user_id,
            "group_id": group_id,
            "reply": answer,
        },
    )

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
