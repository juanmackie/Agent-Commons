import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.store import Conflict, MessageCapacity, ProjectCapacity, ProjectStore


def message(**changes):
    return {
        "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
        "sender": "hermes",
        "recipient": "pi",
        "thread_id": "b3d9dcc7-137c-481f-b268-2a40c7e20c88",
        "reply_to": None,
        "hop": 0,
        "text": "review this",
        "complete": False,
    } | changes


def test_join_directory_two_way_delivery_retry_and_reopen(tmp_path):
    path = tmp_path / "messages.sqlite3"
    store = ProjectStore(path, clock=lambda: 100)
    assert store.join("demo", "hermes") is True
    assert store.join("demo", "pi") is False
    assert [p["agent_id"] for p in store.participants("demo")] == ["hermes", "pi"]
    first, created = store.post("demo", message())
    assert created
    assert store.post("demo", message())[0] == first
    reply, created = store.post(
        "demo",
        message(
            client_msg_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14",
            sender="pi",
            recipient="hermes",
            reply_to=first["seq"],
            text="looks good",
        ),
    )
    assert created and reply["reply_to"] == first["seq"]
    store.close()
    reopened = ProjectStore(path, clock=lambda: 101)
    page, cursor = reopened.read("demo", after=0, limit=1)
    assert page == [first] and cursor == first["seq"]
    assert reopened.read("demo", after=cursor, limit=1)[0] == [reply]


def test_project_store_rejects_conflicts_foreign_replies_and_capacity():
    store = ProjectStore(":memory:", max_active_projects=1, max_messages_per_project=1)
    store.join("demo", "pi")
    original, _ = store.post("demo", message())
    with pytest.raises(Conflict):
        store.post("demo", message(text="changed"))
    with pytest.raises(ValueError, match="reply_to"):
        store.post(
            "demo",
            message(client_msg_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14", reply_to=999),
        )
    with pytest.raises(MessageCapacity):
        store.post(
            "demo", message(client_msg_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14")
        )
    with pytest.raises(ProjectCapacity):
        store.join("other", "hermes")
    assert store.read("demo", after=0, limit=10)[0] == [original]


def test_concurrent_duplicate_posts_append_once():
    store = ProjectStore(":memory:")
    store.join("demo", "pi")
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: store.post("demo", message()), range(20)))
    assert sum(created for _, created in outcomes) == 1
    assert len({msg["seq"] for msg, _ in outcomes}) == 1
    assert len(store.read("demo", after=0, limit=100)[0]) == 1


def test_legacy_room_database_migrates_history_to_project_id_without_loss(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    room_hash = "a" * 64
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE rooms(room_hash TEXT PRIMARY KEY,created_at INTEGER NOT NULL,last_posted_at INTEGER NOT NULL);
          CREATE TABLE messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,room_hash TEXT NOT NULL REFERENCES rooms(room_hash),client_msg_id TEXT NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,thread_id TEXT NOT NULL,reply_to INTEGER,hop INTEGER NOT NULL,text TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(room_hash,client_msg_id));
        """)
        db.execute("INSERT INTO rooms VALUES(?,?,?)", (room_hash, 100, 100))
        db.execute(
            "INSERT INTO messages VALUES(1,?,?,?,?,?,?,?,?,?)",
            (
                room_hash,
                "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
                "hermes",
                "pi",
                "b3d9dcc7-137c-481f-b268-2a40c7e20c88",
                None,
                0,
                "old history",
                100,
            ),
        )
    store = ProjectStore(path, clock=lambda: 101)
    assert store.read(room_hash, after=0, limit=10)[0][0]["text"] == "old history"
    assert store.projects()[0]["project"] == room_hash


@pytest.mark.parametrize("project", ["Upper", "space name", "", "../path"])
def test_invalid_project_names_rejected(project):
    store = ProjectStore(":memory:")
    with pytest.raises(ValueError):
        store.join(project, "pi")
