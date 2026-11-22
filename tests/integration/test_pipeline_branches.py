"""Pipeline 编排集成测试：五分支统一返回、异常传播、零外部调用与临时 Session。"""

import copy

import pytest

from aurora import agent, pipeline, rewrite
from aurora.chunker import Chunk
from aurora.session import Session
from aurora.vector_store import SearchResult

QUESTION = "反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo"


def _route_info(circuit_type):
    return {
        "circuit_type": circuit_type,
        "confidence": 0.9,
        "reason": f"{circuit_type}-reason",
    }


def _fake_hits():
    chunk = Chunk(text="运放遵循虚短与虚断。", source="synthetic-opamp.md", heading="集成运放")
    return [SearchResult(chunk=chunk, score=0.9)]


@pytest.mark.parametrize(
    ("circuit_type", "expected_prompt"),
    [
        ("opamp", None),
        ("feedback", pipeline.FEEDBACK_PROMPT),
        ("power", pipeline.POWER_PROMPT),
        ("concept", None),
        ("fallback", None),
    ],
    ids=["opamp", "feedback", "power", "concept", "fallback"],
)
def test_pip_int_001__five_branches_share_schema_and_writeback(
    monkeypatch, circuit_type, expected_prompt
):
    """PIP-INT-001：五分支返回七字段；trace/hits 不同时非 None；Session 写回原话与最终答案。"""
    session = Session()
    store = object()
    route_calls = []
    agent_calls = []
    rag_calls = []
    agent_trace = [
        {
            "tool": "calculate_expression",
            "arguments": {"expr": "-(100/10)*(-3)"},
            "result": "30",
            "warning": "钳位在 +15V",
        }
    ]
    hits = _fake_hits()

    def fake_route(question):
        route_calls.append(question)
        return _route_info(circuit_type)

    def fake_agent(question, validators=None, history=None):
        agent_calls.append(
            {"question": question, "validators": validators, "history": copy.deepcopy(history)}
        )
        return "opamp-answer", agent_trace

    def fake_rag(question, store, top_k=3, history=None, system_prompt=None, query=None):
        rag_calls.append(
            {
                "question": question,
                "store": store,
                "top_k": top_k,
                "history": copy.deepcopy(history),
                "system_prompt": system_prompt,
                "query": query,
            }
        )
        return "rag-answer", hits

    monkeypatch.setattr(pipeline, "route", fake_route)
    monkeypatch.setattr(pipeline, "run_agent", fake_agent)
    monkeypatch.setattr(pipeline, "ask_rag", fake_rag)

    result = pipeline.run(QUESTION, store, top_k=5, session=session)

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
    assert isinstance(result["confidence"], float)
    assert isinstance(result["reason"], str) and result["reason"]
    assert result["standalone"] == QUESTION
    assert isinstance(result["answer"], str) and result["answer"]
    assert not (result["trace"] is not None and result["hits"] is not None)
    assert route_calls == [QUESTION]

    if circuit_type == "opamp":
        assert len(agent_calls) == 1
        assert agent_calls[0]["question"] == QUESTION
        assert agent_calls[0]["validators"] is pipeline.OPAMP_VALIDATORS
        assert agent_calls[0]["history"] == []
        assert rag_calls == []
        assert result["trace"] == agent_trace
        assert result["hits"] is None
        assert result["answer"] == "opamp-answer"
    elif circuit_type == "fallback":
        assert agent_calls == []
        assert rag_calls == []
        assert result["trace"] is None
        assert result["hits"] is None
        assert QUESTION in result["answer"]
    else:
        assert agent_calls == []
        assert len(rag_calls) == 1
        assert rag_calls[0]["question"] == QUESTION
        assert rag_calls[0]["store"] is store
        assert rag_calls[0]["top_k"] == 5
        assert rag_calls[0]["history"] == []
        assert rag_calls[0]["query"] == QUESTION
        assert rag_calls[0]["system_prompt"] == expected_prompt
        assert result["hits"] is hits
        assert result["trace"] is None
        assert result["answer"] == "rag-answer"

    assert session.context() == [
        {"role": "user", "content": QUESTION},
        {"role": "assistant", "content": result["answer"]},
    ]
    assert len(session) == 2


@pytest.mark.parametrize(
    ("circuit_type", "failing", "message"),
    [
        ("concept", "route", "Router 服务不可用"),
        ("opamp", "agent", "Agent 服务不可用"),
        ("concept", "rag", "RAG 服务不可用"),
    ],
    ids=["router-error", "agent-error", "rag-error"],
)
def test_pip_int_002__downstream_error_propagates_without_writeback(
    monkeypatch, circuit_type, failing, message
):
    """PIP-INT-002①②③：route/run_agent/ask_rag 异常向上传播，Session 长度保持 0。"""
    session = Session()

    def fail(*args, **kwargs):
        raise RuntimeError(message)

    def unexpected(*args, **kwargs):
        raise AssertionError("该分支不应被调用")

    if failing == "route":
        monkeypatch.setattr(pipeline, "route", fail)
        monkeypatch.setattr(pipeline, "run_agent", unexpected)
        monkeypatch.setattr(pipeline, "ask_rag", unexpected)
    else:
        monkeypatch.setattr(pipeline, "route", lambda question: _route_info(circuit_type))
        if failing == "agent":
            monkeypatch.setattr(pipeline, "run_agent", fail)
            monkeypatch.setattr(pipeline, "ask_rag", unexpected)
        else:
            monkeypatch.setattr(pipeline, "run_agent", unexpected)
            monkeypatch.setattr(pipeline, "ask_rag", fail)

    with pytest.raises(RuntimeError, match=message):
        pipeline.run(QUESTION, object(), session=session)

    assert len(session) == 0
    assert session.context() == []


def test_pip_int_002__rewrite_error_falls_back_to_original_question(monkeypatch):
    """PIP-INT-002④：改写异常在 rewrite 内部回退原问题，Pipeline 继续执行并正常写回。"""
    session = Session()
    session.add_turn("上一轮问题", "上一轮回答")
    route_calls = []

    def failing_get_client():
        raise RuntimeError("改写服务不可用")

    monkeypatch.setattr(rewrite, "get_client", failing_get_client)
    monkeypatch.setattr(
        pipeline,
        "route",
        lambda question: route_calls.append(question) or _route_info("concept"),
    )
    monkeypatch.setattr(
        pipeline, "ask_rag", lambda *args, **kwargs: ("概念回答", [])
    )

    result = pipeline.run("那 R1 换成 20k 呢", object(), session=session)

    assert route_calls == ["那 R1 换成 20k 呢"]
    assert result["standalone"] == "那 R1 换成 20k 呢"
    assert result["answer"] == "概念回答"
    assert len(session) == 4
    assert session.context()[2:] == [
        {"role": "user", "content": "那 R1 换成 20k 呢"},
        {"role": "assistant", "content": "概念回答"},
    ]


def test_pip_int_003__fallback_makes_no_downstream_calls(monkeypatch):
    """PIP-INT-003：fallback 分支不调用 run_agent/ask_rag，trace 与 hits 均为 None。"""
    session = Session()

    def forbidden(*args, **kwargs):
        raise AssertionError("fallback 不应调用下游能力")

    monkeypatch.setattr(pipeline, "route", lambda question: _route_info("fallback"))
    monkeypatch.setattr(pipeline, "run_agent", forbidden)
    monkeypatch.setattr(pipeline, "ask_rag", forbidden)

    result = pipeline.run("用 Python 写快排", object(), session=session)

    assert result["trace"] is None
    assert result["hits"] is None
    assert "用 Python 写快排" in result["answer"]
    assert len(session) == 2
    assert session.context()[0] == {"role": "user", "content": "用 Python 写快排"}


def test_pip_int_004__temporary_sessions_do_not_leak_between_calls(monkeypatch):
    """PIP-INT-004：不传 session 的连续两次调用各自从空历史开始，互不共享状态。"""
    captured_histories = []

    monkeypatch.setattr(pipeline, "route", lambda question: _route_info("concept"))

    def fake_rag(question, store, top_k=3, history=None, system_prompt=None, query=None):
        captured_histories.append(copy.deepcopy(history))
        return f"回答-{question}", []

    monkeypatch.setattr(pipeline, "ask_rag", fake_rag)

    first = pipeline.run("第一问", object())
    second = pipeline.run("第二问", object())

    assert captured_histories == [[], []]
    assert captured_histories[0] is not captured_histories[1]
    assert first["answer"] == "回答-第一问"
    assert second["answer"] == "回答-第二问"


@pytest.mark.xfail(
    strict=True,
    reason="DEF-07 轮数耗尽占位文案被当正常答案写回 Session",
)
def test_pip_int_005__max_rounds_exhaustion_does_not_write_history(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tool_call_factory,
):
    """PIP-INT-005：§13-3 已定案——轮数耗尽抛 RuntimeError，Session 长度不变。

    缺陷 DEF-07：当前 run_agent 返回占位文案，pipeline 将其写入历史，与正常答案不可区分。
    """
    session = Session()
    monkeypatch.setattr(pipeline, "route", lambda question: _route_info("opamp"))
    responses = [
        response_factory(
            message_factory(
                tool_calls=[
                    tool_call_factory(
                        "calculate_expression", '{"expr":"1"}', call_id=f"call-{index}"
                    )
                ]
            ),
            finish_reason="tool_calls",
        )
        for index in range(5)
    ]
    fake = scripted_client_factory(responses)
    monkeypatch.setattr(agent, "get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="最大工具调用轮数"):
        pipeline.run(QUESTION, object(), session=session)

    assert len(session) == 0
    assert fake.call_count == 5
    fake.assert_exhausted()
