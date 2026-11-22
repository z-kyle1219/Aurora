"""离线多轮 E2E：真实 Session 串联改写、Router、Agent 与 RAG。"""

import numpy as np

from aurora import agent, pipeline, rag, rewrite, router
from aurora.session import Session
from aurora.vector_store import VectorStore

FIRST = "反相比例运算电路：R1=10kΩ，Rf=100kΩ，ui=-3V，求 uo。"
FOLLOW_UP = "那如果 R1 换成 20kΩ 呢？"
SWITCH_TOPIC = "不聊这个了，说说负反馈四种组态怎么判断？"
SECOND_STANDALONE = "反相比例运算电路 R1=20kΩ，Rf=100kΩ，ui=-3V，求 uo。"
THIRD_STANDALONE = "负反馈四种组态怎么判断？"


def _route(response_factory, message_factory, tool_call_factory, circuit_type, index):
    arguments = (
        '{"circuit_type":"%s","confidence":0.9,"reason":"第%d轮分类"}'
        % (circuit_type, index)
    )
    call = tool_call_factory("route_circuit", arguments, call_id=f"route-{index}")
    return response_factory(message_factory(tool_calls=[call]), finish_reason="tool_calls")


def _calculation(response_factory, message_factory, tool_call_factory, expression, index):
    call = tool_call_factory(
        "calculate_expression",
        '{"expr":"%s"}' % expression,
        call_id=f"calculate-{index}",
    )
    return response_factory(message_factory(tool_calls=[call]), finish_reason="tool_calls")


def test_ses_e2e_001__three_turn_conversation_closes_offline(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
    tiny_vector_data,
):
    """SES-E2E-001：三轮真实编排按 0→2→4→6 累积并在第三轮切换类型。"""
    responses = [
        _route(response_factory, message_factory, tool_call_factory, "opamp", 1),
        _calculation(
            response_factory, message_factory, tool_call_factory, "-(100/10)*(-3)", 1
        ),
        response_factory(message_factory(content="第一轮：uo = +15V（饱和钳位）")),
        response_factory(message_factory(content=SECOND_STANDALONE)),
        _route(response_factory, message_factory, tool_call_factory, "opamp", 2),
        _calculation(
            response_factory, message_factory, tool_call_factory, "-(100/20)*(-3)", 2
        ),
        response_factory(message_factory(content="第二轮：uo = +15V")),
        response_factory(message_factory(content=THIRD_STANDALONE)),
        _route(response_factory, message_factory, tool_call_factory, "feedback", 3),
        response_factory(message_factory(content="第三轮：四种负反馈组态说明")),
    ]
    fake = scripted_client_factory(responses)
    monkeypatch.setattr(rewrite, "get_client", lambda: fake)
    monkeypatch.setattr(router, "get_client", lambda: fake)
    monkeypatch.setattr(agent, "get_client", lambda: fake)
    monkeypatch.setattr(rag, "get_client", lambda: fake)
    monkeypatch.setattr(
        rag, "encode_query", lambda question: np.asarray([0.0, 1.0], dtype="float32")
    )
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)
    session = Session()

    lengths = [len(session)]
    first = pipeline.run(FIRST, store, session=session)
    lengths.append(len(session))
    second = pipeline.run(FOLLOW_UP, store, session=session)
    lengths.append(len(session))
    third = pipeline.run(SWITCH_TOPIC, store, session=session)
    lengths.append(len(session))

    assert lengths == [0, 2, 4, 6]
    assert [first["circuit_type"], second["circuit_type"], third["circuit_type"]] == [
        "opamp",
        "opamp",
        "feedback",
    ]
    assert second["standalone"] == SECOND_STANDALONE
    assert "20kΩ" in second["standalone"]
    assert second["trace"][0]["arguments"] == {"expr": "-(100/20)*(-3)"}
    assert second["trace"][0]["result"] == "15"
    assert third["standalone"] == THIRD_STANDALONE
    assert third["trace"] is None
    assert third["hits"] is not None
    assert session.context()[2]["content"] == FOLLOW_UP
    assert session.context()[4]["content"] == SWITCH_TOPIC
    assert all(message["role"] in {"user", "assistant"} for message in session.context())
    assert all("tool_calls" not in message for message in session.context())
    fake.assert_exhausted()
