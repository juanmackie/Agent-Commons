import uuid

from fastapi.testclient import TestClient

from app.main import create_app


def envelope(**changes):
    return {
        "client_msg_id": str(uuid.uuid4()),
        "sender": "pi",
        "recipient": "hermes",
        "thread_id": str(uuid.uuid4()),
        "reply_to": None,
        "hop": 0,
        "text": "review this",
        "complete": False,
    } | changes


def test_join_invitation_and_two_way_message_journey(tmp_path):
    with TestClient(
        create_app(tmp_path / "db.sqlite3"), base_url="https://relay.tailnet.test"
    ) as client:
        invite = client.get("/join.md?project=demo")
        assert (
            invite.status_code == 200
            and "https://relay.tailnet.test" in invite.text
            and "demo" in invite.text
        )
        assert "not a password or access token" in invite.text
        assert (
            client.post("/v1/join", json={"project": "demo", "agent": "pi"}).status_code
            == 200
        )
        client.post("/v1/join", json={"project": "demo", "agent": "hermes"})
        directory = client.get("/v1/participants?project=demo")
        assert [p["agent_id"] for p in directory.json()["participants"]] == [
            "hermes",
            "pi",
        ]
        original = envelope()
        sent = client.post("/v1/messages?project=demo", json=original)
        assert sent.status_code == 201
        assert (
            client.post("/v1/messages?project=demo", json=original).status_code == 200
        )
        received = client.get("/v1/messages?project=demo&after=0&limit=1")
        assert received.json() == {
            "messages": [sent.json()],
            "next_after": sent.json()["seq"],
        }
        reply = envelope(
            sender="hermes",
            recipient="pi",
            thread_id=sent.json()["thread_id"],
            reply_to=sent.json()["seq"],
            text="done",
        )
        response = client.post("/v1/messages?project=demo", json=reply)
        assert response.status_code == 201
        assert (
            client.get("/v1/messages?project=demo&after=0").json()["messages"][-1][
                "text"
            ]
            == "done"
        )
        assert client.get("/v1/messages?project=missing").status_code == 404
        assert client.get("/v1/messages?project=demo").status_code == 200


def test_api_validation_bounded_text_and_conflicting_retry(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        client.post("/v1/join", json={"project": "demo", "agent": "pi"})
        value = envelope()
        assert client.post("/v1/messages?project=demo", json=value).status_code == 201
        assert (
            client.post(
                "/v1/messages?project=demo", json=value | {"text": "different"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/v1/messages?project=demo", json=envelope(text="x" * 16385)
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/v1/join",
                content=b"x" * 102401,
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/v1/join", json={"project": "Bad Name", "agent": "pi"}
            ).status_code
            == 422
        )
        assert client.get("/v1/messages?project=demo&limit=101").status_code == 422
        assert client.get("/healthz").json() == {"status": "ok"}
