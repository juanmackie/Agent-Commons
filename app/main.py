"""Unauthenticated HTTP API for the private-network Agent Commons relay."""

import os
import re
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
)

from app.client import LOOPBACK_HOSTS
from app.store import (
    Conflict,
    NotParticipant,
    ProjectCapacity,
    ProjectsExist,
    ProjectStore,
)

MAX_TEXT_BYTES = 16 * 1024
MAX_BODY_BYTES = 100 * 1024
_MAX_SQLITE_INTEGER = (1 << 63) - 1
# Keep in sync with pyproject.toml: the relay image does not install the
# package, so importlib.metadata cannot discover the version there.
RELAY_VERSION = "0.1.1"

_RELAY_SUPPORTS = {
    "projects",
    "messages",
    "participants",
    "listener_presence",
}
_LABEL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _origin(request: Request) -> str:
    parsed = urllib.parse.urlsplit(str(request.base_url))
    host = parsed.hostname or ""
    scheme = (
        "http"
        if parsed.scheme == "http" and host.lower() in LOOPBACK_HOSTS
        else "https"
    )
    return urllib.parse.urlunsplit((scheme, parsed.netloc, "", "", "")).rstrip("/")


class JoinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: StrictStr
    agent: StrictStr

    @field_validator("project")
    @classmethod
    def valid_project(cls, value):
        if not _PROJECT.fullmatch(value):
            raise ValueError(
                "project must be 1-64 lowercase letters, digits, underscores, or hyphens"
            )
        return value

    @field_validator("agent")
    @classmethod
    def valid_agent(cls, value):
        if not _LABEL.fullmatch(value):
            raise ValueError(
                "agent must contain 1-64 letters, digits, underscores, or hyphens"
            )
        return value


class ProjectInput(JoinInput):
    allow_existing: StrictBool = False


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_msg_id: StrictStr
    sender: StrictStr
    recipient: StrictStr
    thread_id: StrictStr
    reply_to: StrictInt | None = Field(default=None, ge=0, le=_MAX_SQLITE_INTEGER)
    hop: StrictInt = Field(default=0, ge=0, le=8)
    text: StrictStr
    complete: StrictBool = False
    delivery: Literal["follow_up", "interrupt"] = "follow_up"

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
            raise ValueError(
                "must contain 1-64 letters, digits, underscores, or hyphens"
            )
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


class BodyLimitMiddleware:
    """Bound streamed JSON bodies before FastAPI parses them."""

    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        limited_paths = {"/v1/join", "/v1/projects", "/v1/messages"}
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in limited_paths
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared:
            try:
                if int(declared) > self.max_body_bytes:
                    await JSONResponse(
                        {"detail": "request body too large"}, status_code=413
                    )(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse(
                    {"detail": "invalid content length"}, status_code=400
                )(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_body_bytes:
                await JSONResponse(
                    {"detail": "request body too large"}, status_code=413
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)


def create_app(
    db_path: str | Path | None = None, *, store: ProjectStore | None = None
) -> FastAPI:
    configured_path = db_path or os.getenv("ROOMS_DB", "/data/rooms.sqlite3")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    from fastapi.responses import FileResponse

    @app.get("/", include_in_schema=False)
    def room_ui():
        return FileResponse(Path(__file__).with_name("static") / "index.html")

    @app.get("/assets/{name}", include_in_schema=False)
    def room_asset(name: str):
        base = Path(__file__).with_name("static")
        asset_path = base / "assets" / name
        if asset_path.exists() and asset_path.is_file():
            return FileResponse(asset_path)
        legacy_path = base / name
        allowed_legacy = {"room.js", "room-scroll.mjs", "room.css"}
        if name in allowed_legacy and legacy_path.exists() and legacy_path.is_file():
            return FileResponse(legacy_path)
        raise HTTPException(status_code=404)
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=MAX_BODY_BYTES)
    app.state.store = store
    store_lock = threading.Lock()

    def get_store() -> ProjectStore:
        with store_lock:
            if app.state.store is None:
                app.state.store = ProjectStore(
                    configured_path,
                    idle_ttl_seconds=int(os.getenv("ROOM_IDLE_TTL_SECONDS", "604800")),
                    max_active_projects=int(os.getenv("MAX_ACTIVE_PROJECTS", "16")),
                    max_messages_per_project=int(
                        os.getenv("MAX_MESSAGES_PER_PROJECT", "1000")
                    ),
                )
            return app.state.store

    @app.exception_handler(RequestValidationError)
    async def malformed_or_invalid(request, exc):
        errors = [
            {
                "loc": error.get("loc"),
                "msg": error.get("msg"),
                "type": error.get("type"),
            }
            for error in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/healthz")
    def healthz():
        # Moltnet-style capability handshake: clients (roomctl doctor) use the
        # advertised capabilities to detect a relay build older than their
        # feature expectations instead of failing quietly on missing data.
        return {
            "status": "ok",
            "relay_version": RELAY_VERSION,
            "capabilities": sorted(_RELAY_SUPPORTS),
        }

    @app.get("/agent_setup", response_class=PlainTextResponse)
    def agent_setup(request: Request):
        origin = _origin(request)
        content = Path(__file__).with_name("agent_setup.md").read_text(encoding="utf-8")
        return PlainTextResponse(
            content.replace("{{ORIGIN}}", origin),
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="agent-commons-invite.md"',
            },
        )

    @app.get("/join.md", response_class=PlainTextResponse)
    def invitation(request: Request, project: str = Query(min_length=1, max_length=64)):
        if not _PROJECT.fullmatch(project):
            raise HTTPException(status_code=422, detail="invalid project name")
        origin = _origin(request)
        content = Path(__file__).with_name("join.md").read_text(encoding="utf-8")
        return PlainTextResponse(
            content.replace("{{PROJECT}}", project).replace("{{ORIGIN}}", origin),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/join")
    def join(value: JoinInput):
        try:
            get_store().join(value.project, value.agent)
        except KeyError:
            return JSONResponse(
                {
                    "detail": "project does not exist; create it with POST /v1/projects",
                    "code": "project_missing",
                    "projects": get_store().projects(),
                },
                status_code=404,
            )
        return {
            "project": value.project,
            "agent": value.agent,
            "created": False,
            "participants": get_store().participants(value.project),
        }

    @app.post("/v1/projects", status_code=201)
    def create_project(value: ProjectInput):
        try:
            created = get_store().create(
                value.project, value.agent, allow_existing=value.allow_existing
            )
        except ProjectsExist as error:
            return JSONResponse(
                {
                    "detail": "another project already exists; join it instead of creating a duplicate",
                    "code": "projects_exist",
                    "projects": error.projects,
                },
                status_code=409,
            )
        except ProjectCapacity:
            return JSONResponse(
                {"detail": "project capacity reached", "code": "project_capacity"},
                status_code=429,
            )
        return JSONResponse(
            {
                "project": value.project,
                "agent": value.agent,
                "created": created,
                "participants": get_store().participants(value.project),
            },
            status_code=201 if created else 200,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/projects")
    def projects():
        return JSONResponse(
            {"projects": get_store().projects()}, headers={"Cache-Control": "no-store"}
        )

    @app.get("/v1/participants")
    def participants(project: str = Query(min_length=1, max_length=64)):
        try:
            return JSONResponse(
                {"project": project, "participants": get_store().participants(project)},
                headers={"Cache-Control": "no-store"},
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.post("/v1/messages", status_code=201)
    def post_message(
        message: MessageInput, project: str = Query(min_length=1, max_length=64)
    ):
        if len(message.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise HTTPException(
                status_code=413, detail="text exceeds 16 KiB UTF-8 limit"
            )
        try:
            store = get_store()
            result, created = store.post(project, message.model_dump())
        except KeyError:
            raise HTTPException(
                status_code=404, detail="project not found; join first"
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except Conflict:
            raise HTTPException(
                status_code=409, detail="client_msg_id payload conflict"
            ) from None
        return JSONResponse(
            result | {
                "recipient_joined": store.recipient_joined(project, result["recipient"]),
                "recipient_last_seen": store.participant_last_seen(project, result["recipient"]),
            },

            status_code=201 if created else 200,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/messages")
    def read_messages(
        project: str = Query(min_length=1, max_length=64),
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        before: int | None = Query(default=None, ge=1),
        listener: str | None = Query(default=None, min_length=1, max_length=64),
    ):
        if before is not None and after != 0:
            raise HTTPException(status_code=422, detail="before and after cannot be used together")
        try:
            messages, next_after = get_store().read(
                project, after=after, limit=limit, before=before, listener=listener
            )
        except NotParticipant:
            raise HTTPException(status_code=403, detail="listener is not a project participant") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return JSONResponse(
            {"messages": messages, "next_after": next_after},
            headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app()
