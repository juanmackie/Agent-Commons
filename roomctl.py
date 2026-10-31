"""Small CLI for creating rooms and initiating/read messages."""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from bridge.client import RelayClient, RelayError


def config_path() -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home())) / "agent-room-relay"
    return root / "config.json"


def read_config():
    path = config_path()
    return json.loads(path.read_text(encoding="utf-8"))


def write_config(config):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config), encoding="utf-8")
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def state_db():
    path = config_path().parent / "roomctl.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS pending(client_msg_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    return db


def main(argv=None):
    parser = argparse.ArgumentParser(prog="roomctl")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--api", required=True)
    send = commands.add_parser("send")
    send.add_argument("--to", required=True)
    send.add_argument("--thread", required=True)
    send.add_argument("--text", required=True)
    read = commands.add_parser("read")
    read.add_argument("--after", type=int, default=0)
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            created = RelayClient.create_room(args.api)
            write_config({"api": args.api.rstrip("/"), "room_id": created["room_id"], "agent_id": os.getenv("AGENT_ID", "hermes")})
            print(json.dumps({"idle_ttl_seconds": created["idle_ttl_seconds"], "config": str(config_path())}))
            return 0
        config = read_config()
        client = RelayClient(config["api"], config["room_id"])
        agent_id = config.get("agent_id", "hermes")
        if args.command == "send":
            payload = {"client_msg_id": str(uuid.uuid4()), "sender": agent_id, "recipient": args.to,
                       "thread_id": str(uuid.UUID(args.thread)), "reply_to": None, "hop": 0, "text": args.text}
            db = state_db()
            existing = db.execute("SELECT client_msg_id,payload FROM pending LIMIT 1").fetchone()
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if existing:
                old = json.loads(existing[1])
                if any(old[key] != payload[key] for key in ("sender", "recipient", "thread_id", "text")):
                    raise RelayError("a previous send is unresolved; repeat it exactly before sending another")
                payload = old
            else:
                db.execute("INSERT INTO pending VALUES (?,?)", (payload["client_msg_id"], serialized))
                db.commit()
            response = client.post(payload)
            db.execute("DELETE FROM pending WHERE client_msg_id=?", (payload["client_msg_id"],))
            db.commit()
            print(json.dumps(response, ensure_ascii=False))
        elif args.command == "read":
            print(json.dumps(client.read(args.after), ensure_ascii=False))
        else:
            print(json.dumps({"agent_id": agent_id, "api": config["api"], "config_path": str(config_path())}))
        return 0
    except (OSError, ValueError, KeyError, RelayError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
