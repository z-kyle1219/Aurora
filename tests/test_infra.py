"""阶段 0 测试基础设施自检。"""

import os
import socket

import numpy as np
import pytest

from aurora import config, embedder, router
from aurora.chunker import Chunk
from tests.conftest import (
    OFFLINE_BGE_ERROR,
    OFFLINE_NETWORK_ERROR,
    OFFLINE_OPENAI_ERROR,
    OfflineProtectionError,
    TEST_API_KEY,
    _load_dotenv_calls,
)
from tests.fixtures.llm_responses import ROUTER_CONCEPT_ARGUMENTS


def test_inf_unit_001__openai_client_construction_is_blocked(monkeypatch):
    """INF-UNIT-001：dummy key 下仍禁止构造真实 OpenAI 客户端。"""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "stage-0-dummy-key")
    network_attempts = []

    def unexpected_connection(*args, **kwargs):
        network_attempts.append((args, kwargs))
        raise AssertionError("OpenAI 构造哨兵之前不应尝试网络连接")

    monkeypatch.setattr(socket, "create_connection", unexpected_connection)

    with pytest.raises(OfflineProtectionError, match=OFFLINE_OPENAI_ERROR):
        config.get_client()

    assert network_attempts == []


def test_inf_unit_002__socket_create_connection_blocks_business_client_path(monkeypatch):
    """INF-UNIT-002：create_connection 防线覆盖项目 get_client 调用路径。"""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "stage-0-dummy-key")
    constructor_calls = []

    def socket_using_openai(**kwargs):
        constructor_calls.append(kwargs)
        socket.create_connection(("192.0.2.1", 9), timeout=1)

    monkeypatch.setattr(config, "OpenAI", socket_using_openai)

    with pytest.raises(OfflineProtectionError, match=OFFLINE_NETWORK_ERROR):
        config.get_client()

    assert constructor_calls == [
        {"api_key": "stage-0-dummy-key", "base_url": config.DEEPSEEK_BASE_URL}
    ]


def test_inf_unit_003__bge_model_loading_is_blocked():
    """INF-UNIT-003：encode_query 在构造或下载真实 BGE 前失败。"""
    with pytest.raises(OfflineProtectionError, match=OFFLINE_BGE_ERROR):
        embedder.encode_query("probe")


def test_inf_unit_004__tiny_vector_fixture_matches_chunk_contract(tiny_vector_data):
    """INF-UNIT-004：实际调用 Fixture 并验证合成向量与 Chunk 字段。"""
    vectors, chunks = tiny_vector_data

    assert vectors.dtype == np.float32
    assert vectors.shape == (3, 2)
    assert vectors.tolist() == [[1.0, 0.0], [0.0, 1.0], [0.6000000238418579, 0.800000011920929]]
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [(chunk.source, chunk.heading) for chunk in chunks] == [
        ("synthetic-opamp.md", "集成运放"),
        ("synthetic-feedback.md", "负反馈"),
        ("synthetic-power.md", "直流电源"),
    ]


def test_inf_unit_005__router_accepts_scripted_client(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
):
    """INF-UNIT-005：Fake 注入使用方并校验 Router 的关键请求参数。"""
    tool_call = tool_call_factory(
        "route_circuit",
        ROUTER_CONCEPT_ARGUMENTS,
    )
    response = response_factory(message_factory(tool_calls=[tool_call]))
    fake = scripted_client_factory([response])
    monkeypatch.setattr(router, "get_client", lambda: fake)

    result = router.route("什么是虚短？")

    assert result == {
        "circuit_type": "concept",
        "confidence": 0.9,
        "reason": "基础概念",
    }
    assert fake.call_count == 1
    assert fake.last_call["model"] == config.DEEPSEEK_MODEL
    assert fake.last_call["temperature"] == 0.0
    assert fake.last_call["messages"][-1] == {"role": "user", "content": "什么是虚短？"}
    assert fake.last_call["tools"] == [router.ROUTE_TOOL]
    assert fake.last_call["tool_choice"]["function"]["name"] == "route_circuit"
    fake.assert_exhausted()


def test_inf_unit_006__business_module_client_reference_is_blocked(monkeypatch):
    """INF-UNIT-006：业务模块绑定的 get_client 仍命中 config.OpenAI 哨兵。"""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", TEST_API_KEY)

    assert router.get_client is config.get_client
    with pytest.raises(OfflineProtectionError, match=OFFLINE_OPENAI_ERROR):
        router.get_client()


def test_inf_unit_007__socket_connect_is_blocked():
    """INF-UNIT-007：socket.socket.connect 自身具有独立负向证据。"""
    probe = socket.socket()
    probe.settimeout(1)
    try:
        with pytest.raises(OfflineProtectionError, match=OFFLINE_NETWORK_ERROR):
            probe.connect(("192.0.2.1", 9))
    finally:
        probe.close()


def test_inf_unit_008__test_credentials_and_dotenv_are_isolated():
    """INF-UNIT-008：默认测试配置只含 dummy key，且 dotenv 屏蔽确实生效。"""
    assert os.environ["DEEPSEEK_API_KEY"] == TEST_API_KEY
    assert config.DEEPSEEK_API_KEY == TEST_API_KEY
    assert _load_dotenv_calls == 1
