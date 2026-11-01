import { isNearBottom, scrollTopAfterPrepend } from "/assets/room-scroll.mjs";

const $ = id => document.getElementById(id);
const label = /^[A-Za-z0-9_-]{1,64}$/;
const sentKey = id => `agent-commons-fanout-${id}`;
const themeKey = "agent-commons-theme";
const systemColorScheme = window.matchMedia?.("(prefers-color-scheme: dark)");
let project = "", projectEpoch = 0, cursor = 0, oldest = null, seen = new Set(), messages = new Map(), closedThreads = new Set(), timer, refreshing = false;

let savedTheme = null;
try {
  const candidate = localStorage.getItem(themeKey);
  if (candidate === "light" || candidate === "dark") savedTheme = candidate;
} catch {}

function activeTheme() {
  return document.documentElement.dataset.theme || (systemColorScheme?.matches ? "dark" : "light");
}

function updateThemeControl(theme = activeTheme()) {
  const button = $("theme-toggle"), next = theme === "dark" ? "light" : "dark";
  button.setAttribute("aria-label", `Switch to ${next} theme`);
  button.setAttribute("aria-pressed", String(theme === "dark"));
  button.textContent = `Use ${next} theme`;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#0c0a09" : "#fafaf9";
}

updateThemeControl();
$("theme-toggle").addEventListener("click", () => {
  const next = activeTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  savedTheme = next;
  try { localStorage.setItem(themeKey, next); } catch {}
  updateThemeControl(next);
});
systemColorScheme?.addEventListener("change", () => {
  if (savedTheme === null) updateThemeControl();
});

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options,
    headers: { "Content-Type": "application/json", ...options.headers } });
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : `Relay returned HTTP ${response.status}`);
  return data;
}
function status(message, error = false) {
  $("status").textContent = message;
  $("status").dataset.error = String(error);
}
function render(message, prepend = false) {
  if (seen.has(message.client_msg_id)) return;
  seen.add(message.client_msg_id); messages.set(message.client_msg_id, message);
  if (message.complete) closedThreads.add(message.thread_id);
  const article = document.createElement("article");
  article.className = "message";
  const head = document.createElement("div"); head.className = "message-head";
  const who = document.createElement("strong"); who.textContent = `${message.sender} → ${message.recipient}`;
  const time = document.createElement("time"); time.dateTime = message.created_at; time.textContent = new Date(message.created_at).toLocaleString();
  head.append(who, time);
  const body = document.createElement("p"); body.textContent = message.text;
  article.dataset.reply = JSON.stringify({ sender: message.sender, recipient: message.recipient,
    seq: message.seq, thread_id: message.thread_id, hop: message.hop });
  article.append(head, body);
  if (prepend) $("feed").prepend(article); else $("feed").append(article);
}
async function refresh({ older = false, initial = false } = {}) {
  if (refreshing || !project) return;
  const requestedProject = project, requestedEpoch = projectEpoch;
  refreshing = true;
  try {
    const [people, page] = await Promise.all([
      api(`/v1/participants?project=${encodeURIComponent(requestedProject)}`),
      api(`/v1/messages?project=${encodeURIComponent(requestedProject)}&limit=100&${older ? `before=${oldest}` : `after=${cursor}`}`),
    ]);
    if (requestedEpoch !== projectEpoch) return;
    $("connection").textContent = "Connected · live updates every 3 seconds";
    $("connection").dataset.error = "false";
    $("members").replaceChildren(...people.participants.map(person => {
      const item = document.createElement("span"); item.className = "member"; item.textContent = person.agent_id; return item;
    }));
    const select = $("recipient"), prior = new Set([...select.selectedOptions].map(option => option.value));
    select.replaceChildren(...people.participants.map(person => person.agent_id)
      .filter(name => name !== $("sender").value)
      .map(name => { const option = new Option(name, name); option.selected = prior.has(name); return option; }));
    $("composer").querySelector("button[type=submit]").disabled = select.options.length === 0;
    if (older) {
      const pageMessages = initial ? page.messages : page.messages.slice().reverse();
      const feed = $("feed"), previousTop = feed.scrollTop, previousHeight = feed.scrollHeight;
      for (const message of pageMessages) render(message, !initial);
      if (initial) feed.scrollTop = feed.scrollHeight;
      else feed.scrollTop = scrollTopAfterPrepend(previousTop, previousHeight, feed.scrollHeight);
      cursor = Math.max(cursor, page.next_after);
      oldest = page.messages[0]?.seq ?? oldest;
      $("older").hidden = page.messages.length < 100;
    } else {
      const feed = $("feed"), follow = isNearBottom(feed), previousCount = messages.size;
      for (const message of page.messages) render(message);
      if (messages.size > previousCount && follow) feed.scrollTop = feed.scrollHeight;
      cursor = Math.max(cursor, page.next_after);
      if (oldest === null && page.messages.length) oldest = page.messages[0].seq;
    }
    const reply = $("reply"), selectedReply = reply.value;
    reply.replaceChildren(new Option("New conversation", ""), ...[...messages.values()].map(message => {
      const closed = message.complete || closedThreads.has(message.thread_id);
      const option = new Option(`${message.sender} → ${message.recipient}: ${message.text.slice(0, 60)}${closed ? " (closed)" : ""}`, JSON.stringify(message));
      option.disabled = closed;
      return option;
    }));
    reply.value = selectedReply;
  } catch (error) {
    $("connection").textContent = `Disconnected · ${error.message}`;
    $("connection").dataset.error = "true";
  } finally {
    refreshing = false;
    if (requestedEpoch !== projectEpoch && project) {
      const initial = oldest === Number.MAX_SAFE_INTEGER;
      void refresh({ older: initial, initial });
    }
  }
}
$("project").addEventListener("change", () => {
  project = $("project").value; projectEpoch++; cursor = 0; oldest = Number.MAX_SAFE_INTEGER;
  seen.clear(); messages.clear(); closedThreads.clear(); $("feed").replaceChildren();
  $("older").hidden = true;
  $("recipient").replaceChildren();
  $("reply").replaceChildren(new Option("New conversation", ""));
  $("composer").querySelector("button[type=submit]").disabled = true;
  refresh({ older: true, initial: true });
});
$("refresh").onclick = () => refresh();
$("older").onclick = () => refresh({ older: true });
$("all").onclick = () => { for (const option of $("recipient").options) option.selected = true; };
$("sender").value = localStorage.getItem("agent-commons-sender") || "human";
$("sender").addEventListener("change", () => {
  if (label.test($("sender").value)) localStorage.setItem("agent-commons-sender", $("sender").value);
});
$("composer").addEventListener("submit", async event => {
  event.preventDefault();
  const sender = $("sender").value, recipients = [...$("recipient").selectedOptions].map(option => option.value), text = $("text").value;
  const submittedProject = project;
  if (!label.test(sender) || !recipients.length || !text.trim()) return status("Choose a sender, recipients, and a message.", true);
  const reply = $("reply").value ? JSON.parse($("reply").value) : null;
  if (reply && (reply.complete || closedThreads.has(reply.thread_id))) return status("This thread is closed. Start a new conversation.", true);
  if (reply?.hop >= 8) return status("This thread reached its reply limit. Start a new conversation.", true);
  if (reply) {
    const target = reply.sender === sender ? reply.recipient : reply.sender;
    for (const option of $("recipient").options) option.selected = option.value === target;
    recipients.splice(0, recipients.length, target);
  }
  let batch;
  try { batch = JSON.parse(sessionStorage.getItem(sentKey(submittedProject)) || "null"); } catch { batch = null; }
  const replyMatches = reply
    ? batch?.thread_id === reply.thread_id && batch.reply_to === reply.seq && batch.hop === reply.hop + 1
    : batch?.reply_to === null && batch.hop === 0;
  if (!batch || batch.text !== text || batch.sender !== sender || !replyMatches
      || JSON.stringify(batch.targets.slice().sort()) !== JSON.stringify(recipients.slice().sort())) {
    batch = { sender, text, targets: recipients.slice().sort(), recipients: recipients.map(recipient => ({ recipient, client_msg_id: crypto.randomUUID() })),
      thread_id: reply?.thread_id || crypto.randomUUID(), reply_to: reply?.seq ?? null, hop: reply ? reply.hop + 1 : 0 };
  }
  sessionStorage.setItem(sentKey(submittedProject), JSON.stringify(batch));
  const button = event.submitter; button.disabled = true;
  const remaining = [];
  for (const target of batch.recipients) {
    try {
      await api(`/v1/messages?project=${encodeURIComponent(submittedProject)}`, { method: "POST", body: JSON.stringify({
        client_msg_id: target.client_msg_id, sender, recipient: target.recipient, thread_id: batch.thread_id,
        reply_to: batch.reply_to, hop: batch.hop, text, complete: false, delivery: "interrupt",
      }) });
    } catch (error) { remaining.push(target); status(`${target.recipient} failed: ${error.message}`, true); }
    if (!remaining.some(item => item.recipient === target.recipient)) {
      batch.recipients = batch.recipients.filter(item => item.recipient !== target.recipient);
      sessionStorage.setItem(sentKey(submittedProject), JSON.stringify(batch));
    }
  }
  button.disabled = false;
  if (remaining.length) {
    batch.recipients = remaining; sessionStorage.setItem(sentKey(submittedProject), JSON.stringify(batch));
    status(`${remaining.length} recipient(s) failed. Submit again to retry only those recipients.`, true);
  } else {
    sessionStorage.removeItem(sentKey(submittedProject));
    if ($("text").value === text) $("text").value = "";
    status(`Accepted by the relay for ${recipients.length} recipient(s) in ${submittedProject}. Session delivery is unconfirmed.`);
    if (project === submittedProject) await refresh();
  }
});
(async () => {
  try {
    const result = await api("/v1/projects");
    const projects = (result.projects || []).map(entry => entry.project).filter(name => typeof name === "string");
    $("project").replaceChildren(...projects.map(name => new Option(name, name)));
    project = projects[0] || "";
    if (project) { $("project").value = project; oldest = Number.MAX_SAFE_INTEGER; await refresh({ older: true, initial: true }); }
    else { $("connection").textContent = "No projects yet"; $("connection").dataset.error = "true"; }
  } catch (error) { $("connection").textContent = `Disconnected · ${error.message}`; $("connection").dataset.error = "true"; }
})();
function startPolling() {
  if (timer) return;
  timer = setInterval(() => {
    if (document.visibilityState === "visible") void refresh();
  }, 3000);
}
function stopPolling() {
  clearInterval(timer);
  timer = null;
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void refresh();
});
window.addEventListener("pagehide", stopPolling);
window.addEventListener("pageshow", () => {
  startPolling();
  if (document.visibilityState === "visible") void refresh();
});
startPolling();
