# MakeCloud

Describe infrastructure in a clean chat interface, gather requirements, generate cloud-specific Terraform, review a real plan, and explicitly apply that saved plan. The workflow is cloud agnostic; the resulting resources are specific to AWS, Azure, or GCP.

## Run locally

Requires Python 3.12+, Node 22+, and npm. Terraform 1.5+ is needed for live plans; Docker includes Terraform. No AI or cloud credentials are required for demo mode.

```bash
git clone https://github.com/SachinVedGupta/MakeCloud.git
cd MakeCloud
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
npm ci
```

Start the API in one terminal:

```bash
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 5000 --workers 1 --env-file .env
```

Start the frontend in another:

```bash
npm run dev
```

Open http://localhost:3000. Describe a private storage bucket, answer the five requirements questions, inspect the generated Terraform, edit/save it, and copy or download `main.tf`. Conversations survive reloads and backend restarts.

**Demo mode is storage-only and never provisions infrastructure.** Budget and arbitrary requirements are collected but are not interpreted by the deterministic demo templates. AWS, Azure, and GCP examples are available. Azure requires a subscription UUID and GCP requires a project ID in the final answer. Demo is a product walkthrough, not evidence of a live cloud deployment.

## Live AI and cloud deployment

Set the following in `.env` and restart the API:

```dotenv
MAKECLOUD_MODE=live
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o
MAKECLOUD_ENABLE_APPLY=false
```

`OPENAI_BASE_URL` can point to an OpenAI-compatible API supporting JSON chat completions. Your requirements and recent conversation are sent to that service. The API key stays on the backend. Live generation supports resources belonging to one of the three selected providers; the policy deliberately rejects modules, remote backends, provisioners, file access, inline credentials/passwords, and non-selected providers. HCL parsing/policy is an early check; only a successful `terraform validate` and `terraform plan` establishes provider/schema validity. Unsupported AI output is returned as an actionable error; requirements can be corrected and retried.

Install Terraform on the backend and configure least-privilege cloud identity there:

| Cloud | Identity | Requirements |
| --- | --- | --- |
| AWS | Scoped workload role, or `AWS_PROFILE` with local CLI credentials | Region, resource/project name |
| GCP | Application Default Credentials or workload identity | Existing project ID, region, enabled APIs |
| Azure | Workload identity or `ARM_*` service principal environment | Subscription ID, region |

Do not paste cloud keys into chat or generated code. The model and browser do not need them. All projects in an installation share the operator's configured cloud identity.

Use **Create Terraform plan**. The backend runs `fmt`, `init`, `validate`, `plan -out`, and `show -json`, displaying resource addresses and create/update/delete actions without returning raw secret-bearing plan values. Costs are not estimated by this release: inspect cloud pricing before proceeding. If the plan is correct, enable `MAKECLOUD_ENABLE_APPLY=true`, restart the API, refresh the UI, and acknowledge the plan. Deletions and replacements require an additional checkbox. Approval lasts 15 minutes, is single-use, and is invalidated when code changes. Apply executes only the saved plan with matching configuration and plan hashes.

Terraform operations run synchronously with a five-minute timeout per command; the UI shows progress and prevents duplicate submissions. Reloading a running conversation polls for completion. Failed or interrupted apply may have created resources: inspect the cloud account and retained state before any further operation. Completed or failed deployment workspaces are intentionally locked against casual edits. This release has no automated destroy button; use Terraform from the trusted operator workspace for deliberate cleanup.

## Operator security and hosting

**This release is for one trusted operator per installation. It is not a public multi-tenant SaaS.** AI-generated Terraform and provider plugins must be treated as privileged code. The allowlist is not an execution sandbox. Run the backend in a dedicated environment, keep credentials scoped, and never share the operator login with untrusted users.

The Next.js frontend proxies `/api/cloud/*` to `MAKECLOUD_BACKEND_URL`, so browsers never call localhost on a different machine or mix HTTP and HTTPS APIs. Local development binds both services to loopback. `npm start` explicitly sets `MAKECLOUD_LOCAL_ONLY=true` for local production testing; do not set that flag on a remotely reachable frontend. Hosted production fails closed without an operator password, including requests with a spoofed localhost Host header. For remote hosting:

- Set a long random `MAKECLOUD_API_TOKEN` identically on frontend and backend. Direct non-loopback backend requests require that token.
- Set `MAKECLOUD_OPERATOR_PASSWORD` on the frontend. HTTP Basic authentication uses username `operator`. Remote frontend access is blocked without a password. Use HTTPS.
- Use exactly **one API worker and one backend replica**. Locks and SQLite coordinate one process, not distributed workers. Disable API autoscaling.
- Persist `MAKECLOUD_DATA_DIR` on a protected volume. Sessions, Terraform state, saved plans, and error logs live there with restrictive filesystem permissions. Back it up securely; plans and state can contain sensitive data.
- The Fly deployment is manual through Actions, includes Terraform, and mounts `/data`. Create `makecloud_data` first, set the API token as a Fly secret, and deploy a single machine. Deploy the frontend separately with backend URL, API token, and operator password. The existing Fly app name is retained; change it for a new instance.

Before a public multi-user launch, implement account authentication and per-project authorization, per-tenant scoped cloud identity, isolated execution workers, encrypted durable state, background jobs/cancellation, audit history, quotas, billing/cost estimates, secret management, and reviewed cleanup/recovery. Do not expose the shared operator API as a substitute.

**Credential incident:** historical prototype files committed AWS and Terraform Cloud credentials. Current files remove these values; Git history still contains them. Revoke/rotate every exposed credential, audit its usage, and separately coordinate history cleanup with existing clones. Removing a current file does not revoke a credential.

## Verification

```bash
python -m pytest -q tests
npm run lint
npm run typecheck
npm run build
npm start
# Integrated smoke test starts its own servers on ports 3101 and 5101:
python tests/proxy_smoke.py
```

The regression suite covers AWS/Azure/GCP demo generation, input retries, persistence/restarts, HCL policy rejection, live AI adapter behavior with mocked responses, authentication, plan expiration, destructive-change acknowledgment, approval invalidation, Terraform failure handling, exact saved-plan apply and tampering. Real AI requests, provider downloads, cloud plans and deployments need working external credentials/network; mocked coverage is not a claim those operations ran live.

The automated proxy smoke test runs the built frontend and backend together for all three cloud demos, code edits/reloads, cross-origin rejection, remote frontend authentication, fail-closed hosted configuration, and graceful backend downtime. Browser-based visual verification requires an installed browser and remains separate from these HTTP checks.
