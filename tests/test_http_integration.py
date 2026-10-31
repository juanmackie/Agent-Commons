import asyncio
import socket

import httpx
import uvicorn

from app import main as relay_module
from app.store import RoomStore


def test_two_real_http_clients_exchange_messages_over_loopback(tmp_path):
    async def scenario():
        app = relay_module.app
        previous_store = app.state.store
        test_store = RoomStore(tmp_path / "rooms.sqlite3")
        app.state.store = test_store
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host, port = listener.getsockname()
        config = uvicorn.Config("app.main:app", host=host, port=port, log_level="critical", access_log=False, lifespan="off")
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started, "Uvicorn did not start on the loopback socket"
            async with httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=5) as client_a, httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=5) as client_b:
                room_id = (await client_a.post("/v1/rooms")).json()["room_id"]
                headers = {"Authorization": f"Bearer {room_id}"}
                message = {
                    "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
                    "sender": "hermes",
                    "recipient": "pi",
                    "thread_id": "b3d9dcc7-137c-481f-b268-2a40c7e20c88",
                    "reply_to": None,
                    "hop": 0,
                    "text": "check loopback",
                }
                accepted = await client_a.post("/v1/messages", headers=headers, json=message)
                replay = await client_b.post("/v1/messages", headers=headers, json=message)
                read = await client_b.get("/v1/messages?after=0&limit=100", headers=headers)
                assert accepted.status_code == 201
                assert replay.status_code == 200
                assert accepted.json() == replay.json()
                assert read.json()["messages"] == [accepted.json()]
                assert read.json()["next_after"] == accepted.json()["seq"]
                assert (await client_b.get("/v1/messages", headers={"Authorization": "Bearer invalid"})).status_code == 401
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                test_store.close()
                app.state.store = previous_store

    asyncio.run(scenario())
