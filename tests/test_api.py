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
        invite = client.get("/agent_setup")
        assert (
            invite.status_code == 200
            and "https://relay.tailnet.test" in invite.text
        )
        assert invite.headers["content-disposition"] == 'attachment; filename="agent-commons-invite.md"'
        assert "does not create or join a project" in invite.text
        assert "python roomctl.py create-project --project agent-commons" in invite.text
        assert "{{ORIGIN}}" not in invite.text
        assert "Every agent receiving this same invite uses that same rule and project name" in invite.text
        assert "If another project appears during a race" in invite.text
        # The downloaded setup must include native activation and an unsolicited
        # session probe.
        assert "pi install ./integrations/pi/agent-commons" in invite.text
        assert "hermes plugins enable agent-commons" in invite.text
        assert "## Verify automatic delivery" in invite.text
        assert "without asking the agent to check messages or running `roomctl read`" in invite.text
        assert "Report setup incomplete" in invite.text
        assert client.get("/join.md?project=demo").status_code == 200
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
        assert received.json() == {
            "messages": [sent.json()],
            "next_after": sent.json()["seq"],
        }
        older = client.get(f"/v1/messages?project=demo&before={sent.json()['seq']}")
        assert older.json()["messages"] == []
        assert client.get(f"/v1/messages?project=demo&after=1&before=2").status_code == 422
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


def test_room_console_and_allowlisted_assets_are_served(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="feed"' in page.text
        assert 'src="/assets/room.js"' in page.text
        assert client.get("/assets/room.js").status_code == 200
        assert client.get("/assets/room.css").status_code == 200
        assert client.get("/assets/../main.py").status_code == 404


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
        assert client.get("/healthz").json() == {"status": "ok"}


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
