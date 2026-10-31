import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

const MAX_SEEN = 500;
const MAX_PENDING = 100;
const MAX_OUTBOX = 100;
const LABEL = /^[A-Za-z0-9_-]{1,64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function validateOutboundMessage(message) {
  return message && typeof message === "object" && !Array.isArray(message)
    && typeof message.recipient === "string" && LABEL.test(message.recipient)
    && typeof message.thread_id === "string" && UUID.test(message.thread_id)
    && typeof message.client_msg_id === "string" && UUID.test(message.client_msg_id)
    && (message.reply_to === null || (Number.isSafeInteger(message.reply_to) && message.reply_to >= 0))
    && Number.isInteger(message.hop) && message.hop >= 0 && message.hop <= 8
    && typeof message.text === "string" && message.text.trim().length > 0
    && Buffer.byteLength(message.text, "utf8") <= 16 * 1024
    && typeof message.complete === "boolean"
    && typeof message.target_session_id === "string" && message.target_session_id.length > 0;
}

function validateMessage(message) {
  return message && typeof message === "object" && !Array.isArray(message)
    && typeof message.sender === "string" && LABEL.test(message.sender)
    && typeof message.recipient === "string" && LABEL.test(message.recipient)
    && typeof message.thread_id === "string" && UUID.test(message.thread_id)
    && typeof message.client_msg_id === "string" && UUID.test(message.client_msg_id)
    && Number.isSafeInteger(message.seq) && message.seq >= 1
    && (message.reply_to === null || (Number.isSafeInteger(message.reply_to) && message.reply_to >= 0))
    && Number.isInteger(message.hop) && message.hop >= 0 && message.hop <= 8
    && typeof message.text === "string" && message.text.trim().length > 0
    && Buffer.byteLength(message.text, "utf8") <= 16 * 1024
    && typeof message.complete === "boolean"
    && typeof message.target_session_id === "string" && message.target_session_id.length > 0;
}

export async function loadState(file, expectedFingerprint = null) {
  try {
    const value = JSON.parse(await readFile(file, "utf8"));
    if (!Number.isSafeInteger(value.cursor) || value.cursor < 0 || !Array.isArray(value.seen)) throw new Error("invalid state");
    const peer = value.peerContext;
    if (peer !== undefined && peer !== null && (
      typeof peer.sender !== "string" || !LABEL.test(peer.sender) ||
      typeof peer.thread_id !== "string" || !UUID.test(peer.thread_id) ||
      !Number.isSafeInteger(peer.seq) || peer.seq < 1 ||
      (peer.client_msg_id != null && !UUID.test(peer.client_msg_id)) ||
      (peer.hop != null && (!Number.isInteger(peer.hop) || peer.hop < 0 || peer.hop > 8)) ||
      (peer.complete != null && typeof peer.complete !== "boolean")
    )) throw new Error("invalid peer context");
    if (value.sessionId != null && typeof value.sessionId !== "string") throw new Error("invalid session identity");
    const rawPendingInbox = value.pendingInbox ?? [];
    const pendingInbox = Array.isArray(rawPendingInbox) ? rawPendingInbox.map((message) => {
      if (message?.target_session_id == null) {
        if (!value.sessionId) throw new Error("pending message has no verified session owner");
        return { ...message, target_session_id: value.sessionId };
      }
      return message;
    }) : rawPendingInbox;
    const outbox = value.outbox ?? [];
    const closedThreads = value.closedThreads ?? [];
    if (!Array.isArray(closedThreads) || closedThreads.length > 10000
      || closedThreads.some((threadId) => typeof threadId !== "string" || !UUID.test(threadId))) throw new Error("invalid closed threads");
    if (!Array.isArray(pendingInbox) || pendingInbox.length > MAX_PENDING || pendingInbox.some((item) => !validateMessage(item))) {
      throw new Error("invalid pending inbox");
    }
    if (!Array.isArray(outbox) || outbox.length > MAX_OUTBOX || outbox.some((item) => !validateOutboundMessage(item))) {
      throw new Error("invalid outbox");
    }
    const effectiveClosedThreads = new Set([
      ...closedThreads,
      ...pendingInbox.filter((message) => message.complete).map((message) => message.thread_id),
      ...outbox.filter((message) => message.complete).map((message) => message.thread_id),
      ...(peer?.complete ? [peer.thread_id] : []),
    ]);
    if (effectiveClosedThreads.size > 10000) throw new Error("too many closed threads");
    if (expectedFingerprint && value.fingerprint !== expectedFingerprint) {
      if (value.fingerprint == null && value.cursor === 0 && pendingInbox.length === 0) {
        value.fingerprint = expectedFingerprint;
      } else {
        throw new Error("different room or agent");
      }
    }
    return {
      cursor: value.cursor,
      seen: value.seen.filter((id) => typeof id === "string").slice(-MAX_SEEN),
      peerContext: peer ? { hop: 0, complete: false, ...peer } : null,
      pendingInbox,
      outbox,
      closedThreads: [...effectiveClosedThreads],
      fingerprint: value.fingerprint ?? expectedFingerprint ?? null,
      sessionId: value.sessionId ?? null,
    };
  } catch (error) {
    if (error?.code === "ENOENT") {
      return { cursor: 0, seen: [], peerContext: null, pendingInbox: [], outbox: [], closedThreads: [], fingerprint: expectedFingerprint, sessionId: null };
    }
    const reason = error?.message === "different room or agent" ? "different room or agent" : "unreadable";
    throw new Error(`Agent Commons state is ${reason}; refusing to skip relay messages`);
  }
}

export async function saveState(file, state) {
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(state), { encoding: "utf8", mode: 0o600, flag: "wx" });
  try {
    await rename(temporary, file);
  } catch (error) {
    const { unlink } = await import("node:fs/promises");
    await unlink(temporary).catch(() => {});
    throw error;
  }
}
