"""Pi 1.4+ RPC adapter with a no-tools session per thread."""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from bridge.adapters.base import AgentResult


class PiRpcError(RuntimeError):
    pass


class PiRpcAdapter:
    def __init__(self, session_dir: str | Path, executable: str | None = None):
        self.session_dir = Path(session_dir)
        self.executable = executable or shutil.which("pi") or "pi"

    def _command(self, session_id: str) -> list[str]:
        executable = self.executable
        if os.name == "nt" and executable.lower().endswith((".ps1", ".cmd", ".bat")):
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                raise PiRpcError("PowerShell is required to launch the configured Pi executable")
            return [shell, "-NoProfile", "-NonInteractive", "-File", executable,
                    "--mode", "rpc", "--session-dir", str(self.session_dir), "--session-id", session_id,
                    "--no-tools", "--no-mcp", "--no-extensions", "--no-context-files"]
        return [executable, "--mode", "rpc", "--session-dir", str(self.session_dir), "--session-id", session_id,
                "--no-tools", "--no-mcp", "--no-extensions", "--no-context-files"]

    def run(self, *, thread_id: str, session_ref: str | None, prompt: str, timeout: float, request_id: str | None = None) -> AgentResult:
        try:
            parsed_thread = uuid.UUID(thread_id)
        except ValueError:
            raise PiRpcError("thread_id must be a UUID") from None
        session_id = session_ref or str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-room:" + str(parsed_thread)))
        self.session_dir.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(self._command(session_id), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd=str(self.session_dir), bufsize=0)
        except OSError as error:
            raise PiRpcError(f"could not start Pi RPC: {type(error).__name__}") from None

        records: queue.Queue[bytes | None] = queue.Queue()
        def read_stdout():
            try:
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    records.put(line)
            finally:
                records.put(None)

        def drain_stderr():
            while process.stderr.readline():
                pass

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=drain_stderr, daemon=True).start()
        deadline = time.monotonic() + timeout

        def send(command):
            process.stdin.write(json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n")
            process.stdin.flush()

        def wait_response(request_id, *, settled=False):
            accepted = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PiRpcError("Pi RPC timed out")
                try:
                    line = records.get(timeout=remaining)
                except queue.Empty:
                    raise PiRpcError("Pi RPC timed out") from None
                if line is None:
                    raise PiRpcError("Pi RPC exited before completing the request")
                if len(line) > 1024 * 1024:
                    raise PiRpcError("Pi RPC record exceeded 1 MiB")
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise PiRpcError("Pi RPC returned malformed JSONL") from None
                if record.get("id") == request_id and record.get("type") == "response":
                    if not record.get("success"):
                        raise PiRpcError("Pi RPC command failed")
                    if not settled:
                        disposition = (record.get("data") or {}).get("disposition")
                        if disposition != "started":
                            raise PiRpcError("Pi RPC did not start a model run")
                        accepted = True
                if settled and record.get("type") == "agent_settled":
                    if record.get("aborted"):
                        raise PiRpcError("Pi RPC run was aborted")
                    return
                if not settled and accepted:
                    return

        try:
            send({"id": "agent-room-prompt", "type": "prompt", "message": prompt})
            wait_response("agent-room-prompt")
            wait_response("agent-room-prompt", settled=True)
            send({"id": "agent-room-result", "type": "get_last_assistant_text"})
            # Reuse the normal record reader until the correlated result arrives.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PiRpcError("Pi RPC timed out retrieving the result")
                try:
                    line = records.get(timeout=remaining)
                except queue.Empty:
                    raise PiRpcError("Pi RPC timed out retrieving the result") from None
                if line is None:
                    raise PiRpcError("Pi RPC exited before returning the result")
                if len(line) > 1024 * 1024:
                    raise PiRpcError("Pi RPC record exceeded 1 MiB")
                record = json.loads(line)
                if record.get("id") == "agent-room-result" and record.get("type") == "response":
                    if not record.get("success"):
                        raise PiRpcError("Pi RPC result retrieval failed")
                    text = (record.get("data") or {}).get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise PiRpcError("Pi RPC returned no assistant text")
                    return AgentResult(text=text, session_ref=session_id)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
