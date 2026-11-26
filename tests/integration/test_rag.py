"""RAG 在线链集成测试：检索查询、提示词与生成输入的边界。"""

import copy

import numpy as np
import pytest

from aurora import pipeline, rag
from aurora.session import Session
from aurora.vector_store import VectorStore

QUERY_VECTOR = np.asarray([1.0, 0.0], dtype="float32")


class RecordingStore(VectorStore):
    """记录 top_k 的向量库；检索本身仍走真实 VectorStore 实现。"""

    def __init__(self, chunks, vectors):
        super().__init__(chunks=chunks, vectors=vectors)
        self.requested_top_k = []

    def search(self, query_vector, top_k=3):
        self.requested_top_k.append(top_k)
        return super().search(query_vector, top_k=top_k)


def _route_as_concept(question):
    return {"circuit_type": "concept", "confidence": 0.9, "reason": "基础概念"}


def test_rag_int_001__query_only_affects_retrieval_not_generation(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tiny_vector_data,
):
    """RAG-INT-001：encode_query 只收 query；生成消息含原问题与全部片段，history 不被改写。"""
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)
    question = "那 R1 换成 20k 呢"
    query = "反相比例电路 R1=20k 求 uo"
    history = [
        {"role": "user", "content": "反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo"},
        {"role": "assistant", "content": "uo = +30V"},
    ]
    history_snapshot = copy.deepcopy(history)
    encoded = []

    def fake_encode_query(text):
        encoded.append(text)
        return QUERY_VECTOR

    monkeypatch.setattr(rag, "encode_query", fake_encode_query)
    fake = scripted_client_factory(
        [response_factory(message_factory(content="uo = +60V"))]
    )
    monkeypatch.setattr(rag, "get_client", lambda: fake)

    answer, hits = rag.ask_rag(
        question, store, top_k=2, history=history, query=query
    )

    assert answer == "uo = +60V"
    assert encoded == [query]
    assert [hit.chunk.source for hit in hits] == [
        "synthetic-opamp.md",
        "synthetic-power.md",
    ]
    assert [hit.score for hit in hits] == pytest.approx([1.0, 0.6])

    messages = fake.last_call["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[0]["content"] == rag.RAG_SYSTEM_PROMPT
    assert messages[1] == history[0]
    assert messages[2] == history[1]

    user_message = messages[-1]["content"]
    assert question in user_message
    for index, hit in enumerate(hits, 1):
        assert f"【课件片段 {index}】（来源：{hit.chunk.source}）" in user_message
        assert hit.chunk.text in user_message

    assert history == history_snapshot
    assert fake.last_call["model"] == rag.DEEPSEEK_MODEL
    assert fake.last_call["temperature"] == 0.3
    assert fake.call_count == 1
    fake.assert_exhausted()


@pytest.mark.parametrize(
    ("system_prompt", "expected_prompt"),
    [
        ("自定义提示词 X", "自定义提示词 X"),
        (None, rag.RAG_SYSTEM_PROMPT),
    ],
    ids=["custom-prompt", "default-prompt"],
)
def test_rag_int_002__custom_system_prompt_overrides_default(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tiny_vector_data,
    system_prompt,
    expected_prompt,
):
    """RAG-INT-002：system_prompt 覆盖默认提示词；两种情况下 temperature 均为 0.3。"""
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)
    monkeypatch.setattr(rag, "encode_query", lambda text: QUERY_VECTOR)
    fake = scripted_client_factory(
        [response_factory(message_factory(content="基于片段的回答"))]
    )
    monkeypatch.setattr(rag, "get_client", lambda: fake)

    answer, hits = rag.ask_rag("虚短是什么", store, system_prompt=system_prompt)

    assert answer == "基于片段的回答"
    assert len(hits) == 3
    assert fake.last_call["messages"][0] == {
        "role": "system",
        "content": expected_prompt,
    }
    assert fake.last_call["temperature"] == 0.3
    assert fake.call_count == 1
    fake.assert_exhausted()


@pytest.mark.parametrize(
    ("top_k", "expected_hits", "model_called"),
    [(1, 1, True), (5, 3, True), (0, 0, False)],
    ids=["k-1", "k-over-size", "k-0"],
)
def test_rag_int_003__top_k_is_passed_to_store_search(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tiny_vector_data,
    top_k,
    expected_hits,
    model_called,
):
    """RAG-INT-003：top_k 原样传给 store.search；k=0 时走空索引分支且不调用模型。"""
    vectors, chunks = tiny_vector_data
    store = RecordingStore(chunks=chunks, vectors=vectors)
    monkeypatch.setattr(rag, "encode_query", lambda text: QUERY_VECTOR)

    if model_called:
        fake = scripted_client_factory(
            [response_factory(message_factory(content="回答"))]
        )
        monkeypatch.setattr(rag, "get_client", lambda: fake)
    else:
        def forbidden_client():
            raise AssertionError("k=0 空 hits 时不应调用生成模型")

        monkeypatch.setattr(rag, "get_client", forbidden_client)

    answer, hits = rag.ask_rag("虚短是什么", store, top_k=top_k)

    assert store.requested_top_k == [top_k]
    assert len(hits) == expected_hits
    if model_called:
        assert answer == "回答"
        assert fake.call_count == 1
        fake.assert_exhausted()
    else:
        assert answer == "课件库还是空的，请先运行 build_index.py 建库。"
        assert hits == []


@pytest.mark.xfail(
    strict=True,
    reason="DEF-05 store=None 时抛 AttributeError 而非显式 ValueError",
)
def test_rag_int_004__missing_store_raises_explicit_error(monkeypatch):
    """RAG-INT-004：§13-1 已定案——显式 ValueError，不调用 None.search，Session 不写回。

    缺陷 DEF-05：当前 retrieve(None, ...) 触发 `None.search` 的 AttributeError。
    """
    session = Session()
    monkeypatch.setattr(pipeline, "route", _route_as_concept)
    monkeypatch.setattr(rag, "encode_query", lambda text: QUERY_VECTOR)

    with pytest.raises(ValueError, match="VectorStore"):
        pipeline.run("虚短是什么", None, session=session)

    assert len(session) == 0


@pytest.mark.parametrize("content", [None, "", " \n"])
def test_rag_int_005__none_content_does_not_pollute_history(
    monkeypatch,
    scripted_client_factory,
    response_factory,
    message_factory,
    tiny_vector_data,
    content,
):
    """RAG-INT-005：空回答抛 RuntimeError，保护会话历史（DEF-06）。"""
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks=chunks, vectors=vectors)
    session = Session()
    monkeypatch.setattr(pipeline, "route", _route_as_concept)
    monkeypatch.setattr(rag, "encode_query", lambda text: QUERY_VECTOR)
    fake = scripted_client_factory(
        [response_factory(message_factory(content=content))]
    )
    monkeypatch.setattr(rag, "get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="模型没有返回有效内容"):
        pipeline.run("虚短是什么", store, session=session)

    assert len(session) == 0
    assert fake.call_count == 1
    fake.assert_exhausted()
