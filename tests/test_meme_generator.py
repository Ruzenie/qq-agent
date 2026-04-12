"""meme_generator 单元测试。"""

from __future__ import annotations

import unittest

from qq_agent.meme_generator import available_templates, render_meme, templates_help_text


class MemeGeneratorTests(unittest.TestCase):
    """验证表情包模板渲染主流程。"""

    def test_templates_registered(self) -> None:
        templates = available_templates()
        self.assertIn("classic", templates)
        self.assertIn("alert", templates)
        text = templates_help_text()
        self.assertIn("meme classic", text)
        self.assertIn("meme alert", text)

    def test_render_classic_and_alert(self) -> None:
        classic_path = render_meme("classic", "上句测试|下句测试")
        alert_path = render_meme("alert", "紧急通知|今晚十点发布")
        self.assertTrue(classic_path.exists())
        self.assertTrue(alert_path.exists())
        self.assertGreater(classic_path.stat().st_size, 0)
        self.assertGreater(alert_path.stat().st_size, 0)

    def test_render_invalid_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "参数格式错误"):
            render_meme("classic", "没有分隔符")
        with self.assertRaisesRegex(ValueError, "不支持的模板"):
            render_meme("unknown", "a|b")


if __name__ == "__main__":
    unittest.main()
