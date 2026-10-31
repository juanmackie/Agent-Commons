import asyncio
import hashlib
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


def test_live_room_directory_is_public_but_does_not_expose_capabilities(client):
    app, run, close = client
    async def scenario():
        async for c in run():
            capability = (await c.post("/v1/rooms")).json()["room_id"]
            response = await c.get("/v1/rooms")
            room = response.json()["rooms"][0]
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert room["room_ref"] == hashlib.sha256(capability.encode()).hexdigest()[:12]
            assert room["message_count"] == 0
            assert room["last_activity"].endswith("Z")
            assert capability not in response.text
            assert set(room) == {"room_ref", "message_count", "last_activity"}
    try:
        asyncio.run(scenario())
    finally:
        close()


def test_discussion_monitor_is_served_without_caching(client):
    app, run, close = client
    async def scenario():
        async for c in run():
            response = await c.get("/")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "Room Monitor" in response.text
            assert "no account or room capability is needed" in response.text.lower()
            assert 'name="capability"' not in response.text
            assert 'id="compose-form"' in response.text
            assert "steering message" in response.text.lower()
            assert "/v1/monitor/messages" in response.text
    try:
        asyncio.run(scenario())
    finally:
        close()


def test_public_monitor_reads_across_rooms_but_writes_stay_authenticated(client):
    app, run, close = client
    async def scenario():
        async for c in run():
            first_room = (await c.post("/v1/rooms")).json()["room_id"]
            second_room = (await c.post("/v1/rooms")).json()["room_id"]
            message = {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c21", "sender":"pi", "recipient":"hermes", "thread_id":"b3d9dcc7-137c-481f-b268-2a40c7e20c88", "reply_to":None, "hop":0, "text":"first room"}
            first = await c.post("/v1/messages", headers={"Authorization":f"Bearer {first_room}"}, json=message)
            second = await c.post("/v1/messages", headers={"Authorization":f"Bearer {second_room}"}, json=message | {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c22", "text":"second room"})
            assert first.status_code == second.status_code == 201

            page1 = await c.get("/v1/monitor/messages?after=0&limit=1")
            page2 = await c.get(f"/v1/monitor/messages?after={page1.json()['next_after']}&limit=1")
            assert page1.status_code == page2.status_code == 200
            assert page1.headers["cache-control"] == "no-store"
            assert page1.json()["messages"][0]["text"] == "first room"
            assert page2.json()["messages"][0]["text"] == "second room"
            assert page1.json()["messages"][0]["room_ref"] == hashlib.sha256(first_room.encode()).hexdigest()[:12]
            assert page2.json()["messages"][0]["room_ref"] == hashlib.sha256(second_room.encode()).hexdigest()[:12]
            assert first_room not in page1.text + page2.text
            assert second_room not in page1.text + page2.text
            assert (await c.get("/v1/messages")).status_code == 401
            assert (await c.post("/v1/messages", json=message)).status_code == 401
    try:
        asyncio.run(scenario())
    finally:
        close()


def test_public_monitor_can_steer_agent_in_selected_room_without_opening_agent_write_api(client):
    app, run, close = client
    async def scenario():
        async for c in run():
            capability = (await c.post("/v1/rooms")).json()["room_id"]
            headers = {"Authorization":f"Bearer {capability}"}
            original = {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c41", "sender":"hermes", "recipient":"pi", "thread_id":"b3d9dcc7-137c-481f-b268-2a40c7e20c88", "reply_to":None, "hop":0, "text":"Can you review this?"}
            accepted = await c.post("/v1/messages", headers=headers, json=original)
            assert accepted.status_code == 201
            room_ref = hashlib.sha256(capability.encode()).hexdigest()[:12]
            user_message = {"room_ref":room_ref, "client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c42", "recipient":"pi", "thread_id":original["thread_id"], "reply_to":accepted.json()["seq"], "text":"Please focus on the error handling."}
            sent = await c.post("/v1/monitor/messages", json=user_message)
            assert sent.status_code == 201
            body = sent.json()
            assert body["sender"] == "user"
            assert body["recipient"] == "pi"
            assert body["thread_id"] == original["thread_id"]
            assert body["reply_to"] == accepted.json()["seq"]
            assert body["hop"] == 0 and body["complete"] is False
            assert body["room_ref"] == room_ref
            assert body["text"] == user_message["text"]
            replay = await c.post("/v1/monitor/messages", json=user_message)
            assert replay.status_code == 200
            assert replay.json() == body

            feed = await c.get("/v1/monitor/messages")
            assert [message["text"] for message in feed.json()["messages"]] == [original["text"], user_message["text"]]
            assert (await c.post("/v1/messages", json=original)).status_code == 401
            assert (await c.post("/v1/monitor/messages", json=user_message | {"room_ref":"000000000000"})).status_code == 404
            assert (await c.post("/v1/monitor/messages", json=user_message | {"recipient":"bad label"})).status_code == 422
            assert (await c.post("/v1/monitor/messages", json=user_message | {"sender":"pi"})).status_code == 422
            oversized_reply = user_message | {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c44", "reply_to":9223372036854775808}
            assert (await c.post("/v1/monitor/messages", json=oversized_reply)).status_code == 422
            oversized = user_message | {"client_msg_id":"cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c43", "text":"é" * 8193}
            assert (await c.post("/v1/monitor/messages", json=oversized)).status_code == 413
    try:
        asyncio.run(scenario())
    finally:
        close()


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
            assert first.json()["complete"] is False
            assert first.json()["seq"] == replay.json()["seq"]
            changed = await c.post("/v1/messages", headers=headers, json=msg | {"text":"changed"})
            assert changed.status_code == 409
            final = await c.post("/v1/messages", headers=headers, json=msg | {
                "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14", "complete": True,
            })
            assert final.status_code == 201 and final.json()["complete"] is True
            invalid_complete = await c.post("/v1/messages", headers=headers, json=msg | {
                "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c15", "complete": "true",
            })
            assert invalid_complete.status_code == 422
            changed_completion = await c.post("/v1/messages", headers=headers, json=msg | {"complete": True})
            assert changed_completion.status_code == 409
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
            assert (await c.post("/v1/messages", headers=headers, json=msg | {"reply_to":9223372036854775808})).status_code == 422
            assert (await c.post("/v1/messages", headers=headers, json=msg)).status_code == 413
            assert (await c.post("/v1/messages", headers=headers, content="{" )).status_code in (400, 422)
            invalid_payload = json.dumps(msg | {
                "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c16", "text": "\ud800",
            })
            invalid_utf8 = await c.post("/v1/messages", headers=headers, content=invalid_payload)
            assert invalid_utf8.status_code == 422
    try:
        asyncio.run(scenario())
    finally:
        close()
