"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Cloud,
  Plus,
  ArrowUp,
  Download,
  Copy,
  Check,
  RefreshCw,
  ShieldCheck,
  LoaderCircle,
  Code2,
  CircleAlert,
} from "lucide-react";

type Session = {
  id: string;
  status: string;
  mode: string;
  messages: { role: string; content: string }[];
  code: string;
  summary: string;
  error: string | null;
  approval: string | null;
  plan: {
    changes: { address: string; actions: string[] }[];
    creates: number;
    updates: number;
    deletes: number;
  } | null;
};
type Health = {
  mode: string;
  ai_configured: boolean;
  terraform_installed: boolean;
  apply_enabled: boolean;
};
type Recent = { id: string; title: string; status: string };
async function api(path: string, method = "GET", data?: unknown) {
  const response = await fetch("/api/cloud/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: data ? JSON.stringify(data) : undefined,
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "Check your input and try again.",
    );
  return result;
}
export default function MakeCloud() {
  const [session, setSession] = useState<Session | null>(null);
  const [recent, setRecent] = useState<Recent[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [input, setInput] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState("terraform");
  const [confirmed, setConfirmed] = useState(false);
  const [destructive, setDestructive] = useState(false);
  const [copied, setCopied] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const hasInfrastructure =
    !!session &&
    ["applied", "apply_failed", "interrupted"].includes(session.status);
  const running =
    busy ||
    (session && ["planning", "applying"].includes(session.status)
      ? session.status
      : "");
  const adopt = useCallback((next: Session) => {
    setSession(next);
    setCode(next.code);
    setConfirmed(false);
    setDestructive(false);
    localStorage.setItem("makecloud-session", next.id);
  }, []);
  const load = useCallback(
    async (id: string) => {
      setError("");
      try {
        adopt(await api("sessions/" + id));
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [adopt],
  );
  const refresh = useCallback(async () => {
    try {
      const [status, list] = await Promise.all([
        api("health"),
        api("sessions"),
      ]);
      setHealth(status);
      setRecent(list);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const id = localStorage.getItem("makecloud-session");
    if (id) load(id);
  }, [load, refresh]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages, busy]);
  const sessionId = session?.id;
  const sessionStatus = session?.status;
  useEffect(() => {
    if (
      !sessionId ||
      !sessionStatus ||
      !["planning", "applying"].includes(sessionStatus)
    )
      return;
    const timer = setInterval(() => load(sessionId), 3000);
    return () => clearInterval(timer);
  }, [sessionId, sessionStatus, load]);
  async function send() {
    if (!input.trim() || running || hasInfrastructure) return;
    const text = input.trim();
    setBusy("Thinking");
    setError("");
    try {
      adopt(
        await api(
          session ? `sessions/${session.id}/messages` : "sessions",
          "POST",
          { message: text },
        ),
      );
      setInput("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
      inputRef.current?.focus();
    }
  }
  async function action(kind: "save" | "plan" | "apply") {
    if (!session || running) return;
    setBusy(
      kind === "plan"
        ? "Initializing, validating, and planning"
        : kind === "apply"
          ? "Applying reviewed plan"
          : "Saving",
    );
    setError("");
    try {
      let next = session;
      if (kind !== "apply" && code !== session.code)
        next = await api(`sessions/${session.id}/code`, "PUT", { code });
      if (kind === "plan") {
        next = await api(`sessions/${session.id}/plan`, "POST");
        setTab("plan");
      }
      if (kind === "apply")
        next = await api(`sessions/${session.id}/apply`, "POST", {
          approval: session.approval,
          confirm: confirmed,
          confirm_destructive: destructive,
        });
      adopt(next);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  function start() {
    setSession(null);
    setCode("");
    setInput("");
    setError("");
    setTab("terraform");
    localStorage.removeItem("makecloud-session");
    inputRef.current?.focus();
  }
  function download() {
    const url = URL.createObjectURL(new Blob([code], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "main.tf";
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          <span className="brand-mark">
            <Cloud size={23} />
          </span>{" "}
          MakeCloud
        </Link>
        <button className="new-button" onClick={start} disabled={!!running}>
          <Plus size={17} /> New project
        </button>
        <p className="eyebrow">YOUR PROJECTS</p>
        <nav aria-label="Projects">
          {recent.map((item) => (
            <button
              className={`project ${item.id === session?.id ? "selected" : ""}`}
              key={item.id}
              disabled={!!running}
              onClick={() => load(item.id)}
            >
              <Cloud size={16} />
              <span>
                {item.title}
                <small>{item.status.replaceAll("_", " ")}</small>
              </span>
            </button>
          ))}
          {!recent.length && (
            <p className="empty-history">
              Your conversations will appear here.
            </p>
          )}
        </nav>
        <div className="sidebar-footer">
          <ShieldCheck size={18} />
          <span>
            Credentials stay outside chat
            <small>One trusted operator per instance</small>
          </span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <span className="eyebrow">INFRASTRUCTURE, SIMPLIFIED</span>
            <h1>
              {session
                ? "Your cloud workspace"
                : "Build something that scales."}
            </h1>
          </div>
          <span
            className={`mode-pill ${health?.mode === "live" ? "live" : ""}`}
          >
            <span />{" "}
            {health
              ? health.mode === "demo"
                ? "Demo · no deployment"
                : "Live mode"
              : "Connecting…"}
          </span>
        </header>
        <div className="mobile-nav">
          <button className="secondary" disabled={!!running} onClick={start}>
            <Plus size={15} /> New project
          </button>
          <select
            aria-label="Open a project"
            disabled={!!running}
            value={session?.id || ""}
            onChange={(e) => (e.target.value ? load(e.target.value) : start())}
          >
            <option value="">Projects</option>
            {recent.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </div>
        <div className={`workspace ${session?.code ? "with-code" : ""}`}>
          <section className="chat-panel">
            {!session ? (
              <div className="welcome">
                <div className="hero-icon">
                  <Cloud size={35} />
                </div>
                <h2>
                  Your idea.
                  <br />
                  <span>Your cloud.</span>
                </h2>
                <p>
                  Tell us what you want to build. We’ll help shape the
                  requirements and turn them into Terraform you can review.
                </p>
                <div className="examples">
                  {[
                    "A private storage bucket for my app",
                    "A web app with a database on AWS",
                    "Private object storage on Google Cloud",
                  ].map((text, index) => (
                    <button
                      disabled={!!running}
                      key={text}
                      onClick={() => {
                        setInput(text);
                        inputRef.current?.focus();
                      }}
                    >
                      <span>{["01", "02", "03"][index]}</span>
                      {text}
                      <ArrowUp size={15} />
                    </button>
                  ))}
                </div>
                <p className="demo-note">
                  {health?.mode === "demo"
                    ? "Demo supports private storage examples on AWS, Azure, and GCP. Configure live AI for other infrastructure."
                    : "Review generated code and a real Terraform plan before applying any changes."}
                </p>
              </div>
            ) : (
              <div className="messages" aria-live="polite">
                {session.messages.map((message, i) => (
                  <article className={`message ${message.role}`} key={i}>
                    <span className="avatar">
                      {message.role === "user" ? "You" : <Cloud size={18} />}
                    </span>
                    <div>
                      <small>
                        {message.role === "user" ? "YOU" : "MAKECLOUD"}
                      </small>
                      <p>{message.content}</p>
                    </div>
                  </article>
                ))}
                {busy && (
                  <p className="working">
                    <LoaderCircle size={16} className="spin" /> {busy}…
                  </p>
                )}
                <div ref={bottom} />
              </div>
            )}
            {(error || session?.error) && (
              <div className="error" role="alert">
                <CircleAlert size={17} />
                <span>{error || session?.error}</span>
                <button
                  onClick={() => {
                    refresh();
                    if (session) load(session.id);
                  }}
                  aria-label="Refresh status"
                >
                  <RefreshCw size={16} />
                </button>
              </div>
            )}
            {hasInfrastructure && (
              <div className="notice">
                {session?.status === "applied"
                  ? "Deployment completed. Terraform state is retained on the backend."
                  : "Inspect the workspace and cloud account before retrying; resources may exist."}
              </div>
            )}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <textarea
                ref={inputRef}
                aria-label="Message MakeCloud"
                value={input}
                maxLength={8000}
                disabled={!!running || hasInfrastructure}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  session
                    ? session.code
                      ? "Ask for a change in live mode…"
                      : "Answer the question above…"
                    : "Describe your infrastructure…"
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <button
                type="submit"
                disabled={!input.trim() || !!running || hasInfrastructure}
                aria-label="Send message"
              >
                {busy ? (
                  <LoaderCircle size={19} className="spin" />
                ) : (
                  <ArrowUp size={20} />
                )}
              </button>
            </form>
            <div className="composer-hint">
              {health?.mode === "demo"
                ? "Demo templates · export and inspect · no cloud changes"
                : "AI can make mistakes. Review code, permissions, and costs."}
              <button
                onClick={() => {
                  refresh();
                  if (session) load(session.id);
                }}
                disabled={!!busy}
                aria-label="Reload workspace"
              >
                <RefreshCw size={13} />
              </button>
            </div>
          </section>
          {session?.code && (
            <section className="code-panel">
              <div className="code-header">
                <div>
                  <Code2 size={18} />
                  <strong>Infrastructure</strong>
                  <span className="status-label">
                    {session.status.replaceAll("_", " ")}
                  </span>
                </div>
                <div>
                  <button
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(code);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1800);
                      } catch {
                        setError(
                          "Clipboard unavailable. Select the code or download it.",
                        );
                      }
                    }}
                    aria-label="Copy Terraform"
                  >
                    {copied ? <Check size={16} /> : <Copy size={16} />}
                  </button>
                  <button onClick={download} aria-label="Download Terraform">
                    <Download size={16} />
                  </button>
                </div>
              </div>
              <div className="tabs">
                <button
                  className={tab === "terraform" ? "active" : ""}
                  onClick={() => setTab("terraform")}
                >
                  Terraform
                </button>
                <button
                  className={tab === "plan" ? "active" : ""}
                  onClick={() => setTab("plan")}
                >
                  Plan review
                </button>
                <button
                  className={tab === "setup" ? "active" : ""}
                  onClick={() => setTab("setup")}
                >
                  Connection
                </button>
              </div>
              {tab === "terraform" && (
                <>
                  <div className="file-title">
                    <span /> main.tf{" "}
                    <small>
                      {code !== session.code
                        ? "Unsaved edits · new plan required"
                        : "Review before deployment"}
                    </small>
                  </div>
                  <textarea
                    className="code-editor"
                    spellCheck={false}
                    aria-label="Terraform code"
                    value={code}
                    disabled={!!running || hasInfrastructure}
                    onChange={(e) => {
                      setCode(e.target.value);
                      setConfirmed(false);
                    }}
                  />
                  <div className="code-actions">
                    <button
                      className="secondary"
                      disabled={
                        !!running || hasInfrastructure || code === session.code
                      }
                      onClick={() => action("save")}
                    >
                      Save changes
                    </button>
                    <button
                      className="primary"
                      disabled={
                        !!running ||
                        hasInfrastructure ||
                        health?.mode !== "live"
                      }
                      onClick={() => action("plan")}
                    >
                      Create Terraform plan
                    </button>
                  </div>
                </>
              )}
              {tab === "plan" && (
                <div className="detail-panel">
                  <h3>Review the exact changes</h3>
                  <p>
                    A real plan initializes providers, validates configuration,
                    and checks your cloud account. Approval expires after 15
                    minutes.
                  </p>
                  {session.plan ? (
                    <>
                      <div className="plan-stats">
                        <span>
                          <b>{session.plan.creates}</b> create
                        </span>
                        <span>
                          <b>{session.plan.updates}</b> update
                        </span>
                        <span>
                          <b>{session.plan.deletes}</b> delete
                        </span>
                      </div>
                      <ul className="changes">
                        {session.plan.changes.map((change) => (
                          <li key={change.address}>
                            <code>{change.address}</code>
                            <span>{change.actions.join(" → ")}</span>
                          </li>
                        ))}
                      </ul>
                      {!session.plan.changes.length && (
                        <p>No infrastructure changes.</p>
                      )}
                      <label className="check-row">
                        <input
                          type="checkbox"
                          checked={confirmed}
                          onChange={(e) => setConfirmed(e.target.checked)}
                        />
                        I reviewed this plan and understand it may incur cloud
                        charges.
                      </label>
                      {session.plan.deletes > 0 && (
                        <label className="check-row">
                          <input
                            type="checkbox"
                            checked={destructive}
                            onChange={(e) => setDestructive(e.target.checked)}
                          />
                          I explicitly approve deletions and replacements.
                        </label>
                      )}
                      <button
                        className="primary"
                        disabled={
                          !!running ||
                          !health?.apply_enabled ||
                          session.status !== "planned" ||
                          code !== session.code ||
                          !confirmed ||
                          (!!session.plan.deletes && !destructive)
                        }
                        onClick={() => action("apply")}
                      >
                        Apply reviewed plan
                      </button>
                      {!health?.apply_enabled && (
                        <p>Deployment is disabled in backend settings.</p>
                      )}
                    </>
                  ) : (
                    <div className="blank-plan">
                      <ShieldCheck size={30} />
                      <p>
                        {health?.mode === "demo"
                          ? "Demo mode never creates a real plan or provisions infrastructure."
                          : "Create a plan from the Terraform tab to see proposed changes."}
                      </p>
                    </div>
                  )}
                </div>
              )}
              {tab === "setup" && (
                <div className="detail-panel">
                  <h3>Connect your cloud</h3>
                  <p>
                    Configure identity on the trusted backend. Never paste keys
                    into chat or Terraform.
                  </p>
                  <dl>
                    <dt>AI mode</dt>
                    <dd>
                      {health?.mode}
                      {health?.mode === "live" && !health.ai_configured
                        ? " · API key missing"
                        : ""}
                    </dd>
                    <dt>Terraform</dt>
                    <dd>
                      {health?.terraform_installed
                        ? "Installed"
                        : "Not installed"}
                    </dd>
                    <dt>Deployment</dt>
                    <dd>{health?.apply_enabled ? "Enabled" : "Disabled"}</dd>
                  </dl>
                  <p>
                    AWS: use a scoped role or AWS_PROFILE.
                    <br />
                    GCP: use Application Default Credentials.
                    <br />
                    Azure: use a scoped service principal or workload identity.
                  </p>
                  <p>
                    This installation serves one trusted operator. Public
                    multi-user hosting requires separate identities, isolated
                    runners, and tenant state.
                  </p>
                </div>
              )}
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
