"""Router 结构化协议解析与兜底的单元测试（畸形响应矩阵见 TEST_PLAN §8.3）。"""

import pytest

from aurora import router


def _routed(monkeypatch, scripted_client_factory, response, question="反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo"):
    """注入只含单次响应的 Fake 后执行 route，返回 (fake, 结果)。"""
    fake = scripted_client_factory([response])
    monkeypatch.setattr(router, "get_client", lambda: fake)
    result = router.route(question)
    fake.assert_exhausted()
    return fake, result


def test_rtr_unit_001__parses_valid_structured_tool_call(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory
):
    """RTR-UNIT-001：合法结构化输出三字段原样返回，confidence 为 float。"""
    tool_call = tool_call_factory(
        "route_circuit",
        '{"circuit_type":"opamp","confidence":0.9,"reason":"求 uo"}',
    )
    response = response_factory(message_factory(tool_calls=[tool_call]))

    fake, result = _routed(monkeypatch, scripted_client_factory, response)

    assert result == {"circuit_type": "opamp", "confidence": 0.9, "reason": "求 uo"}
    assert isinstance(result["confidence"], float)
    assert fake.call_count == 1
    fake.assert_exhausted()


@pytest.mark.parametrize("tool_calls", [[], None], ids=["empty-list", "none"])
def test_rtr_unit_002__missing_tool_calls_falls_back_to_concept(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_calls
):
    """RTR-UNIT-002：message 无 tool_call（空列表或 None）时兜底走 concept。"""
    response = response_factory(message_factory(content=None, tool_calls=tool_calls))

    _, result = _routed(monkeypatch, scripted_client_factory, response, "什么是虚短？")

    assert result == {
        "circuit_type": "concept",
        "confidence": 0.0,
        "reason": "Router 未返回有效分类",
    }


def test_rtr_unit_003__malformed_json_arguments_fall_back_to_concept(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory
):
    """RTR-UNIT-003：arguments 坏 JSON 时兜底 concept 并给出可识别 reason。"""
    tool_call = tool_call_factory("route_circuit", "{oops")
    response = response_factory(message_factory(tool_calls=[tool_call]))

    _, result = _routed(monkeypatch, scripted_client_factory, response, "判断这个电路的反馈组态")

    assert result == {"circuit_type": "concept", "confidence": 0.0, "reason": "分类参数解析失败"}


@pytest.mark.parametrize(
    ("arguments", "expected_reason"),
    [
        ('{"circuit_type":"unknown","confidence":0.5,"reason":"不在枚举内"}', "不在枚举内"),
        ('{"confidence":0.5,"reason":"缺少类型"}', "缺少类型"),
    ],
    ids=["unknown-enum", "missing-field"],
)
def test_rtr_unit_004__invalid_or_missing_circuit_type_falls_back_to_concept(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory,
    arguments, expected_reason,
):
    """RTR-UNIT-004：非法枚举与缺失 circuit_type 均回退 concept，reason 保留。"""
    tool_call = tool_call_factory("route_circuit", arguments)
    response = response_factory(message_factory(tool_calls=[tool_call]))

    _, result = _routed(monkeypatch, scripted_client_factory, response, "这个电路属于哪类？")

    assert result == {"circuit_type": "concept", "confidence": 0.5, "reason": expected_reason}


@pytest.mark.parametrize(
    "arguments",
    ["[]", '"opamp"', "null"],
    ids=["json-array", "json-string", "json-null"],
)
@pytest.mark.xfail(strict=True, reason="DEF-03 非 dict 参数未兜底，AttributeError 会中断 Pipeline")
def test_rtr_unit_005__non_dict_arguments_fall_back_like_other_bad_outputs(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory, arguments
):
    """RTR-UNIT-005 / DEF-03：非 dict JSON 应与其他坏输出一致回退，而非抛异常。"""
    tool_call = tool_call_factory("route_circuit", arguments)
    response = response_factory(message_factory(tool_calls=[tool_call]))

    _, result = _routed(monkeypatch, scripted_client_factory, response, "求输出电压")

    assert result["circuit_type"] == router.DEFAULT_TYPE
    assert result["confidence"] == 0.0
    assert result["reason"]


@pytest.mark.parametrize(
    ("confidence_json", "expected_confidence"),
    [('"high"', 0.0), ("null", 0.0), ("2.5", 1.0), ("-1", 0.0)],
    ids=["string", "null", "above-range", "negative"],
)
@pytest.mark.xfail(strict=True, reason="DEF-04 非数值 confidence 抛异常、越界值未钳位")
def test_rtr_unit_006__invalid_confidence_falls_back_to_zero_and_clamps(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory,
    confidence_json, expected_confidence,
):
    """RTR-UNIT-006 / DEF-04：非数值 confidence 回退 0.0，数值钳位到 [0, 1]，不抛异常。"""
    arguments = '{"circuit_type":"opamp","confidence":%s,"reason":"置信度异常"}' % confidence_json
    tool_call = tool_call_factory("route_circuit", arguments)
    response = response_factory(message_factory(tool_calls=[tool_call]))

    _, result = _routed(monkeypatch, scripted_client_factory, response, "求输出电压")

    assert result == {
        "circuit_type": "opamp",
        "confidence": expected_confidence,
        "reason": "置信度异常",
    }


def test_rtr_unit_006__missing_confidence_defaults_to_zero(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory
):
    """RTR-UNIT-006：缺失 confidence 字段回退 0.0（现状已符合目标契约）。"""
    tool_call = tool_call_factory(
        "route_circuit",
        '{"circuit_type":"feedback","reason":"缺少置信度"}',
    )
    response = response_factory(message_factory(tool_calls=[tool_call]))

    _, result = _routed(monkeypatch, scripted_client_factory, response, "判断反馈组态")

    assert result == {"circuit_type": "feedback", "confidence": 0.0, "reason": "缺少置信度"}
