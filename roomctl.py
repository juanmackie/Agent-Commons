"""Small CLI for joining a project and sending or reading relay messages."""

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from app.client import RelayClient, RelayError, validate_agent_id, validate_api_url


def config_path() -> Path:
    return (
        Path(os.getenv("LOCALAPPDATA", Path.home())) / "agent-commons" / "config.json"
    )


def read_config():
    return json.loads(config_path().read_text(encoding="utf-8"))


@contextmanager
def config_lock():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock_file:
        if os.name == "nt":
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_config(config):
    path = config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config), encoding="utf-8")
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def config_identity(config):
    return config.get("api"), config.get("project"), config.get("agent_id")


def write_config(config):
    path = config_path()
    with config_lock():
        current = read_config() if path.exists() else {}
        if config_identity(current) == config_identity(config) and "cursor" in current:
            config = config | {"cursor": max(current["cursor"], config.get("cursor", 0))}
        _write_config(config)


def save_wait_cursor(config, cursor):
    path = config_path()
    with config_lock():
        current = read_config() if path.exists() else {}
        if config_identity(current) != config_identity(config):
            raise RelayError("relay setup changed while waiting; cursor was not saved")
        _write_config(current | {"cursor": max(current.get("cursor", 0), cursor)})


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


def resolve_reply_target(client, seq):
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or seq >= (1 << 63) - 1:
        raise ValueError("reply sequence must be a positive SQLite sequence number")
    result = client.read_before(seq + 1, limit=1)
    messages = result.get("messages") if isinstance(result, dict) else None
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(messages[0], dict)
        or messages[0].get("seq") != seq
    ):
        raise RelayError(f"reply target sequence {seq} is pruned or missing; choose a retained message")
    target = messages[0]
    try:
        validate_agent_id(target["sender"])
        if str(uuid.UUID(target["thread_id"])) != target["thread_id"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RelayError("relay returned an invalid reply target") from None
    if (
        not isinstance(target.get("hop"), int)
        or isinstance(target.get("hop"), bool)
        or not 0 <= target["hop"] <= 8
        or not isinstance(target.get("complete"), bool)
    ):
        raise RelayError("relay returned an invalid reply target")
    if target["complete"]:
        raise RelayError("reply thread is closed; start a new conversation")
    hop = target["hop"] + 1
    if hop >= 8:
        raise RelayError("reply thread reached hop 8; start a new conversation")
    cursor = 0
    while True:
        page = client.read(cursor)
        if not isinstance(page, dict) or not isinstance(page.get("messages"), list):
            raise RelayError("relay returned an invalid reply history page")
        for message in page["messages"]:
            if not isinstance(message, dict):
                raise RelayError("relay returned an invalid reply history message")
            if message.get("thread_id") == target["thread_id"] and message.get("complete") is True:
                raise RelayError("reply thread is closed; start a new conversation")
        next_after = page.get("next_after", cursor)
        if not page["messages"] or not isinstance(next_after, int) or isinstance(next_after, bool) or next_after <= cursor:
            break
        cursor = next_after
    return target


def configure_utf8_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def doctor_checks(client, config, *, now=None):
    checks = []
    try:
        health = client.healthz()
    except RelayError as error:
        status = f"HTTP {error.status}" if error.status is not None else type(error).__name__
        return [{"name": "relay", "ok": False, "detail": f"health check failed ({status})"}]
    checks.append({
        "name": "relay",
        "ok": isinstance(health, dict) and health.get("status") == "ok",
        "detail": "healthy" if isinstance(health, dict) and health.get("status") == "ok" else "health endpoint did not report ok",
    })
    if not checks[-1]["ok"]:
        return checks

    project = config.get("project")
    if not project:
        checks.append({"name": "project", "ok": False, "detail": "join a project first"})
        return checks
    try:
        result = client.participants()
    except RelayError as error:
        status = f"HTTP {error.status}" if error.status is not None else type(error).__name__
        checks.append({"name": "participant", "ok": False, "detail": f"lookup failed ({status})"})
        return checks
    participants = result.get("participants") if isinstance(result, dict) else None
    if not isinstance(participants, list):
        checks.append({"name": "participant", "ok": False, "detail": "relay returned an invalid participant list"})
        return checks
    agent_id = config.get("agent_id")
    participant = next(
        (item for item in participants if isinstance(item, dict) and item.get("agent_id") == agent_id),
        None,
    )
    if participant is None:
        checks.append({"name": "participant", "ok": False, "detail": "agent has not joined this project"})
        return checks
    checks.append({"name": "participant", "ok": True, "detail": "joined"})

    last_seen = participant.get("last_seen")
    try:
        seen_at = datetime.fromisoformat(last_seen)
        if seen_at.tzinfo is None:
            raise ValueError("missing timezone")
    except (AttributeError, TypeError, ValueError):
        checks.append({"name": "listener", "ok": False, "detail": "no valid listener poll has been recorded"})
        return checks
    current_time = time.time() if now is None else now
    age = max(0.0, current_time - seen_at.timestamp())
    fresh = age < 120
    age_display = math.ceil(age)
    checks.append({
        "name": "listener",
        "ok": fresh,
        "detail": f"last polled {age_display}s ago" if fresh else f"stale; last polled {age_display}s ago",
    })
    return checks


def main(argv=None):
    configure_utf8_output()
    parser = argparse.ArgumentParser(prog="roomctl")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "setup", help="save this agent's relay address and name before choosing a project"
    )
    setup.add_argument("--api", required=True)
    setup.add_argument("--agent", default=os.getenv("AGENT_ID"))
    setup.add_argument("--replace", action="store_true")
    join = commands.add_parser(
        "join", help="join a project using the saved relay setup"
    )
    join.add_argument("--api")
    join.add_argument("--project")
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
    send.add_argument("--to")
    send.add_argument("--thread", default=None)
    send.add_argument("--reply-to", type=int, default=None)
    send.add_argument("--text", required=True)
    send.add_argument("--complete", action="store_true")
    read = commands.add_parser("read")
    read.add_argument("--after", type=int, default=0)
    wait = commands.add_parser("wait", help="wait for a message addressed to this agent")
    wait.add_argument("--timeout", type=float)
    commands.add_parser("projects")
    commands.add_parser("participants")
    commands.add_parser("status")
    commands.add_parser("doctor", help="check relay, project membership, and listener health without changing state")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            api = validate_api_url(args.api)
            existing = read_config() if config_path().exists() else None
            agent = args.agent or (
                existing.get("agent_id")
                if existing and existing.get("api") == api
                else None
            ) or f"agent-{uuid.uuid4().hex[:8]}"
            agent = validate_agent_id(agent)
            if RelayClient(api, None, agent).healthz().get("status") != "ok":
                raise RelayError(f"relay at {api} is not healthy; check the URL and tailnet access")
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
                if "cursor" in existing:
                    config["cursor"] = existing["cursor"]
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
            ):
                raise RelayError("local config already exists; back it up, then use --replace")
            if replacing:
                ensure_config_replace_allowed(True)
            client = RelayClient(api, project, agent)
            if (
                project is not None
                and existing
                and not replacing
                and existing.get("project")
                and existing["project"] != project
            ):
                raise RelayError("local config already exists; back it up, then use --replace")
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
                projects = [
                    entry.get("project") for entry in client.projects().get("projects", [])
                ]
                create = False
                if project is None:
                    if len(projects) == 1:
                        project = projects[0]
                    elif not projects:
                        project = "agent-commons"
                        create = True
                    else:
                        raise RelayError(
                            "several projects exist; choose one with --project: "
                            + ", ".join(projects)
                        )
                if (
                    existing
                    and not replacing
                    and existing.get("project")
                    and existing["project"] != project
                ):
                    raise RelayError("local config already exists; back it up, then use --replace")
                try:
                    if create:
                        result = client.create(project)
                    else:
                        result = client.join(project)
                except RelayError as error:
                    if create and error.payload.get("code") == "projects_exist":
                        projects = [
                            entry.get("project")
                            for entry in client.projects().get("projects", [])
                        ]
                        if len(projects) == 1:
                            project = projects[0]
                            if (
                                existing
                                and not replacing
                                and existing.get("project")
                                and existing["project"] != project
                            ):
                                raise RelayError(
                                    "local config already exists; back it up, then use --replace"
                                )
                            result = client.join(project)
                        else:
                            raise RelayError(
                                "project creation raced with another project; choose one "
                                "with --project: "
                                + ", ".join(projects or ["(none listed)"])
                            ) from None
                    elif error.payload.get("code") == "project_missing":
                        projects = [
                            entry.get("project")
                            for entry in error.payload.get("projects", [])
                        ]
                        raise RelayError(
                            f"project '{project}' does not exist. Existing projects: "
                            f"{', '.join(projects) or '(none)'}."
                        ) from None
                    else:
                        raise
            saved = {"api": api, "project": project, "agent_id": agent}
            if config_identity(existing) == config_identity(saved) and "cursor" in existing:
                saved["cursor"] = existing["cursor"]
            write_config(saved)
            print(json.dumps(result | {"config": str(config_path())}, ensure_ascii=False))
            return 0

        config = read_config()
        client = RelayClient(config["api"], config.get("project"), config["agent_id"])
        if args.command == "send":
            requested_thread = str(uuid.UUID(args.thread)) if args.thread else None
            db = state_db()
            existing = db.execute(
                "SELECT client_msg_id,payload FROM pending LIMIT 1"
            ).fetchone()
            if existing:
                old = json.loads(existing[1])
                matches = (
                    isinstance(old, dict)
                    and old.get("client_msg_id") == existing[0]
                    and old.get("sender") == client.agent_id
                    and (args.to is None or old.get("recipient") == args.to)
                    and (args.to is not None or args.reply_to is not None)
                    and old.get("reply_to") == args.reply_to
                    and old.get("complete") is args.complete
                    and old.get("text") == args.text
                    and (requested_thread is None or old.get("thread_id") == requested_thread)
                    and (args.reply_to is not None or old.get("hop") == 0)
                )
                if not matches:
                    raise RelayError(
                        "a previous send is unresolved; retry it exactly before sending another"
                    )
                payload = old
            else:
                reply_target = resolve_reply_target(client, args.reply_to) if args.reply_to is not None else None
                recipient = args.to or (reply_target["sender"] if reply_target else None)
                if not recipient:
                    raise ValueError("--to is required unless --reply-to identifies the recipient")
                validate_agent_id(recipient)
                if reply_target:
                    if requested_thread is not None and requested_thread != reply_target["thread_id"]:
                        raise ValueError("--thread must match the reply target's thread")
                    requested_thread = reply_target["thread_id"]
                    reply_hop = reply_target["hop"] + 1
                else:
                    reply_hop = 0
                payload = {
                    "client_msg_id": str(uuid.uuid4()),
                    "sender": client.agent_id,
                    "recipient": recipient,
                    "thread_id": requested_thread or str(uuid.uuid4()),
                    "reply_to": args.reply_to,
                    "hop": reply_hop,
                    "text": args.text,
                    "complete": args.complete,
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
        elif args.command == "wait":
            if config.get("project") is None:
                raise RelayError("create or join a project first")
            if args.timeout is not None and (
                not math.isfinite(args.timeout) or args.timeout < 0
            ):
                raise ValueError("timeout must be a finite non-negative number")
            cursor = config.get("cursor", 0)
            deadline = None if args.timeout is None else time.monotonic() + args.timeout
            errors = 0
            last = {"messages": [], "next_after": cursor}
            while deadline is None or time.monotonic() < deadline:
                try:
                    last = client.read(cursor, listener=client.agent_id)
                    errors = 0
                except RelayError as error:
                    if error.status is not None and 400 <= error.status < 500 and error.status != 429:
                        if error.status == 404:
                            raise RelayError("project may be expired or missing; rejoin the project") from None
                        raise
                    errors += 1
                    delay = min(3 * (2 ** min(errors - 1, 5)), 60)
                else:
                    cursor = last["next_after"]
                    incoming = [
                        message
                        for message in last["messages"]
                        if message["recipient"] == client.agent_id
                        and message["sender"] != client.agent_id
                    ]
                    if incoming:
                        last = {"messages": incoming, "next_after": cursor}
                        print(json.dumps(last, ensure_ascii=False), flush=True)
                        save_wait_cursor(config, cursor)
                        return 0
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                delay = 3 if errors == 0 else delay
                time.sleep(delay if remaining is None else min(delay, remaining))
            last = {"messages": [], "next_after": cursor}
            print(json.dumps(last, ensure_ascii=False), flush=True)
            save_wait_cursor(config, cursor)
            return 0
        elif args.command == "participants":
            print(json.dumps(client.participants(), ensure_ascii=False))
        elif args.command == "projects":
            print(json.dumps(client.projects(), ensure_ascii=False))
        elif args.command == "doctor":
            checks = doctor_checks(client, config)
            report = {
                "ok": all(check["ok"] for check in checks),
                "agent_id": config.get("agent_id"),
                "project": config.get("project"),
                "checks": checks,
            }
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["ok"] else 1
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
