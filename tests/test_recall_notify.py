"""撤回通知逻辑单元测试。"""

from __future__ import annotations

import unittest

import qq_agent.qq_bot as bot


class RecallNotifyTests(unittest.IsolatedAsyncioTestCase):
    """验证撤回通知（合并转发/降级文本）行为。"""

    def test_build_forward_nodes(self) -> None:
        nodes = bot._build_forward_nodes(
            group_id="10001",
            summary={
                "sender_name": "张三",
                "user_id": "20001",
                "text": "测试消息",
                "recalled_at": "2026-04-10 18:00:00",
            },
        )
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["type"], "node")
        self.assertEqual(nodes[1]["type"], "node")

    async def test_notify_forward_fallback_to_private(self) -> None:
        old_notify = bot.QQ_RECALL_NOTIFY_SUPERADMINS
        old_mode = bot.QQ_RECALL_NOTIFY_MODE
        old_admins = set(bot.QQ_SUPER_ADMINS)
        old_forward = bot._send_private_forward_msg
        old_private = bot._send_private_msg
        try:
            bot.QQ_RECALL_NOTIFY_SUPERADMINS = True
            bot.QQ_RECALL_NOTIFY_MODE = "forward"
            bot.QQ_SUPER_ADMINS = {"20001"}

            called = {"forward": 0, "private": 0}

            async def fake_forward(user_id: int, nodes: list[dict]) -> None:
                called["forward"] += 1
                raise RuntimeError("forward failed")

            async def fake_private(user_id: int, text: str) -> None:
                called["private"] += 1

            bot._send_private_forward_msg = fake_forward
            bot._send_private_msg = fake_private

            await bot._notify_super_admins(
                "10001",
                {
                    "sender_name": "张三",
                    "user_id": "u1",
                    "text": "hello",
                    "recalled_at": "2026-04-10 18:00:00",
                },
            )
            self.assertEqual(called["forward"], 1)
            self.assertEqual(called["private"], 1)
        finally:
            bot.QQ_RECALL_NOTIFY_SUPERADMINS = old_notify
            bot.QQ_RECALL_NOTIFY_MODE = old_mode
            bot.QQ_SUPER_ADMINS = old_admins
            bot._send_private_forward_msg = old_forward
            bot._send_private_msg = old_private


if __name__ == "__main__":
    unittest.main()
