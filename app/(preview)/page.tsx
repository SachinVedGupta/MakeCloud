"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Cloud, Plus, ArrowUp, RefreshCw, ShieldCheck, LoaderCircle, Code2, CircleAlert, Archive, Download, LogOut, X } from "lucide-react";
import { CodeTools, ConnectionPanel, DeploymentPanel, HistoryPanel, PlanPanel, ProposalPanel, RequirementsPanel } from "../../components/workspace";
import { CloudAccounts } from "../../components/cloud-accounts";
import { CloudConnection, Health, Recent, Session, clouds } from "../../components/types";

async function api(path: string, method = "GET", data?: unknown) {
  const request = () => fetch("/api/cloud/" + path, { method, headers: { "Content-Type": "application/json" }, body: data === undefined ? undefined : JSON.stringify(data), cache: "no-store" });
  let response: Response;
  try { response = await request(); }
  catch (e) { if (method !== "GET") throw e; response = await request(); }
  // A sleeping free backend may need more than one proxy timeout to wake.
  // Repeat reads only; never repeat a deployment or other mutation.
  if (method === "GET" && [502, 503, 504].includes(response.status)) response = await request();
  let result;
  try { result = await response.json(); } catch { throw new Error("The server returned an unexpected response. Check your connection and reload."); }
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check your input and try again.");
  return result;
}
const runningStatus = (session: Session | null) => !!session && ["planning", "applying"].includes(session.status);
const panels = ["requirements", "proposal", "terraform", "plan", "deployment", "history", "connection"];

export default function MakeCloud() {
  const [session, setSession] = useState<Session | null>(null);
  const [recent, setRecent] = useState<Recent[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [connections, setConnections] = useState<CloudConnection[]>([]);
  const [account, setAccount] = useState<{ id: string; email: string } | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [input, setInput] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState("requirements");
  const [archived, setArchived] = useState(false);
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [showAccounts, setShowAccounts] = useState(false);
  const [notice, setNotice] = useState("");
  const [resetToken, setResetToken] = useState("");
  const sessionRef = useRef<Session | null>(null);
  const accountRef = useRef<string>("operator");
  const loadVersion = useRef(0);
  const loadTarget = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const dirty = !!session && code !== session.code;
  const running = runningStatus(session);
  const blocked = !!busy || running || !!session?.recovery_required || !!session?.archived;
  const draftKey = useCallback((id: string) => `makecloud-draft:${accountRef.current}:${id}`, []);
  const adopt = useCallback((next: Session) => {
    if (sessionRef.current?.id === next.id && sessionRef.current.updated_at > next.updated_at) return;
    const previous = sessionRef.current;
    sessionRef.current = next;
    loadTarget.current = next.id;
    setSession(next);
    let editor = next.code;
    try {
      const draft = JSON.parse(localStorage.getItem(draftKey(next.id)) || "null");
      if (draft && draft.revision === next.revision) editor = draft.code;
      else if (draft) setError("A local edit belongs to an older revision. Its text is kept in this browser; export before restoring it manually.");
    } catch { /* A malformed browser draft does not block server state. */ }
    setCode(editor);
    localStorage.setItem(`makecloud-session:${accountRef.current}`, next.id);
    if (next.proposal && previous?.proposal?.id !== next.proposal.id) setTab("proposal");
    if (next.status === "planned" && previous?.status !== "planned") setTab("plan");
    if (["applied", "destroyed", "apply_failed", "interrupted", "canceled_apply"].includes(next.status) && previous?.status !== next.status) setTab("deployment");
  }, [draftKey]);
  const load = useCallback(async (id: string, polling = false) => {
    if (polling && loadTarget.current !== id) return;
    if (!polling) loadTarget.current = id;
    const version = ++loadVersion.current;
    try {
      const next = await api("sessions/" + id);
      if (version === loadVersion.current) adopt(next);
    } catch (e) { if (version === loadVersion.current) setError((e as Error).message); }
  }, [adopt]);
  const refresh = useCallback(async () => {
    try {
      const [status, list, available] = await Promise.all([api("health"), api(`sessions?archived=${archived}`), api("connections")]);
      setHealth(status); setRecent(list); setConnections(available);
    } catch (e) { setError((e as Error).message); }
  }, [archived]);
  useEffect(() => {
    let canceled = false;
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (fragment.has("connected") || fragment.has("connection_error")) setShowAccounts(true);
    const verifyToken = fragment.get("verify");
    const reset = fragment.get("reset");
    if (reset) setResetToken(reset);
    if (verifyToken || reset) window.history.replaceState(null, "", window.location.pathname);
    if (verifyToken) api("auth/verify", "POST", { token: verifyToken }).then((result) => setNotice(result.message)).catch((e) => setError(e.message));
    (async () => {
      try {
        const status = await api("health");
        if (canceled) return;
        setHealth(status);
        try {
          const identity = await api("auth/me");
          if (canceled) return;
          accountRef.current = identity.id; setAccount(identity);
          const id = localStorage.getItem(`makecloud-session:${identity.id}`);
          if (id) await load(id);
        } catch { setAccount(null); }
      } catch (e) { if (!canceled) setError((e as Error).message); }
      if (!canceled) setAuthReady(true);
    })();
    return () => { canceled = true; };
  }, [load]);
  useEffect(() => { if (account) refresh(); }, [account, refresh]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [session?.messages.length, busy]);
  const sessionId = session?.id;
  useEffect(() => {
    if (!sessionId || !running || busy) return;
    let canceled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await load(sessionId, true);
      if (!canceled) timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 5000);
    return () => { canceled = true; clearTimeout(timer); };
  }, [sessionId, running, busy, load]);
  async function send(text = input.trim()) {
    if (!text || busy || session?.archived || dirty) return;
    ++loadVersion.current;
    setBusy("Thinking"); setError("");
    try {
      adopt(await api(session ? `sessions/${session.id}/messages` : "sessions", "POST", { message: text, revision: session?.revision }));
      setInput(""); await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(""); inputRef.current?.focus(); }
  }
  async function action(path: string, method = "POST", data?: unknown) {
    if (!session || busy) return;
    ++loadVersion.current;
    setBusy(path.startsWith("plan") ? "Starting plan" : path === "apply" ? "Starting deployment" : "Saving");
    setError("");
    try {
      const next = await api(`sessions/${session.id}/${path}`, method, data);
      if (path === "code" || path === "proposal" || path.includes("restore")) localStorage.removeItem(draftKey(session.id));
      adopt(next); await refresh();
      if (runningStatus(next)) setTab("deployment");
    } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  }
  function start() {
    if (dirty || busy) return;
    ++loadVersion.current;
    loadTarget.current = null;
    sessionRef.current = null; setSession(null); setCode(""); setInput(""); setError(""); setTab("requirements");
    localStorage.removeItem(`makecloud-session:${accountRef.current}`); inputRef.current?.focus();
  }
  async function signIn() {
    ++loadVersion.current;
    setBusy("Signing in"); setError("");
    try {
      if (resetToken) { const result = await api("auth/reset", "POST", { token: resetToken, password }); setNotice(result.message); setResetToken(""); setPassword(""); return; }
      if (register) { const result = await api("auth/register", "POST", { email, password, invite }); if (result.verification_required) { setNotice(result.message); setRegister(false); setPassword(""); return; } }
      const identity = await api("auth/login", "POST", { email, password });
      accountRef.current = identity.id; setAccount(identity); setPassword(""); setInvite("");
      sessionRef.current = null; setSession(null); setCode("");
      const id = localStorage.getItem(`makecloud-session:${identity.id}`); if (id) await load(id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  }
  async function signOut() {
    if (dirty) return;
    ++loadVersion.current;
    loadTarget.current = null;
    try { await api("auth/logout", "POST"); accountRef.current = ""; sessionRef.current = null; setAccount(null); setSession(null); setCode(""); setRecent([]); setConnections([]); } catch (e) { setError((e as Error).message); }
  }
  async function exportProject() {
    if (!session) return;
    try {
      const result = await api(`sessions/${session.id}/export`);
      if (dirty) result.unsaved_local_edit = code;
      const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = `${session.spec.name || "makecloud"}-project.json`; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError((e as Error).message); }
  }
  if (health?.auth_mode === "accounts" && authReady && !account) return <main className="auth-shell"><div className="auth-card"><Cloud size={35} /><h1>MakeCloud</h1><p>{resetToken ? "Choose a new password" : register ? "Create your cloud workspace" : "Sign in to your cloud workspace"}</p>
    <form onSubmit={(e) => { e.preventDefault(); signIn(); }}>{!resetToken && <label>Email<input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>}<label>Password<input type="password" required minLength={12} maxLength={128} autoComplete={register || resetToken ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} /></label>{register && health.registration !== "public" && <label>Invitation code<input required value={invite} onChange={(e) => setInvite(e.target.value)} /></label>}{notice && <div role="status" className="notice">{notice}</div>}{error && <div role="alert" className="error">{error}</div>}<button className="primary" disabled={!!busy}>{busy || (resetToken ? "Reset password" : register ? "Create account" : "Sign in")}</button></form>
    {!resetToken && <><button className="text-button" onClick={() => { setRegister(!register); setError(""); }}>{register ? "Already have an account? Sign in" : "Create an account"}</button><div className="account-links">{["reset", "verify"].map((purpose) => <button className="text-button" disabled={!!busy || !email} key={purpose} onClick={async () => { setBusy("Requesting email"); setError(""); try { const result = await api("auth/request-link", "POST", { email, purpose }); setNotice(result.message); } catch (e) { setError((e as Error).message); } finally { setBusy(""); } }}>{purpose === "reset" ? "Forgot password?" : "Resend verification email"}</button>)}</div><p className="fine-print">Enter your email above to request a link.</p></>}
  </div></main>;
  return <div className="app-shell">
    <aside className="sidebar"><button className="brand" onClick={start} disabled={dirty}><span className="brand-mark"><Cloud size={23} /></span>MakeCloud</button><button className="new-button" onClick={start} disabled={!!busy || dirty}><Plus size={17} />New project</button><div className="sidebar-label"><p className="eyebrow">{archived ? "ARCHIVED PROJECTS" : "YOUR PROJECTS"}</p><button aria-label={archived ? "Show active projects" : "Show archived projects"} onClick={() => setArchived(!archived)}><Archive size={14} /></button></div><nav aria-label="Projects">{recent.map((item) => <button className={`project ${item.id === session?.id ? "selected" : ""}`} key={item.id} disabled={!!busy || dirty} onClick={() => load(item.id)}><Cloud size={16} /><span>{item.title}<small>{item.cloud ? clouds[item.cloud] + " · " : ""}{item.status.replaceAll("_", " ")}</small></span></button>)}{!recent.length && <p className="empty-history">{archived ? "No archived projects." : "Your projects will appear here."}</p>}</nav><div className="sidebar-footer"><ShieldCheck size={18} /><span>Review before cloud changes<small>{account?.email || "Connecting…"}</small>{health?.auth_mode === "accounts" && <button className="text-button" disabled={dirty} onClick={signOut}><LogOut size={12} />Sign out</button>}</span></div></aside>
    <main><header className="topbar"><div><span className="eyebrow">INFRASTRUCTURE, SIMPLIFIED</span><h1>{session ? session.spec.name || "Your cloud workspace" : "Build something that scales."}</h1></div><span className={`mode-pill ${health?.mode === "live" ? "live" : ""}`}><span />{health ? health.mode === "demo" ? "Blueprints · no deployment" : health.ai_configured ? "Live AI + cloud" : "Live cloud · blueprints" : "Connecting…"}</span></header>
      <div className="cloud-toolbar"><button className="secondary" onClick={() => setShowAccounts(true)}><Cloud size={16} />Your cloud accounts</button></div>
      {showAccounts && <div className="modal-backdrop"><div role="dialog" aria-modal="true" aria-label="Cloud accounts" className="cloud-modal"><button className="modal-close" aria-label="Close cloud accounts" onClick={() => setShowAccounts(false)}><X size={20} /></button><CloudAccounts health={health} connections={connections} refresh={refresh} /></div></div>}
      <div className="mobile-nav"><button className="secondary" disabled={!!busy || dirty} onClick={start}><Plus size={15} />New project</button><select aria-label="Open a project" disabled={!!busy || dirty} value={session?.id || ""} onChange={(e) => e.target.value ? load(e.target.value) : start()}><option value="">Projects</option>{recent.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></div>
      {error && <div className="error global-error" role="alert"><CircleAlert size={17} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError("")}><X size={16} /></button></div>}
      <div className={`workspace ${session ? "with-code" : ""}`}><section className="chat-panel">
        {!session ? <div className="welcome"><div className="hero-icon"><Cloud size={35} /></div><h2>Your idea.<br /><span>Your cloud.</span></h2><p>Describe what you are building. We will clarify the requirements, explain the trade-offs, and propose infrastructure you can review.</p><div className="examples">{["A private storage bucket on AWS", "A public container app on Google Cloud", "A static website with a CDN on AWS", "Private PostgreSQL on AWS"].map((text, index) => <button disabled={!!busy} key={text} onClick={() => { setInput(text); inputRef.current?.focus(); }}><span>{String(index + 1).padStart(2, "0")}</span>{text}<ArrowUp size={15} /></button>)}</div><p className="demo-note">{health?.mode === "demo" ? "Local blueprints support storage and container apps on AWS, Azure and Google Cloud, plus AWS sites and PostgreSQL. Demo never deploys." : "Ask questions, review code and approve a real plan before cloud changes."}</p></div> : <div className="messages" aria-live="polite">{session.messages.map((message, i) => <article className={`message ${message.role}`} key={i}><span className="avatar">{message.role === "user" ? "You" : <Cloud size={18} />}</span><div><small>{message.role === "user" ? "YOU" : "MAKECLOUD"}</small><p>{message.content}</p></div></article>)}{busy === "Thinking" && <p className="working"><LoaderCircle size={16} className="spin" />Thinking…</p>}<div ref={bottom} /></div>}
        {session?.error && <div className="error" role="alert"><CircleAlert size={17} /><span>{session.error}</span></div>}
        {running && <div className="operation-banner"><LoaderCircle size={16} className="spin" /><span>{session?.job?.stage.replaceAll("_", " ") || session?.status}<small>Saved operation · safe to reload</small></span><button className="secondary" disabled={!!busy} onClick={() => action("cancel", "POST")}>Cancel</button></div>}
        {session?.archived && <div className="notice">Archived project. Infrastructure is retained.<button className="text-button" onClick={() => action("archive", "PUT", { archived: false })}>Restore project</button></div>}
        {dirty && <div className="notice">Unsaved Terraform edits are kept in this browser. Save or discard them before sending a message or switching projects.</div>}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}><textarea ref={inputRef} aria-label="Message MakeCloud" value={input} maxLength={8000} disabled={!!busy || !!session?.archived || dirty} onChange={(e) => setInput(e.target.value)} placeholder={running || session?.recovery_required ? "Ask about this project or its status…" : session ? "Answer, ask a question, or describe a change…" : "Describe your infrastructure…"} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} /><button type="submit" disabled={!input.trim() || !!busy || !!session?.archived || dirty} aria-label="Send message">{busy === "Thinking" ? <LoaderCircle size={19} className="spin" /> : <ArrowUp size={20} />}</button></form><div className="composer-hint"><span>{health?.ai_configured && health.mode === "live" ? "Live AI · review assumptions and changes" : "Local blueprint assistant · credentials stay outside chat"}</span><button aria-label="Reload workspace" disabled={!!busy} onClick={() => { refresh(); if (session) load(session.id); }}><RefreshCw size={13} /></button></div>
      </section>
      {session && <section className="code-panel"><div className="code-header"><div><Code2 size={18} /><strong>Project workspace</strong><span className="status-label">{session.status.replaceAll("_", " ")}</span></div><div><button aria-label="Export project" onClick={exportProject}><Download size={16} /></button><button aria-label={session.archived ? "Restore project" : "Archive project"} title="Archiving retains cloud resources" disabled={running || dirty || !!busy} onClick={() => action("archive", "PUT", { archived: !session.archived })}><Archive size={16} /></button></div></div><div className="tabs" role="tablist" aria-label="Project workspace">{panels.map((panel) => <button key={panel} role="tab" aria-selected={tab === panel} className={tab === panel ? "active" : ""} onClick={() => setTab(panel)}>{panel === "proposal" ? `Proposal${session.proposal ? " •" : ""}` : panel.charAt(0).toUpperCase() + panel.slice(1)}</button>)}</div>
        {tab === "requirements" && <RequirementsPanel session={session} action={action} blocked={blocked || dirty} />}
        {tab === "proposal" && <ProposalPanel session={session} action={action} blocked={blocked || dirty} />}
        {tab === "terraform" && <>{session.code ? <><div className="file-title"><span />main.tf<small>{dirty ? "Unsaved local edits" : `Accepted revision ${session.revision}`}</small><CodeTools code={code} onError={setError} /></div><textarea className="code-editor" spellCheck={false} aria-label="Terraform code" value={code} disabled={blocked} onChange={(e) => { setCode(e.target.value); localStorage.setItem(draftKey(session.id), JSON.stringify({ revision: session.revision, code: e.target.value })); }} /><div className="code-actions"><button className="secondary" disabled={blocked || !dirty} onClick={() => action("code", "PUT", { code, revision: session.revision })}>Save changes</button>{dirty && <button className="text-button" onClick={() => { localStorage.removeItem(draftKey(session.id)); setCode(session.code); }}>Discard local edits</button>}<button className="primary" disabled={blocked || dirty || !!session.proposal || health?.mode !== "live"} onClick={() => action("plan", "POST", { revision: session.revision })}>Create Terraform plan</button></div></> : <div className="blank-plan"><Code2 size={32} /><p>Accept a proposal to create your first configuration revision.</p><button className="secondary" onClick={() => setTab("proposal")}>Review proposal</button></div>}</>}
        {tab === "plan" && <PlanPanel session={session} action={action} blocked={blocked || dirty} health={health} />}
        {tab === "deployment" && <DeploymentPanel session={session} action={action} blocked={blocked || dirty} />}
        {tab === "history" && <HistoryPanel session={session} action={action} blocked={blocked || dirty} />}
        {tab === "connection" && <ConnectionPanel session={session} action={action} blocked={blocked || dirty} health={health} connections={connections} />}
      </section>}
      </div>
    </main>
  </div>;
}
