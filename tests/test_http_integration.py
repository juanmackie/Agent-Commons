import asyncio
import json
import os
import socket
import sys
import uuid
from pathlib import Path

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
                await pi.post("/v1/projects", json={"project": "demo", "agent": "pi"})
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
                accepted_message = accepted.json()
                accepted_message.pop("recipient_joined")
                assert read.json()["messages"] == [accepted_message]
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
                answer_message = answer.json()
                answer_message.pop("recipient_joined")
                caught_up = await pi.get(
                    f"/v1/messages?project=demo&after={accepted.json()['seq']}&limit=100"
                )
                assert caught_up.json()["messages"] == [answer_message]
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                store.close()

    asyncio.run(scenario())


def test_agents_setup_before_first_agent_creates_a_project(tmp_path, monkeypatch):
    async def scenario():
        store = ProjectStore(tmp_path / "setup.sqlite3")
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
        api = f"http://{host}:{port}"
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started, "Uvicorn did not start on loopback"
            async with httpx.AsyncClient(base_url=api, timeout=5) as client:
                setup_page = await client.get("/agent_setup")
                assert setup_page.status_code == 200
                assert setup_page.headers["content-disposition"] == 'attachment; filename="agent-commons-invite.md"'
                assert api in setup_page.text
                assert "python roomctl.py join" in setup_page.text
                assert (await client.get("/v1/projects")).json() == {"projects": []}

            repo = Path(__file__).resolve().parents[1]
            env_a = os.environ.copy() | {"LOCALAPPDATA": str(tmp_path / "agent-a")}
            env_b = os.environ.copy() | {"LOCALAPPDATA": str(tmp_path / "agent-b")}
            env_a.pop("AGENT_ID", None)
            env_b.pop("AGENT_ID", None)

            async def cli(env, *args):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(repo / "roomctl.py"),
                    *args,
                    cwd=repo,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode()
                return json.loads(stdout)

            setup_a = await cli(env_a, "setup", "--api", api)
            setup_b = await cli(env_b, "setup", "--api", api)
            agent_a = setup_a["agent_id"]
            agent_b = setup_b["agent_id"]
            await cli(env_a, "join")
            await cli(env_b, "join")
            assert store.projects()[0]["project"] == "agent-commons"
            assert {member["agent_id"] for member in store.participants("agent-commons")} == {
                agent_a,
                agent_b,
            }

            waiter = await asyncio.create_subprocess_exec(
                sys.executable,
                str(repo / "roomctl.py"),
                "wait",
                "--timeout",
                "5",
                cwd=repo,
                env=env_a,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            sent = await cli(
                env_b, "send", "--to", agent_a, "--text", "setup-check-integration"
            )
            stdout, stderr = await asyncio.wait_for(waiter.communicate(), timeout=8)
            assert waiter.returncode == 0, stderr.decode()
            received = json.loads(stdout)
            assert [message["seq"] for message in received["messages"]] == [sent["seq"]]

            cursor = await cli(env_a, "wait", "--timeout", "0")
            assert cursor == {"messages": [], "next_after": sent["seq"]}
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                store.close()

    asyncio.run(scenario())


def test_two_roomctl_agents_keep_rolling_retention_and_reply_to_latest(tmp_path):
    async def scenario():
        store = ProjectStore(tmp_path / "rolling.sqlite3", max_messages_per_project=5)
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
        api = f"http://{host}:{port}"
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started, "Uvicorn did not start on loopback"
            repo = Path(__file__).resolve().parents[1]
            env_pi = os.environ.copy() | {"LOCALAPPDATA": str(tmp_path / "pi")}
            env_hermes = os.environ.copy() | {"LOCALAPPDATA": str(tmp_path / "hermes")}
            env_pi.pop("AGENT_ID", None)
            env_hermes.pop("AGENT_ID", None)

            async def cli(env, *args):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(repo / "roomctl.py"),
                    *args,
                    cwd=repo,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode()
                return json.loads(stdout)

            pi = await cli(env_pi, "setup", "--api", api, "--agent", "pi")
            hermes = await cli(env_hermes, "setup", "--api", api, "--agent", "hermes")
            await cli(env_pi, "create-project", "--project", "demo")
            await cli(env_hermes, "join", "--project", "demo")

            receipts = [
                await cli(env_pi, "send", "--to", "hermes", "--text", f"message-{index}")
                for index in range(1, 8)
            ]
            assert [receipt["seq"] for receipt in receipts] == list(range(1, 8))
            retained = await cli(env_hermes, "read", "--after", "0")
            assert [message["seq"] for message in retained["messages"]] == [3, 4, 5, 6, 7]
            pruned = store.db.execute(
                "SELECT pruned_through FROM projects WHERE project_id=?", ("demo",)
            ).fetchone()[0]
            assert pruned == 2

            reply = await cli(
                env_hermes,
                "send",
                "--reply-to",
                "7",
                "--text",
                "completion reply",
                "--complete",
            )
            assert reply["seq"] == 8
            final = await cli(env_pi, "read", "--after", "3")
            assert [message["seq"] for message in final["messages"]] == [4, 5, 6, 7, 8]
            pruned = store.db.execute(
                "SELECT pruned_through FROM projects WHERE project_id=?", ("demo",)
            ).fetchone()[0]
            assert pruned == 3
            completion = final["messages"][-1]
            assert completion["reply_to"] == 7
            assert completion["hop"] == 1
            assert completion["complete"] is True
            assert {pi["agent_id"], hermes["agent_id"]} == {"pi", "hermes"}
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                store.close()

    asyncio.run(scenario())
