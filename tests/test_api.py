import re
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
        create_app(tmp_path / "db.sqlite3"), base_url="http://relay.tailnet.test"
    ) as client:
        invite = client.get("/agent_setup")
        assert (
            invite.status_code == 200
            and "https://relay.tailnet.test" in invite.text
        )
        assert invite.headers["content-disposition"] == 'attachment; filename="agent-commons-invite.md"'
        assert "{{ORIGIN}}" not in invite.text
        assert "agent-setup" in invite.text
        assert "https://relay.tailnet.test" in client.get("/join.md?project=demo").text
        assert (
            client.post(
                "/v1/projects", json={"project": "demo", "agent": "pi"}
            ).status_code
            == 201
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
        posted_message = sent.json()
        posted_message.pop("recipient_joined")
        posted_message.pop("recipient_last_seen")
        assert received.json() == {
            "messages": [posted_message],
            "next_after": posted_message["seq"],
        }
        older = client.get(f"/v1/messages?project=demo&before={sent.json()['seq']}")
        assert older.json()["messages"] == []
        assert client.get("/v1/messages?project=demo&after=1&before=2").status_code == 422
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


def test_loopback_invite_keeps_http_origin(tmp_path):
    with TestClient(
        create_app(tmp_path / "db.sqlite3"), base_url="http://127.0.0.1:8080"
    ) as client:
        invite = client.get("/agent_setup")
        assert "Relay: http://127.0.0.1:8080" in invite.text


def test_https_loopback_invite_keeps_https_origin(tmp_path):
    with TestClient(
        create_app(tmp_path / "db.sqlite3"), base_url="https://localhost:8443"
    ) as client:
        invite = client.get("/agent_setup")
        assert "Relay: https://localhost:8443" in invite.text


def test_room_console_and_allowlisted_assets_are_served(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        page = client.get("/")
        assert page.status_code == 200
        if 'id="root"' in page.text:
            scripts = re.findall(
                r'<script\b[^>]*\bsrc="(/assets/[^" ]+\.js)"', page.text
            )
            stylesheets = re.findall(
                r'<link\b[^>]*\bhref="(/assets/[^" ]+\.css)"', page.text
            )
            assert len(scripts) == 1
            assert len(stylesheets) == 1
            assert client.get(scripts[0]).status_code == 200
            assert client.get(stylesheets[0]).status_code == 200
        else:
            assert 'id="feed"' in page.text
            assert 'src="/assets/room.js"' in page.text
        assert client.get("/assets/room.js").status_code == 200
        assert client.get("/assets/room-scroll.mjs").status_code == 200
        assert client.get("/assets/room.css").status_code == 200
        assert client.get("/assets/not-allowlisted.mjs").status_code == 404
        assert client.get("/assets/../main.py").status_code == 404


def test_pruned_message_retry_returns_receipt_without_duplicate_or_eviction(tmp_path):
    from app.store import ProjectStore

    store = ProjectStore(tmp_path / "db.sqlite3", max_messages_per_project=1)
    with TestClient(create_app(store=store)) as client:
        client.post("/v1/projects", json={"project": "demo", "agent": "pi"})
        original = envelope()
        first = client.post("/v1/messages?project=demo", json=original)
        newer = client.post(
            "/v1/messages?project=demo",
            json=envelope(client_msg_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14", text="newer"),
        )
        replay = client.post("/v1/messages?project=demo", json=original)
        assert first.status_code == 201 and newer.status_code == 201
        assert replay.status_code == 200
        assert replay.json()["seq"] == first.json()["seq"]
        assert replay.json()["pruned"] is True
        assert len(client.get("/v1/messages?project=demo&after=0").json()["messages"]) == 1
        assert client.get("/v1/messages?project=demo&after=0").json()["messages"][0]["seq"] == newer.json()["seq"]


def test_message_post_reports_current_recipient_membership_and_rolls_capacity(tmp_path):
    from app.store import ProjectStore

    store = ProjectStore(tmp_path / "db.sqlite3", max_messages_per_project=1)
    with TestClient(create_app(store=store)) as client:
        client.post("/v1/projects", json={"project": "demo", "agent": "pi"})
        value = envelope(recipient="hermes")
        first = client.post("/v1/messages?project=demo", json=value)
        assert first.status_code == 201
        assert first.json()["recipient_joined"] is False

        client.post("/v1/join", json={"project": "demo", "agent": "hermes"})
        replay = client.post("/v1/messages?project=demo", json=value)
        assert replay.status_code == 200
        assert replay.json()["recipient_joined"] is True

        next_message = client.post(
            "/v1/messages?project=demo",
            json=envelope(recipient="hermes", text="next"),
        )
        assert next_message.status_code == 201
        assert next_message.json()["seq"] > first.json()["seq"]
        messages = client.get("/v1/messages?project=demo&after=0").json()["messages"]
        assert len(messages) == 1 and messages[0]["seq"] == next_message.json()["seq"]
        assert next_message.status_code != 507


def test_listener_poll_reports_last_seen_and_send_receipt_warns_after_two_minutes(tmp_path):
    from app.store import ProjectStore

    now = [100]
    store = ProjectStore(tmp_path / "db.sqlite3", clock=lambda: now[0])
    with TestClient(create_app(store=store)) as client:
        client.post("/v1/projects", json={"project": "demo", "agent": "pi"})
        client.post("/v1/join", json={"project": "demo", "agent": "hermes"})
        participants = client.get("/v1/participants?project=demo").json()["participants"]
        assert {p["agent_id"]: p["last_seen"] for p in participants} == {
            "hermes": None, "pi": None,
        }
        manual = client.get("/v1/messages?project=demo&after=0")
        assert manual.status_code == 200
        assert store.participants("demo")[0]["last_seen"] is None

        heard = client.get("/v1/messages?project=demo&after=0&listener=hermes")
        assert heard.status_code == 200
        assert {p["agent_id"]: p["last_seen"] for p in client.get(
            "/v1/participants?project=demo"
        ).json()["participants"]}["hermes"] == "1970-01-01T00:01:40Z"
        sent = client.post("/v1/messages?project=demo", json=envelope(recipient="hermes"))
        assert sent.status_code == 201
        assert sent.json()["recipient_last_seen"] == "1970-01-01T00:01:40Z"

        now[0] = 221
        stale = client.post("/v1/messages?project=demo", json=envelope(
            recipient="hermes", client_msg_id=str(uuid.uuid4())
        ))
        assert stale.status_code == 201
        assert stale.json()["recipient_last_seen"] == "1970-01-01T00:01:40Z"

        unjoined = client.get("/v1/messages?project=demo&listener=unknown")
        assert unjoined.status_code == 403
        assert client.get("/v1/messages?project=demo&listener=bad%20label").status_code == 422


def test_api_validation_bounded_text_and_conflicting_retry(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        client.post("/v1/projects", json={"project": "demo", "agent": "pi"})
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
        healthz = client.get("/healthz").json()
        assert healthz["status"] == "ok"
        assert isinstance(healthz["relay_version"], str) and healthz["relay_version"]
        assert "listener_presence" in healthz["capabilities"]


def test_project_creation_checks_existing_projects_before_starting_a_new_one(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        missing = client.post("/v1/join", json={"project": "crew", "agent": "pi"})
        assert missing.status_code == 404
        assert missing.json()["code"] == "project_missing"
        assert missing.json()["projects"] == []

        created = client.post("/v1/projects", json={"project": "crew", "agent": "pi"})
        assert created.status_code == 201 and created.json()["created"] is True

        refused = client.post(
            "/v1/projects", json={"project": "crew-2", "agent": "hermes"}
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "projects_exist"
        assert [entry["project"] for entry in refused.json()["projects"]] == ["crew"]

        joined = client.post("/v1/join", json={"project": "crew", "agent": "hermes"})
        assert joined.status_code == 200 and joined.json()["created"] is False

        again = client.post("/v1/projects", json={"project": "crew", "agent": "pi"})
        assert again.status_code == 200 and again.json()["created"] is False
        assert [
            entry["project"]
            for entry in client.get("/v1/projects").json()["projects"]
        ] == ["crew"]

        extra = client.post(
            "/v1/projects",
            json={"project": "crew-2", "agent": "pi", "allow_existing": True},
        )
        assert extra.status_code == 201 and extra.json()["created"] is True

        nowhere = client.post("/v1/join", json={"project": "nope", "agent": "pi"})
        assert nowhere.status_code == 404
        assert {entry["project"] for entry in nowhere.json()["projects"]} == {
            "crew",
            "crew-2",
        }
