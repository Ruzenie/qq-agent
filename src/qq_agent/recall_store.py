"""群消息监听与撤回留痕存储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import time


@dataclass(frozen=True)
class RecallStoreConfig:
    """撤回留痕存储配置。"""

    file_path: Path
    max_messages_per_group: int
    raw_message_ttl_seconds: int
    recalled_message_ttl_seconds: int
    cleanup_interval_seconds: int


def load_recall_store_config_from_env() -> RecallStoreConfig:
    """从环境变量加载撤回留痕配置。"""
    return RecallStoreConfig(
        file_path=Path(os.getenv("QQ_RECALL_STORE_FILE", "data/memory/group_recall_store.json")),
        max_messages_per_group=max(50, int(os.getenv("QQ_RECALL_STORE_MAX_PER_GROUP", "500"))),
        raw_message_ttl_seconds=max(3600, int(os.getenv("QQ_RECALL_RAW_TTL_HOURS", "24")) * 3600),
        recalled_message_ttl_seconds=max(86400, int(os.getenv("QQ_RECALL_KEEP_DAYS", "30")) * 86400),
        cleanup_interval_seconds=max(60, int(os.getenv("QQ_RECALL_CLEANUP_INTERVAL_SECONDS", "3600"))),
    )


class GroupRecallStore:
    """基于 JSON 文件的群消息留痕存储。"""

    def __init__(self, config: RecallStoreConfig) -> None:
        self._config = config
        self._last_cleanup_ts = 0.0

    @staticmethod
    def _safe_parse_ts(raw: str) -> Optional[datetime]:
        """解析时间字符串，失败返回 None。"""
        raw = str(raw).strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _load_all(self) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
        path = self._config.file_path
        if not path.exists():
            return {"groups": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("groups"), dict):
                return data
        except Exception:
            return {"groups": {}}
        return {"groups": {}}

    def _save_all(self, data: Dict[str, Dict[str, List[Dict[str, object]]]]) -> None:
        path = self._config.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def cleanup_expired(self) -> Dict[str, int]:
        """清理过期记录。

        - 非撤回消息：按 raw TTL 清理；
        - 已撤回消息：按 recalled TTL 清理。
        """
        data = self._load_all()
        groups = data.setdefault("groups", {})
        now = datetime.now()
        removed_count = 0
        removed_groups = 0
        remaining_count = 0

        for group_id in list(groups.keys()):
            bucket = groups.get(group_id, {})
            messages = bucket.get("messages", [])
            kept: List[Dict[str, object]] = []
            for item in messages:
                is_recalled = bool(item.get("recalled"))
                ref_ts = self._safe_parse_ts(str(item.get("recalled_at", ""))) if is_recalled else self._safe_parse_ts(
                    str(item.get("ts", ""))
                )
                if ref_ts is None:
                    # 时间异常时做保守清理，避免脏数据无限增长
                    removed_count += 1
                    continue
                age_seconds = int((now - ref_ts).total_seconds())
                ttl_seconds = (
                    self._config.recalled_message_ttl_seconds if is_recalled else self._config.raw_message_ttl_seconds
                )
                if age_seconds > ttl_seconds:
                    removed_count += 1
                    continue
                kept.append(item)

            if kept:
                bucket["messages"] = kept[-self._config.max_messages_per_group :]
                remaining_count += len(bucket["messages"])
                groups[group_id] = bucket
            else:
                removed_groups += 1
                groups.pop(group_id, None)

        data["groups"] = groups
        self._save_all(data)
        return {
            "removed_count": removed_count,
            "removed_groups": removed_groups,
            "remaining_count": remaining_count,
        }

    def cleanup_if_due(self) -> Optional[Dict[str, int]]:
        """按配置间隔触发清理。"""
        now_ts = time.time()
        if now_ts - self._last_cleanup_ts < self._config.cleanup_interval_seconds:
            return None
        self._last_cleanup_ts = now_ts
        return self.cleanup_expired()

    def append_message(
        self,
        group_id: str,
        message_id: str,
        user_id: str,
        text: str,
        sender_name: str,
        message_segments: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """追加一条群消息。"""
        data = self._load_all()
        groups = data.setdefault("groups", {})
        group_bucket = groups.setdefault(group_id, {"messages": []})
        messages = group_bucket.setdefault("messages", [])

        messages.append(
            {
                "message_id": message_id,
                "user_id": user_id,
                "sender_name": sender_name,
                "text": text,
                "message_segments": message_segments or [],
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "recalled": False,
                "recalled_at": "",
                "operator_id": "",
            }
        )
        group_bucket["messages"] = messages[-self._config.max_messages_per_group :]
        self._save_all(data)

    def mark_recalled(self, group_id: str, message_id: str, operator_id: str) -> Dict[str, Any]:
        """将消息标记为已撤回，返回留痕摘要。"""
        data = self._load_all()
        groups = data.setdefault("groups", {})
        group_bucket = groups.setdefault(group_id, {"messages": []})
        messages = group_bucket.setdefault("messages", [])

        hit: Optional[Dict[str, object]] = None
        for item in reversed(messages):
            if str(item.get("message_id", "")) == message_id:
                hit = item
                break

        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if hit is None:
            hit = {
                "message_id": message_id,
                "user_id": "",
                "sender_name": "",
                "text": "[未捕获到原始消息文本]",
                "message_segments": [],
                "ts": "",
                "recalled": True,
                "recalled_at": now_ts,
                "operator_id": operator_id,
            }
            messages.append(hit)
        else:
            hit["recalled"] = True
            hit["recalled_at"] = now_ts
            hit["operator_id"] = operator_id

        group_bucket["messages"] = messages[-self._config.max_messages_per_group :]
        self._save_all(data)

        return {
            "message_id": message_id,
            "user_id": str(hit.get("user_id", "")),
            "sender_name": str(hit.get("sender_name", "")),
            "text": str(hit.get("text", "")),
            "message_segments": hit.get("message_segments", []),
            "ts": str(hit.get("ts", "")),
            "recalled_at": str(hit.get("recalled_at", "")),
            "operator_id": str(hit.get("operator_id", "")),
        }

    def list_recalled(self, group_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """按时间倒序获取群内撤回记录。"""
        data = self._load_all()
        groups = data.get("groups", {})
        bucket = groups.get(group_id, {})
        messages = bucket.get("messages", [])
        recalled = [
            {
                "message_id": str(item.get("message_id", "")),
                "user_id": str(item.get("user_id", "")),
                "sender_name": str(item.get("sender_name", "")),
                "text": str(item.get("text", "")),
                "message_segments": item.get("message_segments", []),
                "ts": str(item.get("ts", "")),
                "recalled_at": str(item.get("recalled_at", "")),
                "operator_id": str(item.get("operator_id", "")),
            }
            for item in messages
            if bool(item.get("recalled"))
        ]
        recalled.sort(key=lambda x: x.get("recalled_at", ""), reverse=True)
        return recalled[: max(1, limit)]
