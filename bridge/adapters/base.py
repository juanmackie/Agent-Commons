from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AgentResult:
    text: str
    session_ref: str | None = None


class AgentAdapter(Protocol):
    def run(self, *, thread_id: str, session_ref: str | None, prompt: str, timeout: float, request_id: str) -> AgentResult: ...
