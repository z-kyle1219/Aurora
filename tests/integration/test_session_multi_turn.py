"""Session 与多轮集成测试：上下文传递、原话写回与失败轮次不产生半条历史。"""

import copy

import pytest

from aurora import pipeline
from aurora.session import Session

FIRST_QUESTION = "反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo"
FOLLOW_UP = "那 R1 换成 20k 呢"
STANDALONE = "反相比例电路 R1 换成 20k 时，求输出电压 uo"


def _route_as_concept(question):
    return {"circuit_type": "concept", "confidence": 0.9, "reason": "基础概念"}


def test_ses_int_001__history_is_reused_and_original_follow_up_is_written(monkeypatch):
    """SES-INT-001：同一 Session 两轮；第二轮改写收 2 条历史，生成用原追问，Session 共 4 条。"""
    session = Session()
    store = object()
    rewrite_calls = []
    rag_calls = []

    def fake_rewrite(question, history=None):
        rewrite_calls.append(
            {"question": question, "history": copy.deepcopy(history)}
        )
        return STANDALONE if history else question

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
        return f"answer-{len(rag_calls)}", []

    monkeypatch.setattr(pipeline, "rewrite_question", fake_rewrite)
    monkeypatch.setattr(pipeline, "route", _route_as_concept)
    monkeypatch.setattr(pipeline, "ask_rag", fake_rag)

    first = pipeline.run(FIRST_QUESTION, store, session=session)
    second = pipeline.run(FOLLOW_UP, store, session=session)

    assert rewrite_calls[0] == {"question": FIRST_QUESTION, "history": []}
    expected_history = [
        {"role": "user", "content": FIRST_QUESTION},
        {"role": "assistant", "content": "answer-1"},
    ]
    assert rewrite_calls[1] == {"question": FOLLOW_UP, "history": expected_history}

    # 生成侧：原追问 + 纯文本历史，standalone 只用于检索 query。
    assert rag_calls[0]["history"] == []
    assert rag_calls[1]["question"] == FOLLOW_UP
    assert rag_calls[1]["history"] == expected_history
    assert rag_calls[1]["query"] == STANDALONE
    assert [message["role"] for message in rag_calls[1]["history"]] == [
        "user",
        "assistant",
    ]
    assert all("tool_calls" not in message for message in rag_calls[1]["history"])

    assert first["standalone"] == FIRST_QUESTION
    assert second["standalone"] == STANDALONE
    assert first["answer"] == "answer-1"
    assert second["answer"] == "answer-2"

    assert len(session) == 4
    assert session.context() == [
        {"role": "user", "content": FIRST_QUESTION},
        {"role": "assistant", "content": "answer-1"},
        {"role": "user", "content": FOLLOW_UP},
        {"role": "assistant", "content": "answer-2"},
    ]
    # 第 3 条必须是原追问，而不是改写后的 standalone。
    assert session.context()[2]["content"] != STANDALONE


def test_ses_int_003__failed_turn_leaves_no_partial_history(monkeypatch):
    """SES-INT-003：第 1 轮成功后第 2 轮 ask_rag 抛错，Session 仍为 2 条且内容不变。"""
    session = Session()
    monkeypatch.setattr(pipeline, "route", _route_as_concept)
    monkeypatch.setattr(
        pipeline, "rewrite_question", lambda question, history=None: question
    )
    monkeypatch.setattr(
        pipeline, "ask_rag", lambda *args, **kwargs: ("第一轮回答", [])
    )

    pipeline.run(FIRST_QUESTION, object(), session=session)
    assert len(session) == 2
    snapshot = copy.deepcopy(session.context())

    def failing_rag(*args, **kwargs):
        raise RuntimeError("RAG 服务不可用")

    monkeypatch.setattr(pipeline, "ask_rag", failing_rag)

    with pytest.raises(RuntimeError, match="RAG 服务不可用"):
        pipeline.run(FOLLOW_UP, object(), session=session)

    assert len(session) == 2
    assert session.context() == snapshot
    assert [message["role"] for message in session.context()] == ["user", "assistant"]
