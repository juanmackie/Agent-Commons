import { randomUUID } from "node:crypto";

const MAX_APPROVAL_DETAILS = 8 * 1024;
const MAX_TEXT_BYTES = 16 * 1024;
const MAX_HOP = 8;
const LABEL = /^[A-Za-z0-9_-]{1,64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SECRET_PATTERNS = [
  /\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|authorization|private[_-]?key)\b\s*[:=]\s*["']?[^\s"',}]{4,}/i,
  /\bBearer\s+[A-Za-z0-9._~+\/-]{12,}/i,
  /\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b/,
];
const ROOMCTL_SEND = /\broomctl(?:\.exe)?\b(?:(?![;&|]).){0,100}\b(?:send|post)\b/i;

export function shouldGateToolCall(toolName, peerActive, input) {
  return peerActive || toolName === "agent_commons_send" || isRoomctlSend(toolName, input);
}

export function requiresHumanPermission(annotations) {
  return annotations?.readOnlyHint !== true;
}

function* stringsIn(value) {
  if (typeof value === "string") {
    yield value;
  } else if (Array.isArray(value)) {
    for (const item of value) yield* stringsIn(item);
  } else if (value && typeof value === "object") {
    for (const item of Object.values(value)) yield* stringsIn(item);
  }
}

export function isRoomctlSend(toolName, input) {
  if (!["bash", "terminal", "powershell"].includes(toolName) || !input || typeof input !== "object") return false;
  return [...stringsIn(input)].some((value) => ROOMCTL_SEND.test(value));
}

export function resolveOutboundEnvelope(input, peer, createThreadId = randomUUID, closedThreads = []) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new TypeError("message arguments must be an object");
  const text = input.text;
  if (typeof text !== "string" || !text.trim() || Buffer.byteLength(text, "utf8") > MAX_TEXT_BYTES) {
    throw new TypeError("message text must be non-empty and at most 16 KiB UTF-8");
  }
  const recipient = input.recipient ?? peer?.sender;
  if (typeof recipient !== "string" || !LABEL.test(recipient)) throw new TypeError("recipient is required and must be a valid agent ID");
  const thread_id = input.thread_id ?? peer?.thread_id ?? createThreadId();
  if (typeof thread_id !== "string" || !UUID.test(thread_id)) throw new TypeError("thread ID must be a UUID");
  if (closedThreads.includes(thread_id)) throw new TypeError("thread is closed; start a new thread");
  const client_msg_id = input.client_msg_id ?? createThreadId();
  if (typeof client_msg_id !== "string" || !UUID.test(client_msg_id)) throw new TypeError("client message ID must be a UUID");
  const complete = input.complete ?? false;
  if (typeof complete !== "boolean") throw new TypeError("complete must be a boolean");

  let reply_to = null;
  let hop = 0;
  if (peer && thread_id === peer.thread_id) {
    if (peer.complete) throw new TypeError("peer thread is closed; start a new thread");
    if (!Number.isSafeInteger(peer.hop) || peer.hop >= MAX_HOP) throw new TypeError("peer thread reached the hop limit");
    if (recipient !== peer.sender) throw new TypeError("a reply must target the active peer");
    reply_to = peer.seq;
    hop = peer.hop + 1;
  }
  if (input.reply_to !== undefined && input.reply_to !== reply_to) throw new TypeError("reply_to does not match the active thread");
  if (input.hop !== undefined && input.hop !== hop) throw new TypeError("hop does not match the active thread");
  return { recipient, thread_id, reply_to, hop, complete, client_msg_id, text };
}

function containsSensitiveValue(text) {
  return SECRET_PATTERNS.some((pattern) => pattern.test(text));
}

export async function gatePeerToolCall(event, tool, ctx, activePeer = null, closedThreads = []) {
  const outboundSend = tool?.name === "agent_commons_send";
  const shellSend = isRoomctlSend(event.toolName, event.input);
  if (!outboundSend && !shellSend && !requiresHumanPermission(tool?.annotations)) return undefined;

  if (outboundSend) {
    try {
      const envelope = resolveOutboundEnvelope(event.input, activePeer, randomUUID, closedThreads);
      Object.assign(event.input, envelope);
    } catch (error) {
      return { block: true, reason: `Agent Commons message blocked: ${error.message}. Fix the exact envelope before requesting approval.` };
    }
  }

  let args;
  try {
    args = JSON.stringify(event.input);
  } catch {
    return { block: true, reason: "Peer-requested action blocked: its exact target could not be displayed for human approval." };
  }
  if (Buffer.byteLength(args ?? "", "utf8") > MAX_APPROVAL_DETAILS) {
    return { block: true, reason: "Peer-requested action blocked: its details exceed the safe approval display limit." };
  }
  if (containsSensitiveValue(args ?? "")) {
    return { block: true, reason: "Peer-requested action blocked: its exact details may contain a secret and cannot be shown safely." };
  }
  if (!ctx.hasUI) {
    return { block: true, reason: "Peer-requested action blocked: no human approval interface is available." };
  }

  const details = [
    "Review this exact operation and target before allowing it.",
    `Action: ${event.toolName}`,
    `Target and arguments: ${args}`,
  ].join("\n");
  const approved = await ctx.ui.confirm(outboundSend ? "Agent Commons message approval" : "Peer-requested action", details);
  if (approved) return undefined;
  return { block: true, reason: "Peer-requested action was not approved by the human." };
}
