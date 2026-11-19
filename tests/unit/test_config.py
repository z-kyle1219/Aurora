"""配置层纯逻辑测试。"""

import pytest

from aurora import config


def test_cfg_unit_001__missing_api_key_fails_before_client_construction(monkeypatch):
    """CFG-UNIT-001：缺少 API key 时给出配置指引且不构造客户端。"""
    constructor_calls = []

    def unexpected_openai(*args, **kwargs):
        constructor_calls.append((args, kwargs))
        raise AssertionError("缺少 key 时不应构造 OpenAI 客户端")

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", None)
    monkeypatch.setattr(config, "OpenAI", unexpected_openai)

    with pytest.raises(RuntimeError) as exc_info:
        config.get_client()

    message = str(exc_info.value)
    assert "DEEPSEEK_API_KEY" in message
    assert ".env" in message
    assert constructor_calls == []
