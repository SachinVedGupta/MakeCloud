"use client";
import { useEffect, useState } from "react";
import { CloudConnection, Health, clouds } from "./types";

type Instructions = { connection: CloudConnection; console_url?: string; authorization_url?: string; permissions?: string; template?: unknown; parameters?: unknown; setup_script?: string; setup_note?: string; target_fields?: {key: string; label: string; placeholder: string}[] };
async function connectionApi(path: string, method = "GET", data?: unknown) {
  const response = await fetch("/api/cloud/connections" + path, { method, headers: { "Content-Type": "application/json" }, body: data === undefined ? undefined : JSON.stringify(data), cache: "no-store" });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the connection details and try again.");
  return result;
}

export function CloudAccounts({ health, connections, refresh }: { health: Health | null; connections: CloudConnection[]; refresh: () => Promise<void> }) {
  const [cloud, setCloud] = useState("aws");
  const [label, setLabel] = useState("");
  const [profile, setProfile] = useState("storage");
  const [instructions, setInstructions] = useState<Instructions | null>(null);
  const [target, setTarget] = useState("");
  const [identityFields, setIdentityFields] = useState<Record<string, string>>({});
  const [projects, setProjects] = useState<{ id: string; label: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [remove, setRemove] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const choices = connections.map((item) => item.id).join(",");
  const verifying = connections.some((item) => item.status === "verifying");
  useEffect(() => {
    if (!verifying) return;
    const timer = setInterval(() => { refresh().catch(() => {}); }, 5000);
    return () => clearInterval(timer);
  }, [verifying, refresh]);
  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const cid = fragment.get("connected");
    const failure = fragment.get("connection_error");
    if (cid || failure) {
      if (failure) setError(failure);
      if (cid) setChoice(cid);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);
  useEffect(() => {
    if (!choice || !choices.split(",").includes(choice)) return;
    let canceled = false;
    (async () => {
      try {
        const result = await connectionApi("/" + choice + "/instructions");
        if (!canceled) { setInstructions(result); setIdentityFields({}); setTarget(result.connection.project_id || result.connection.subscription_id || (result.connection.account_id ? `arn:aws:iam::${result.connection.account_id}:role/MakeCloud-${result.connection.id}` : "")); setProjects([]); }
      } catch (e) { if (!canceled) setError((e as Error).message); }
    })();
    return () => { canceled = true; };
  }, [choice, choices]);
  async function perform(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const download = (value: unknown, name: string) => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
  };
  const selected = instructions?.connection;
  const hcp = health?.runner === "hcp";
  return <section className="cloud-accounts"><h3>Your cloud accounts</h3><p>Connect in your browser. {hcp ? "HCP Terraform runs your reviewed infrastructure changes. Each connection belongs to one MakeCloud project, keeping its state separate." : "MakeCloud runs your reviewed infrastructure changes."} You keep ownership of your cloud account.</p>
    {connections.map((row) => <div className="connection-card" key={row.id}><div><strong>{row.label}</strong><small>{clouds[row.cloud]} · {row.status || "configured"}{row.account_id ? " · " + row.account_id : ""}{row.project_id ? " · " + row.project_id : ""}{row.subscription_id ? " · " + row.subscription_id : ""}</small>{row.error && <p className="error">{row.error}</p>}</div>{row.method && row.status !== "revoked" && <><button className="secondary" disabled={busy} onClick={() => { setChoice(row.id); }}>Open setup</button><button className="text-button" disabled={busy} onClick={() => setRemove(row.id)}>Disconnect</button></>}</div>)}
    {remove && <div className="notice"><p>Remove this connection from MakeCloud? Existing deployments must be cleaned up first. To revoke cloud permissions immediately, use your cloud console.</p><button className="danger" disabled={busy} onClick={() => perform(async () => { const result = await connectionApi("/" + remove, "DELETE"); setNotice(result.message); setRemove(null); setInstructions(null); await refresh(); })}>Confirm disconnect</button><button className="text-button" onClick={() => setRemove(null)}>Keep connection</button></div>}
    <div className="fields"><label>Cloud<select value={cloud} disabled={busy} onChange={(e) => setCloud(e.target.value)}>{Object.entries(clouds).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select></label><label>Connection name<input value={label} maxLength={100} disabled={busy} placeholder="My development account" onChange={(e) => setLabel(e.target.value)} /></label></div>
    {(cloud === "aws" || hcp) && <label>Permissions<select value={profile} disabled={busy} onChange={(e) => setProfile(e.target.value)}><option value="storage">Storage only{cloud === "aws" ? " · makecloud-* buckets" : ""}</option><option value="standard">Infrastructure · broad access in a dedicated account</option></select></label>}
    {!hcp && cloud === "google" && <p className="fine-print">Google consent grants Cloud API access under your existing permissions. MakeCloud limits this connection to the selected project and reviewed storage or Cloud Run resource types. Use a dedicated project.</p>}
    {!hcp && cloud === "azurerm" && <p className="fine-print">An Azure subscription owner approves Contributor access through Lighthouse. Use a dedicated subscription. You can remove the delegation in Azure’s Service providers page.</p>}
    <button className="primary" disabled={busy || !health?.onboarding?.[cloud]} onClick={() => perform(async () => { const result: Instructions = await connectionApi("/begin", "POST", { cloud, label, profile }); if (result.authorization_url) { window.location.assign(result.authorization_url); return; } setChoice(""); setInstructions(result); setTarget(""); setIdentityFields({}); setProjects([]); await refresh(); })}>{busy ? "Working…" : cloud === "google" && !hcp ? "Connect with Google" : "Start cloud setup"}</button>
    {!health?.onboarding?.[cloud] && <p className="notice">The MakeCloud operator needs to enable {clouds[cloud]} onboarding before you can connect. No credentials need to be pasted into chat.</p>}
    {selected && selected.status !== "revoked" && <div className="onboarding-steps"><h4>Finish {selected.label}</h4>
      {instructions.permissions && <div className="notice">{instructions.permissions}</div>}
      {instructions.setup_note && <p>{instructions.setup_note}</p>}
      {instructions.setup_script && <details><summary>Review cloud setup script</summary><pre>{instructions.setup_script}</pre><button className="secondary" onClick={() => perform(async () => { await navigator.clipboard.writeText(instructions.setup_script!); setNotice("Setup script copied. Review it in Cloud Shell before running it."); })}>Copy setup script</button></details>}
      {instructions.console_url && <a className="primary" href={instructions.console_url} target="_blank" rel="noopener noreferrer">Review and approve in {clouds[selected.cloud]}</a>}
      {!hcp && selected.cloud === "aws" && <p>Review the stack’s IAM permissions, then create it. Copy its RoleArn output below. If the quick setup link is unavailable, upload the downloaded template in CloudFormation and enter the displayed parameters.</p>}
      {!hcp && selected.cloud === "azurerm" && <p>Select your subscription in Azure, review the delegation, then deploy it. Paste that subscription’s ID below. This portal link expires in 30 minutes; reopen setup to renew it.</p>}
      {instructions.template !== undefined && <button className="text-button" onClick={() => download(instructions.template, "makecloud-connection.json")}>Download permission template</button>}
      {instructions.parameters !== undefined && <details><summary>CloudFormation parameters</summary><pre>{JSON.stringify(instructions.parameters, null, 2)}</pre></details>}
      {!hcp && selected.cloud === "google" && <><button className="secondary" disabled={busy} onClick={() => perform(async () => { const result = await connectionApi("/" + selected.id + "/projects"); setProjects(result.projects); if (result.truncated) setNotice("Showing the first 1,000 projects. Enter a project ID manually if yours is missing."); })}>Load my Google Cloud projects</button>{!!projects.length && <label>Project<select value={target} onChange={(e) => setTarget(e.target.value)}><option value="">Choose a project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.label} · {p.id}</option>)}</select></label>}</>}
      {instructions.target_fields ? instructions.target_fields.map((field) => <label key={field.key}>{field.label}<input value={identityFields[field.key] || ""} placeholder={field.placeholder} maxLength={200} disabled={busy} onChange={(e) => setIdentityFields((values) => ({...values, [field.key]: e.target.value.trim()}))} /></label>) : <label>{selected.cloud === "aws" ? "Role ARN" : selected.cloud === "google" ? "Project ID" : "Subscription ID"}<input value={target} maxLength={200} disabled={busy} onChange={(e) => setTarget(e.target.value.trim())} /></label>}
      <button className="primary" disabled={busy || (instructions.target_fields ? instructions.target_fields.some((field) => !identityFields[field.key]) : !target)} onClick={() => perform(async () => { const result = await connectionApi("/" + selected.id + "/verify", "POST", { target: instructions.target_fields ? JSON.stringify(identityFields) : target }); setNotice(result.status === "verifying" ? "HCP Terraform is checking your cloud identity. This page will update when it finishes." : "Cloud identity checked. Select this connection in your project; the Terraform plan checks the exact workload permissions."); setInstructions(null); setChoice(""); await refresh(); })}>Verify cloud access</button>
    </div>}
    {notice && <p role="status" className="notice">{notice}</p>}{error && <p role="alert" className="error">{error}</p>}
    <p className="fine-print">Disconnecting in MakeCloud does not revoke the cloud’s permission grant. {hcp ? "Delete the AWS bootstrap stack or role, the Google workload identity pool and service account, or the Azure application and its role assignments to revoke access." : "AWS users delete the bootstrap stack or role; Google users remove MakeCloud’s grant from their Google account; Azure users remove the Lighthouse delegation."}</p>
  </section>;
}
