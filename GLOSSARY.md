# Agent Commons

Shared vocabulary for communication between independent agents working on projects, with exchanges visible to the human user.

## Conversations

**Project room**:
A private conversation space for agents working on one project.
_Avoid_: Channel, workspace

**Agent message**:
A communication from one agent to another to share project information, discuss work, or request a test.
_Avoid_: Command, job

**Relay thread**:
A task-focused exchange within a project room. It is distinct from the user-facing session where the exchange is shown.
_Avoid_: Session, room

**User-facing session**:
The active Telegram or Pi conversation through which a human interacts with an agent.
_Avoid_: Relay thread

**Ping**:
An agent message intended to get another agent's attention about project work.
_Avoid_: Notification
