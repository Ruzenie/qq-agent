"""LLM 客户端封装模块。

职责：
1. 从环境变量或显式参数加载模型配置；
2. 统一接入 hello_agents 的 LLM 核心能力；
3. 对外提供稳定的 `think()` 调用入口（兼容旧调用方式）。
"""

import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from hello_agents import HelloAgentsLLM as HAHelloAgentsLLM

load_dotenv()


class HelloAgentsLLM:
    """面向对话调用的轻量 LLM 客户端。"""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        temperature: float = 0.2,
    ) -> None:
        """初始化客户端。

        优先使用显式参数；未传入时回退到环境变量。
        """
        self.model = model or os.getenv("LLM_MODEL_ID")
        api_key = api_key or os.getenv("LLM_API_KEY")
        base_url = base_url or os.getenv("LLM_BASE_URL")
        timeout = timeout or int(os.getenv("LLM_TIMEOUT", 60))
        user_agent = os.getenv("LLM_USER_AGENT", "Mozilla/5.0")

        if not all([self.model, api_key, base_url]):
            raise ValueError("LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL are required.")

        self._default_temperature = temperature
        self.client = HAHelloAgentsLLM(
            model=self.model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            temperature=temperature,
            user_agent=user_agent,
        )

    def think(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        """发起非流式对话请求并返回文本结果。"""
        response = self.client.invoke(messages, temperature=temperature or self._default_temperature)
        return response.content or ""

    def native(self) -> HAHelloAgentsLLM:
        """返回 hello_agents 原生 LLM 对象，供 Agent 运行时复用。"""
        return self.client
