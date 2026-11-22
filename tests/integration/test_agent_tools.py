"""Agent 与真实本地工具执行之间的集成测试。"""

from aurora import agent
from aurora.tools import TOOLS, validate_opamp_output


def test_agt_int_001__real_tool_result_and_validator_warning_flow_back(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
):
    """AGT-INT-001：真实计算得到 30 并触发饱和警告，警告随 tool 消息回传模型。"""
    tool_call_id = "call-opamp-output"
    tool_call = tool_call_factory(
        "calculate_expression",
        '{"expr":"-(100/10)*(-3)"}',
        call_id=tool_call_id,
    )
    first_response = response_factory(
        message_factory(tool_calls=[tool_call]),
        finish_reason="tool_calls",
    )
    final_answer = "uo = +15V（运放饱和，输出被钳位）"
    second_response = response_factory(message_factory(content=final_answer))
    fake = scripted_client_factory([first_response, second_response])
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    validators = {
        "calculate_expression": lambda result: validate_opamp_output(result, 15.0)
    }

    answer, trace = agent.run_agent(
        "反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo",
        validators=validators,
    )

    assert answer == final_answer
    assert len(trace) == 1
    step = trace[0]
    assert step["tool"] == "calculate_expression"
    assert step["arguments"] == {"expr": "-(100/10)*(-3)"}
    assert step["result"] == "30"
    assert "饱和" in step["warning"]
    assert "钳位在 +15V" in step["warning"]

    second_round_messages = fake.calls[1]["messages"]
    assert [message["role"] for message in second_round_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert second_round_messages[-2]["tool_calls"][0]["id"] == tool_call_id
    tool_message = second_round_messages[-1]
    assert tool_message["tool_call_id"] == tool_call_id
    assert tool_message["content"] == (
        "30" + "\n[合理性校验] " + step["warning"]
    )

    # 首次请求携带真实工具菜单与固定参数。
    first_call = fake.calls[0]
    assert first_call["tools"] == TOOLS
    assert first_call["model"] == agent.DEEPSEEK_MODEL
    assert first_call["temperature"] == 0.3
    assert fake.call_count == 2
    fake.assert_exhausted()


def test_agt_int_002__intermediate_values_trigger_opamp_validation(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
):
    """AGT-INT-002：现状锁定——每次 calculate_expression 都跑 validator，中间量也告警。

    TEST_PLAN §13 第 4 条已决定暂时保留现状，但不把误报视为成功契约；
    后续改造 validator 适用范围时，trace[0]["warning"] 预期改为 None。
    """
    first_tool_call = tool_call_factory(
        "calculate_expression", '{"expr":"100000*3"}', call_id="call-intermediate"
    )
    second_tool_call = tool_call_factory(
        "calculate_expression", '{"expr":"0.5*10"}', call_id="call-final-value"
    )
    responses = [
        response_factory(
            message_factory(tool_calls=[first_tool_call]), finish_reason="tool_calls"
        ),
        response_factory(
            message_factory(tool_calls=[second_tool_call]), finish_reason="tool_calls"
        ),
        response_factory(message_factory(content="增益为 5，中间量 300000 不代表输出电压")),
    ]
    fake = scripted_client_factory(responses)
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    validators = {
        "calculate_expression": lambda result: validate_opamp_output(result, 15.0)
    }

    answer, trace = agent.run_agent("先算中间量再算增益", validators=validators)

    assert answer == "增益为 5，中间量 300000 不代表输出电压"
    assert [step["result"] for step in trace] == ["300000", "5"]
    assert "钳位在 +15V" in trace[0]["warning"]
    assert trace[1]["warning"] is None

    second_round_tool_message = fake.calls[1]["messages"][-1]
    assert second_round_tool_message["role"] == "tool"
    assert "[合理性校验]" in second_round_tool_message["content"]

    third_round_tool_message = fake.calls[2]["messages"][-1]
    assert third_round_tool_message == {
        "role": "tool",
        "tool_call_id": "call-final-value",
        "content": "5",
    }
    assert fake.call_count == 3
    fake.assert_exhausted()


def test_agt_int_003__returns_non_real_calculation_error_to_agent(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
):
    """AGT-INT-003：非实数计算错误进入 tool 消息和 trace 后 Agent 可完成。"""
    tool_call_id = "call-non-real-result"
    tool_call = tool_call_factory(
        "calculate_expression",
        '{"expr":"(-8)**(1/3)"}',
        call_id=tool_call_id,
    )
    first_response = response_factory(
        message_factory(tool_calls=[tool_call]),
        finish_reason="tool_calls",
    )
    final_answer = "该表达式的结果不是实数。"
    second_response = response_factory(message_factory(content=final_answer))
    fake = scripted_client_factory([first_response, second_response])
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    answer, trace = agent.run_agent("计算 (-8)**(1/3)")

    expected_tool_error = "计算失败：计算结果不是实数"
    second_round_messages = fake.calls[1]["messages"]

    assert answer == final_answer
    assert trace == [
        {
            "tool": "calculate_expression",
            "arguments": {"expr": "(-8)**(1/3)"},
            "result": expected_tool_error,
            "warning": None,
        }
    ]
    assert second_round_messages[-2]["role"] == "assistant"
    assert second_round_messages[-2]["tool_calls"][0]["id"] == tool_call_id
    assert second_round_messages[-1] == {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": expected_tool_error,
    }
    assert fake.call_count == 2
    fake.assert_exhausted()
