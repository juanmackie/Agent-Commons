const MAX_SEEN = 500;
const MAX_CLOSED_THREADS = 10000;
const MAX_OUTBOX = 100;

export function closeRelayThread(state, threadId) {
  const closedThreads = state.closedThreads ?? [];
  if (closedThreads.includes(threadId)) return state;
  if (closedThreads.length >= MAX_CLOSED_THREADS) throw new Error("closed-thread state limit reached; refusing to advance the relay cursor");
  const peerContext = state.peerContext?.thread_id === threadId
    ? { ...state.peerContext, complete: true }
    : state.peerContext;
  return { ...state, closedThreads: [...closedThreads, threadId], peerContext };
}

export function queueOutboundMessage(state, message) {
  const existing = state.outbox.find((item) => item.client_msg_id === message.client_msg_id);
  if (existing) {
    if (JSON.stringify(existing) !== JSON.stringify(message)) throw new Error("outbox ID already belongs to a different message");
    return state;
  }
  if (state.outbox.length >= MAX_OUTBOX) throw new Error("outbox is full; refusing an untracked send");
  return { ...state, outbox: [...state.outbox, message] };
}

export function closedThreadsForOutbound(state) {
  return [...new Set([
    ...(state.closedThreads ?? []),
    ...state.outbox.filter((message) => message.complete).map((message) => message.thread_id),
  ])];
}

export function hasQueuedOutboundMessage(state, message) {
  return state?.outbox?.some((queued) =>
    queued.client_msg_id === message.client_msg_id
    && Object.keys(queued).length === Object.keys(message).length
    && Object.entries(message).every(([key, value]) => queued[key] === value)
  ) ?? false;
}

export function nextOutboundForSession(state, sessionId) {
  if (!sessionId) return null;
  return state.outbox.find((message) => message.target_session_id === sessionId) ?? null;
}

export function classifyOutboundFailure(error) {
  if (error?.status === 404) return "hold";
  if ([409, 413, 422].includes(error?.status)) return "reject";
  return "retry";
}

export function rejectOutboundMessage(state, clientMessageId) {
  if (!state.outbox.some((item) => item.client_msg_id === clientMessageId)) return state;
  return { ...state, outbox: state.outbox.filter((item) => item.client_msg_id !== clientMessageId) };
}

export function acknowledgeOutboundMessage(state, clientMessageId) {
  const message = state.outbox.find((item) => item.client_msg_id === clientMessageId);
  if (!message) return state;
  const next = { ...state, outbox: state.outbox.filter((item) => item.client_msg_id !== clientMessageId) };
  return message.complete ? closeRelayThread(next, message.thread_id) : next;
}

export function queueRelayPage(state, page, agentId, targetSessionId, maxPending = 100) {
  const pendingInbox = [...state.pendingInbox];
  const pendingIds = new Set(pendingInbox.map((message) => message.client_msg_id));
  const seen = new Set(state.seen);
  const closedThreads = new Set(state.closedThreads ?? []);
  let cursor = state.cursor;
  let full = false;
  for (const message of page.messages) {
    if (message.seq <= cursor) continue;
    if (message.recipient === agentId && message.sender !== agentId
        && !seen.has(message.client_msg_id) && !pendingIds.has(message.client_msg_id)) {
      if (typeof targetSessionId !== "string" || !targetSessionId) {
        throw new Error("Pi did not provide the target session identity; refusing to advance the relay cursor");
      }
      if (closedThreads.has(message.thread_id)) {
        seen.add(message.client_msg_id);
        cursor = message.seq;
        continue;
      }
      if (pendingInbox.length >= maxPending) {
        full = true;
        break;
      }
      pendingInbox.push({ ...message, target_session_id: targetSessionId });
      pendingIds.add(message.client_msg_id);
      if (message.complete) {
        if (closedThreads.size >= MAX_CLOSED_THREADS) throw new Error("closed-thread state limit reached; refusing to advance the relay cursor");
        closedThreads.add(message.thread_id);
      }
    }
    cursor = message.seq;
  }
  if (!full) cursor = Math.max(cursor, page.next_after);
  return {
    ...state,
    cursor,
    seen: [...seen].slice(-MAX_SEEN),
    pendingInbox,
    closedThreads: [...closedThreads],
  };
}

export function acknowledgeRelayMessage(state, clientMessageId, sessionId) {
  const message = state.pendingInbox.find((item) => item.client_msg_id === clientMessageId);
  if (!message || (message.target_session_id && message.target_session_id !== sessionId)) return state;
  return {
    ...state,
    pendingInbox: state.pendingInbox.filter((item) => item.client_msg_id !== clientMessageId),
    seen: [...state.seen, clientMessageId].slice(-MAX_SEEN),
    peerContext: {
      sender: message.sender,
      thread_id: message.thread_id,
      seq: message.seq,
      client_msg_id: message.client_msg_id,
      hop: message.hop,
      complete: message.complete,
    },
  };
}

export function hasVisiblePeerNotice(entries, clientMessageId) {
  return Array.isArray(entries) && entries.some((entry) =>
    entry?.type === "custom_message"
      && entry.customType === "agent-commons-peer"
      && entry.details?.client_msg_id === clientMessageId,
  );
}

export function nextRelayMessage(state, sessionId, queuedIds = new Set()) {
  const pending = state.pendingInbox.filter((message) =>
    message.target_session_id === sessionId && !queuedIds.has(message.client_msg_id),
  );
  return pending.find((message) => message.delivery === "interrupt") ?? pending[0] ?? null;
}

export function visiblePendingMessages(state, sessionId, isVisible) {
  return state.pendingInbox.filter((message) =>
    message.target_session_id === sessionId && isVisible(message.client_msg_id),
  );
}
