"""将电路图片提取为可检查的纯文字题目。"""

import base64
import binascii
import json
import os
from dataclasses import dataclass

from openai import OpenAI


MAX_IMAGE_BYTES = 5 * 1024 * 1024
PROMPT = (
    "你只负责读取模拟电子电路题图片，不解题、不补全看不清的内容。"
    "只返回 JSON 对象，字段为题干、元件参数、连接关系、电源极性、不确定项。"
    "前四项是字符串或字符串数组；不确定项是对象数组，每项包含内容、原因、影响解题(boolean)。"
    "交叉线有无连接点、器件端子、数值单位和正负极看不清时必须列入不确定项；"
    "如果无法确认图片是否为电路图，也列为影响解题的不确定项。不要猜测。"
)


@dataclass(frozen=True)
class Extraction:
    text: str
    uncertainties: list[dict[str, str | bool]]
    requires_confirmation: bool


def decode_image(data: str) -> tuple[str, bytes]:
    """验证 data URL 的媒体类型、体积和文件签名。"""
    try:
        header, encoded = data.split(",", 1)
        mime = {"data:image/png;base64": "image/png",
                "data:image/jpeg;base64": "image/jpeg",
                "data:image/webp;base64": "image/webp"}[header]
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, KeyError, binascii.Error) as exc:
        raise ValueError("仅支持 PNG、JPEG 或 WebP 图片") from exc
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能为空或超过 5 MB")
    signatures = {"image/png": image.startswith(b"\x89PNG\r\n\x1a\n"),
                  "image/jpeg": image.startswith(b"\xff\xd8\xff"),
                  "image/webp": image.startswith(b"RIFF") and image[8:12] == b"WEBP"}
    if not signatures[mime]:
        raise ValueError("图片内容与格式不符")
    return mime, image


def extract_circuit(image_data: str, question: str = "") -> Extraction:
    """调用独立视觉模型；无有效结构时失败，不把猜测送入答题链。"""
    mime, image = decode_image(image_data)
    key = os.getenv("VISION_API_KEY")
    model = os.getenv("VISION_MODEL")
    if not key or not model:
        raise RuntimeError("视觉模型尚未配置")
    client = OpenAI(api_key=key, base_url=os.getenv("VISION_BASE_URL") or None)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": question or "请提取这张电路题图片"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"}},
            ]},
        ],
    )
    content = response.choices[0].message.content
    if not isinstance(content, str):
        raise RuntimeError("视觉模型没有返回有效结构")
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("视觉模型没有返回有效结构") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("视觉模型没有返回有效结构")
    sections = []
    missing_critical = False
    for field in ("题干", "元件参数", "连接关系", "电源极性"):
        value = raw.get(field)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            value = "；".join(value)
        if not isinstance(value, str):
            raise RuntimeError("视觉模型没有返回有效结构")
        if field in ("元件参数", "连接关系", "电源极性") and not value.strip():
            missing_critical = True
        sections.append(f"{field}：{value.strip() or '未识别'}")
    issues = raw.get("不确定项")
    if not isinstance(issues, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("内容"), str)
        or not isinstance(item.get("原因"), str) or type(item.get("影响解题")) is not bool
        for item in issues
    ):
        raise RuntimeError("视觉模型没有返回有效结构")
    if all(section.endswith("未识别") for section in sections):
        raise RuntimeError("图片中未识别到电路信息")
    if missing_critical:
        issues.append({"内容": "关键电路信息未识别", "原因": "视觉模型未提供完整参数、连接或极性", "影响解题": True})
    return Extraction("\n".join(sections), issues, any(item["影响解题"] for item in issues))
