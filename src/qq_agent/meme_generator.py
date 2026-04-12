"""表情包渲染模块。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import io
from pathlib import Path
import tempfile
import uuid
from typing import Callable, Dict, List

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class MemeTemplate:
    """模板元信息。"""

    key: str
    title: str
    usage: str
    example: str
    description: str


_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


_TEMPLATES: Dict[str, MemeTemplate] = {
    "classic": MemeTemplate(
        key="classic",
        title="黑白经典双行",
        usage="meme classic 上句|下句",
        example="meme classic 我真的会谢|但我还能写代码",
        description="白底黑框，上下两段文案居中。",
    ),
    "alert": MemeTemplate(
        key="alert",
        title="黄色警示牌",
        usage="meme alert 标题|内容",
        example="meme alert 紧急通知|今晚九点发版",
        description="黄底黑边，标题条 + 内容区。",
    ),
}


def available_templates() -> Dict[str, MemeTemplate]:
    """返回可用模板。"""
    return dict(_TEMPLATES)


def templates_help_text() -> str:
    """返回模板帮助文本。"""
    lines = ["meme 模板列表："]
    for template in _TEMPLATES.values():
        lines.append(f"- {template.key}（{template.title}）：{template.description}")
        lines.append(f"  用法：{template.usage}")
        lines.append(f"  示例：{template.example}")
    return "\n".join(lines)


def render_to_cq_code(template_key: str, payload: str) -> str:
    """按模板渲染图片并返回 CQ 图片码。"""
    image = _render_image(template_key=template_key, payload=payload)
    image_bytes = _encode_png_bytes(image)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"[CQ:image,file=base64://{encoded}]"


def render_meme(template_key: str, payload: str) -> Path:
    """渲染模板图片并返回本地文件路径。"""
    image = _render_image(template_key=template_key, payload=payload)
    output_dir = Path(tempfile.gettempdir()) / "qq-agent-memes"
    output_dir.mkdir(parents=True, exist_ok=True)
    key = template_key.strip().lower()
    output_path = output_dir / f"{key}_{uuid.uuid4().hex[:12]}.png"
    image.save(output_path, format="PNG")
    return output_path


def _render_image(template_key: str, payload: str) -> Image.Image:
    """根据模板渲染图片对象。"""
    key = template_key.strip().lower()
    if key not in _TEMPLATES:
        raise ValueError(f"不支持的模板：{template_key}")

    renderer: Callable[[str], Image.Image]
    if key == "classic":
        renderer = _render_classic
    elif key == "alert":
        renderer = _render_alert
    else:
        raise ValueError(f"不支持的模板：{template_key}")

    return renderer(payload)


def _encode_png_bytes(image: Image.Image) -> bytes:
    """将图片编码为 PNG 字节流。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _render_classic(payload: str) -> Image.Image:
    """渲染 classic 模板。"""
    top_text, bottom_text = _split_two_parts(payload)
    width, height = 680, 680
    image = Image.new("RGB", (width, height), color="#FFFFFF")
    draw = ImageDraw.Draw(image)

    draw.rectangle((8, 8, width - 8, height - 8), outline="#000000", width=8)
    draw.line((30, height // 2, width - 30, height // 2), fill="#000000", width=5)

    _draw_text_block(
        draw=draw,
        text=top_text,
        box=(40, 40, width - 40, height // 2 - 20),
        text_color="#000000",
        preferred_size=62,
    )
    _draw_text_block(
        draw=draw,
        text=bottom_text,
        box=(40, height // 2 + 20, width - 40, height - 40),
        text_color="#000000",
        preferred_size=62,
    )
    return image


def _render_alert(payload: str) -> Image.Image:
    """渲染 alert 模板。"""
    title, body = _split_two_parts(payload)
    width, height = 720, 520
    image = Image.new("RGB", (width, height), color="#F6D43B")
    draw = ImageDraw.Draw(image)

    draw.rectangle((8, 8, width - 8, height - 8), outline="#111111", width=8)
    draw.rectangle((24, 24, width - 24, 140), fill="#111111")
    draw.rectangle((24, 170, width - 24, height - 24), fill="#FFFFFF", outline="#111111", width=4)

    _draw_text_block(
        draw=draw,
        text=title,
        box=(36, 32, width - 36, 132),
        text_color="#F6D43B",
        preferred_size=56,
    )
    _draw_text_block(
        draw=draw,
        text=body,
        box=(44, 184, width - 44, height - 36),
        text_color="#111111",
        preferred_size=48,
    )
    return image


def _split_two_parts(payload: str) -> tuple[str, str]:
    """将输入按 `|` 拆分为两段。"""
    raw = payload.strip()
    if "|" not in raw:
        raise ValueError("参数格式错误：请用 `|` 分隔两段文案。")
    first, second = raw.split("|", 1)
    first = first.strip()
    second = second.strip()
    if not first or not second:
        raise ValueError("参数格式错误：两段文案都不能为空。")
    return first, second


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    text_color: str,
    preferred_size: int,
) -> None:
    """在给定区域内自适应绘制居中文本。"""
    x1, y1, x2, y2 = box
    max_width = max(40, x2 - x1)
    max_height = max(40, y2 - y1)
    font_size = preferred_size

    while font_size >= 18:
        font = _load_font(font_size)
        lines = _wrap_text(draw=draw, text=text, font=font, max_width=max_width)
        line_height = _line_height(draw=draw, font=font)
        total_height = line_height * len(lines) + max(0, len(lines) - 1) * 6
        if total_height <= max_height:
            break
        font_size -= 2

    start_y = y1 + (max_height - total_height) // 2
    for line in lines:
        line_width = int(draw.textlength(line, font=font))
        x = x1 + (max_width - line_width) // 2
        draw.text((x, start_y), line, font=font, fill=text_color)
        start_y += line_height + 6


def _line_height(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
    """计算行高。"""
    bbox = draw.textbbox((0, 0), "测试Ag", font=font)
    return max(20, bbox[3] - bbox[1])


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """按像素宽度换行。"""
    rows: List[str] = []
    for segment in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not segment:
            rows.append("")
            continue
        current = ""
        for ch in segment:
            candidate = f"{current}{ch}"
            if current and draw.textlength(candidate, font=font) > max_width:
                rows.append(current)
                current = ch
            else:
                current = candidate
        if current:
            rows.append(current)
    return rows or [text]


def _load_font(size: int) -> ImageFont.ImageFont:
    """加载字体并在不可用时降级。"""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()
