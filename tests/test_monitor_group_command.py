"""监听群管理命令单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import qq_agent.qq_bot as bot


class MonitorGroupCommandTests(unittest.TestCase):
    """验证 mg add/del/list 行为。"""

    def setUp(self) -> None:
        self._old_admins = set(bot.QQ_SUPER_ADMINS)
        self._old_groups = set(bot._RUNTIME_MONITOR_GROUPS)
        self._old_group_file = bot.QQ_MONITOR_GROUP_FILE
        self._tmpdir = tempfile.TemporaryDirectory()
        bot.QQ_SUPER_ADMINS = {"20001"}
        bot._RUNTIME_MONITOR_GROUPS = set()
        bot.QQ_MONITOR_GROUP_FILE = Path(self._tmpdir.name) / "monitor_groups.txt"

    def tearDown(self) -> None:
        bot.QQ_SUPER_ADMINS = self._old_admins
        bot._RUNTIME_MONITOR_GROUPS = self._old_groups
        bot.QQ_MONITOR_GROUP_FILE = self._old_group_file
        self._tmpdir.cleanup()

    def test_add_and_list_and_del(self) -> None:
        event = {"user_id": "20001"}
        msg = bot._handle_admin_command(event, "mg add 123456")
        self.assertIn("已添加监听群", msg or "")
        self.assertIn("123456", bot._RUNTIME_MONITOR_GROUPS)

        msg = bot._handle_admin_command(event, "mg list")
        self.assertIn("123456", msg or "")

        msg = bot._handle_admin_command(event, "mg del 123456")
        self.assertIn("已移除监听群", msg or "")
        self.assertNotIn("123456", bot._RUNTIME_MONITOR_GROUPS)

    def test_non_admin_denied(self) -> None:
        msg = bot._handle_admin_command({"user_id": "not-admin"}, "mg add 123456")
        self.assertEqual(msg, "无权限：仅超级管理员可管理白名单和监听群。")


if __name__ == "__main__":
    unittest.main()
