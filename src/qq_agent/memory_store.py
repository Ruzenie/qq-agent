"""会话长期记忆存储模块。

职责：
1. 将每个会话的最近若干轮对话持久化到本地文件；
2. 为运行时提供可注入的长期记忆片段；
3. 与运行时的短期内存配合形成“分层记忆”。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class MemoryStoreConfig:
    """长期记忆配置。"""

    file_path: Path
    max_turns: int


def load_memory_store_config_from_env() -> MemoryStoreConfig:
    """从环境变量加载长期记忆配置。"""
    return MemoryStoreConfig(
        file_path=Path(os.getenv("QQ_MEMORY_FILE", "data/memory/sessions.json")),
        max_turns=max(1, int(os.getenv("QQ_MEMORY_MAX_TURNS", "8"))),
    )


class SessionMemoryStore:
    """基于 JSON 文件的会话长期记忆存储。"""

    def __init__(self, config: MemoryStoreConfig) -> None:
        """初始化存储对象。"""
        self._config = config

    def _load_all(self) -> Dict[str, Dict[str, object]]:
        """读取全部会话记忆。"""
        path = self._config.file_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
        return {}

    def _save_all(self, data: Dict[str, Dict[str, object]]) -> None:
        """写回全部会话记忆。"""
        path = self._config.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_memory_lines(self, session_id: str) -> List[str]:
        """获取可注入到提示词中的长期记忆行。"""
        data = self._load_all()
        item = data.get(session_id)
        if not isinstance(item, dict):
            return []
        turns = item.get("turns", [])
        if not isinstance(turns, list):
            return []
        lines: List[str] = []
        for turn in turns[-self._config.max_turns :]:
            if not isinstance(turn, dict):
                continue
            user = str(turn.get("user", "")).strip()
            assistant = str(turn.get("assistant", "")).strip()
            if not user and not assistant:
                continue
            lines.append(f"用户: {user}")
            lines.append(f"助手: {assistant}")
        return lines

    def append_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        """追加一轮对话到长期记忆。"""
        data = self._load_all()
        record = data.get(session_id)
        if not isinstance(record, dict):
            record = {"turns": []}
        turns = record.get("turns", [])
        if not isinstance(turns, list):
            turns = []

        turns.append(
            {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user": user_text,
                "assistant": assistant_text,
            }
        )
        record["turns"] = turns[-self._config.max_turns :]
        record["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[session_id] = record
        self._save_all(data)
