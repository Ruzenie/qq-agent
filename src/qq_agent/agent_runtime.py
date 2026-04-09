"""会话运行时模块。

职责：
1. 维护按会话隔离的上下文历史。
2. 控制历史裁剪策略，避免上下文无限增长。
3. 将 QQ 文本请求桥接到 LLM，并返回回复内容。
"""

from __future__ import annotations

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

    def _bootstrap(self) -> List[Dict[str, str]]:
        """为新会话构造初始系统提示词。"""
        return [
            {
                "role": "system",
                "content": (
                    "你是一个运行在 QQ 里的 HelloAgents 助手。"
                    "回答简洁、可执行，优先给出步骤和命令。"
                ),
            }
        ]

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

        answer = self._llm.think(messages)
        if not answer:
            answer = "当前请求失败，请稍后重试。"
        messages.append({"role": "assistant", "content": answer})
        self._sessions[session_id] = self._trim(messages)
        return answer, session_id
