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
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

from .anti_risk import load_anti_risk_config_from_env, random_command_delay, sanitize_for_config
from .agent_runtime import AgentRuntime
from .audit_logger import AuditLogger
from .meme_generator import render_to_cq_code, templates_help_text
from .recall_store import GroupRecallStore, load_recall_store_config_from_env

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
QQ_MONITOR_GROUP_IDS = {
    item.strip()
    for item in os.getenv("QQ_MONITOR_GROUP_IDS", "").replace(";", ",").split(",")
    if item.strip()
}
QQ_RECALL_NOTIFY_SUPERADMINS = os.getenv("QQ_RECALL_NOTIFY_SUPERADMINS", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
QQ_RECALL_NOTIFY_MODE = os.getenv("QQ_RECALL_NOTIFY_MODE", "forward").strip().lower()
QQ_WHITELIST_FILE = Path(os.getenv("QQ_WHITELIST_FILE", "data/whitelist_users.txt"))
QQ_MONITOR_GROUP_FILE = Path(os.getenv("QQ_MONITOR_GROUP_FILE", "data/monitor_groups.txt"))
_ANTI_RISK_CONFIG = load_anti_risk_config_from_env()
_AUDIT_LOGGER = AuditLogger()
_RECALL_STORE = GroupRecallStore(load_recall_store_config_from_env())
_COMMAND_HELP_FLAGS = {"--help", "-h", "help", "帮助"}
_COMMAND_ALIASES = {
    "帮助": "help",
    "菜单": "help",
    "撤回": "recall",
    "防撤回": "recall",
    "表情包": "meme",
}
_COMMAND_REGISTRY: Dict[str, Dict[str, Any]] = {
    "help": {
        "summary": "查询所有指令",
        "subcommands": {},
    },
    "chat": {
        "summary": "当前会话 LLM 开关",
        "subcommands": {
            "on": "开启当前会话 LLM 对话",
            "off": "关闭当前会话 LLM 对话",
            "status": "查看当前会话状态",
        },
    },
    "wl": {
        "summary": "白名单类指令",
        "subcommands": {
            "add": "添加白名单",
            "del": "删除白名单",
            "list": "查看白名单",
        },
    },
    "mg": {
        "summary": "监听群管理指令",
        "subcommands": {
            "add": "添加监听群",
            "del": "删除监听群",
            "list": "查看监听群",
        },
    },
    "recall": {
        "summary": "撤回留痕查询",
        "subcommands": {
            "list [N]": "查看本群最近 N 条撤回记录（默认10，最大20）",
            "cleanup": "立即清理过期留痕记录",
        },
    },
    "meme": {
        "summary": "表情包生成",
        "subcommands": {
            "list": "查看可用模板",
            "classic 上句|下句": "生成黑白经典双行模板",
            "alert 标题|内容": "生成黄色警示牌模板",
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


def _load_monitor_group_file() -> set[str]:
    """从监听群文件加载群号。"""
    if not QQ_MONITOR_GROUP_FILE.exists():
        return set()
    groups: set[str] = set()
    for line in QQ_MONITOR_GROUP_FILE.read_text(encoding="utf-8").splitlines():
        gid = "".join(ch for ch in line.strip() if ch.isdigit())
        if gid:
            groups.add(gid)
    return groups


def _save_monitor_group_file(groups: set[str]) -> None:
    """将监听群列表写回文件，保证重启后仍生效。"""
    QQ_MONITOR_GROUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(sorted(groups))
    if content:
        content += "\n"
    QQ_MONITOR_GROUP_FILE.write_text(content, encoding="utf-8")


_RUNTIME_WHITELIST = set(QQ_USER_WHITELIST) | _load_whitelist_file()
_RUNTIME_MONITOR_GROUPS = set(QQ_MONITOR_GROUP_IDS) | _load_monitor_group_file()
_SESSION_CHAT_ENABLED: Dict[str, bool] = {}


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


def _extract_message_segments(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取 OneBot 原始 message 段数组。"""
    message = event.get("message")
    if not isinstance(message, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        data = seg.get("data", {})
        if not isinstance(seg_type, str) or not isinstance(data, dict):
            continue
        normalized.append({"type": seg_type, "data": data})
    return normalized


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


def _is_monitored_group(event: Dict[str, Any]) -> bool:
    """判断是否为已配置监听的群。"""
    if event.get("message_type") != "group":
        return False
    if not _RUNTIME_MONITOR_GROUPS:
        return False
    group_id = str(event.get("group_id", ""))
    return bool(group_id and group_id in _RUNTIME_MONITOR_GROUPS)


def _sender_name(event: Dict[str, Any]) -> str:
    """获取发送者展示名。"""
    sender = event.get("sender", {})
    if not isinstance(sender, dict):
        return ""
    for key in ("card", "nickname"):
        value = str(sender.get(key, "")).strip()
        if value:
            return value
    return ""


def _format_recall_notice(group_id: str, summary: Dict[str, Any]) -> str:
    """格式化撤回通知文本。"""
    sender_name = str(summary.get("sender_name", "")).strip()
    user_id = str(summary.get("user_id", "")).strip() or "未知账号"
    who = f"{sender_name}({user_id})" if sender_name else user_id
    text = str(summary.get("text", "")).strip() or "[空消息]"
    recalled_at = str(summary.get("recalled_at", "")).strip() or "未知时间"
    return f"【撤回留痕】群 {group_id}\n发送者: {who}\n撤回时间: {recalled_at}\n内容: {text}"


async def _notify_super_admins(group_id: str, summary: Dict[str, Any]) -> None:
    """将撤回留痕通知超级管理员。"""
    if not QQ_RECALL_NOTIFY_SUPERADMINS:
        return
    if not QQ_SUPER_ADMINS:
        return

    notice = sanitize_for_config(
        _format_recall_notice(group_id=group_id, summary=summary),
        _ANTI_RISK_CONFIG,
        keep_newlines=True,
        skip_length_limit=True,
    )
    forward_nodes = _build_forward_nodes(group_id=group_id, summary=summary)
    for admin_id in QQ_SUPER_ADMINS:
        if not admin_id.isdigit():
            continue
        try:
            if QQ_RECALL_NOTIFY_MODE == "forward":
                try:
                    await _send_private_forward_msg(int(admin_id), forward_nodes)
                except Exception:
                    await _send_private_msg(int(admin_id), notice)
            else:
                await _send_private_msg(int(admin_id), notice)
        except Exception as exc:
            _AUDIT_LOGGER.log(
                "message_failed",
                {
                    "pipeline": "recall_notify",
                    "group_id": group_id,
                    "admin_id": admin_id,
                    "notify_mode": QQ_RECALL_NOTIFY_MODE,
                    "error": str(exc),
                },
            )


def _run_recall_cleanup_if_due() -> None:
    """按配置周期执行撤回留痕清理。"""
    result = _RECALL_STORE.cleanup_if_due()
    if not result:
        return
    _AUDIT_LOGGER.log("recall_cleanup", result)


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


def _parse_monitor_group_command(text: str) -> tuple[str, str]:
    """解析监听群管理命令，返回 (action, arg)。"""
    raw = text.strip()
    m = re.match(r"^/?mg\s+(add|del|list)\s*(.*)$", raw, flags=re.IGNORECASE)
    if m:
        action = m.group(1).lower()
        arg = m.group(2).strip()
        return action, arg

    for prefix in ("添加监听群", "监听群添加"):
        if raw.startswith(prefix):
            return "add", raw[len(prefix) :].strip()
    for prefix in ("删除监听群", "移除监听群", "监听群删除"):
        if raw.startswith(prefix):
            return "del", raw[len(prefix) :].strip()
    if raw in ("监听群列表", "查看监听群"):
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
    if root_cmd in {"recall", "chat", "meme"}:
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

    if root_cmd == "chat":
        return _handle_chat_command(event, args)

    if root_cmd == "recall":
        return _handle_recall_command(event, args)

    if root_cmd == "meme":
        return _handle_meme_command(args)

    return _handle_admin_command(event, text)


def _handle_chat_command(event: Dict[str, Any], args: list[str]) -> str:
    """处理当前会话 chat on/off/status 指令。"""
    session_id = _session_id(event)
    current = _SESSION_CHAT_ENABLED.get(session_id, False)
    if not args:
        return f"当前会话 LLM 状态：{'on' if current else 'off'}。用法：chat on|off|status"

    action = args[0].strip().lower()
    if action in {"status", "state"}:
        return f"当前会话 LLM 状态：{'on' if current else 'off'}。"
    if action in {"on", "start", "open", "开启"}:
        _SESSION_CHAT_ENABLED[session_id] = True
        return "已开启当前会话 LLM 对话。"
    if action in {"off", "stop", "close", "关闭"}:
        _SESSION_CHAT_ENABLED[session_id] = False
        return "已关闭当前会话 LLM 对话。"
    return "用法：chat on|off|status"


def _handle_recall_command(event: Dict[str, Any], args: list[str]) -> str:
    """处理撤回留痕命令。"""
    if event.get("message_type") != "group":
        return "recall 命令仅支持在群聊中使用。"
    if not _is_super_admin(event):
        return "无权限：仅超级管理员可查看撤回留痕。"
    if not _is_monitored_group(event):
        return "当前群未开启监听留痕。"

    sub = args[0].lower() if args else "list"
    if sub == "cleanup":
        result = _RECALL_STORE.cleanup_expired()
        return (
            f"清理完成：删除 {result['removed_count']} 条，"
            f"移除空群 {result['removed_groups']} 个，剩余 {result['remaining_count']} 条。"
        )
    if sub not in {"list", "ls"}:
        return "用法：recall list [N] | recall cleanup"

    limit = 10
    if len(args) >= 2 and args[1].isdigit():
        limit = min(20, max(1, int(args[1])))

    group_id = str(event.get("group_id", ""))
    records = _RECALL_STORE.list_recalled(group_id=group_id, limit=limit)
    if not records:
        return "暂无撤回记录。"

    lines = [f"最近 {len(records)} 条撤回记录："]
    for idx, item in enumerate(records, start=1):
        sender_name = item.get("sender_name", "").strip()
        user_id = item.get("user_id", "").strip() or "未知账号"
        who = f"{sender_name}({user_id})" if sender_name else user_id
        text = item.get("text", "").strip() or "[空消息]"
        recalled_at = item.get("recalled_at", "").strip() or "未知时间"
        lines.append(f"{idx}. {who} 撤回于 {recalled_at}：{text}")
    return "\n".join(lines)


def _handle_meme_command(args: list[str]) -> str:
    """处理 meme 命令。"""
    if not args:
        return (
            "用法：meme list | meme classic 上句|下句 | meme alert 标题|内容\n"
            "可先执行 meme list 查看模板。"
        )

    sub = args[0].strip().lower()
    if sub in {"list", "ls", "help", "--help", "-h", "帮助"}:
        return templates_help_text()

    payload = " ".join(args[1:]).strip()
    if not payload:
        return f"参数不足：请提供文案。示例：meme {sub} 文案A|文案B"

    try:
        return render_to_cq_code(template_key=sub, payload=payload)
    except ValueError as exc:
        return f"生成失败：{exc}"
    except Exception:
        return "生成失败：渲染异常，请稍后重试。"


async def _handle_notice_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """处理 OneBot notice 事件（撤回留痕）。"""
    notice_type = str(event.get("notice_type", ""))
    if notice_type != "group_recall":
        _AUDIT_LOGGER.log("event_ignored", {"reason": "non-group-recall", "notice_type": notice_type})
        return {"ok": True, "ignored": "non-group-recall"}

    group_id = str(event.get("group_id", ""))
    if not group_id or group_id not in _RUNTIME_MONITOR_GROUPS:
        _AUDIT_LOGGER.log("event_ignored", {"reason": "group-not-monitored", "group_id": group_id})
        return {"ok": True, "ignored": "group-not-monitored"}

    message_id = str(event.get("message_id", ""))
    operator_id = str(event.get("operator_id", ""))
    summary = _RECALL_STORE.mark_recalled(group_id=group_id, message_id=message_id, operator_id=operator_id)
    _AUDIT_LOGGER.log("group_message_recalled", {"group_id": group_id, **summary})
    await _notify_super_admins(group_id=group_id, summary=summary)
    return {"ok": True, "notice": "group-recall-recorded", "group_id": group_id}


def _handle_admin_command(event: Dict[str, Any], text: str) -> Optional[str]:
    """处理超级管理员的白名单命令；非命令返回 None。"""
    wl_action, wl_arg = _parse_admin_command(text)
    mg_action, mg_arg = _parse_monitor_group_command(text)
    if not wl_action and not mg_action:
        return None
    if not _is_super_admin(event):
        return "无权限：仅超级管理员可管理白名单和监听群。"

    if wl_action == "list":
        if not _RUNTIME_WHITELIST:
            return "白名单为空。"
        return f"白名单账号：{'、'.join(sorted(_RUNTIME_WHITELIST))}"

    if wl_action:
        uid = "".join(ch for ch in wl_arg if ch.isdigit())
        if not uid:
            return "参数错误：请提供有效QQ号。"

        if wl_action == "add":
            if uid in _RUNTIME_WHITELIST:
                return f"账号 {uid} 已在白名单中。"
            _RUNTIME_WHITELIST.add(uid)
            _save_whitelist_file(_RUNTIME_WHITELIST)
            return f"已添加白名单账号：{uid}"

        if wl_action == "del":
            if uid not in _RUNTIME_WHITELIST:
                return f"账号 {uid} 不在白名单中。"
            _RUNTIME_WHITELIST.remove(uid)
            _save_whitelist_file(_RUNTIME_WHITELIST)
            return f"已移除白名单账号：{uid}"

        return "未知命令。"

    if mg_action == "list":
        if not _RUNTIME_MONITOR_GROUPS:
            return "监听群为空。"
        return f"监听群：{'、'.join(sorted(_RUNTIME_MONITOR_GROUPS))}"

    gid = "".join(ch for ch in mg_arg if ch.isdigit())
    if not gid:
        return "参数错误：请提供有效群号。"

    if mg_action == "add":
        if gid in _RUNTIME_MONITOR_GROUPS:
            return f"群 {gid} 已在监听列表中。"
        _RUNTIME_MONITOR_GROUPS.add(gid)
        _save_monitor_group_file(_RUNTIME_MONITOR_GROUPS)
        return f"已添加监听群：{gid}"

    if mg_action == "del":
        if gid not in _RUNTIME_MONITOR_GROUPS:
            return f"群 {gid} 不在监听列表中。"
        _RUNTIME_MONITOR_GROUPS.remove(gid)
        _save_monitor_group_file(_RUNTIME_MONITOR_GROUPS)
        return f"已移除监听群：{gid}"

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


def _build_forward_nodes(group_id: str, summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    """构造合并转发节点。"""
    sender_name = str(summary.get("sender_name", "")).strip()
    user_id = str(summary.get("user_id", "")).strip() or "未知账号"
    sender_uin = user_id if user_id.isdigit() else "10000"
    who = f"{sender_name}({user_id})" if sender_name else user_id
    text = str(summary.get("text", "")).strip() or "[空消息]"
    recalled_at = str(summary.get("recalled_at", "")).strip() or "未知时间"
    raw_segments = summary.get("message_segments", [])
    if isinstance(raw_segments, list) and raw_segments:
        content_segments = raw_segments
    else:
        content_segments = [{"type": "text", "data": {"text": text}}]
    nodes = [
        {
            "type": "node",
            "data": {
                "nickname": "qq-agent",
                "user_id": "10000",
                "content": [{"type": "text", "data": {"text": f"群 {group_id} 撤回留痕通知"}}],
            },
        },
        {
            "type": "node",
            "data": {
                "nickname": sender_name or "群成员",
                "user_id": sender_uin,
                "content": content_segments,
            },
        },
        {
            "type": "node",
            "data": {
                "nickname": "qq-agent",
                "user_id": "10000",
                "content": [{"type": "text", "data": {"text": f"发送者: {who}\n撤回时间: {recalled_at}"}}],
            },
        },
    ]
    return nodes


async def _send_private_forward_msg(user_id: int, nodes: list[Dict[str, Any]]) -> None:
    """调用 OneBot 私聊合并转发接口。"""
    url = f"{ONEBOT_API_BASE.rstrip('/')}/send_private_forward_msg"
    headers: Dict[str, str] = {}
    if ONEBOT_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ONEBOT_ACCESS_TOKEN}"
    payload = {
        "user_id": str(user_id),
        "message": nodes,
        "messages": nodes,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, headers=headers)
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
    _run_recall_cleanup_if_due()
    post_type = str(event.get("post_type", ""))
    msg_type = str(event.get("message_type", ""))
    user_id = str(event.get("user_id", ""))
    group_id = str(event.get("group_id", ""))

    if post_type == "notice":
        return await _handle_notice_event(event)
    if post_type != "message":
        _AUDIT_LOGGER.log(
            "event_ignored",
            {"reason": "non-message", "post_type": post_type},
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
    if _is_monitored_group(event):
        _RECALL_STORE.append_message(
            group_id=str(event.get("group_id", "")),
            message_id=str(event.get("message_id", "")),
            user_id=user_id,
            text=text,
            sender_name=_sender_name(event),
            message_segments=_extract_message_segments(event),
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
    if not _SESSION_CHAT_ENABLED.get(session_id, False):
        _AUDIT_LOGGER.log(
            "event_ignored",
            {"reason": "chat-off", "message_type": msg_type, "session_id": session_id},
        )
        return {"ok": True, "ignored": "chat-off"}

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
