"""会话运行时模块。

职责：
1. 基于 hello_agents SimpleAgent 维护按会话隔离的对话运行时；
2. 复用 hello_agents 的工具注册、历史管理等核心能力；
3. 将 QQ 文本请求桥接到 Agent，并返回风控友好的回复内容。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Dict, List, Tuple

from hello_agents import Config, Message, SimpleAgent, ToolRegistry
from hello_agents.context.builder import ContextBuilder, ContextConfig, ContextPacket
from hello_agents.tools import CalculatorTool

from .anti_risk import load_anti_risk_config_from_env, sanitize_for_config
from .llm_client import HelloAgentsLLM
from .memory_store import SessionMemoryStore, load_memory_store_config_from_env


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "off", "no"}


@dataclass
class _SessionState:
    """单会话状态容器。"""

    agent: SimpleAgent
    context_builder: ContextBuilder


class AgentRuntime:
    """QQ 会话态运行时。"""

    def __init__(self, max_turns: int = 8) -> None:
        """初始化运行时。

        Args:
            max_turns: 每个会话最多保留的用户-助手轮次数。
        """
        self._llm = HelloAgentsLLM()
        self._sessions: Dict[str, _SessionState] = {}
        self._max_turns = max_turns
        self._anti_risk = load_anti_risk_config_from_env()
        self._memory_store = SessionMemoryStore(load_memory_store_config_from_env())
        self._enable_context_builder = _env_bool("QQ_ENABLE_CONTEXT_BUILDER", False)
        self._context_max_tokens = int(os.getenv("QQ_CONTEXT_MAX_TOKENS", "2000"))

    def _build_system_prompt(self, session_id: str) -> str:
        """为新会话构造系统提示词。"""
        memory_lines = self._memory_store.get_memory_lines(session_id)
        memory_hint = ""
        if memory_lines:
            memory_hint = "\n历史记忆（仅参考，冲突时以当前用户输入为准）：\n" + "\n".join(memory_lines)

        return (
            "你是运行在 QQ 的助手。"
            "回复必须短、稳、自然，避免触发风控。"
            "规则："
            "1) 单条尽量 1 句，不超过 50 字；"
            "2) 不用 markdown、代码块、链接、表情；"
            "3) 不要长段解释；"
            "4) 不确定时用保守短句回复。"
            "5) 仅在确实需要时调用工具。"
            f"{memory_hint}"
        )

    def _build_agent_config(self) -> Config:
        """构建 hello_agents 运行配置。"""
        return Config(
            max_history_length=max(20, self._max_turns * 4),
            min_retain_rounds=max(4, self._max_turns // 2),
            trace_enabled=_env_bool("QQ_HA_TRACE_ENABLED", False),
            trace_dir=os.getenv("QQ_HA_TRACE_DIR", "data/traces"),
            skills_enabled=_env_bool("QQ_HA_SKILLS_ENABLED", False),
            session_enabled=_env_bool("QQ_HA_SESSION_ENABLED", False),
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
            stream_enabled=False,
        )

    def _build_tool_registry(self) -> ToolRegistry:
        """构建 hello_agents 工具注册表。"""
        registry = ToolRegistry()
        registry.register_tool(CalculatorTool())
        return registry

    def _create_session(self, session_id: str) -> _SessionState:
        """创建新会话状态。"""
        agent = SimpleAgent(
            name=f"qq-session-{session_id}",
            llm=self._llm.native(),
            system_prompt=self._build_system_prompt(session_id),
            config=self._build_agent_config(),
            tool_registry=self._build_tool_registry(),
            enable_tool_calling=True,
            max_tool_iterations=max(1, int(os.getenv("QQ_HA_MAX_TOOL_ITERS", "2"))),
        )
        context_builder = ContextBuilder(
            config=ContextConfig(
                max_tokens=self._context_max_tokens,
                reserve_ratio=0.2,
                enable_compression=True,
                min_relevance=0.2,
            )
        )
        return _SessionState(agent=agent, context_builder=context_builder)

    def _get_session(self, session_id: str) -> _SessionState:
        """获取或创建会话状态。"""
        state = self._sessions.get(session_id)
        if state is not None:
            return state
        state = self._create_session(session_id)
        self._sessions[session_id] = state
        return state

    def _build_context_input(self, state: _SessionState, session_id: str, user_text: str) -> str:
        """按需使用 hello_agents ContextBuilder 进行上下文构建。"""
        if not self._enable_context_builder:
            return user_text
        try:
            history: List[Message] = state.agent.get_history()[-8:]
            memory_lines = self._memory_store.get_memory_lines(session_id)
            packets: List[ContextPacket] = []
            if memory_lines:
                packets.append(
                    ContextPacket(
                        content="\n".join(memory_lines[-8:]),
                        metadata={"type": "related_memory"},
                    )
                )
            return state.context_builder.build(
                user_query=user_text,
                conversation_history=history,
                system_instructions=None,
                additional_packets=packets,
            )
        except Exception:
            return user_text

    def _normalize_answer(self, answer: str) -> str:
        """将模型回复裁剪为简短纯文本，降低风控风险。"""
        return sanitize_for_config(answer, self._anti_risk)

    def reply(self, session_id: str, user_text: str) -> Tuple[str, str]:
        """处理单次用户输入并返回回复。

        流程：会话获取 ->（可选）上下文构建 -> 调用 hello_agents Agent -> 风控裁剪。
        """
        state = self._get_session(session_id)
        agent_input = self._build_context_input(state, session_id, user_text)

        try:
            answer = state.agent.run(agent_input)
        except Exception:
            answer = self._anti_risk.fallback_reply
        answer = self._normalize_answer(answer)

        try:
            self._memory_store.append_turn(session_id, user_text, answer)
        except Exception:
            pass
        return answer, session_id
