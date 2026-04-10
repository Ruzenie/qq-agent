"""recall_store 单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qq_agent.recall_store import GroupRecallStore, RecallStoreConfig


class RecallStoreTests(unittest.TestCase):
    """验证群撤回留痕的存取行为。"""

    @staticmethod
    def _build_store(tmpdir: str) -> GroupRecallStore:
        return GroupRecallStore(
            RecallStoreConfig(
                file_path=Path(tmpdir) / "recall.json",
                max_messages_per_group=100,
                raw_message_ttl_seconds=3600,
                recalled_message_ttl_seconds=86400,
                cleanup_interval_seconds=60,
            )
        )

    def test_mark_recalled_from_existing_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._build_store(tmpdir)
            store.append_message(
                group_id="10001",
                message_id="m1",
                user_id="u1",
                text="hello",
                sender_name="张三",
            )
            summary = store.mark_recalled(group_id="10001", message_id="m1", operator_id="u2")
            self.assertEqual(summary["message_id"], "m1")
            self.assertEqual(summary["text"], "hello")
            records = store.list_recalled(group_id="10001", limit=10)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["operator_id"], "u2")

    def test_mark_recalled_when_message_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._build_store(tmpdir)
            summary = store.mark_recalled(group_id="10001", message_id="unknown", operator_id="u2")
            self.assertEqual(summary["message_id"], "unknown")
            self.assertEqual(summary["text"], "[未捕获到原始消息文本]")
            records = store.list_recalled(group_id="10001", limit=10)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["message_id"], "unknown")

    def test_cleanup_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = GroupRecallStore(
                RecallStoreConfig(
                    file_path=Path(tmpdir) / "recall.json",
                    max_messages_per_group=100,
                    raw_message_ttl_seconds=3600,
                    recalled_message_ttl_seconds=86400,
                    cleanup_interval_seconds=60,
                )
            )
            data = {
                "groups": {
                    "10001": {
                        "messages": [
                            {
                                "message_id": "raw_old",
                                "user_id": "u1",
                                "sender_name": "A",
                                "text": "old raw",
                                "ts": "2000-01-01 00:00:00",
                                "recalled": False,
                                "recalled_at": "",
                                "operator_id": "",
                            },
                            {
                                "message_id": "recalled_old",
                                "user_id": "u2",
                                "sender_name": "B",
                                "text": "old recalled",
                                "ts": "2000-01-01 00:00:00",
                                "recalled": True,
                                "recalled_at": "2000-01-02 00:00:00",
                                "operator_id": "u3",
                            },
                            {
                                "message_id": "recalled_new",
                                "user_id": "u4",
                                "sender_name": "C",
                                "text": "recent recalled",
                                "ts": "2099-01-01 00:00:00",
                                "recalled": True,
                                "recalled_at": "2099-01-01 00:00:00",
                                "operator_id": "u5",
                            },
                        ]
                    }
                }
            }
            store._save_all(data)
            result = store.cleanup_expired()
            self.assertGreaterEqual(result["removed_count"], 2)
            recalled = store.list_recalled("10001", 10)
            self.assertEqual(len(recalled), 1)
            self.assertEqual(recalled[0]["message_id"], "recalled_new")


if __name__ == "__main__":
    unittest.main()
