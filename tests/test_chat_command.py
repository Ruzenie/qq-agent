"""chat on/off 会话开关单元测试。"""

from __future__ import annotations

import unittest

import qq_agent.qq_bot as bot


class ChatCommandTests(unittest.TestCase):
    """验证 chat 指令按会话生效。"""

    def setUp(self) -> None:
        self._old_state = dict(bot._SESSION_CHAT_ENABLED)
        bot._SESSION_CHAT_ENABLED.clear()

    def tearDown(self) -> None:
        bot._SESSION_CHAT_ENABLED.clear()
        bot._SESSION_CHAT_ENABLED.update(self._old_state)

    def test_group_session_chat_toggle(self) -> None:
        event = {"message_type": "group", "group_id": "10001", "user_id": "20001"}
        self.assertEqual(bot._handle_chat_command(event, ["status"]), "当前会话 LLM 状态：off。")
        self.assertEqual(bot._handle_chat_command(event, ["on"]), "已开启当前会话 LLM 对话。")
        self.assertEqual(bot._handle_chat_command(event, ["status"]), "当前会话 LLM 状态：on。")
        self.assertEqual(bot._handle_chat_command(event, ["off"]), "已关闭当前会话 LLM 对话。")
        self.assertEqual(bot._handle_chat_command(event, ["status"]), "当前会话 LLM 状态：off。")

    def test_private_session_isolated_from_group(self) -> None:
        group_event = {"message_type": "group", "group_id": "10001", "user_id": "20001"}
        private_event = {"message_type": "private", "user_id": "20001"}
        bot._handle_chat_command(group_event, ["on"])
        self.assertEqual(bot._handle_chat_command(group_event, ["status"]), "当前会话 LLM 状态：on。")
        self.assertEqual(bot._handle_chat_command(private_event, ["status"]), "当前会话 LLM 状态：off。")


if __name__ == "__main__":
    unittest.main()
