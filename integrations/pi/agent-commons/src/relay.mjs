import { randomUUID } from "node:crypto";

const MAX_TEXT_BYTES = 16 * 1024;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const PROJECT = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const LABEL = /^[A-Za-z0-9_-]{1,64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class RelayError extends Error {}

export class RelayClient {
  constructor({ api, project, agentId, fetchImpl = fetch }) {
    const endpoint = new URL(api);
    if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== "/") {
      throw new TypeError("relay API URL must not include credentials, path, query, or fragment");
    }
    if (endpoint.protocol !== "https:" && !(endpoint.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(endpoint.hostname))) {
      throw new TypeError("relay API must use HTTPS, or HTTP on loopback");
    }
    if (!PROJECT.test(project)) throw new TypeError("invalid project name");
    if (!LABEL.test(agentId)) throw new TypeError("invalid agent name");
    this.baseUrl = endpoint.origin;
    this.project = project;
    this.agentId = agentId;
    this.fetch = fetchImpl;
  }

  async #request(path, options = {}) {
    let response;
    try {
      response = await this.fetch(`${this.baseUrl}${path}`, {
        ...options,
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
        headers: { "Content-Type": "application/json", ...options.headers },
      });
    } catch (error) {
      throw new RelayError(`relay unavailable (${error?.name || "Error"})`);
    }
    if (!response.ok) throw new RelayError(`relay returned HTTP ${response.status}`);
    const declaredLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(declaredLength) && declaredLength > MAX_RESPONSE_BYTES) throw new RelayError("relay response exceeded 2 MiB");
    const reader = response.body?.getReader();
    if (!reader) throw new RelayError("relay returned an empty response");
    const chunks = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new RelayError("relay response exceeded 2 MiB");
      }
      chunks.push(Buffer.from(value));
    }
    try { return JSON.parse(Buffer.concat(chunks, length).toString("utf8")); }
    catch { throw new RelayError("relay returned invalid JSON"); }
  }


  async read(after) {
    if (!Number.isSafeInteger(after) || after < 0) throw new TypeError("relay cursor must be a non-negative safe integer");
    const query = new URLSearchParams({ project: this.project, after: String(after), limit: "100" });
    const result = await this.#request(`/v1/messages?${query}`);
    if (!result || !Array.isArray(result.messages) || result.messages.length > 100
      || !Number.isSafeInteger(result.next_after) || result.next_after < after) throw new RelayError("relay returned an invalid message page");
    let previousSeq = after;
    for (const message of result.messages) {
      if (!message || typeof message !== "object" || Array.isArray(message)
        || typeof message.sender !== "string" || !LABEL.test(message.sender)
        || typeof message.recipient !== "string" || !LABEL.test(message.recipient)
        || typeof message.thread_id !== "string" || !UUID.test(message.thread_id)
        || typeof message.client_msg_id !== "string" || !UUID.test(message.client_msg_id)
        || !Number.isSafeInteger(message.seq) || message.seq <= previousSeq || message.seq > result.next_after
        || !(message.reply_to === null || (Number.isSafeInteger(message.reply_to) && message.reply_to >= 0))
        || !Number.isInteger(message.hop) || message.hop < 0 || message.hop > 8
        || typeof message.text !== "string" || !message.text.trim()
        || Buffer.byteLength(message.text, "utf8") > MAX_TEXT_BYTES || typeof message.complete !== "boolean"
        || (message.delivery !== undefined && !["follow_up", "interrupt"].includes(message.delivery))) {
        throw new RelayError("relay returned an invalid message");
      }
      previousSeq = message.seq;
    }
    return result;
  }

  async send({ recipient, threadId, clientMessageId = randomUUID(), text, replyTo = null, hop = 0, complete = false }) {
    if (!LABEL.test(recipient)) throw new TypeError("invalid recipient name");
    if (!UUID.test(threadId) || !UUID.test(clientMessageId)) throw new TypeError("thread and client message IDs must be UUIDs");
    if (typeof text !== "string" || !text.trim()) throw new TypeError("message must be non-empty text");
    if (Buffer.byteLength(text, "utf8") > MAX_TEXT_BYTES) throw new TypeError("message exceeds 16 KiB UTF-8 limit");
    if (replyTo !== null && (!Number.isSafeInteger(replyTo) || replyTo < 0)) throw new TypeError("reply sequence must be non-negative");
    if (!Number.isInteger(hop) || hop < 0 || hop > 8 || typeof complete !== "boolean") throw new TypeError("invalid reply controls");
    const query = new URLSearchParams({ project: this.project });
    const receipt = await this.#request(`/v1/messages?${query}`, { method: "POST", body: JSON.stringify({
      client_msg_id: clientMessageId, sender: this.agentId, recipient, thread_id: threadId,
      reply_to: replyTo, hop, text, complete,
      delivery: "follow_up",
    }) });
    if (!receipt || !Number.isSafeInteger(receipt.seq) || receipt.seq < 1) throw new RelayError("relay returned an invalid send receipt");
    return receipt;
  }
}
