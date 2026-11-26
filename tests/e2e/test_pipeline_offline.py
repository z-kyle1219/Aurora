"""离线 Pipeline E2E：从问题输入走到统一返回与 Session 写回。"""

import json

import numpy as np
import pytest

from aurora import agent, pipeline, rag, router
from aurora.session import Session
from aurora.vector_store import VectorStore


QUESTIONS = {
    "opamp": "反相比例电路 R1=10k，Rf=100k，ui=-0.5V，求 uo",
    "feedback": "要稳定输出电压并增大输入电阻，应选哪种负反馈组态",
    "power": "直流稳压电源由哪几个部分组成",
    "concept": "什么是虚短和虚断",
    "fallback": "请分析 JK 触发器的时序逻辑",
}


@pytest.mark.parametrize(
    "question,intent,circuit_type,has_store,clarification",
    [
        pytest.param("啥是二极管？", "explain", "concept", False, None, id="explain"),
        pytest.param("怎么学模电？", "study", "fallback", False, None, id="study"),
        pytest.param("教材哪节讲二极管？", "locate", "concept", True, None, id="locate"),
        pytest.param("教材哪节讲二极管？", "locate", "concept", False, None, id="no-index"),
        pytest.param("这个运放增益多少？", "analyze", "opamp", False,
                     "能发电路图或贴题干吗？", id="clarify"),
    ],
)
def test_rtr_e2e_002__intent_drives_pipeline(
    monkeypatch, scripted_client_factory, response_factory, message_factory,
    tool_call_factory, tiny_vector_data, question, intent, circuit_type,
    has_store, clarification,
):
    """RTR-E2E-002：主意图控制检索、直接解答和澄清。"""
    arguments = json.dumps({
        "circuit_type": circuit_type, "intent": intent, "confidence": 0.9,
        "clarification": clarification,
    })
    tool = tool_call_factory("route_circuit", arguments)
    responses = [response_factory(message_factory(tool_calls=[tool]), finish_reason="tool_calls")]
    generates = intent in {"explain", "study"} or has_store
    if generates:
        responses.append(response_factory(message_factory(content="最终回答")))
    fake = scripted_client_factory(responses)
    for module in (router, pipeline, rag, agent):
        monkeypatch.setattr(module, "get_client", lambda: fake)
    queries = []

    def encode_query(query):
        queries.append(query)
        return np.asarray([1.0, 0.0], dtype="float32")

    monkeypatch.setattr(rag, "encode_query", encode_query)
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors) if has_store else None
    session = Session()
    result = pipeline.run(question, store, session=session)

    fake.assert_exhausted()
    assert fake.calls[0]["tools"] == [router.ROUTE_TOOL]
    assert queries == ([question] if has_store else [])
    assert result["intent"] == intent
    assert result["trace"] is None
    if generates:
        expected_prompt = pipeline.LOCATE_PROMPT if has_store else pipeline.DIRECT_PROMPTS[intent]
        assert expected_prompt in fake.calls[1]["messages"][0]["content"]
        assert result["answer"] == "最终回答"
    elif clarification:
        assert result["answer"] == clarification
    else:
        assert "没有可用的资料索引" in result["answer"]
    assert session.context() == [
        {"role": "user", "content": question},
        {"role": "assistant", "content": result["answer"]},
    ]


@pytest.mark.parametrize("intent", [None, "", "typo"])
def test_rtr_e2e_003__invalid_intent_stops_before_dispatch(
    monkeypatch, scripted_client_factory, response_factory, message_factory,
    tool_call_factory, intent,
):
    """RTR-E2E-003：非法意图终止分流，历史不变。"""
    call = tool_call_factory("route_circuit", json.dumps({
        "circuit_type": "opamp", "confidence": 0.9, "intent": intent,
    }))
    fake = scripted_client_factory([
        response_factory(message_factory(tool_calls=[call]), finish_reason="tool_calls"),
    ])
    monkeypatch.setattr(router, "get_client", lambda: fake)
    session = Session()

    with pytest.raises(RuntimeError, match="无效意图"):
        pipeline.run("求输出电压", session=session)

    assert fake.call_count == 1
    fake.assert_exhausted()
    assert session.context() == []


def _router_response(response_factory, message_factory, tool_call_factory, circuit_type):
    arguments = (
        '{"circuit_type":"%s","confidence":0.9,"reason":"离线分类"}'
        % circuit_type
    )
    tool_call = tool_call_factory("route_circuit", arguments, call_id=f"route-{circuit_type}")
    return response_factory(message_factory(tool_calls=[tool_call]), finish_reason="tool_calls")


def _script_for_branch(
    circuit_type, response_factory, message_factory, tool_call_factory
):
    responses = [
        _router_response(
            response_factory, message_factory, tool_call_factory, circuit_type
        )
    ]
    if circuit_type == "opamp":
        tool_call = tool_call_factory(
            "calculate_expression",
            '{"expr":"-(100/10)*(-0.5)"}',
            call_id="calculate-output",
        )
        responses.extend(
            [
                response_factory(
                    message_factory(tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                ),
                response_factory(message_factory(content="uo = +5V")),
            ]
        )
    elif circuit_type != "fallback":
        responses.append(
            response_factory(message_factory(content=f"{circuit_type}-rag-answer"))
        )
    return responses


def _run_branch(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
    tiny_vector_data,
    circuit_type,
):
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)
    fake = scripted_client_factory(
        _script_for_branch(
            circuit_type, response_factory, message_factory, tool_call_factory
        )
    )
    monkeypatch.setattr(router, "get_client", lambda: fake)
    monkeypatch.setattr(agent, "get_client", lambda: fake)
    monkeypatch.setattr(rag, "get_client", lambda: fake)
    monkeypatch.setattr(
        rag, "encode_query", lambda question: np.asarray([1.0, 0.0], dtype="float32")
    )
    session = Session()

    result = pipeline.run(QUESTIONS[circuit_type], store, top_k=2, session=session)

    fake.assert_exhausted()
    return result, session


@pytest.mark.parametrize(
    "circuit_type",
    ["opamp", "feedback", "power", "concept", "fallback"],
)
def test_pip_e2e_001__five_branches_complete_offline(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
    tiny_vector_data,
    circuit_type,
):
    """PIP-E2E-001：五类问题均离线跑完整 Pipeline，并写回统一结果。"""
    result, session = _run_branch(
        monkeypatch,
        scripted_client_factory,
        response_factory,
        message_factory,
        tool_call_factory,
        tiny_vector_data,
        circuit_type,
    )

    assert set(result) == {
        "circuit_type",
        "confidence",
        "reason",
        "standalone",
        "answer",
        "trace",
        "hits",
    }
    assert result["circuit_type"] == circuit_type
    assert result["standalone"] == QUESTIONS[circuit_type]
    assert session.context() == [
        {"role": "user", "content": QUESTIONS[circuit_type]},
        {"role": "assistant", "content": result["answer"]},
    ]
    if circuit_type == "opamp":
        assert result["trace"][0]["result"] == "5"
        assert result["hits"] is None
    elif circuit_type == "fallback":
        assert result["trace"] is None
        assert result["hits"] is None
        assert QUESTIONS[circuit_type] in result["answer"]
    else:
        assert result["trace"] is None
        assert len(result["hits"]) == 2


@pytest.mark.parametrize(
    "circuit_type",
    ["opamp", "feedback", "power", "concept", "fallback"],
)
def test_rtr_e2e_001__router_label_matches_branch_artifacts(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
    tiny_vector_data,
    circuit_type,
):
    """RTR-E2E-001：Router 标签与完整链路的 trace/hits 产物一致。"""
    result, _ = _run_branch(
        monkeypatch,
        scripted_client_factory,
        response_factory,
        message_factory,
        tool_call_factory,
        tiny_vector_data,
        circuit_type,
    )

    assert result["circuit_type"] == circuit_type
    assert (result["trace"] is not None) == (circuit_type == "opamp")
    assert (result["hits"] is not None) == (
        circuit_type in {"feedback", "power", "concept"}
    )


def test_pip_e2e_002__empty_index_answer_is_written_to_session(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
):
    """PIP-E2E-002：空索引降级文案作为可见回答写入 Session。"""
    fake = scripted_client_factory(
        [
            _router_response(
                response_factory, message_factory, tool_call_factory, "concept"
            )
        ]
    )
    monkeypatch.setattr(router, "get_client", lambda: fake)
    monkeypatch.setattr(
        rag, "encode_query", lambda question: np.asarray([1.0, 0.0], dtype="float32")
    )
    session = Session()

    result = pipeline.run("什么是虚短", VectorStore(), session=session)

    expected = "课件库还是空的，请先运行 build_index.py 建库。"
    assert result["answer"] == expected
    assert result["hits"] == []
    assert result["trace"] is None
    assert session.context() == [
        {"role": "user", "content": "什么是虚短"},
        {"role": "assistant", "content": expected},
    ]
    fake.assert_exhausted()
