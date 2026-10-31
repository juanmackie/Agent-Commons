"""HTTP API for the private room mailbox."""

import os
import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from app.store import Conflict, MessageCapacity, RoomCapacity, RoomStore

MAX_TEXT_BYTES = 16 * 1024
MAX_BODY_BYTES = 100 * 1024
_LABEL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_msg_id: StrictStr
    sender: StrictStr
    recipient: StrictStr
    thread_id: StrictStr
    reply_to: StrictInt | None
    hop: StrictInt = Field(ge=0, le=8)
    text: StrictStr
    complete: StrictBool = False

    @field_validator("client_msg_id", "thread_id")
    @classmethod
    def canonical_uuid(cls, value):
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("must be canonical UUID text") from None
        return value

    @field_validator("sender", "recipient")
    @classmethod
    def valid_label(cls, value):
        if not _LABEL.fullmatch(value):
            raise ValueError("must contain 1-64 letters, digits, underscores, or hyphens")
        return value

    @field_validator("reply_to")
    @classmethod
    def valid_reply_to(cls, value):
        if value is not None and value < 0:
            raise ValueError("must be a non-negative sequence")
        return value

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        if not value:
            raise ValueError("must not be empty")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("must be valid UTF-8 text") from None
        return value


def create_app(db_path: str | Path | None = None, *, store: RoomStore | None = None) -> FastAPI:
    configured_path = db_path or os.getenv("ROOMS_DB", "/data/rooms.sqlite3")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    store_lock = threading.Lock()

    def get_store() -> RoomStore:
        with store_lock:
            if app.state.store is None:
                app.state.store = RoomStore(
                    configured_path,
                    idle_ttl_seconds=int(os.getenv("ROOM_IDLE_TTL_SECONDS", "604800")),
                    max_active_rooms=int(os.getenv("MAX_ACTIVE_ROOMS", "16")),
                    max_messages_per_room=int(os.getenv("MAX_MESSAGES_PER_ROOM", "1000")),
                )
            return app.state.store

    @app.middleware("http")
    async def enforce_body_limit(request: Request, call_next):
        if request.method == "POST" and request.url.path in {"/v1/rooms", "/v1/messages"}:
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) > MAX_BODY_BYTES:
                        return JSONResponse({"detail": "request body too large"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "invalid content length"}, status_code=400)
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
            if request.url.path == "/v1/rooms" and body:
                return JSONResponse({"detail": "room creation body must be empty"}, status_code=422)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def malformed_or_invalid(request, exc):
        errors = [{"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    def authorized(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="invalid room id")
        room_id = authorization[7:]
        if not room_id or len(room_id) > 128:
            raise HTTPException(status_code=401, detail="invalid room id")
        return room_id

    @app.get("/", include_in_schema=False)
    def discussion_monitor():
        return FileResponse(Path(__file__).with_name("monitor.html"), headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/v1/rooms")
    def list_rooms():
        return JSONResponse({"rooms": get_store().list_active_rooms()}, headers={"Cache-Control": "no-store"})

    @app.post("/v1/rooms", status_code=201)
    def create_room():
        try:
            return {"room_id": get_store().create_room(), "idle_ttl_seconds": get_store().idle_ttl_seconds}
        except RoomCapacity:
            return JSONResponse({"detail": "room capacity reached", "code": "room_capacity"}, status_code=429)

    @app.get("/v1/monitor/messages")
    def monitor_messages(after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        messages, next_after = get_store().read_active_messages(after=after, limit=limit)
        return JSONResponse({"messages": messages, "next_after": next_after}, headers={"Cache-Control": "no-store"})

    @app.post("/v1/messages", status_code=201)
    def post_message(message: MessageInput, authorization: str | None = Header(default=None)):
        room_id = authorized(authorization)
        if len(message.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise HTTPException(status_code=413, detail="text exceeds 16 KiB UTF-8 limit")
        try:
            result, created = get_store().post(room_id, message.model_dump())
        except KeyError:
            raise HTTPException(status_code=401, detail="invalid room id") from None
        except Conflict:
            raise HTTPException(status_code=409, detail="client_msg_id payload conflict") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except MessageCapacity:
            return JSONResponse({"detail": "message capacity reached", "code": "message_capacity"}, status_code=507)
        return JSONResponse(result, status_code=201 if created else 200)

    @app.get("/v1/messages")
    def read_messages(authorization: str | None = Header(default=None), after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        room_id = authorized(authorization)
        try:
            messages, next_after = get_store().read(room_id, after=after, limit=limit)
        except KeyError:
            raise HTTPException(status_code=401, detail="invalid room id") from None
        return {"messages": messages, "next_after": next_after}

    return app


app = create_app()
