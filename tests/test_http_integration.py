import asyncio
import socket
import uuid

import httpx
import uvicorn

from app.main import create_app
from app.store import ProjectStore


def test_two_real_http_clients_exchange_messages_over_loopback(tmp_path):
    async def scenario():
        store = ProjectStore(tmp_path / "messages.sqlite3")
        app = create_app(store=store)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host, port = listener.getsockname()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="critical",
                access_log=False,
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started, "Uvicorn did not start on loopback"
            async with (
                httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=5) as pi,
                httpx.AsyncClient(
                    base_url=f"http://{host}:{port}", timeout=5
                ) as hermes,
            ):
                await pi.post("/v1/join", json={"project": "demo", "agent": "pi"})
                await hermes.post(
                    "/v1/join", json={"project": "demo", "agent": "hermes"}
                )
                message = {
                    "client_msg_id": str(uuid.uuid4()),
                    "sender": "pi",
                    "recipient": "hermes",
                    "thread_id": str(uuid.uuid4()),
                    "reply_to": None,
                    "hop": 0,
                    "text": "check loopback",
                    "complete": False,
                }
                accepted = await pi.post("/v1/messages?project=demo", json=message)
                replay = await hermes.post("/v1/messages?project=demo", json=message)
                read = await hermes.get("/v1/messages?project=demo&after=0&limit=100")
                assert accepted.status_code == 201 and replay.status_code == 200
                assert accepted.json() == replay.json()
                assert read.json()["messages"] == [accepted.json()]
                assert read.json()["next_after"] == accepted.json()["seq"]
                reply = message | {
                    "client_msg_id": str(uuid.uuid4()),
                    "sender": "hermes",
                    "recipient": "pi",
                    "reply_to": accepted.json()["seq"],
                    "text": "reply from Hermes",
                }
                answer = await hermes.post("/v1/messages?project=demo", json=reply)
                assert answer.status_code == 201
                caught_up = await pi.get(
                    f"/v1/messages?project=demo&after={accepted.json()['seq']}&limit=100"
                )
                assert caught_up.json()["messages"] == [answer.json()]
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                store.close()

    asyncio.run(scenario())
