"""本机聊天 HTTP 契约与 SQLite 集成测试。"""

import json
import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import numpy as np
import pytest
from anyio.from_thread import start_blocking_portal
from fastapi.testclient import TestClient

from aurora import api, rag, rewrite, router, vision
from aurora.vector_store import VectorStore


IMAGE = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nexample").decode("ascii")


@pytest.fixture(scope="module")
def http_request():
    # Windows 在初始化事件循环时建立内部唤醒通道，先于函数级网络防线完成。
    with start_blocking_portal() as portal:
        def request(app, method, path, body=None, *, raw=False, **headers):
            client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
            client.portal = portal
            try:
                response = client.request(method, path, json=body, headers=headers)
            finally:
                client.close()
            if raw:
                return response
            return response.status_code, response.json() if response.content else None

        yield request


def test_api_int_001__rounds_survive_restart(
    http_request,
    tmp_path, monkeypatch, tiny_vector_data, scripted_client_factory,
    response_factory, message_factory, tool_call_factory,
):
    """API-INT-001：真实 Pipeline 和数据库保留两轮文字历史。"""
    request = http_request
    db = tmp_path / "chat.sqlite3"
    vectors, chunks = tiny_vector_data
    store = VectorStore(chunks, vectors)
    classification = response_factory(message_factory(tool_calls=[tool_call_factory(
        "route_circuit", '{"circuit_type":"concept","confidence":0.9,"reason":"概念"}',
    )]))
    rewrite_response = response_factory(message_factory(content="续问的独立问题"))
    fake = scripted_client_factory([
        classification, response_factory(message_factory(content="第一轮答案")),
        rewrite_response, classification, response_factory(message_factory(content="第二轮答案")),
    ])
    for module in (router, rag, rewrite):
        monkeypatch.setattr(module, "get_client", lambda: fake)
    queries = []

    def encode(query):
        queries.append(query)
        return np.asarray([1.0, 0.0], dtype="float32")

    monkeypatch.setattr(rag, "encode_query", encode)
    app = api.create_app(db, store)
    status, created = request(app, "POST", "/api/v1/sessions")
    assert status == 201
    path = f"/api/v1/sessions/{created['session_id']}"
    assert request(app, "GET", f"{path}/messages") == (200, {"messages": []})
    status, result = request(app, "POST", f"{path}/messages", {"content": " 第一问 "})
    assert status == 200
    assert result == {
        "role": "assistant", "content": "第一轮答案", "circuit_type": "concept",
        "retrieval_sources": [
            {"source": hit.chunk.source, "heading": hit.chunk.heading or None}
            for hit in store.search(np.asarray([1.0, 0.0], dtype="float32"))
        ],
    }
    app = api.create_app(db, store)
    assert request(app, "GET", "/api/v1/sessions") == (200, {"sessions": [
        {**created, "title": "第一问"},
    ]})
    assert request(app, "POST", f"{path}/messages", {"content": "为什么？"})[0] == 200
    assert queries == [" 第一问 ", "续问的独立问题"]
    assert fake.last_call["messages"][1:3] == [
        {"role": "user", "content": " 第一问 "},
        {"role": "assistant", "content": "第一轮答案"},
    ]
    assert request(app, "GET", f"{path}/messages") == (200, {"messages": [
        {"role": "user", "content": " 第一问 "},
        {"role": "assistant", "content": "第一轮答案"},
        {"role": "user", "content": "为什么？"},
        {"role": "assistant", "content": "第二轮答案"},
    ]})
    assert request(app, "DELETE", path) == (204, None)
    assert request(app, "GET", f"{path}/messages")[0] == 404
    assert request(app, "POST", f"{path}/messages", {"content": "已删除"})[0] == 404
    fake.assert_exhausted()


def test_api_int_002__failed_requests_leave_history_unchanged(tmp_path, monkeypatch, http_request):
    """API-INT-002：请求校验、上游和数据库失败均不留下半轮。"""
    request = http_request
    db = tmp_path / "chat.sqlite3"
    unavailable = api.create_app(db, None)
    created = request(unavailable, "POST", "/api/v1/sessions")[1]
    path = f"/api/v1/sessions/{created['session_id']}/messages"
    assert request(unavailable, "GET", "/health")[0] == 200
    assert request(unavailable, "GET", "/ready")[0] == 503
    assert request(unavailable, "POST", path, {"content": "问题"})[0] == 503
    app = api.create_app(db, VectorStore())
    assert request(app, "GET", "/ready")[0] == 200
    for body in ({}, {"content": True}, {"content": "  "}):
        assert request(app, "POST", path, body)[0] == 422
    assert request(app, "POST", path, {"content": "问题"}, Origin="http://other.test")[0] == 403
    assert request(app, "GET", "/api/v1/sessions", Host="other.test")[0] == 403

    def fail(*args, **kwargs):
        raise RuntimeError("模型无有效回答")

    monkeypatch.setattr(router, "get_client", fail)
    assert request(app, "POST", path, {"content": "问题"})[0] == 502
    monkeypatch.setattr(api.pipeline, "route", lambda _: {"circuit_type": "concept"})
    monkeypatch.setattr(rag, "encode_query", lambda _: np.asarray([1.0, 0.0], dtype="float32"))
    request_body = {"content": "问题"}
    assert request(app, "POST", path, request_body)[0] == 200
    before = request(app, "GET", path)
    assert len(before[1]["messages"]) == 2
    assert "课件库还是空的" in before[1]["messages"][-1]["content"]
    monkeypatch.setattr(api.pipeline, "rewrite_question", lambda question, history: question)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TRIGGER reject_update BEFORE UPDATE ON sessions "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    assert request(app, "POST", path, request_body)[0] == 500
    assert request(app, "GET", path) == before


def test_api_int_003__owners_cannot_access_each_others_sessions(tmp_path, http_request):
    """API-INT-003：持久会话列表、读取、发送和删除均按所有者隔离。"""
    request = http_request
    db = tmp_path / "chat.sqlite3"
    a = api.create_app(db, None, owner_id="a")
    b = api.create_app(db, None, owner_id="b")
    created = request(a, "POST", "/api/v1/sessions")[1]
    path = f"/api/v1/sessions/{created['session_id']}"
    assert request(b, "GET", "/api/v1/sessions") == (200, {"sessions": []})
    assert request(b, "GET", f"{path}/messages")[0] == 404
    assert request(b, "POST", f"{path}/messages", {"content": "问题"})[0] == 404
    assert request(b, "DELETE", path)[0] == 404
    assert request(a, "GET", f"{path}/messages") == (200, {"messages": []})


def test_api_int_004__overlapping_requests_keep_both_turns(tmp_path, monkeypatch, http_request):
    """API-INT-004：第二个请求读到第一轮已提交的历史。"""
    request = http_request
    app = api.create_app(tmp_path / "chat.sqlite3", VectorStore())
    created = request(app, "POST", "/api/v1/sessions")[1]
    path = f"/api/v1/sessions/{created['session_id']}/messages"
    first_entered, second_sent = Event(), Event()
    seen = {}

    def answer(question, store, *, session):
        seen[question] = list(session.context())
        if question == "第一问":
            first_entered.set()
            assert second_sent.wait(5)
        session.add_turn(question, "回答")
        return {"answer": "回答", "circuit_type": "concept", "hits": []}

    def send_second():
        second_sent.set()
        return request(app, "POST", path, {"content": "第二问"})

    monkeypatch.setattr(api.pipeline, "run", answer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request, app, "POST", path, {"content": "第一问"})
        assert first_entered.wait(5)
        second = pool.submit(send_second)
        assert first.result(timeout=10)[0] == second.result(timeout=10)[0] == 200
    assert seen == {"第一问": [], "第二问": [
        {"role": "user", "content": "第一问"}, {"role": "assistant", "content": "回答"},
    ]}
    assert len(request(app, "GET", path)[1]["messages"]) == 4


def test_api_int_005__serves_chat_page_and_assets(tmp_path, http_request):
    """API-INT-005：根路由与聊天页面静态资源可访问。"""
    app = api.create_app(tmp_path / "chat.sqlite3", None)

    page = http_request(app, "GET", "/", raw=True)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert 'href="/assets/chat.css"' in page.text
    assert 'src="/assets/chat.js"' in page.text

    script = http_request(app, "GET", "/assets/chat.js", raw=True)
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "function renderMarkdown(text)" in script.text
    assert "body.append(renderMarkdown(turn.content))" in script.text

    stylesheet = http_request(app, "GET", "/assets/chat.css", raw=True)
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert ".message-body blockquote" in stylesheet.text
    assert ".message-body pre code" in stylesheet.text


def test_api_int_006__session_list_shows_question_summary(tmp_path, http_request):
    """API-INT-006：列表在打开会话前就给出首条提问摘要，空会话无摘要。"""
    db = tmp_path / "chat.sqlite3"
    app = api.create_app(db, None)
    empty = http_request(app, "POST", "/api/v1/sessions")[1]
    brief = http_request(app, "POST", "/api/v1/sessions")[1]
    verbose = http_request(app, "POST", "/api/v1/sessions")[1]
    question = "  反相比例电路的增益怎么算？  "
    long_question = "反相比例电路" * 15
    with sqlite3.connect(db) as connection:
        for session, content in ((brief, question), (verbose, long_question)):
            connection.execute(
                "UPDATE sessions SET history = ? WHERE id = ?",
                (json.dumps([
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": "答案"},
                ], ensure_ascii=False), session["session_id"]),
            )

    listed = http_request(app, "GET", "/api/v1/sessions")[1]["sessions"]
    assert listed == [
        {"session_id": verbose["session_id"], "title": long_question[:59] + "…"},
        {"session_id": brief["session_id"], "title": "反相比例电路的增益怎么算？"},
        {"session_id": empty["session_id"], "title": None},
    ]


def test_api_int_007__image_draft_requires_confirmation_without_history(
    tmp_path, monkeypatch, http_request, scripted_client_factory, response_factory, message_factory,
):
    """API-INT-007：图片校验、视觉请求和关键歧义均不写会话。"""
    fake = scripted_client_factory([response_factory(message_factory(content=json.dumps({
        "题干": "求输出电压", "元件参数": ["R1=10kΩ"],
        "连接关系": "运放输入端连接看不清", "电源极性": "±15V",
        "不确定项": [{"内容": "反相输入端连接", "原因": "线条遮挡", "影响解题": True}],
    }, ensure_ascii=False)))])
    monkeypatch.setenv("VISION_API_KEY", "dummy")
    monkeypatch.setenv("VISION_MODEL", "vision-test")
    monkeypatch.setattr(vision, "OpenAI", lambda **kwargs: fake)
    app = api.create_app(tmp_path / "chat.sqlite3", VectorStore())
    session_id = http_request(app, "POST", "/api/v1/sessions")[1]["session_id"]
    path = f"/api/v1/sessions/{session_id}"
    assert http_request(app, "POST", f"{path}/images", {"image": "data:image/png;base64,abcd"})[0] == 422
    assert fake.call_count == 0
    status, draft = http_request(app, "POST", f"{path}/images", {"image": IMAGE, "content": "怎么算？"})
    assert status == 200
    assert draft["requires_confirmation"] is True
    assert "R1=10kΩ" in draft["text"]
    assert fake.last_call["model"] == "vision-test"
    assert fake.last_call["messages"][1]["content"][1]["image_url"]["url"] == IMAGE
    assert http_request(app, "GET", f"{path}/messages") == (200, {"messages": []})
    fake.assert_exhausted()


def test_api_int_008__confirmed_draft_uses_text_pipeline(tmp_path, monkeypatch, http_request):
    """API-INT-008：确认后的描述沿原文字链路成对写回。"""
    app = api.create_app(tmp_path / "chat.sqlite3", VectorStore())
    session_id = http_request(app, "POST", "/api/v1/sessions")[1]["session_id"]
    path = f"/api/v1/sessions/{session_id}/messages"
    seen = []

    def answer(question, store, *, session):
        seen.append(question)
        session.add_turn(question, "计算结果")
        return {"answer": "计算结果", "circuit_type": "opamp", "hits": []}

    monkeypatch.setattr(api.pipeline, "run", answer)
    content = "学生问题：求输出\n根据上传图片提取的电路描述：\n连接关系：R1 接反相端"
    assert http_request(app, "POST", path, {"content": content})[0] == 200
    assert seen == [content]
    assert http_request(app, "GET", path)[1]["messages"] == [
        {"role": "user", "content": content}, {"role": "assistant", "content": "计算结果"},
    ]
