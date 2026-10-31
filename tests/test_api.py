import asyncio
import json

import httpx
import pytest

from app.main import create_app


@pytest.fixture
def client():
    app = create_app(":memory:")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c
    def close():
        if app.state.store:
            app.state.store.close()
    return app, run, close


def test_two_clients_auth_pagination_replay_and_conflict(client):
    app, run, close = client
    async def scenario():
        async for c in run():
            room = (await c.post("/v1/rooms")).json()["room_id"]
            headers = {"Authorization": f"Bearer {room}"}
            msg = {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13", "sender":"hermes", "recipient":"pi", "thread_id":"b3d9dcc7-137c-481f-b268-2a40c7e20c88", "reply_to":None, "hop":0, "text":"hi"}
            first = await c.post("/v1/messages", headers=headers, json=msg)
            replay = await c.post("/v1/messages", headers=headers, json=msg)
            assert first.status_code == 201 and replay.status_code == 200
            assert first.json()["seq"] == replay.json()["seq"]
            changed = await c.post("/v1/messages", headers=headers, json=msg | {"text":"changed"})
            assert changed.status_code == 409
            assert (await c.get("/v1/messages?after=0&limit=1", headers=headers)).json() == {"messages":[first.json()], "next_after":1}
            assert (await c.get("/v1/messages", headers={"Authorization":"Bearer invalid"})).json() == {"detail":"invalid room id"}
            assert (await c.get("/healthz")).json() == {"status":"ok"}
    try:
        asyncio.run(scenario())
    finally:
        close()


def test_concurrent_room_creation_initializes_one_store_and_keeps_capabilities_unique():
    app = create_app(":memory:")
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            responses = await asyncio.gather(*(c.post("/v1/rooms") for _ in range(12)))
        assert all(response.status_code == 201 for response in responses)
        ids = [response.json()["room_id"] for response in responses]
        assert len(set(ids)) == len(ids)
        assert app.state.store.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == len(ids)
    try:
        asyncio.run(scenario())
    finally:
        if app.state.store:
            app.state.store.close()


def test_message_size_and_validation(client):
    app, run, close = client
    async def scenario():
        async for c in run():
            room = (await c.post("/v1/rooms")).json()["room_id"]
            headers = {"Authorization":f"Bearer {room}"}
            msg = {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13", "sender":"hermes", "recipient":"pi", "thread_id":"b3d9dcc7-137c-481f-b268-2a40c7e20c88", "reply_to":None, "hop":0, "text":"é" * 8193}
            assert (await c.post("/v1/messages", headers=headers, json=msg)).status_code == 413
            assert (await c.post("/v1/messages", headers=headers, content="{" )).status_code in (400, 422)
    try:
        asyncio.run(scenario())
    finally:
        close()
