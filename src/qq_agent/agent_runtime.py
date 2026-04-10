"""会话运行时模块。

职责：
1. 维护按会话隔离的上下文历史。
2. 控制历史裁剪策略，避免上下文无限增长。
3. 将 QQ 文本请求桥接到 LLM，并返回回复内容。
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

from .llm_client import HelloAgentsLLM


class AgentRuntime:
    """QQ 会话态运行时。"""

    def __init__(self, max_turns: int = 8) -> None:
        """初始化运行时。

        Args:
            max_turns: 每个会话最多保留的用户-助手轮次数。
        """
        self._llm = HelloAgentsLLM()
        self._sessions: Dict[str, List[Dict[str, str]]] = {}
        self._max_turns = max_turns
        self._max_reply_chars = int(os.getenv("BOT_MAX_REPLY_CHARS", "60"))
        self._fallback_reply = os.getenv("BOT_FALLBACK_REPLY", "收到，稍后回复你。")

    def _bootstrap(self) -> List[Dict[str, str]]:
        """为新会话构造初始系统提示词。"""
        return [
            {
                "role": "system",
                "content": (
                    "你是运行在 QQ 的助手。"
                    "回复必须短、稳、自然，避免触发风控。"
                    "规则："
                    "1) 单条尽量 1 句，不超过 50 字；"
                    "2) 不用 markdown、代码块、链接、表情；"
                    "3) 不要长段解释；"
                    "4) 不确定时用保守短句回复。"
                ),
            }
        ]

    def _normalize_answer(self, answer: str) -> str:
        """将模型回复裁剪为简短纯文本，降低风控风险。"""
        text = answer or ""
        text = text.replace("```", " ")
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"^\s*[-*#>]+\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()

        if not text:
            return self._fallback_reply

        if len(text) > self._max_reply_chars:
            text = text[: self._max_reply_chars].rstrip("，,。.;；:：!?！？ ")
            text = f"{text}。"
        return text

    def _trim(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """裁剪历史，仅保留系统提示与最近若干轮对话。"""
        if len(messages) <= 1:
            return messages
        keep = 1 + self._max_turns * 2
        if len(messages) <= keep:
            return messages
        return [messages[0], *messages[-(keep - 1) :]]

    def reply(self, session_id: str, user_text: str) -> Tuple[str, str]:
        """处理单次用户输入并返回回复。

        流程：追加用户消息 -> 裁剪上下文 -> 调用模型 -> 写回会话历史。
        """
        messages = self._sessions.get(session_id, self._bootstrap())
        messages.append({"role": "user", "content": user_text})
        messages = self._trim(messages)

        try:
            answer = self._llm.think(messages)
        except Exception:
            answer = self._fallback_reply
        answer = self._normalize_answer(answer)

        messages.append({"role": "assistant", "content": answer})
        self._sessions[session_id] = self._trim(messages)
        return answer, session_id
