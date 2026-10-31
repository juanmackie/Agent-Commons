const PEER_MARKER = /\[Agent Commons peer delivery client_msg_id=([0-9a-f-]{36}) thread=([0-9a-f-]{36}) seq=(\d+) hop=(\d+) complete=(true|false)\]/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function parsePeerMarker(value) {
  if (typeof value !== "string") return null;
  const match = value.match(PEER_MARKER);
  if (!match || !UUID.test(match[1]) || !UUID.test(match[2])) return null;
  const seq = Number(match[3]);
  const hop = Number(match[4]);
  if (!Number.isSafeInteger(seq) || seq < 1 || !Number.isInteger(hop) || hop < 0 || hop > 8) return null;
  return {
    client_msg_id: match[1],
    thread_id: match[2],
    seq,
    hop,
    complete: match[5].toLowerCase() === "true",
  };
}

function messageText(message) {
  if (typeof message?.content === "string") return message.content;
  if (!Array.isArray(message?.content)) return "";
  return message.content.filter((part) => part?.type === "text" && typeof part.text === "string").map((part) => part.text).join("\n");
}

export function latestPeerContext(entries) {
  if (!Array.isArray(entries)) return null;
  for (let index = entries.length - 1; index >= 0; index--) {
    const entry = entries[index];
    if (entry?.type !== "message" || entry.message?.role !== "user") continue;
    const marker = messageText(entry.message);
    const parsed = parsePeerMarker(marker);
    if (!parsed) continue;
    const sender = marker.match(/^From: ([A-Za-z0-9_-]{1,64})\s*$/m)?.[1];
    if (!sender) continue;
    return { ...parsed, sender };
  }
  return null;
}
