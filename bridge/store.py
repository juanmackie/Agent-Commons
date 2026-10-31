"""Durable single-worker inbox, cursor, and idempotent outbox."""

import contextlib
import json
import sqlite3
from pathlib import Path


class BridgeStore:
    def __init__(self, path):
        self.db_path = str(path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
          INSERT OR IGNORE INTO state VALUES('cursor', 0);
          CREATE TABLE IF NOT EXISTS inbox(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
          CREATE TABLE IF NOT EXISTS outbox(inbound_seq INTEGER PRIMARY KEY REFERENCES inbox(seq), payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
          CREATE TABLE IF NOT EXISTS thread_sessions(thread_id TEXT PRIMARY KEY, session_ref TEXT, reply_count INTEGER NOT NULL DEFAULT 0);
        """)

    @contextlib.contextmanager
    def _write_transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @property
    def cursor(self):
        return self.db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0]

    def ingest(self, messages, next_after):
        """Persist each relevant item (or its safe ignore) before advancing cursor."""
        with self._write_transaction():
            cursor = self.cursor
            for message in messages:
                seq = message["seq"]
                if seq <= cursor:
                    continue
                self.db.execute("INSERT OR IGNORE INTO inbox(seq,payload,state) VALUES(?,?,?)", (seq, json.dumps(message, ensure_ascii=False), "pending"))
                self.db.execute("UPDATE state SET value=? WHERE key='cursor'", (seq,))
                cursor = seq
            if next_after > cursor:
                # Sequence gaps can be caused by messages for other rooms; the relay's
                # global sequence is not contiguous within a room, so record observed cursor.
                self.db.execute("UPDATE state SET value=? WHERE key='cursor'", (next_after,))
        return self.cursor

    def status(self):
        return {
            "cursor": self.cursor,
            "pending_count": self.db.execute("SELECT COUNT(*) FROM inbox WHERE state IN ('pending','publishing')").fetchone()[0],
            "recent_errors": [{"seq": row[0], "attempts": row[1], "error_category": row[2]} for row in self.db.execute("SELECT seq,attempts,error FROM inbox WHERE error IS NOT NULL ORDER BY seq DESC LIMIT 20")],
        }

    def pending(self):
        row = self.db.execute("SELECT i.seq,i.payload,o.payload AS outgoing FROM inbox i LEFT JOIN outbox o ON o.inbound_seq=i.seq WHERE i.state IN ('pending','publishing') ORDER BY i.seq LIMIT 1").fetchone()
        if row is None:
            return None
        return row["seq"], json.loads(row["payload"]), json.loads(row["outgoing"]) if row["outgoing"] else None

    def attempt(self, seq):
        self.db.execute("UPDATE inbox SET attempts=attempts+1 WHERE seq=?", (seq,))

    def defer(self, seq, error):
        self.db.execute("UPDATE inbox SET state='pending',error=? WHERE seq=?", (error[:200], seq))

    def save_reply(self, seq, payload):
        with self._write_transaction():
            self.db.execute("INSERT OR IGNORE INTO outbox(inbound_seq,payload,state) VALUES(?,?,'pending')", (seq, json.dumps(payload, ensure_ascii=False)))
            stored = self.db.execute("SELECT payload FROM outbox WHERE inbound_seq=?", (seq,)).fetchone()[0]
            if json.loads(stored) != payload:
                raise ValueError("outbox payload is immutable")
            self.db.execute("UPDATE inbox SET state='publishing' WHERE seq=?", (seq,))

    def mark_done(self, seq, *, replied_thread=None):
        with self._write_transaction():
            self.db.execute("UPDATE inbox SET state='done',error=NULL WHERE seq=?", (seq,))
            self.db.execute("UPDATE outbox SET state='sent' WHERE inbound_seq=?", (seq,))
            if replied_thread is not None:
                self.db.execute("INSERT INTO thread_sessions(thread_id,reply_count) VALUES(?,1) ON CONFLICT(thread_id) DO UPDATE SET reply_count=reply_count+1", (replied_thread,))

    def thread_reply_count(self, thread_id):
        row = self.db.execute("SELECT reply_count FROM thread_sessions WHERE thread_id=?", (thread_id,)).fetchone()
        return 0 if row is None else row[0]

    def thread_session_ref(self, thread_id):
        row = self.db.execute("SELECT session_ref FROM thread_sessions WHERE thread_id=?", (thread_id,)).fetchone()
        return None if row is None else row[0]

    def set_thread_session_ref(self, thread_id, session_ref):
        with self._write_transaction():
            self.db.execute("INSERT INTO thread_sessions(thread_id,session_ref,reply_count) VALUES(?,?,0) ON CONFLICT(thread_id) DO UPDATE SET session_ref=excluded.session_ref", (thread_id, session_ref))
