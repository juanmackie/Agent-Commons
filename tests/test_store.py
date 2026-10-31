from concurrent.futures import ThreadPoolExecutor

import pytest

from app.store import Conflict, RoomCapacity, RoomStore


def payload(**changes):
    value = {
        "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
        "sender": "hermes",
        "recipient": "pi",
        "thread_id": "b3d9dcc7-137c-481f-b268-2a40c7e20c88",
        "reply_to": None,
        "hop": 0,
        "text": "review this",
    }
    return value | changes


@pytest.mark.parametrize(("options",), [
    ({"idle_ttl_seconds": 0},),
    ({"max_active_rooms": 0},),
    ({"max_messages_per_room": -1},),
])
def test_store_rejects_nonpositive_retention_and_capacity(options):
    with pytest.raises(ValueError):
        RoomStore(":memory:", **options)


def test_room_capability_is_hashed_and_messages_survive_reopen(tmp_path):
    db = tmp_path / "rooms.sqlite3"
    store = RoomStore(db, clock=lambda: 100)
    room_id = store.create_room()
    store.post(room_id, payload())
    store.close()

    reopened = RoomStore(db, clock=lambda: 101)
    assert reopened.read(room_id, after=0, limit=100)[0][0]["seq"] == 1
    assert room_id.encode() not in db.read_bytes()


def test_idempotency_conflict_order_and_reply_validation():
    store = RoomStore(":memory:", clock=lambda: 100)
    room = store.create_room()
    first, created = store.post(room, payload())
    replay, created_again = store.post(room, payload())
    assert created and not created_again and replay == first
    assert store.post(room, payload(client_msg_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14"))[0]["seq"] == 2
    with pytest.raises(Conflict):
        store.post(room, payload(text="changed"))
    with pytest.raises(ValueError, match="reply_to"):
        store.post(room, payload(client_msg_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c15", reply_to=999))


def test_expiry_ignores_reads_and_exact_replays_and_capacity_is_explicit():
    now = [100]
    store = RoomStore(":memory:", clock=lambda: now[0], idle_ttl_seconds=10, max_active_rooms=1)
    room = store.create_room()
    with pytest.raises(RoomCapacity):
        store.create_room()
    store.post(room, payload())
    now[0] = 109
    assert store.post(room, payload())[1] is False
    store.read(room, after=0, limit=100)
    now[0] = 110
    with pytest.raises(KeyError):
        store.read(room, after=0, limit=100)
    assert store.create_room()


def test_concurrent_duplicate_posts_commit_one_message():
    store = RoomStore(":memory:")
    room = store.create_room()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.post(room, payload()), range(20)))
    assert sum(created for _, created in results) == 1
    assert len({message["seq"] for message, _ in results}) == 1
    assert len(store.read(room, after=0, limit=100)[0]) == 1


def test_expired_room_cleanup_and_independent_room_sequences():
    now = [10]
    store = RoomStore(":memory:", clock=lambda: now[0], idle_ttl_seconds=5)
    first = store.create_room()
    second = store.create_room()
    assert store.post(first, payload())[0]["seq"] == 1
    assert store.post(second, payload())[0]["seq"] == 2
    now[0] = 15
    store.create_room()
    with pytest.raises(KeyError):
        store.read(first, after=0, limit=100)
