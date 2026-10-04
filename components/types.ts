export type Spec = {
  cloud: "aws" | "google" | "azurerm" | null;
  workload: "storage" | "static_site" | "container" | "database" | "custom" | null;
  region: string | null; name: string | null; project_id: string | null;
  subscription_id: string | null; environment: "development" | "production";
  budget: number | null; budget_unknown: boolean; public: boolean | null;
  database: boolean | null; image: string | null; port: number | null; versioning: boolean;
  replicas: number; notes: string; certificate_arn: string | null; domain_name: string | null;
};
export type Plan = {
  changes: { address: string; actions: string[]; attributes?: { name: string; before: unknown; after: unknown }[] }[];
  creates: number; updates: number; deletes: number; risks?: string[];
  purpose?: string; revision?: number;
  identity?: { label?: string; account_id?: string; project_id?: string; subscription_id?: string };
  cost?: { status: string; monthly: number | null; currency: string; message: string };
};
export type Session = {
  id: string; status: string; mode: string; spec: Spec; missing: string[];
  revision: number; messages: { role: string; content: string }[];
  code: string; summary: string; error: string | null;
  approval: string | null; approval_expires_at: number | null; plan: Plan | null;
  proposal: { id: string; code: string; summary: string; base_revision: number; diff: string } | null;
  job: { id: string; kind: string; status: string; stage: string; created_at: number } | null;
  revisions: { revision: number; summary: string; at: number }[];
  audit: { id: string; action: string; detail: string; at: number }[];
  outputs: Record<string, { value: unknown; sensitive: boolean }>;
  checks: { name: string; status: string; message?: string }[];
  has_infrastructure: boolean; recovery_required: boolean; archived: boolean;
  connection_id: string | null; updated_at: number;
};
export type Health = {
  mode: string; auth_mode: string; ai_configured: boolean;
  terraform_installed: boolean; apply_enabled: boolean; runner: string; encrypted_storage: boolean;
  onboarding?: Record<string, boolean>; registration?: string;
};
export type Recent = { id: string; title: string; status: string; cloud: string | null; workload: string | null };
export type CloudConnection = { id: string; label: string; cloud: string; account_id?: string; project_id?: string; subscription_id?: string; status?: string; method?: string; workloads?: string[]; error?: string };
export type Action = (path: string, method?: string, data?: unknown) => Promise<void>;
export const clouds: Record<string, string> = { aws: "AWS", google: "Google Cloud", azurerm: "Azure" };
export const date = (seconds: number) => new Date(seconds * 1000).toLocaleString();
export const display = (value: unknown) => value === undefined || value === null ? "—" : typeof value === "string" ? value : JSON.stringify(value);
