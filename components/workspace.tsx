"use client";
import { useEffect, useState } from "react";
import { Check, Copy, Download, ShieldCheck, Clock3, History, TriangleAlert } from "lucide-react";
import { Action, CloudConnection, Health, Session, Spec, clouds, date, display } from "./types";

type Props = { session: Session; action: Action; blocked: boolean };
export function RequirementsPanel({ session, action, blocked }: Props) {
  const [spec, setSpec] = useState<Spec>(session.spec);
  const serverSpec = JSON.stringify(session.spec);
  useEffect(() => setSpec(JSON.parse(serverSpec)), [serverSpec]);
  const field = <K extends keyof Spec>(key: K, value: Spec[K]) => setSpec((s) => ({ ...s, [key]: value }));
  const changed = JSON.stringify(spec) !== JSON.stringify(session.spec);
  return <div className="detail-panel requirements-panel">
    <h3>What we are building</h3>
    <p>Facts stay with this project. Update them here or describe a change in chat.</p>
    {session.missing.length > 0 && <div className="notice">Still needed: {session.missing.map((s) => s.replaceAll("_", " ")).join(", ")}</div>}
    <div className="fields">
      <label>Cloud<select value={spec.cloud || ""} disabled={blocked || session.has_infrastructure} onChange={(e) => setSpec({ ...spec, cloud: e.target.value as Spec["cloud"], region: null, project_id: null, subscription_id: null })}><option value="">Choose a cloud</option>{Object.entries(clouds).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label>Workload<select value={spec.workload || ""} disabled={blocked} onChange={(e) => field("workload", e.target.value as Spec["workload"])}><option value="">Choose a workload</option><option value="storage">Private object storage</option><option value="static_site">Static site and CDN (AWS)</option><option value="container">Container application</option><option value="database">Private PostgreSQL (AWS)</option><option value="custom">Custom design (live AI)</option></select></label>
      <label>Project name<input value={spec.name || ""} disabled={blocked} placeholder="myproject" onChange={(e) => field("name", e.target.value || null)} /></label>
      <label>Region<input value={spec.region || ""} disabled={blocked || session.has_infrastructure} placeholder={spec.cloud === "azurerm" ? "eastus" : spec.cloud === "google" ? "us-central1" : "us-east-1"} onChange={(e) => field("region", e.target.value || null)} /></label>
      {spec.cloud === "google" && <label>Google project ID<input value={spec.project_id || ""} disabled={blocked || session.has_infrastructure} onChange={(e) => field("project_id", e.target.value || null)} /></label>}
      {spec.cloud === "azurerm" && <label>Azure subscription UUID<input value={spec.subscription_id || ""} disabled={blocked || session.has_infrastructure} onChange={(e) => field("subscription_id", e.target.value || null)} /></label>}
      <label>Environment<select disabled={blocked} value={spec.environment} onChange={(e) => field("environment", e.target.value as Spec["environment"])}><option value="development">Development</option><option value="production">Production</option></select></label>
      <label>Monthly budget (USD)<input type="number" min="0" disabled={blocked || spec.budget_unknown} value={spec.budget ?? ""} onChange={(e) => field("budget", e.target.value ? Number(e.target.value) : null)} /></label>
    </div>
    <label className="check-row"><input type="checkbox" disabled={blocked} checked={spec.budget_unknown} onChange={(e) => setSpec({ ...spec, budget_unknown: e.target.checked, budget: e.target.checked ? null : spec.budget })} />Budget undecided</label>
    {(spec.workload === "container" || spec.workload === "static_site") && <label>Network access<select disabled={blocked} value={spec.public === null ? "" : spec.public ? "public" : "private"} onChange={(e) => field("public", e.target.value ? e.target.value === "public" : null)}><option value="">Choose access</option><option value="private">Private</option><option value="public">Public internet access</option></select></label>}
    {spec.workload === "container" && <>
      <label>Container image<input disabled={blocked} value={spec.image || ""} placeholder="nginx:stable" onChange={(e) => field("image", e.target.value || null)} /></label>
      <div className="fields"><label>Container port<input disabled={blocked} type="number" min="1" max="65535" placeholder="80 or 8080" value={spec.port ?? ""} onChange={(e) => field("port", e.target.value ? Number(e.target.value) : null)} /></label><label>Instances<input disabled={blocked} type="number" min="1" max="10" value={spec.replicas} onChange={(e) => field("replicas", Number(e.target.value))} /></label></div>
      <label>PostgreSQL<select disabled={blocked} value={spec.database === null ? "" : String(spec.database)} onChange={(e) => field("database", e.target.value ? e.target.value === "true" : null)}><option value="">Choose an option</option><option value="false">No database</option><option value="true">Include PostgreSQL (AWS blueprint)</option></select></label>
      {spec.cloud === "aws" && <><label>ACM certificate ARN<input disabled={blocked} value={spec.certificate_arn || ""} placeholder="Required for public production HTTPS" onChange={(e) => field("certificate_arn", e.target.value || null)} /></label><label>HTTPS hostname<input disabled={blocked} value={spec.domain_name || ""} placeholder="app.example.com" onChange={(e) => field("domain_name", e.target.value || null)} /></label><p className="fine-print">After deployment, point this hostname at the load balancer in your DNS provider. The certificate must cover it.</p></>}
    </>}
    {spec.workload === "storage" && <label className="check-row"><input type="checkbox" disabled={blocked} checked={spec.versioning} onChange={(e) => field("versioning", e.target.checked)} />Keep object versions</label>}
    <label>Other requirements<textarea disabled={blocked} value={spec.notes} maxLength={6000} placeholder="Backups, traffic, availability or constraints" onChange={(e) => field("notes", e.target.value)} /></label>
    <button className="primary" disabled={blocked || !changed} onClick={() => action("requirements", "PUT", { updates: spec, revision: session.revision })}>Save requirements</button>
    <p className="fine-print">Defaults: development environment, private object storage, and versioned storage. Confirm the listening port for your container image. Region and account availability are verified when you create a real plan.</p>
  </div>;
}

export function ProposalPanel({ session, action, blocked }: Props) {
  const [view, setView] = useState("code");
  const proposal = session.proposal;
  if (!proposal) return <div className="blank-plan"><ShieldCheck size={32} /><h3>No pending changes</h3><p>Finish the requirements or ask for a change. Your accepted Terraform stays intact.</p></div>;
  return <div className="detail-panel proposal-panel"><h3>Review proposed changes</h3><p>{proposal.summary}</p><div className="segmented"><button className={view === "code" ? "selected" : ""} onClick={() => setView("code")}>Proposed Terraform</button><button className={view === "diff" ? "selected" : ""} onClick={() => setView("diff")}>Difference</button></div><pre className="code-preview" aria-label={view === "code" ? "Proposed Terraform" : "Terraform difference"}>{view === "code" ? proposal.code : proposal.diff || "No code changes."}</pre><div className="button-row"><button className="primary" disabled={blocked} onClick={() => action("proposal", "POST", { confirm: true, revision: session.revision, proposal_id: proposal.id })}><Check size={15} />Accept configuration</button><button className="secondary" disabled={blocked} onClick={() => action("proposal", "DELETE")}>Discard proposal</button></div><p className="fine-print">Accepting saves a new configuration revision. Cloud changes require a separate reviewed plan and approval.</p></div>;
}

export function PlanPanel({ session, action, blocked, health }: Props & { health: Health | null }) {
  const [confirmed, setConfirmed] = useState(false);
  const [destructive, setDestructive] = useState(false);
  const [overBudget, setOverBudget] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { setConfirmed(false); setDestructive(false); setOverBudget(false); }, [session.approval]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 10000); return () => clearInterval(timer); }, []);
  const plan = session.plan;
  const budgetExceeded = plan?.purpose !== "destroy" && plan?.cost?.monthly !== null && plan?.cost?.monthly !== undefined && session.spec.budget !== null && plan.cost.monthly > session.spec.budget;
  const expires = session.approval_expires_at;
  const expired = !!expires && expires * 1000 < now;
  return <div className="detail-panel"><h3>{plan?.purpose === "destroy" ? "Review infrastructure cleanup" : "Review your plan"}</h3><p>A real plan checks the cloud account and shows the changes Terraform will make.</p>
    {!plan ? <div className="blank-plan"><ShieldCheck size={32} /><p>{health?.mode === "demo" ? "Demo mode creates drafts only. No cloud account is accessed." : "Accept a configuration, select your cloud connection, then create a plan."}</p><button className="primary" disabled={blocked || !session.code || !!session.proposal || health?.mode !== "live"} onClick={() => action("plan", "POST", { revision: session.revision })}>Create Terraform plan</button></div> : <>
      <div className="plan-stats"><span><b>{plan.creates}</b>create</span><span><b>{plan.updates}</b>update</span><span><b>{plan.deletes}</b>delete</span></div>
      <div className="notice">Target: {plan.identity?.label || "Operator environment"}{plan.identity?.account_id ? ` · account ${plan.identity.account_id}` : ""}{plan.identity?.project_id ? ` · ${plan.identity.project_id}` : ""}{plan.identity?.subscription_id ? ` · ${plan.identity.subscription_id}` : ""}</div>
      <div className="cost-card"><strong>Cost estimate</strong><p>{plan.cost?.message || "Pricing has not been measured. Check provider pricing before approving."}</p>{plan.cost?.monthly !== null && plan.cost?.monthly !== undefined && <b>{plan.cost.currency} {plan.cost.monthly}/month</b>}</div>
      {plan.risks?.map((risk) => <p className="risk" key={risk}><TriangleAlert size={15} />{risk}</p>)}
      <ul className="changes">{plan.changes.map((change) => <li key={change.address}><code>{change.address}</code><span>{change.actions.join(" → ")}</span>{!!change.attributes?.length && <details><summary>Attribute changes</summary>{change.attributes.map((attr) => <div className="attribute" key={attr.name}><strong>{attr.name}</strong><del>{display(attr.before)}</del><ins>{display(attr.after)}</ins></div>)}</details>}</li>)}</ul>
      {!plan.changes.length && <p>No infrastructure changes.</p>}
      {expires && <p className={expired ? "risk" : "fine-print"}><Clock3 size={14} />{expired ? "Approval expired. Create a new plan." : `Approval expires ${date(expires)}`}</p>}
      <label className="check-row"><input type="checkbox" checked={confirmed} disabled={blocked || expired} onChange={(e) => setConfirmed(e.target.checked)} />I reviewed the target account, changes, permissions and possible charges.</label>
      {(plan.deletes > 0 || plan.purpose === "destroy") && <label className="check-row"><input type="checkbox" checked={destructive} disabled={blocked || expired} onChange={(e) => setDestructive(e.target.checked)} />I explicitly approve these deletions and replacements.</label>}
      {budgetExceeded && <label className="check-row risk"><input type="checkbox" checked={overBudget} disabled={blocked || expired} onChange={(e) => setOverBudget(e.target.checked)} />I acknowledge the estimate exceeds my monthly budget.</label>}
      <button className={plan.purpose === "destroy" ? "danger" : "primary"} disabled={blocked || expired || !health?.apply_enabled || session.status !== "planned" || !!session.proposal || !confirmed || (budgetExceeded && !overBudget) || ((plan.deletes > 0 || plan.purpose === "destroy") && !destructive)} onClick={() => action("apply", "POST", { approval: session.approval, confirm: confirmed, confirm_destructive: destructive, confirm_budget: overBudget, revision: session.revision })}>{plan.purpose === "destroy" ? "Apply reviewed cleanup" : "Apply reviewed plan"}</button>
      {!health?.apply_enabled && <p className="fine-print">Apply is disabled on this backend.</p>}
      <button className="text-button" disabled={blocked || health?.mode !== "live"} onClick={() => action("plan", "POST", { revision: session.revision, purpose: plan.purpose || "deploy" })}>Create a fresh plan</button>
    </>}
  </div>;
}

export function DeploymentPanel({ session, action, blocked }: Props) {
  const [recovery, setRecovery] = useState(false);
  return <div className="detail-panel"><h3>Deployment and recovery</h3><div className="status-card"><strong>{session.status.replaceAll("_", " ")}</strong><span>Configuration revision {session.revision}</span></div>
    {session.job && <dl><dt>Latest operation</dt><dd>{session.job.kind} · {session.job.status}</dd><dt>Progress</dt><dd>{session.job.stage.replaceAll("_", " ")}</dd></dl>}
    {Object.entries(session.outputs).length > 0 && <><h4>Resource outputs</h4><dl>{Object.entries(session.outputs).map(([name, output]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd className="output-value">{display(output.value)}</dd></div>)}</dl></>}
    {session.checks.map((check) => <div className="check-result" key={check.name}><strong>{check.name}</strong><span>{check.status.replaceAll("_", " ")}</span>{check.message && <p>{check.message}</p>}</div>)}
    {session.recovery_required && <div className="recovery-card"><TriangleAlert size={20} /><h4>Inspect possible partial changes</h4><p>Review the cloud account, protected workspace logs and retained Terraform state. A failed or canceled apply may have created resources.</p><label className="check-row"><input type="checkbox" checked={recovery} onChange={(e) => setRecovery(e.target.checked)} />I inspected state and understand resources may exist.</label><button className="secondary" disabled={!recovery || session.status === "applying"} onClick={() => action("recover", "POST", { confirm: true })}>Acknowledge recovery</button></div>}
    {session.has_infrastructure && !session.recovery_required && <><h4>Deliberate cleanup</h4><p>Generate a destroy plan for this project. Data retention or deletion protection may block cleanup. Cloud resources stay active until the reviewed cleanup succeeds.</p><button className="danger" disabled={blocked || session.mode !== "live" || !!session.proposal} onClick={() => action("plan", "POST", { purpose: "destroy", revision: session.revision })}>Create cleanup plan</button></>}
    {!session.has_infrastructure && !session.job && <p>No deployment has been confirmed. A draft or plan alone does not create resources.</p>}
  </div>;
}

export function HistoryPanel({ session, action, blocked }: Props) {
  return <div className="detail-panel"><h3>Configuration history</h3>{!session.revisions.length && <p>Accepted configurations will appear here.</p>}{[...session.revisions].reverse().map((rev) => <div className="revision-card" key={rev.revision}><History size={16} /><div><strong>Revision {rev.revision}</strong><small>{date(rev.at)}</small><p>{rev.summary}</p></div><button className="secondary" disabled={blocked || rev.revision === session.revision} onClick={() => action(`revisions/${rev.revision}/restore`, "POST", { confirm: true, revision: session.revision })}>Restore</button></div>)}<h4>Activity</h4><ul className="activity">{[...session.audit].reverse().map((event) => <li key={event.id}><strong>{event.action.replaceAll("_", " ")}</strong><small>{date(event.at)}</small>{event.detail && <p>{event.detail}</p>}</li>)}</ul></div>;
}

export function ConnectionPanel({ session, health, connections, action, blocked }: Props & { health: Health | null; connections: CloudConnection[] }) {
  return <div className="detail-panel"><h3>Cloud connection</h3><p>Keep credentials outside the chat and generated code. Choose a verified connection for this project. Use Your cloud accounts to connect through your browser.</p><label>Identity<select value={session.connection_id || ""} disabled={blocked || session.has_infrastructure} onChange={(e) => e.target.value && action("connection", "PUT", { connection_id: e.target.value })}><option value="">{health?.auth_mode === "operator" ? "Operator environment" : "Choose a scoped connection"}</option>{connections.filter((c) => c.cloud === session.spec.cloud && (!c.status || c.status === "connected")).map((c) => <option key={c.id} value={c.id}>{c.label || c.id}</option>)}</select></label>{health?.auth_mode === "accounts" && !connections.length && <div className="notice">Connect your cloud using Your cloud accounts before planning.</div>}<dl><dt>Assistant</dt><dd>{health?.ai_configured && health.mode === "live" ? "Live AI" : "Local blueprint assistant"}</dd><dt>Cloud operations</dt><dd>{health?.mode === "live" ? "Live mode" : "Demo · no deployment"}</dd><dt>Terraform</dt><dd>{health?.terraform_installed ? "Installed" : "Local binary unavailable"}</dd><dt>Execution environment</dt><dd>{health?.runner === "docker" ? "Isolated Docker runner" : "Trusted operator environment"}</dd><dt>Saved data</dt><dd>{health?.encrypted_storage ? "Encrypted projects, plans and state" : "Unavailable"}</dd></dl><p className="fine-print">Account execution requires a scoped connection, an isolated runner, and a separate encryption key. Live AI sends project requirements, current Terraform and recent conversation to the configured AI service.</p></div>;
}

export function CodeTools({ code, onError }: { code: string; onError: (error: string) => void }) {
  const [copied, setCopied] = useState(false);
  const download = () => { const url = URL.createObjectURL(new Blob([code], { type: "text/plain" })); const link = document.createElement("a"); link.href = url; link.download = "main.tf"; link.click(); URL.revokeObjectURL(url); };
  return <div><button aria-label="Copy Terraform" onClick={async () => { try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch { onError("Clipboard unavailable. Select the code or download it."); } }}>{copied ? <Check size={16} /> : <Copy size={16} />}</button><button aria-label="Download Terraform" onClick={download}><Download size={16} /></button></div>;
}
