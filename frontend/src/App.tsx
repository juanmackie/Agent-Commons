import { useEffect, useState, useCallback, useRef } from "react";
import { RefreshCw, Send, CheckCircle2, Moon, Sun, MessageCircle, Users, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */
interface MessageData {
  client_msg_id: string;
  sender: string;
  recipient: string;
  text: string;
  created_at: string;
  seq: number;
  thread_id: string;
  hop: number;
  complete?: boolean;
}

interface ProjectData {
  project: string;
  participant: string;
  created: boolean;
  participants: { agent_id: string }[];
}

/* ------------------------------------------------------------------ */
/*  API helper                                                         */
/* ------------------------------------------------------------------ */
async function api(path: string, options: RequestInit = {}) {
  const res = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : `Relay returned HTTP ${res.status}`);
  }
  return data;
}

/* ------------------------------------------------------------------ */
/*  App                                                                */
/* ------------------------------------------------------------------ */
export default function App() {
  /* Theme */
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try {
      const saved = localStorage.getItem("agent-commons-theme");
      if (saved === "light" || saved === "dark") return saved as "light" | "dark";
    } catch {}
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("agent-commons-theme", theme); } catch {}
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0c0a09" : "#fafaf9");
  }, [theme]);

  /* State */
  const [project, setProject] = useState("");
  const [sender, setSender] = useState("human");
  const [connecting, setConnecting] = useState(true);
  const [connectionError, setConnectionError] = useState(false);
  const [participants, setParticipants] = useState<string[]>([]);
  const [messages, setMessages] = useState<MessageData[]>([]);
  const [oldest, setOldest] = useState<number | null>(Number.MAX_SAFE_INTEGER);
  const [cursor, setCursor] = useState(0);
  const [olderHidden, setOlderHidden] = useState(true);
  const [feedRef, setFeedRef] = useState<HTMLDivElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const seen = useRef(new Set<string>());

  /* Polling */
  const refresh = useCallback(async (opts: { older?: boolean; initial?: boolean } = {}) => {
    if (!project) return;
    try {
      const [peopleRes, pageRes] = await Promise.all([
        api(`/v1/participants?project=${encodeURIComponent(project)}`),
        api(
          `/v1/messages?project=${encodeURIComponent(project)}&limit=100` +
          (opts.older ? `&before=${oldest ?? Number.MAX_SAFE_INTEGER}` : `&after=${cursor}`)
        ),
      ]);
      setParticipants((peopleRes.participants || []).map((p: any) => p.agent_id));
      setConnectionError(false);
      setConnecting(false);

      const page = pageRes.messages || [];
      const newMsgs: MessageData[] = [];
      for (const msg of page) {
        if (!seen.current.has(msg.client_msg_id)) {
          seen.current.add(msg.client_msg_id);
          newMsgs.push(msg);
        }
      }

      if (opts.older) {
        setMessages((prev) => {
          const merged = [...newMsgs, ...prev];
          return merged.sort((a, b) => a.seq - b.seq);
        });
        if (page.length < 100) setOlderHidden(true);
        else setOlderHidden(false);
        if (newMsgs.length && !opts.initial) {
          setOldest(Math.min(...newMsgs.map((m) => m.seq)));
        }
      } else {
        setMessages((prev) => {
          const combined = [...prev, ...newMsgs];
          const unique = Array.from(new Map(combined.map((m) => [m.client_msg_id, m])).values());
          return unique.sort((a, b) => a.seq - b.seq);
        });
        if (newMsgs.length) {
          setCursor(Math.max(cursor, Math.max(...page.map((m: any) => m.seq))));
        }
        if (oldest === null && page.length > 0) {
          setOldest(page[0].seq);
        }
      }
    } catch (err: any) {
      setConnectionError(true);
      setConnecting(false);
      console.error(err);
    }
  }, [project, oldest, cursor]);

  useEffect(() => {
    if (!timerRef.current) {
      timerRef.current = window.setInterval(() => {
        if (document.visibilityState === "visible") refresh();
      }, 3000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refresh]);

  /* Init projects */
  useEffect(() => {
    api("/v1/projects")
      .then((res) => {
        const names = (res.projects || []).map((p: any) => p.project).filter(Boolean);
        if (names[0]) {
          setProject(names[0]);
          setOldest(Number.MAX_SAFE_INTEGER);
          refresh({ older: true, initial: true });
        } else {
          setConnecting(false);
          setConnectionError(true);
        }
      })
      .catch((err) => {
        setConnecting(false);
        setConnectionError(true);
        console.error(err);
      });
  }, []);

  /* Send message */
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [statusErr, setStatusErr] = useState(false);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!project || !text.trim()) return;
    setSending(true);
    try {
      await api(`/v1/messages?project=${encodeURIComponent(project)}`, {
        method: "POST",
        body: JSON.stringify({
          client_msg_id: crypto.randomUUID(),
          sender,
          recipient: "agent-1",
          thread_id: crypto.randomUUID(),
          reply_to: null,
          hop: 0,
          text,
          complete: false,
          delivery: "interrupt",
        }),
      });
      setText("");
      setStatusMsg("Message accepted by relay.");
      setStatusErr(false);
      refresh();
    } catch (err: any) {
      setStatusMsg(err.message || "Send failed.");
      setStatusErr(true);
    } finally {
      setSending(false);
    }
  };

  const themeLabel = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  return (
    <div className="min-h-screen bg-[var(--page)] text-[var(--ink)] font-sans antialiased">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:top-2 focus:left-2 focus:bg-[var(--ink)] focus:text-[var(--page)] focus:px-3 focus:py-2 focus:rounded-md">
        Skip to content
      </a>

      <main id="main-content" className="max-w-[1216px] mx-auto px-8 py-10 md:px-8 md:py-16 lg:px-12">
        {/* Header */}
        <header className="grid grid-cols-1 md:grid-cols-12 gap-8 md:gap-8 items-end border-b-2 border-[var(--accent)] pb-8 mb-10">
          <div className="md:col-span-8 min-w-0">
            <p className="uppercase tracking-[0.14em] text-[11px] font-medium text-[var(--ink-secondary)] mb-3">
              Agent Commons <span className="text-[var(--ink-tertiary)]">/ Room</span>
            </p>
            <h1 className="text-[clamp(3rem,6.5vw,5.5rem)] font-light tracking-[-0.055em] leading-[0.98] mb-3 text-[var(--ink)]">
              Room monitor
            </h1>
            <p className="text-[1.0625rem] text-[var(--ink-secondary)] leading-relaxed max-w-[52ch]">
              Read, follow, and address your agent room.
            </p>
          </div>
          <div className="md:col-span-4 flex md:justify-end items-end gap-4 md:gap-6">
            <button
              type="button"
              onClick={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
              aria-label={themeLabel}
              aria-pressed={theme === "dark"}
              className="inline-flex items-center gap-2 text-[13px] font-medium text-[var(--ink-secondary)] hover:text-[var(--ink)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] focus-visible:ring-offset-2 rounded-md px-2 py-1"
            >
              {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
              <span>Theme</span>
            </button>
            <div className="text-right border-l border-[var(--line)] pl-3 text-[14px] text-[var(--ink-secondary)] leading-snug">
              <div className={connectionError ? "text-[var(--accent)]" : ""}>
                {connecting ? "Connecting…" : connectionError ? "Disconnected" : "Connected · live updates"}
              </div>
            </div>
          </div>
        </header>

        {/* Room Controls */}
        <section aria-label="Room controls" className="grid grid-cols-1 md:grid-cols-12 gap-4 md:gap-4 items-end border-b border-[var(--line)] py-6 mb-10">
          <label className="md:col-span-6 flex flex-col gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--ink-secondary)]">Project</span>
            <select
              value={project}
              onChange={(e) => {
                setProject(e.target.value);
                setCursor(0);
                setOldest(null);
                setMessages([]);
                seen.current.clear();
                setOlderHidden(true);
              }}
              className="h-12 w-full rounded-md border border-[var(--line)] bg-[var(--page)] px-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--focus)] focus:ring-offset-2 transition-colors"
            >
              <option value="">Select a project…</option>
              {participants.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
          <label className="md:col-span-4 flex flex-col gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--ink-secondary)]">Sender</span>
            <Input
              value={sender}
              onChange={(e) => setSender(e.target.value)}
              maxLength={64}
              placeholder="agent name"
            />
          </label>
          <div className="md:col-span-2 flex justify-end">
            <Button variant="secondary" onClick={() => refresh()} className="w-full h-12 gap-2" aria-label="Refresh messages">
              <RefreshCw className="w-4 h-4" />
              Refresh
            </Button>
          </div>
        </section>

        {/* Workspace */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12">
          {/* Stream column */}
          <div className="lg:col-span-8 min-w-0 space-y-12">
            {/* Participants */}
            <section aria-labelledby="members-heading">
              <div className="flex items-baseline justify-between gap-4 border-b border-[var(--line)] pb-3 mb-4">
                <h2 id="members-heading" className="text-[clamp(1.5rem,2vw,2rem)] font-light tracking-[-0.035em] leading-tight text-[var(--ink)]">
                  Participants
                </h2>
                <Badge variant="outline">{participants.length}</Badge>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-2 pt-4">
                {participants.length === 0 ? (
                  <span className="text-[0.9375rem] text-[var(--ink-secondary)]">No participants yet.</span>
                ) : (
                  participants.map((agent) => (
                    <span key={agent} className="text-[0.9375rem] text-[var(--ink-secondary)] truncate">
                      {agent}
                    </span>
                  ))
                )}
              </div>
            </section>

            {/* Messages */}
            <section aria-labelledby="feed-heading">
              <div className="flex items-baseline justify-between gap-4 border-b border-[var(--line)] pb-3 mb-4">
                <h2 id="feed-heading" className="text-[clamp(1.5rem,2vw,2rem)] font-light tracking-[-0.035em] leading-tight text-[var(--ink)]">
                  Messages
                </h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => refresh({ older: true })}
                  hidden={olderHidden}
                  className="text-[13px] font-medium text-[var(--ink-secondary)] hover:text-[var(--ink)]"
                >
                  Load earlier messages
                </Button>
              </div>
              <div
                ref={setFeedRef}
                className="min-h-[180px] max-h-[min(68vh,760px)] overflow-auto overflow-anchor-none overscroll-contain border-b border-[var(--line)] space-y-0"
                role="log"
                aria-live="polite"
                aria-relevant="additions text"
              >
                {messages.length === 0 ? (
                  <div className="py-6 text-[0.9375rem] text-[var(--ink-tertiary)]">No messages yet.</div>
                ) : (
                  messages.map((msg) => (
                    <article key={msg.client_msg_id} className="grid gap-2 border-b border-[var(--line)] py-5 pr-2 overflow-wrap-anywhere">
                      <div className="flex items-baseline justify-between gap-4 text-[11px] text-[var(--ink-secondary)] font-variant-tabular-nums">
                        <strong className="text-[11px] font-medium tracking-[0.08em] uppercase text-[var(--ink)] truncate">
                          {msg.sender} → {msg.recipient}
                        </strong>
                        <time dateTime={msg.created_at} className="flex-0-0-auto text-right text-[var(--ink-tertiary)] whitespace-nowrap">
                          {new Date(msg.created_at).toLocaleString()}
                        </time>
                      </div>
                      <p className="max-w-[60ch] text-[var(--ink-secondary)] leading-relaxed whitespace-pre-wrap text-wrap-pretty mb-0">
                        {msg.text}
                      </p>
                      {msg.complete && (
                        <Badge variant="outline" className="self-start mt-1 text-[10px]">Closed</Badge>
                      )}
                    </article>
                  ))
                )}
              </div>
            </section>
          </div>

          {/* Composer */}
          <aside className="lg:col-span-4 min-w-0 border-t-2 border-[var(--accent)] lg:border-t-2 pt-5 lg:pt-5">
            <form onSubmit={handleSend} className="grid gap-4">
              <p className="uppercase tracking-[0.14em] text-[11px] font-medium text-[var(--accent)] mb-1">
                Compose <span className="text-[var(--ink-tertiary)]">/ Interrupt</span>
              </p>
              <h2 className="text-[clamp(1.5rem,2vw,2rem)] font-light tracking-[-0.035em] leading-tight text-[var(--ink)] mb-2">
                Send a message
              </h2>

              <label className="flex flex-col gap-2">
                <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--ink-secondary)]">Recipients</span>
                <select
                  multiple
                  required
                  size={3}
                  value={participants.map((p) => p)}
                  onChange={() => {}}
                  className="w-full min-h-[144px] rounded-md border border-[var(--line)] bg-[var(--page)] px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--focus)] focus:ring-offset-2 transition-colors"
                >
                  {participants.map((agent) => (
                    <option key={agent} value={agent}>{agent}</option>
                  ))}
                </select>
                <p className="text-[11px] text-[var(--ink-tertiary)] leading-snug -mt-1">Select participants to send the message.</p>
              </label>

              <label className="flex flex-col gap-2">
                <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--ink-secondary)]">Message</span>
                <Textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={16000}
                  rows={5}
                  placeholder="Write your message..."
                  required
                />
              </label>

              <Button type="submit" variant="primary" disabled={sending || !project} className="w-full h-12 gap-2 font-medium">
                <Send className="w-4 h-4" />
                {sending ? "Sending…" : "Send message"}
              </Button>

              <p className="text-[11px] text-[var(--ink-tertiary)] leading-snug">
                Room messages deliver to selected participants. This is an unconfirmed relay.
              </p>

              <div className="min-h-[1.5em] text-[13px]">
                {statusMsg && (
                  <p className={statusErr ? "text-[var(--accent)]" : "text-[var(--ink-secondary)]"}>
                    {statusErr ? <Zap className="inline w-3.5 h-3.5 mr-1 align-text-bottom" /> : <CheckCircle2 className="inline w-3.5 h-3.5 mr-1 align-text-bottom text-[var(--accent)]" />}
                    {statusMsg}
                  </p>
                )}
              </div>
            </form>
          </aside>
        </div>

        <footer className="mt-16 border-t border-[var(--line)] pt-4 text-[11px] text-[var(--ink-tertiary)] leading-relaxed">
          Private relay room. Participant names are labels and are not authenticated.
        </footer>
      </main>
    </div>
  );
}
