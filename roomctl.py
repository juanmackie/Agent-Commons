"""Small CLI for joining a project and sending or reading relay messages."""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from app.client import RelayClient, RelayError, validate_agent_id, validate_api_url


def config_path() -> Path:
    return (
        Path(os.getenv("LOCALAPPDATA", Path.home())) / "agent-commons" / "config.json"
    )


def read_config():
    return json.loads(config_path().read_text(encoding="utf-8"))


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
    db.execute(
        "CREATE TABLE IF NOT EXISTS pending(client_msg_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return db


def ensure_config_replace_allowed(replace: bool):
    path = config_path()
    if not path.exists():
        return
    if not replace:
        raise RelayError("local config already exists; back it up, then use --replace")
    pending_path = path.parent / "roomctl.sqlite3"
    if pending_path.exists():
        with sqlite3.connect(pending_path) as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending'"
            ).fetchone()
            if table and db.execute("SELECT 1 FROM pending LIMIT 1").fetchone():
                raise RelayError(
                    "a send is unresolved; retry it before replacing the project config"
                )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="roomctl")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "setup", help="save this agent's relay address and name before choosing a project"
    )
    setup.add_argument("--api", required=True)
    setup.add_argument("--agent", default=os.getenv("AGENT_ID", "agent"))
    setup.add_argument("--replace", action="store_true")
    join = commands.add_parser(
        "join", help="join a project using the saved relay setup"
    )
    join.add_argument("--api")
    join.add_argument("--project", required=True)
    join.add_argument("--agent")
    join.add_argument("--replace", action="store_true")
    create_project = commands.add_parser(
        "create-project", help="create a project and join it as the first agent"
    )
    create_project.add_argument("--project", required=True)
    create_project.add_argument("--replace", action="store_true")
    create_project.add_argument(
        "--additional",
        action="store_true",
        help="create this project even though another active project already exists",
    )
    send = commands.add_parser("send")
    send.add_argument("--to", required=True)
    send.add_argument("--thread", default=None)
    send.add_argument("--reply-to", type=int, default=None)
    send.add_argument("--text", required=True)
    read = commands.add_parser("read")
    read.add_argument("--after", type=int, default=0)
    commands.add_parser("projects")
    commands.add_parser("participants")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            api = validate_api_url(args.api)
            agent = validate_agent_id(args.agent)
            if RelayClient(api, None, agent).healthz().get("status") != "ok":
                raise RelayError(f"relay at {api} is not healthy; check the URL and tailnet access")
            existing = read_config() if config_path().exists() else None
            if existing and not args.replace:
                if existing.get("api") == api and existing.get("agent_id") == agent:
                    print(json.dumps({"api": api, "agent_id": agent, "project": existing.get("project"), "config": str(config_path())}))
                    return 0
                raise RelayError("local config already exists; back it up, then use --replace")
            if args.replace:
                ensure_config_replace_allowed(True)
            config = {"api": api, "agent_id": agent}
            if existing and existing.get("api") == api and existing.get("agent_id") == agent:
                if existing.get("project"):
                    config["project"] = existing["project"]
            write_config(config)
            print(json.dumps(config | {"config": str(config_path())}, ensure_ascii=False))
            return 0

        if args.command in {"join", "create-project"}:
            existing = read_config() if config_path().exists() else {}
            project = args.project
            api = validate_api_url(args.api) if args.command == "join" and args.api else existing.get("api")
            if not api:
                raise RelayError("run `roomctl setup --api <relay-url> --agent <name>` first")
            agent = (
                args.agent or existing.get("agent_id") or os.getenv("AGENT_ID", "agent")
                if args.command == "join"
                else existing.get("agent_id")
            )
            if not agent:
                raise RelayError("run `roomctl setup --api <relay-url> --agent <name>` first")
            agent = validate_agent_id(agent)
            replacing = args.replace
            if existing and not replacing and (
                existing.get("api") != api
                or existing.get("agent_id") != agent
                or (existing.get("project") and existing["project"] != project)
            ):
                raise RelayError("local config already exists; back it up, then use --replace")
            if replacing:
                ensure_config_replace_allowed(True)
            client = RelayClient(api, project, agent)
            if args.command == "create-project":
                try:
                    result = client.create(allow_existing=args.additional)
                except RelayError as error:
                    if error.payload.get("code") != "projects_exist":
                        raise
                    existing = [
                        entry.get("project") for entry in error.payload.get("projects", [])
                    ]
                    raise RelayError(
                        "a project already exists; join it instead of creating a duplicate. "
                        f"Existing projects: {', '.join(existing) or '(none listed)'}. "
                        "Run `roomctl join --project <name>`, or pass --additional to create "
                        "another project deliberately."
                    ) from None
                if not result.get("created"):
                    print(
                        "project already existed; joined it instead of creating a duplicate",
                        file=sys.stderr,
                    )
            else:
                try:
                    result = client.join()
                except RelayError as error:
                    if error.payload.get("code") != "project_missing":
                        raise
                    existing = [
                        entry.get("project") for entry in error.payload.get("projects", [])
                    ]
                    raise RelayError(
                        f"project '{project}' does not exist. "
                        f"Existing projects: {', '.join(existing) or '(none)'}. "
                        "Join one of those, or create this project with "
                        "`roomctl create-project --project <name>`."
                    ) from None
            write_config({"api": api, "project": project, "agent_id": agent})
            print(json.dumps(result | {"config": str(config_path())}, ensure_ascii=False))
            return 0

        config = read_config()
        client = RelayClient(config["api"], config.get("project"), config["agent_id"])
        if args.command == "send":
            db = state_db()
            existing = db.execute(
                "SELECT client_msg_id,payload FROM pending LIMIT 1"
            ).fetchone()
            requested_thread = str(uuid.UUID(args.thread)) if args.thread else None
            if existing:
                old = json.loads(existing[1])
                if any(
                    old[key] != value
                    for key, value in (
                        ("sender", client.agent_id),
                        ("recipient", args.to),
                        ("reply_to", args.reply_to),
                        ("text", args.text),
                    )
                ) or (
                    requested_thread is not None
                    and old["thread_id"] != requested_thread
                ):
                    raise RelayError(
                        "a previous send is unresolved; retry it exactly before sending another"
                    )
                payload = old
            else:
                payload = {
                    "client_msg_id": str(uuid.uuid4()),
                    "sender": client.agent_id,
                    "recipient": args.to,
                    "thread_id": requested_thread or str(uuid.uuid4()),
                    "reply_to": args.reply_to,
                    "hop": 0,
                    "text": args.text,
                    "complete": False,
                }
                db.execute(
                    "INSERT INTO pending VALUES (?,?)",
                    (
                        payload["client_msg_id"],
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    ),
                )
                db.commit()
            result = client.post(payload)
            db.execute(
                "DELETE FROM pending WHERE client_msg_id=?", (payload["client_msg_id"],)
            )
            db.commit()
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "read":
            print(json.dumps(client.read(args.after), ensure_ascii=False))
        elif args.command == "participants":
            print(json.dumps(client.participants(), ensure_ascii=False))
        elif args.command == "projects":
            print(json.dumps(client.projects(), ensure_ascii=False))
        else:
            print(
                json.dumps(
                    {
                        "agent_id": client.agent_id,
                        "project": client.project,
                        "api": client.base_url,
                        "config_path": str(config_path()),
                    }
                )
            )
        return 0
    except (OSError, ValueError, KeyError, RelayError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
