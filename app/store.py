"""Durable project membership and append-only relay message storage."""

import contextlib
import datetime
import hashlib
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path


class Conflict(Exception):
    pass


class ProjectCapacity(Exception):
    pass


class MessageCapacity(Exception):
    pass


class ProjectsExist(Exception):
    """Another active project already exists; refuse to create a duplicate."""

    def __init__(self, projects):
        self.projects = projects
        super().__init__("other projects exist")


_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_AGENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProjectStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        idle_ttl_seconds: int = 604800,
        max_active_projects: int = 16,
        max_messages_per_project: int = 1000,
        clock: Callable[[], int] = lambda: int(time.time()),
    ):
        if (
            idle_ttl_seconds <= 0
            or max_active_projects <= 0
            or max_messages_per_project <= 0
        ):
            raise ValueError(
                "retention and project/message capacities must be positive"
            )
        self.db_path = str(db_path)
        self.idle_ttl_seconds = idle_ttl_seconds
        self.max_active_projects = max_active_projects
        self.max_messages_per_project = max_messages_per_project
        self.clock = clock
        self._lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(
            self.db_path, timeout=5, check_same_thread=False, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._initialize()

    def _initialize(self):
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS rooms(room_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL, last_posted_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS messages(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            room_hash TEXT NOT NULL REFERENCES rooms(room_hash) ON DELETE CASCADE,
            client_msg_id TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
            thread_id TEXT NOT NULL, reply_to INTEGER, hop INTEGER NOT NULL,
            text TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
            delivery TEXT NOT NULL DEFAULT 'follow_up',
            UNIQUE(room_hash, client_msg_id));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(messages)")}
        if "complete" not in columns:
            self.db.execute(
                "ALTER TABLE messages ADD COLUMN complete INTEGER NOT NULL DEFAULT 0"
            )
        if "project_id" not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN project_id TEXT")
        if "delivery" not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN delivery TEXT NOT NULL DEFAULT 'follow_up'")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS projects(
            project_id TEXT PRIMARY KEY, room_hash TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL, last_activity INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS participants(
            project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL, joined_at INTEGER NOT NULL,
            PRIMARY KEY(project_id, agent_id));
          CREATE INDEX IF NOT EXISTS idx_messages_project_seq ON messages(project_id, seq);
          CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_project_client_id ON messages(project_id, client_msg_id) WHERE project_id IS NOT NULL;
        """)
        # Existing opaque rooms become projects named by their full, non-secret hash.
        with self._transaction():
            for room in self.db.execute(
                "SELECT room_hash,created_at,last_posted_at FROM rooms"
            ).fetchall():
                project_id = room["room_hash"]
                self.db.execute(
                    "INSERT OR IGNORE INTO projects VALUES(?,?,?,?)",
                    (
                        project_id,
                        room["room_hash"],
                        room["created_at"],
                        room["last_posted_at"],
                    ),
                )
                self.db.execute(
                    "UPDATE messages SET project_id=? WHERE room_hash=? AND project_id IS NULL",
                    (project_id, room["room_hash"]),
                )

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self._lock:
            self.db.close()

    def _purge_expired(self, now):
        cutoff = now - self.idle_ttl_seconds
        expired = self.db.execute(
            "SELECT project_id FROM projects WHERE last_activity<=?", (cutoff,)
        ).fetchall()
        for row in expired:
            room_hash = self.db.execute(
                "SELECT room_hash FROM projects WHERE project_id=?", (row[0],)
            ).fetchone()[0]
            self.db.execute("DELETE FROM messages WHERE project_id=?", (row[0],))
            self.db.execute("DELETE FROM participants WHERE project_id=?", (row[0],))
            self.db.execute("DELETE FROM projects WHERE project_id=?", (row[0],))
            self.db.execute("DELETE FROM rooms WHERE room_hash=?", (room_hash,))

    def _project(self, project_id, now=None):
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise ValueError(
                "project must be 1-64 lowercase letters, digits, underscores, or hyphens"
            )
        now = self.clock() if now is None else now
        row = self.db.execute(
            "SELECT * FROM projects WHERE project_id=? AND last_activity>?",
            (project_id, now - self.idle_ttl_seconds),
        ).fetchone()
        if row is None:
            raise KeyError
        return row

    @staticmethod
    def _validate_membership(project_id: str, agent_id: str):
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise ValueError("invalid project name")
        if not isinstance(agent_id, str) or not _AGENT.fullmatch(agent_id):
            raise ValueError("invalid agent name")

    def _active_projects(self, now):
        rows = self.db.execute(
            "SELECT project_id,last_activity FROM projects WHERE last_activity>? ORDER BY project_id",
            (now - self.idle_ttl_seconds,),
        ).fetchall()
        return [
            {
                "project": row["project_id"],
                "last_activity": self._timestamp(row["last_activity"]),
            }
            for row in rows
        ]

    def join(self, project_id: str, agent_id: str) -> None:
        """Join an existing project. Never creates one."""
        self._validate_membership(project_id, agent_id)
        now = self.clock()
        with self._transaction():
            self._purge_expired(now)
            row = self.db.execute(
                "SELECT project_id FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is None:
                raise KeyError
            self.db.execute(
                "INSERT OR IGNORE INTO participants VALUES(?,?,?)",
                (project_id, agent_id, now),
            )
            self.db.execute(
                "UPDATE projects SET last_activity=? WHERE project_id=?",
                (now, project_id),
            )

    def create(
        self, project_id: str, agent_id: str, *, allow_existing: bool = False
    ) -> bool:
        """Create a project unless another active project already exists.

        Returns True when the project was created and False when the caller
        joined one that already existed. Raises ProjectsExist instead of
        silently starting a competing project.
        """
        self._validate_membership(project_id, agent_id)
        now = self.clock()
        with self._transaction():
            self._purge_expired(now)
            row = self.db.execute(
                "SELECT project_id FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is None:
                others = self._active_projects(now)
                if others and not allow_existing:
                    raise ProjectsExist(others)
                count = self.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                if count >= self.max_active_projects:
                    raise ProjectCapacity
                room_hash = hashlib.sha256(
                    ("project:" + project_id).encode()
                ).hexdigest()
                self.db.execute(
                    "INSERT INTO rooms VALUES(?,?,?)", (room_hash, now, now)
                )
                self.db.execute(
                    "INSERT INTO projects VALUES(?,?,?,?)",
                    (project_id, room_hash, now, now),
                )
            self.db.execute(
                "INSERT OR IGNORE INTO participants VALUES(?,?,?)",
                (project_id, agent_id, now),
            )
        return row is None

    def projects(self) -> list[dict]:
        cutoff = self.clock() - self.idle_ttl_seconds
        with self._lock:
            rows = self.db.execute(
                "SELECT project_id,last_activity FROM projects WHERE last_activity>? ORDER BY project_id",
                (cutoff,),
            ).fetchall()
        return [
            {
                "project": row["project_id"],
                "last_activity": self._timestamp(row["last_activity"]),
            }
            for row in rows
        ]

    def participants(self, project_id: str) -> list[dict]:
        now = self.clock()
        with self._lock:
            self._project(project_id, now)
            rows = self.db.execute(
                "SELECT agent_id,joined_at FROM participants WHERE project_id=? ORDER BY agent_id",
                (project_id,),
            ).fetchall()
        return [
            {
                "agent_id": row["agent_id"],
                "joined_at": self._timestamp(row["joined_at"]),
            }
            for row in rows
        ]

    def post(self, project_id: str, payload: dict) -> tuple[dict, bool]:
        now = self.clock()
        with self._transaction():
            project = self._project(project_id, now)
            payload = dict(payload)
            payload.setdefault("complete", False)
            payload.setdefault("delivery", "follow_up")
            existing = self.db.execute(
                "SELECT * FROM messages WHERE project_id=? AND client_msg_id=?",
                (project_id, payload["client_msg_id"]),
            ).fetchone()
            if existing:
                result = self._message(existing)
                if any(result.get(key) != value for key, value in payload.items()):
                    raise Conflict
                return result, False
            if (
                payload["reply_to"] is not None
                and self.db.execute(
                    "SELECT 1 FROM messages WHERE project_id=? AND seq=?",
                    (project_id, payload["reply_to"]),
                ).fetchone()
                is None
            ):
                raise ValueError("reply_to must reference a message in this project")
            if (
                self.db.execute(
                    "SELECT COUNT(*) FROM messages WHERE project_id=?", (project_id,)
                ).fetchone()[0]
                >= self.max_messages_per_project
            ):
                raise MessageCapacity
            self.db.execute(
                "INSERT OR IGNORE INTO participants VALUES(?,?,?)",
                (project_id, payload["sender"], now),
            )
            cursor = self.db.execute(
                """INSERT INTO messages(room_hash,client_msg_id,sender,recipient,thread_id,reply_to,hop,text,complete,created_at,project_id,delivery)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project["room_hash"],
                    payload["client_msg_id"],
                    payload["sender"],
                    payload["recipient"],
                    payload["thread_id"],
                    payload["reply_to"],
                    payload["hop"],
                    payload["text"],
                    int(payload["complete"]),
                    now,
                    project_id,
                    payload["delivery"],
                ),
            )
            self.db.execute(
                "UPDATE projects SET last_activity=? WHERE project_id=?",
                (now, project_id),
            )
            self.db.execute(
                "UPDATE rooms SET last_posted_at=? WHERE room_hash=?",
                (now, project["room_hash"]),
            )
            row = self.db.execute(
                "SELECT * FROM messages WHERE seq=?", (cursor.lastrowid,)
            ).fetchone()
            return self._message(row), True

    def read(
        self, project_id: str, *, after: int, limit: int, before: int | None = None
    ) -> tuple[list[dict], int]:
        now = self.clock()
        with self._lock:
            self._project(project_id, now)
            if before is None:
                rows = self.db.execute(
                    "SELECT * FROM messages WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?",
                    (project_id, after, limit),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM (SELECT * FROM messages WHERE project_id=? AND seq<? ORDER BY seq DESC LIMIT ?) ORDER BY seq",
                    (project_id, before, limit),
                ).fetchall()
        messages = [self._message(row) for row in rows]
        return messages, messages[-1]["seq"] if messages else after

    @staticmethod
    def _timestamp(value):
        return (
            datetime.datetime.fromtimestamp(value, datetime.UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @classmethod
    def _message(cls, row):
        return {
            "seq": row["seq"],
            "client_msg_id": row["client_msg_id"],
            "sender": row["sender"],
            "recipient": row["recipient"],
            "thread_id": row["thread_id"],
            "reply_to": row["reply_to"],
            "hop": row["hop"],
            "text": row["text"],
            "complete": bool(row["complete"]),
            "delivery": row["delivery"],
            "created_at": cls._timestamp(row["created_at"]),
        }
