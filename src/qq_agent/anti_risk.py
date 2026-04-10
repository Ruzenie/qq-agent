"""反风控策略模块。

职责：
1. 统一管理反风控配置（回复长度、兜底文案、命令延迟）；
2. 统一处理回复文本清洗与限长；
3. 统一提供命令随机延迟能力，降低固定节奏风险。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import random
import re


@dataclass(frozen=True)
class AntiRiskConfig:
    """反风控配置对象。"""

    max_reply_chars: int = 60
    fallback_reply: str = "收到，稍后回复你。"
    cmd_delay_min: float = 0.5
    cmd_delay_max: float = 1.5


def load_anti_risk_config_from_env() -> AntiRiskConfig:
    """从环境变量加载反风控配置。"""
    return AntiRiskConfig(
        max_reply_chars=int(os.getenv("BOT_MAX_REPLY_CHARS", "60")),
        fallback_reply=os.getenv("BOT_FALLBACK_REPLY", "收到，稍后回复你。"),
        cmd_delay_min=float(os.getenv("BOT_CMD_DELAY_MIN", "0.5")),
        cmd_delay_max=float(os.getenv("BOT_CMD_DELAY_MAX", "1.5")),
    )


def sanitize_reply_text(text: str, *, max_chars: int, fallback: str) -> str:
    """将文本清洗为简短、稳定的纯文本回复。"""
    value = text or ""
    value = value.replace("```", " ")
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"^\s*[-*#>]+\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()

    if not value:
        value = (fallback or "").strip() or "收到。"
    if len(value) <= max_chars:
        return value
    clipped = value[:max_chars].rstrip("，,。.;；:：!?！？ ")
    return f"{clipped}。"


def sanitize_for_config(text: str, config: AntiRiskConfig) -> str:
    """按配置清洗回复文本。"""
    return sanitize_reply_text(
        text,
        max_chars=config.max_reply_chars,
        fallback=config.fallback_reply,
    )


async def random_command_delay(config: AntiRiskConfig) -> None:
    """按配置对命令回复增加随机延迟。"""
    low = min(config.cmd_delay_min, config.cmd_delay_max)
    high = max(config.cmd_delay_min, config.cmd_delay_max)
    await asyncio.sleep(random.uniform(low, high))
