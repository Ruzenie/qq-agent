"""审计日志模块。

职责：
1. 记录消息事件处理链路（入站、路由、回包、错误）；
2. 采用 JSONL 追加写入，便于检索与后续分析；
3. 用环境变量控制是否启用和日志文件位置。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict


class AuditLogger:
    """轻量审计日志记录器。"""

    def __init__(self) -> None:
        """初始化审计日志配置。"""
        enabled_raw = os.getenv("QQ_AUDIT_LOG_ENABLED", "1").strip().lower()
        self.enabled = enabled_raw not in {"0", "false", "off", "no"}
        self.file_path = Path(os.getenv("QQ_AUDIT_LOG_FILE", "data/logs/audit.jsonl"))

    def log(self, event_type: str, payload: Dict[str, Any]) -> None:
        """写入一条审计日志。"""
        if not self.enabled:
            return
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_type": event_type,
            "payload": payload,
        }
        with self.file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
