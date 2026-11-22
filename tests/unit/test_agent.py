"""Agent 回环的终止、异常出口与校验消息拼装单元测试（真实工具协作另见集成层）。"""

import pytest

from aurora import agent


def test_agt_unit_001__single_tool_round_then_final_answer(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory
):
    """AGT-UNIT-001：一轮工具调用后终答；messages 顺序 system→user→assistant(tool_calls)→tool，trace 长度 1。"""
    tool_call = tool_call_factory(
        "calculate_expression",
        '{"expr":"-(100/10)*(-3)"}',
        call_id="call-single",
    )
    first = response_factory(message_factory(tool_calls=[tool_call]), finish_reason="tool_calls")
    second = response_factory(message_factory(content="uo = +30V"))
    fake = scripted_client_factory([first, second])
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    tool_inputs = []

    def stub_execute_tool(name, arguments):
        tool_inputs.append((name, arguments))
        return "30"

    monkeypatch.setattr(agent, "execute_tool", stub_execute_tool)

    answer, trace = agent.run_agent("反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo")

    assert answer == "uo = +30V"
    assert trace == [
        {
            "tool": "calculate_expression",
            "arguments": {"expr": "-(100/10)*(-3)"},
            "result": "30",
            "warning": None,
        }
    ]
    assert tool_inputs == [("calculate_expression", {"expr": "-(100/10)*(-3)"})]
    assert [m["role"] for m in fake.calls[0]["messages"]] == ["system", "user"]
    second_messages = fake.calls[1]["messages"]
    assert [m["role"] for m in second_messages] == ["system", "user", "assistant", "tool"]
    assert second_messages[2]["tool_calls"][0]["id"] == "call-single"
    assert second_messages[3] == {"role": "tool", "tool_call_id": "call-single", "content": "30"}
    assert fake.call_count == 2
    fake.assert_exhausted()


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_arguments", "expected_tool_content"),
    [
        ("calculate_expression", "{oops", {}, "错误：缺少字符串参数 expr"),
        ("unknown_tool", '{"expr":"1+1"}', {"expr": "1+1"}, "错误：未知工具 unknown_tool"),
    ],
    ids=["bad-json-arguments", "unknown-tool"],
)
def test_agt_unit_003__bad_arguments_and_unknown_tool_become_tool_messages(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory,
    tool_name, arguments, expected_arguments, expected_tool_content,
):
    """AGT-UNIT-003：坏 JSON 参数变 {} 后得到缺参错误；未知工具返回错误串；均作为 tool 消息回传，不抛异常。"""
    tool_call = tool_call_factory(tool_name, arguments, call_id="call-bad")
    first = response_factory(message_factory(tool_calls=[tool_call]), finish_reason="tool_calls")
    second = response_factory(message_factory(content="已根据工具错误信息说明无法计算"))
    fake = scripted_client_factory([first, second])
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    answer, trace = agent.run_agent("求输出电压")

    assert answer == "已根据工具错误信息说明无法计算"
    assert trace == [
        {
            "tool": tool_name,
            "arguments": expected_arguments,
            "result": expected_tool_content,
            "warning": None,
        }
    ]
    assert fake.calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-bad",
        "content": expected_tool_content,
    }
    assert fake.call_count == 2
    fake.assert_exhausted()


@pytest.mark.xfail(
    strict=True,
    reason="DEF-07 轮数耗尽返回占位文案而非抛 RuntimeError",
)
def test_agt_unit_004__max_rounds_exhaustion_returns_placeholder_with_full_trace(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory
):
    """AGT-UNIT-004①：轮数耗尽应抛 RuntimeError，而不是把占位文案当正常回答。

    TEST_PLAN §13-3 已定案（2026-11-22）：统一抛异常，Pipeline 因此不写回历史。
    缺陷 DEF-07 修复后本用例转为 XPASS，需删除 xfail 并同步 PIP-INT-005。
    """
    responses = [
        response_factory(
            message_factory(
                tool_calls=[
                    tool_call_factory("calculate_expression", '{"expr":"1"}', call_id=f"call-{i}")
                ]
            ),
            finish_reason="tool_calls",
        )
        for i in range(5)
    ]
    fake = scripted_client_factory(responses)
    monkeypatch.setattr(agent, "get_client", lambda: fake)
    monkeypatch.setattr(agent, "execute_tool", lambda name, arguments: "1")

    with pytest.raises(RuntimeError, match="最大工具调用轮数"):
        agent.run_agent("连续计算五轮")

    assert fake.call_count == 5
    fake.assert_exhausted()


def test_agt_unit_004__length_capped_without_content_raises_runtime_error(
    monkeypatch, scripted_client_factory, response_factory, message_factory
):
    """AGT-UNIT-004②：finish_reason=length 且无内容 → RuntimeError（现状契约）。"""
    fake = scripted_client_factory(
        [response_factory(message_factory(content=None), finish_reason="length")]
    )
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="模型输出达到长度限制"):
        agent.run_agent("超长输出")

    assert fake.call_count == 1
    fake.assert_exhausted()


def test_agt_unit_004__empty_response_raises_runtime_error(
    monkeypatch, scripted_client_factory, response_factory, message_factory
):
    """AGT-UNIT-004③：无 tool_call 且 content=None 且 finish_reason=stop → RuntimeError（现状契约）。"""
    fake = scripted_client_factory(
        [response_factory(message_factory(content=None), finish_reason="stop")]
    )
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="模型没有返回有效内容"):
        agent.run_agent("空响应")

    assert fake.call_count == 1
    fake.assert_exhausted()


@pytest.mark.xfail(
    strict=True,
    reason="DEF-08 截断但已有内容时按正常回答返回而非抛 RuntimeError",
)
def test_agt_unit_004__length_capped_with_partial_content_returns_as_success(
    monkeypatch, scripted_client_factory, response_factory, message_factory
):
    """AGT-UNIT-004④：finish_reason=length 且已有内容时也应抛 RuntimeError。

    TEST_PLAN §13-3 已定案（2026-11-22）：与②一致按失败处理，不把半截答案当成功。
    缺陷 DEF-08 修复后本用例转为 XPASS，需删除 xfail。
    """
    fake = scripted_client_factory(
        [response_factory(message_factory(content="…半截"), finish_reason="length")]
    )
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="模型输出达到长度限制"):
        agent.run_agent("半截输出")

    assert fake.call_count == 1
    fake.assert_exhausted()


@pytest.mark.parametrize(
    ("use_validator", "expected_tool_content", "expected_warning"),
    [(True, "30\n[合理性校验] WARN", "WARN"), (False, "30", None)],
    ids=["with-validator", "without-validator"],
)
def test_agt_unit_005__validator_warning_is_appended_to_tool_message(
    monkeypatch, scripted_client_factory, response_factory, message_factory, tool_call_factory,
    use_validator, expected_tool_content, expected_warning,
):
    """AGT-UNIT-005：validator 警告拼进 tool 消息并记入 trace；无 validator 时内容仅为结果。"""
    tool_call = tool_call_factory(
        "calculate_expression",
        '{"expr":"-(100/10)*(-3)"}',
        call_id="call-validate",
    )
    first = response_factory(message_factory(tool_calls=[tool_call]), finish_reason="tool_calls")
    second = response_factory(message_factory(content="uo = +15V（运放饱和钳位）"))
    fake = scripted_client_factory([first, second])
    monkeypatch.setattr(agent, "get_client", lambda: fake)
    monkeypatch.setattr(agent, "execute_tool", lambda name, arguments: "30")

    validators = {"calculate_expression": lambda result: "WARN"} if use_validator else None

    answer, trace = agent.run_agent("求输出电压", validators=validators)

    assert answer == "uo = +15V（运放饱和钳位）"
    assert trace == [
        {
            "tool": "calculate_expression",
            "arguments": {"expr": "-(100/10)*(-3)"},
            "result": "30",
            "warning": expected_warning,
        }
    ]
    assert fake.calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-validate",
        "content": expected_tool_content,
    }
    assert fake.call_count == 2
    fake.assert_exhausted()
