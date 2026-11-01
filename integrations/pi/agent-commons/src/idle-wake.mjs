const DEFAULT_WAKE_TIMEOUT_MS = 10_000;

export class IdleWakeReservation {
  #sessionId = null;
  #messageId = null;
  #reservedAt = null;

  constructor(timeoutMs = DEFAULT_WAKE_TIMEOUT_MS) {
    this.timeoutMs = timeoutMs;
  }

  reserve(sessionId, messageId, now = Date.now()) {
    this.#sessionId = sessionId;
    this.#messageId = messageId;
    this.#reservedAt = now;
  }

  isPending(sessionId, now = Date.now()) {
    if (sessionId !== this.#sessionId || this.#reservedAt === null) return false;
    if (now - this.#reservedAt >= this.timeoutMs) {
      this.#clear();
      return false;
    }
    return true;
  }

  shouldDefer(sessionId, isIdle, now = Date.now()) {
    return isIdle && this.isPending(sessionId, now);
  }

  expire(sessionId, now = Date.now()) {
    if (sessionId !== this.#sessionId || this.#reservedAt === null
        || now - this.#reservedAt < this.timeoutMs) return null;
    const messageId = this.#messageId;
    this.#clear();
    return messageId;
  }

  release(sessionId) {
    if (sessionId !== this.#sessionId || this.#reservedAt === null) return false;
    this.#clear();
    return true;
  }

  #clear() {
    this.#sessionId = null;
    this.#messageId = null;
    this.#reservedAt = null;
  }
}
