"""Aurora 全局配置与 DeepSeek 客户端。

这里只做一件事：从环境变量读取配置，返回一个可复用的 LLM 客户端。

关键认知：DeepSeek 提供「OpenAI 兼容接口」，也就是说它和 OpenAI 用
同一套请求协议，只是 base_url 不同。所以我们直接用 `openai` 库，
换个地址就能连 DeepSeek——这也是面试常问的「OpenAI 兼容」含义。
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

# 自动读取项目根目录下的 .env 文件（不存在也不报错，稍后显式校验 key）
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


def get_client() -> OpenAI:
    """返回一个 DeepSeek 客户端。没有 key 时直接报错，避免静默失败。"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "未找到 DEEPSEEK_API_KEY。\n"
            "请把 aurora/.env.example 复制为 aurora/.env，并填入你的 key。"
        )
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
