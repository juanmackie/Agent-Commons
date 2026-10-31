"""Durable room and append-only message storage."""

import contextlib
import datetime
import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable


class Conflict(Exception):
    pass


class RoomCapacity(Exception):
    pass


class MessageCapacity(Exception):
    pass


class RoomStore:
    def __init__(self, db_path: str | Path, *, idle_ttl_seconds: int = 604800,
                 max_active_rooms: int = 16, max_messages_per_room: int = 1000,
                 clock: Callable[[], int] = lambda: int(time.time())):
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be positive")
        if max_active_rooms <= 0 or max_messages_per_room <= 0:
            raise ValueError("room and message capacities must be positive")
        self.db_path = str(db_path)
        self.idle_ttl_seconds = idle_ttl_seconds
        self.max_active_rooms = max_active_rooms
        self.max_messages_per_room = max_messages_per_room
        self.clock = clock
        self._lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rooms(
              room_hash TEXT PRIMARY KEY,
              created_at INTEGER NOT NULL,
              last_posted_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              room_hash TEXT NOT NULL REFERENCES rooms(room_hash) ON DELETE CASCADE,
              client_msg_id TEXT NOT NULL,
              sender TEXT NOT NULL,
              recipient TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              reply_to INTEGER,
              hop INTEGER NOT NULL,
              text TEXT NOT NULL,
              complete INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              UNIQUE(room_hash, client_msg_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_room_seq ON messages(room_hash, seq);
        """)
        message_columns = {row[1] for row in self.db.execute("PRAGMA table_info(messages)")}
        if "complete" not in message_columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN complete INTEGER NOT NULL DEFAULT 0")

    def close(self):
        with self._lock:
            self.db.close()

    @contextlib.contextmanager
    def _write_transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    @staticmethod
    def _hash(room_id: str) -> str:
        return hashlib.sha256(room_id.encode("utf-8")).hexdigest()

    def _purge_expired(self, now: int):
        self.db.execute("DELETE FROM rooms WHERE last_posted_at <= ?", (now - self.idle_ttl_seconds,))

    def create_room(self) -> str:
        now = self.clock()
        room_id = secrets.token_urlsafe(32)
        room_hash = self._hash(room_id)
        with self._write_transaction():
            self._purge_expired(now)
            count = self.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
            if count >= self.max_active_rooms:
                raise RoomCapacity
            self.db.execute("INSERT INTO rooms VALUES (?, ?, ?)", (room_hash, now, now))
        return room_id

    def _room_exists(self, room_hash: str, now: int) -> bool:
        return self.db.execute("SELECT 1 FROM rooms WHERE room_hash=? AND last_posted_at>?", (room_hash, now - self.idle_ttl_seconds)).fetchone() is not None

    def post(self, room_id: str, payload: dict) -> tuple[dict, bool]:
        payload = dict(payload)
        payload.setdefault("complete", False)
        if not isinstance(payload["complete"], bool):
            raise ValueError("complete must be a boolean")
        now = self.clock()
        room_hash = self._hash(room_id)
        with self._write_transaction():
            if not self._room_exists(room_hash, now):
                raise KeyError
            existing = self.db.execute("SELECT * FROM messages WHERE room_hash=? AND client_msg_id=?", (room_hash, payload["client_msg_id"])).fetchone()
            if existing:
                message = self._message(existing)
                if any(message[field] != payload[field] for field in payload):
                    raise Conflict
                return message, False
            if payload["reply_to"] is not None and self.db.execute("SELECT 1 FROM messages WHERE room_hash=? AND seq=?", (room_hash, payload["reply_to"])).fetchone() is None:
                raise ValueError("reply_to must reference a message in this room")
            count = self.db.execute("SELECT COUNT(*) FROM messages WHERE room_hash=?", (room_hash,)).fetchone()[0]
            if count >= self.max_messages_per_room:
                raise MessageCapacity
            cursor = self.db.execute("""INSERT INTO messages(room_hash,client_msg_id,sender,recipient,thread_id,reply_to,hop,text,complete,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (room_hash, payload["client_msg_id"], payload["sender"], payload["recipient"], payload["thread_id"], payload["reply_to"], payload["hop"], payload["text"], int(payload["complete"]), now))
            self.db.execute("UPDATE rooms SET last_posted_at=? WHERE room_hash=?", (now, room_hash))
            row = self.db.execute("SELECT * FROM messages WHERE seq=?", (cursor.lastrowid,)).fetchone()
            return self._message(row), True

    def list_active_rooms(self) -> list[dict]:
        now = self.clock()
        cutoff = now - self.idle_ttl_seconds
        with self._lock:
            rows = self.db.execute("""SELECT r.room_hash, r.last_posted_at, COUNT(m.seq) AS message_count
                FROM rooms AS r LEFT JOIN messages AS m ON m.room_hash=r.room_hash
                WHERE r.last_posted_at>? GROUP BY r.room_hash ORDER BY r.last_posted_at DESC""", (cutoff,)).fetchall()
        return [
            {
                "room_ref": row["room_hash"][:12],
                "message_count": row["message_count"],
                "last_activity": datetime.datetime.fromtimestamp(row["last_posted_at"], datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            for row in rows
        ]

    def read_active_messages(self, *, after: int, limit: int) -> tuple[list[dict], int]:
        now = self.clock()
        cutoff = now - self.idle_ttl_seconds
        with self._lock:
            rows = self.db.execute("""SELECT m.*, r.room_hash AS active_room_hash
                FROM messages AS m JOIN rooms AS r ON r.room_hash=m.room_hash
                WHERE r.last_posted_at>? AND m.seq>? ORDER BY m.seq LIMIT ?""", (cutoff, after, limit)).fetchall()
        messages = [self._message(row) | {"room_ref": row["active_room_hash"][:12]} for row in rows]
        return messages, messages[-1]["seq"] if messages else after

    def read(self, room_id: str, *, after: int, limit: int) -> tuple[list[dict], int]:
        now = self.clock()
        room_hash = self._hash(room_id)
        with self._lock:
            if not self._room_exists(room_hash, now):
                raise KeyError
            rows = self.db.execute("SELECT * FROM messages WHERE room_hash=? AND seq>? ORDER BY seq LIMIT ?", (room_hash, after, limit)).fetchall()
        messages = [self._message(row) for row in rows]
        return messages, messages[-1]["seq"] if messages else after

    @staticmethod
    def _message(row) -> dict:
        return {"seq": row["seq"], "client_msg_id": row["client_msg_id"], "sender": row["sender"], "recipient": row["recipient"], "thread_id": row["thread_id"], "reply_to": row["reply_to"], "hop": row["hop"], "text": row["text"], "complete": bool(row["complete"]), "created_at": datetime.datetime.fromtimestamp(row["created_at"], datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
