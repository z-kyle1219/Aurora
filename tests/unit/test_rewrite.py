"""上下文改写的门控与异常回退单元测试。"""

import pytest

from aurora import rewrite
from tests.conftest import ScriptStep


def _sample_history():
    """两轮纯文本对，触发改写链路的模型调用。"""
    return [
        {"role": "user", "content": "反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo。"},
        {"role": "assistant", "content": "uo = -30V，输出与输入反相比例。"},
    ]


def test_rwt_unit_001__first_turn_skips_rewrite_without_calling_model(monkeypatch):
    """RWT-UNIT-001：history 为 None / [] 时原样返回，get_client 未被调用（哨兵验证零成本门控）。"""
    client_calls = []

    def unexpected_get_client():
        client_calls.append(1)
        raise AssertionError("首轮改写不应调用模型")

    monkeypatch.setattr(rewrite, "get_client", unexpected_get_client)

    assert rewrite.rewrite_question("反相比例电路求 uo", history=None) == "反相比例电路求 uo"
    assert rewrite.rewrite_question("反相比例电路求 uo", history=[]) == "反相比例电路求 uo"
    assert client_calls == []


def test_rwt_unit_003__client_exception_returns_original_question(
    monkeypatch, scripted_client_factory
):
    """RWT-UNIT-003①：get_client 抛异常时回退原问题且不向外抛。"""
    fake = scripted_client_factory([ScriptStep(exception=RuntimeError("模型不可用"))])
    monkeypatch.setattr(rewrite, "get_client", lambda: fake)

    result = rewrite.rewrite_question("那 R1 换成 20k 呢", history=_sample_history())

    assert result == "那 R1 换成 20k 呢"
    assert fake.call_count == 1
    fake.assert_exhausted()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, "那 R1 换成 20k 呢"),
        ("", "那 R1 换成 20k 呢"),
        ("改" * 501, "那 R1 换成 20k 呢"),
        ('  “反相比例电路 R1 换成 20kΩ 求输出电压”  ', "反相比例电路 R1 换成 20kΩ 求输出电压"),
    ],
    ids=["content-none", "content-empty", "content-too-long", "strip-whitespace-and-quotes"],
)
def test_rwt_unit_003__abnormal_content_falls_back_or_strips(
    monkeypatch, scripted_client_factory, response_factory, message_factory, content, expected
):
    """RWT-UNIT-003②~⑤：空/超长回答回退原问题，正常回答去掉首尾空白与中英文引号。"""
    fake = scripted_client_factory([response_factory(message_factory(content=content))])
    monkeypatch.setattr(rewrite, "get_client", lambda: fake)

    result = rewrite.rewrite_question("那 R1 换成 20k 呢", history=_sample_history())

    assert result == expected
    assert fake.call_count == 1
    fake.assert_exhausted()
