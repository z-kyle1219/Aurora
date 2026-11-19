"""Agent 与真实本地工具执行之间的集成测试。"""

from aurora import agent


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
