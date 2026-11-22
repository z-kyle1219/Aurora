"""LLM-as-judge 裁判解析与类型收敛的单元测试。"""

import pytest

from aurora import judge


@pytest.mark.parametrize(
    ("tool_call_arguments", "expected_reason"),
    [
        (None, "裁判未返回有效判分"),
        ("{oops", "裁判参数解析失败"),
    ],
    ids=["no-tool-call", "bad-json"],
)
def test_jud_unit_001__missing_tool_call_or_bad_json_uses_defaults(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory,
    tool_call_arguments, expected_reason,
):
    """JUD-UNIT-001：无 tool_call / 坏 JSON 时 judge_answer 默认 partial、judge_hallucination 默认 False。"""
    if tool_call_arguments is None:
        response = response_factory(message_factory(content=None))
    else:
        response = response_factory(
            message_factory(tool_calls=[tool_call_factory("judge", tool_call_arguments)])
        )
    # judge_answer 与 judge_hallucination 各调用一次模型，脚本须提供两份同形状响应
    fake = scripted_client_factory([response, response])
    monkeypatch.setattr(judge, "get_client", lambda: fake)

    answer_verdict = judge.judge_answer("uo = -30V", "uo = -30V，输出与输入反相")
    hallucination_verdict = judge.judge_hallucination("uo = -30V", "uo = -30V，输出与输入反相")

    assert answer_verdict == {"judgment": "partial", "reason": expected_reason}
    assert hallucination_verdict == {"hallucinated": False, "reason": expected_reason}
    assert fake.call_count == 2
    fake.assert_exhausted()


def test_jud_unit_002__invalid_judgment_enum_converges_to_partial(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory
):
    """JUD-UNIT-002：judgment 非法枚举收敛为 partial，reason 保留。"""
    tool_call = tool_call_factory("judge", '{"judgment":"maybe","reason":"不在枚举内"}')
    fake = scripted_client_factory([response_factory(message_factory(tool_calls=[tool_call]))])
    monkeypatch.setattr(judge, "get_client", lambda: fake)

    verdict = judge.judge_answer("uo = -30V", "uo = +30V")

    assert verdict == {"judgment": "partial", "reason": "不在枚举内"}
    fake.assert_exhausted()


@pytest.mark.parametrize(
    ("arguments", "expected_reason"),
    [
        ('{"hallucinated":"yes","reason":"字符串不是布尔"}', "字符串不是布尔"),
        ('{"hallucinated":1,"reason":"整数不是布尔"}', "整数不是布尔"),
    ],
    ids=["string-value", "int-value"],
)
def test_jud_unit_002__non_bool_hallucination_converges_to_false(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory,
    arguments, expected_reason,
):
    """JUD-UNIT-002：hallucinated 非 bool（含 int 1）一律收敛 False。"""
    tool_call = tool_call_factory("judge", arguments)
    fake = scripted_client_factory([response_factory(message_factory(tool_calls=[tool_call]))])
    monkeypatch.setattr(judge, "get_client", lambda: fake)

    verdict = judge.judge_hallucination("检索片段", "模型回答")

    assert verdict == {"hallucinated": False, "reason": expected_reason}
    fake.assert_exhausted()
