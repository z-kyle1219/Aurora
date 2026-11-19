"""跨测试层复用的离线防线、Fake 客户端和微型数据。"""

from __future__ import annotations

import copy
import os
import socket
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import dotenv
import pytest

TEST_API_KEY = "stage-0-dummy-key"
_load_dotenv = dotenv.load_dotenv
_load_dotenv_calls = 0
aurora_config: Any = None
embedder: Any = None


def _blocked_load_dotenv(*args: Any, **kwargs: Any) -> bool:
    global _load_dotenv_calls
    _load_dotenv_calls += 1
    return False


def pytest_configure(config: pytest.Config) -> None:
    """在测试模块收集前冻结凭据来源，并受控导入 Aurora 配置。"""
    global aurora_config, embedder

    markexpr = config.option.markexpr or ""
    online_requested = "online" in markexpr and "not online" not in markexpr
    if not online_requested:
        os.environ["DEEPSEEK_API_KEY"] = TEST_API_KEY
    elif not os.environ.get("DEEPSEEK_API_KEY"):
        raise pytest.UsageError("显式 online 测试需要通过进程环境提供 DEEPSEEK_API_KEY")

    dotenv.load_dotenv = _blocked_load_dotenv
    try:
        from aurora import config as aurora_config_module, embedder as aurora_embedder
    finally:
        dotenv.load_dotenv = _load_dotenv

    aurora_config = aurora_config_module
    embedder = aurora_embedder
    if _load_dotenv_calls != 1:
        raise pytest.UsageError("测试配置隔离失效：config 未在 .env 屏蔽窗口首次导入")
    if not online_requested and aurora_config.DEEPSEEK_API_KEY != TEST_API_KEY:
        raise pytest.UsageError("测试配置隔离失效：默认测试必须使用 dummy key")

from tests.fixtures.vectors import make_tiny_chunks, make_tiny_vectors


OFFLINE_OPENAI_ERROR = "离线测试禁止构造真实 OpenAI 客户端"
OFFLINE_NETWORK_ERROR = "离线测试禁止建立网络连接"
OFFLINE_BGE_ERROR = "离线测试禁止加载真实 BGE 模型"


class OfflineProtectionError(RuntimeError):
    """默认测试尝试访问外部依赖时抛出的明确错误。"""


@dataclass(frozen=True)
class ScriptStep:
    """一次预期模型调用的响应、异常和可选参数校验。"""

    response: Any = None
    exception: BaseException | None = None
    validator: Callable[[dict[str, Any]], None] | None = None


class ScriptedClient:
    """严格按脚本消费 OpenAI 兼容响应的测试 Fake。"""

    def __init__(self, steps: Iterable[Any | ScriptStep]) -> None:
        self._steps = deque(steps)
        self.calls: list[dict[str, Any]] = []

    @property
    def chat(self) -> ScriptedClient:
        return self

    @property
    def completions(self) -> ScriptedClient:
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> dict[str, Any]:
        if not self.calls:
            raise AssertionError("模型尚未被调用")
        return self.calls[-1]

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(copy.deepcopy(kwargs))
        if not self._steps:
            raise AssertionError("意外的额外模型调用")

        raw_step = self._steps.popleft()
        step = raw_step if isinstance(raw_step, ScriptStep) else ScriptStep(response=raw_step)
        if step.validator is not None:
            step.validator(kwargs)
        if step.exception is not None:
            raise step.exception
        return step.response

    def assert_exhausted(self) -> None:
        assert not self._steps, "预期的模型调用未发生"


def make_tool_call(name: str, arguments: str, *, call_id: str = "call-1") -> SimpleNamespace:
    """构造业务代码实际读取形状的 OpenAI tool call。"""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def make_message(
    content: str | None = None,
    *,
    tool_calls: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """构造 OpenAI 兼容 message，不自动补齐调用方未要求的内容。"""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def make_response(
    message: SimpleNamespace,
    *,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    """构造包含单个 choice 的 OpenAI 兼容响应。"""
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


@pytest.fixture(autouse=True)
def external_dependency_guards(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """默认阻止真实客户端、socket 出站连接和 BGE 模型加载。"""
    allow_network = request.node.get_closest_marker("online") is not None
    allow_bge = request.node.get_closest_marker("ai_eval") is not None

    original_get_model = embedder.get_model
    original_get_model.cache_clear()

    if not allow_network:
        def blocked_openai(*args: Any, **kwargs: Any) -> None:
            raise OfflineProtectionError(OFFLINE_OPENAI_ERROR)

        def blocked_connect(*args: Any, **kwargs: Any) -> None:
            raise OfflineProtectionError(OFFLINE_NETWORK_ERROR)

        monkeypatch.setattr(aurora_config, "OpenAI", blocked_openai)
        monkeypatch.setattr(socket.socket, "connect", blocked_connect)
        monkeypatch.setattr(socket, "create_connection", blocked_connect)

    if not allow_bge:
        def blocked_get_model() -> None:
            raise OfflineProtectionError(OFFLINE_BGE_ERROR)

        monkeypatch.setattr(embedder, "get_model", blocked_get_model)

    yield
    original_get_model.cache_clear()


@pytest.fixture
def scripted_client_factory() -> Callable[[Iterable[Any | ScriptStep]], ScriptedClient]:
    """为每个用例创建独立响应队列和调用记录。"""
    return ScriptedClient


@pytest.fixture
def tiny_vector_data():
    """返回互不共享的二维向量和对应合成 Chunk。"""
    return make_tiny_vectors(), make_tiny_chunks()


@pytest.fixture
def response_factory() -> Callable[..., SimpleNamespace]:
    return make_response


@pytest.fixture
def message_factory() -> Callable[..., SimpleNamespace]:
    return make_message


@pytest.fixture
def tool_call_factory() -> Callable[..., SimpleNamespace]:
    return make_tool_call
