"""meme 命令单元测试。"""

from __future__ import annotations

import unittest

import qq_agent.qq_bot as bot


class MemeCommandTests(unittest.TestCase):
    """验证 meme 命令路由与响应。"""

    def test_handle_meme_list(self) -> None:
        text = bot._handle_meme_command(["list"])
        self.assertIn("meme 模板列表", text)
        self.assertIn("classic", text)
        self.assertIn("alert", text)

    def test_handle_meme_generate(self) -> None:
        reply = bot._handle_meme_command(["classic", "今天不想写bug|但还是得修"])
        self.assertTrue(reply.startswith("[CQ:image,file=file://"))

    def test_handle_command_route_to_meme(self) -> None:
        event = {"message_type": "group", "group_id": "10001", "user_id": "20001"}
        reply = bot._handle_command(event, "/meme alert 紧急通知|测试内容")
        self.assertIsNotNone(reply)
        self.assertTrue((reply or "").startswith("[CQ:image,file=file://"))
        self.assertTrue(bot._is_help_query_text("meme alert 紧急通知|测试内容"))

    def test_handle_meme_errors(self) -> None:
        self.assertIn("参数不足", bot._handle_meme_command(["classic"]))
        self.assertIn("生成失败", bot._handle_meme_command(["unknown", "a|b"]))


if __name__ == "__main__":
    unittest.main()
