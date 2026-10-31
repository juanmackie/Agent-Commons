import io
import json
import queue
import uuid

from bridge.adapters.pi_rpc import PiRpcAdapter


class FakeStream:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False

    def readline(self):
        if self.closed:
            return b""
        line = self.lines.get()
        if line is None:
            self.closed = True
            return b""
        return line


class FakeStdin:
    def __init__(self, proc):
        self.proc = proc

    def write(self, payload):
        command = json.loads(payload)
        if command["type"] == "prompt":
            self.proc.stdout.lines.put(json.dumps({"id":command["id"],"type":"response","success":True,"data":{"disposition":"started"}}).encode()+b"\n")
            self.proc.stdout.lines.put(b'{"type":"agent_settled","aborted":false}\n')
        else:
            self.proc.stdout.lines.put(json.dumps({"id":command["id"],"type":"response","success":True,"data":{"text":"final answer"}}).encode()+b"\n")
        return len(payload)

    def flush(self):
        pass

    def close(self):
        self.proc.stdout.lines.put(None)


class FakeProcess:
    def __init__(self):
        self.stdout = FakeStream()
        self.stderr = io.BytesIO()
        self.stdin = FakeStdin(self)

    def wait(self, timeout=None):
        return 0


def test_pi_rpc_waits_for_settled_and_reads_full_result(monkeypatch):
    calls = []
    def start(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()
    monkeypatch.setattr("bridge.adapters.pi_rpc.subprocess.Popen", start)
    thread = str(uuid.uuid4())
    result = PiRpcAdapter(".", executable="pi").run(thread_id=thread, session_ref=None, prompt="safe request", timeout=2)
    assert result.text == "final answer"
    assert result.session_ref
    args = calls[0][0]
    assert "--mode" in args and "rpc" in args
    assert "--no-tools" in args and "--no-mcp" in args and "--no-extensions" in args
    assert args[args.index("--session-id") + 1] == result.session_ref
