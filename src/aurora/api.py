"""仅供本机使用的同步聊天 HTTP API。"""

import argparse
import json
import logging
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from aurora import config, pipeline
from aurora.session import Session
from aurora.vector_store import VectorStore

_MAX_BODY = 65536
_LOG = logging.getLogger(__name__)


class MessageInput(BaseModel):
    content: StrictStr

    @field_validator("content")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入有效的消息文本")
        return value


class RetrievalSource(BaseModel):
    source: str
    heading: str | None


class AssistantReply(BaseModel):
    role: str = "assistant"
    content: str
    circuit_type: str
    retrieval_sources: list[RetrievalSource]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def create_app(database: Path, store: VectorStore | None, *, owner_id: str = "dev-user") -> FastAPI:
    """创建本机应用，兼容已有 SQLite 文件。"""
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS sessions "
            "(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, history TEXT NOT NULL)"
        )

    app = FastAPI(title="Aurora Chat API", redirect_slashes=False)
    web = Path(__file__).with_name("web")
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/", include_in_schema=False)
    def chat_page() -> FileResponse:
        return FileResponse(web / "index.html")

    # 同步端点在线程池运行。保持原型的串行访问，覆盖读取、生成及提交。
    lock = Lock()
    sessions = "/api/v1/sessions"
    messages = sessions + "/{session_id}/messages"

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if not re.fullmatch(r"(?:localhost|127[.]0[.]0[.]1)(?::[0-9]+)?", host) or (
            origin is not None and origin != f"http://{host}"
        ):
            return _error(403, "local_access_only", "仅允许本机同源访问")
        if request.method == "POST" and re.fullmatch(
            r"/api/v1/sessions/[^/]+/messages", request.url.path,
        ):
            mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            length = request.headers.get("content-length", "")
            if mime != "application/json" or not re.fullmatch(r"[0-9]{1,5}", length) or not (
                0 < int(length) <= _MAX_BODY
            ):
                return _error(422, "invalid_request", "请输入有效的消息文本")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "请输入有效的消息文本")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            return _error(exc.status_code, exc.detail["code"], exc.detail["message"])
        response = _error(exc.status_code, "route_not_found", "接口不存在或不支持此方法")
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        _LOG.error("聊天 API 请求失败", exc_info=exc)
        return _error(500, "internal_error", "服务处理失败")

    def require_ready() -> None:
        if store is None or not config.DEEPSEEK_API_KEY:
            raise _fail(503, "service_not_ready", "服务尚未就绪")

    def read_history(db: sqlite3.Connection, session_id: str) -> list[dict]:
        row = db.execute(
            "SELECT history FROM sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id),
        ).fetchone()
        if row is None:
            raise _fail(404, "session_not_found", "会话不存在或已失效")
        return json.loads(row[0])

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict:
        require_ready()
        return {"status": "ready"}

    @app.post(sessions, status_code=201)
    def create_session() -> dict:
        session_id = str(uuid4())
        with lock, closing(sqlite3.connect(database)) as db, db:
            db.execute("INSERT INTO sessions VALUES (?, ?, '[]')", (session_id, owner_id))
        return {"session_id": session_id}

    @app.get(sessions)
    def list_sessions() -> dict:
        with lock, closing(sqlite3.connect(database)) as db:
            rows = db.execute(
                "SELECT id FROM sessions WHERE owner_id = ? ORDER BY rowid DESC", (owner_id,),
            )
            return {"sessions": [{"session_id": row[0]} for row in rows]}

    @app.delete(sessions + "/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        with lock, closing(sqlite3.connect(database)) as db, db:
            deleted = db.execute(
                "DELETE FROM sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id),
            )
            if not deleted.rowcount:
                raise _fail(404, "session_not_found", "会话不存在或已失效")
        return Response(status_code=204)

    @app.get(messages)
    def get_messages(session_id: str) -> dict:
        with lock, closing(sqlite3.connect(database)) as db:
            return {"messages": read_history(db, session_id)}

    @app.post(messages)
    def send_message(session_id: str, body: MessageInput) -> AssistantReply:
        with lock, closing(sqlite3.connect(database)) as db, db:
            session = Session(history=read_history(db, session_id))
            require_ready()
            try:
                result = pipeline.run(body.content, store, session=session)
            except (APIError, RuntimeError) as exc:
                raise _fail(502, "upstream_error", "助教暂时无法回答") from exc
            if not isinstance(result["answer"], str) or not result["answer"].strip():
                raise _fail(502, "upstream_error", "助教暂时无法回答")
            response = AssistantReply(
                content=result["answer"],
                circuit_type=result["circuit_type"],
                retrieval_sources=[
                    RetrievalSource(source=hit.chunk.source, heading=hit.chunk.heading or None)
                    for hit in result["hits"] or ()
                ],
            )
            db.execute(
                "UPDATE sessions SET history = ? WHERE id = ? AND owner_id = ?",
                (json.dumps(session.context(), ensure_ascii=False), session_id, owner_id),
            )
        return response

    return app


def main() -> None:
    """启动仅监听回环地址的单进程开发服务器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, default=Path("data/local/chat.sqlite3"))
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    args = parser.parse_args()
    try:
        store = VectorStore.load(args.index)
    except (OSError, ValueError, TypeError, EOFError):
        _LOG.warning("课件索引加载失败，聊天接口暂未就绪")
        store = None
    uvicorn.run(create_app(args.database, store), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
