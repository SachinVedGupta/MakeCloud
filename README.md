# MakeCloud

MakeCloud turns a chat into a persistent infrastructure project: clarify requirements, review proposed Terraform, choose a cloud identity, inspect a saved plan, and explicitly approve deployment. You can ask follow-up questions, revise the design, retain manual edits, and review cleanup later. The workflow spans AWS, Google Cloud and Azure; the Terraform resources remain specific to each provider.

## Hosted use

Customers can use MakeCloud through a hosted website without cloning it or installing Terraform. The operator configures the deployment once. See [hosting and cloud onboarding](HOSTING.md) for the HTTPS stack, self-service AWS/Google/Azure connections, accounts, persistent storage and exact release checks. See the [product plan and research decisions](HOSTED_PLAN.md) for scope and trade-offs. Actual public hosting and real cloud provisioning have not been verified in this session.

## Start locally

Use macOS or Linux, Python 3.12+, Node 22+, and npm. Terraform is needed for real planning; the demo needs no AI key or cloud credentials.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm ci
# Copy only if you do not already have a configuration:
test -f .env || cp .env.example .env
npm run dev:all
```

Open [MakeCloud](http://127.0.0.1:3000). For a local production preview, run `npm run build` and then `npm run start:all` instead. The launcher starts both services on loopback and stops them together when interrupted. It creates an in-memory installation token if none is configured. Local accounts use HTTP cookies for this loopback session; hosted accounts require HTTPS. Change `MAKECLOUD_DEV_UI_PORT` / `MAKECLOUD_DEV_API_PORT` if the defaults are occupied; the launcher checks both before startup. Its backend defaults to 5010 to avoid macOS AirPlay on port 5000.

Try: `A private storage bucket on AWS in us-east-1 named myproject with a $20 budget`. Facts already supplied are extracted; the assistant asks for missing facts. Ask `Why use versioning?` without changing the design. Review and accept the proposal, then edit or download `main.tf`. Conversations and accepted revisions survive reloads and restarts. Unsaved Terraform edits are retained in this browser and must be saved or discarded before switching projects.

**Demo never plans or deploys to a cloud.** Its assistant uses local reference designs and bounded explanations. Arbitrary requests require live AI and may still be rejected by the Terraform policy or provider. Desired budget is recorded, not a spending cap.

| Reference design | AWS | Google Cloud | Azure |
| --- | --- | --- | --- |
| Private object storage | S3, encryption, access block, versioning | Private versioned bucket | Private Blob storage, identity access |
| Container application | Fargate, load balancer, logs, scoped execution role | Cloud Run v2, explicit ingress | Container Apps, managed HTTPS |
| Static website and CDN | Private S3 origin, HTTPS CloudFront | Use live AI for a custom design | Use live AI for a custom design |
| Private PostgreSQL | Encrypted RDS, managed master secret, backups | Use live AI for a custom design | Use live AI for a custom design |
| Container and PostgreSQL | App-to-database security group rule | Use live AI for a custom design | Use live AI for a custom design |

Blueprints provision infrastructure. Upload static assets separately; provide a compatible container image and correct port. Google Cloud must be able to pull the image. Database integration, secret retrieval, migrations and private network access remain application work. AWS public production containers require an existing regional ACM certificate and its matching hostname. After deployment, point that hostname at the load balancer with your DNS provider; automatic DNS onboarding and certificate-domain verification are not implemented. Production database deletion protection and final snapshots are deliberate retention settings; cleanup can require a separate reviewed configuration change.

## Live AI and cloud workflow

In `.env`, set `MAKECLOUD_MODE=live` and configure `OPENAI_API_KEY` securely on the backend. The default model is `gpt-4o`; `OPENAI_BASE_URL` supports a compatible JSON chat-completions service. Keys stay outside chat and generated Terraform. The assistant receives named requirements, recent messages, accepted Terraform, configuration revision, deployment status and the redacted plan summary. It returns a validated answer, clarification or proposal. Answering a question does not modify code or approval.

Local blueprints also work in live mode without an AI key. Custom Terraform needs live AI. Policies reject extra providers, modules, provisioners, remote backends, local file functions and inline password/credential arguments. Provider region and project/subscription must match the project's requirements. Provider configuration uses literal targets; configure identity outside Terraform. Parsing is an early check; successful provider validation and an actual plan are required before apply.

1. Gather requirements and **accept a proposed configuration**. Acceptance creates a revision, not cloud resources.
2. Configure a scoped cloud identity. Account mode requires an authorized connection selected in the Connection panel.
3. **Create Terraform plan**. A background job runs format, initialization, validation, plan and JSON inspection. The UI shows progress, resource and attribute changes, risk warnings, target identity and pricing availability.
4. Review the exact plan. Enable `MAKECLOUD_ENABLE_APPLY=true` separately on the backend to permit deployment. Acknowledge charges and permissions; deletions/replacements and estimates above your budget require additional acknowledgment.
5. **Apply reviewed plan**. The backend checks configuration and plan hashes and executes the saved plan. Approval is single use, expires after 15 minutes, and is invalidated by code, requirements or connection changes.
6. Inspect outputs and application readiness. Terraform completion does not prove an application is reachable; connectivity is explicitly marked not run.
7. Continue asking questions or propose an update against retained state. Deployed projects keep their original cloud, region and account target; use a new project for migrations. **Create cleanup plan** for deliberate destruction; review and approve its deletions separately.

Jobs run on a two-thread executor with per-project exclusion and a configurable timeout per Terraform command. Cancel stops the subprocess or Docker container. A failed, canceled or interrupted apply may have changed resources. Inspect the account, protected logs and retained state, then acknowledge recovery and create a fresh plan. Jobs are persisted atomically with their project. A restart resumes queued work that never started and obtains fresh credentials; started operations are marked interrupted and require review. Applies are never silently replayed. Queued applies also recheck plan expiry. Chat explanations remain available during operations and recovery.

History records accepted revisions and project events. Restore creates another revision and requires a fresh plan. Project export contains conversation, requirements, code, history and safe outputs; it excludes credentials, raw state and approval tokens. Archiving is reversible and retains any cloud resources.

## Cloud connections and accounts

The default `MAKECLOUD_AUTH_MODE=operator` is for one trusted operator. The Terraform runner uses that operator's configured identity when no connection is selected:

| Cloud | Backend identity |
| --- | --- |
| AWS | Scoped workload role or local AWS profile/environment |
| Google Cloud | Application Default Credentials or workload identity for an existing project |
| Azure | Scoped service principal or workload identity with `ARM_*` configuration |

For invited accounts, set `MAKECLOUD_AUTH_MODE=accounts` on both services and `MAKECLOUD_INVITE_CODE` on the backend. Passwords must be 12–128 characters, are hashed with scrypt, and login tokens are stored as hashes with 24-hour expiry. Backend authorization checks ownership on every project route. Secure, HTTP-only, SameSite=Lax cookies are the hosted default so provider callbacks can return to the signed-in account; use HTTPS. Public registration is available with MAKECLOUD_REGISTRATION=public and working TLS SMTP. It requires email verification; password recovery invalidates previous login sessions. Registration and login attempts, messages and operations have limits.

Use **Your cloud accounts** for browser onboarding: AWS CloudFormation delegation with a unique external ID, Google Cloud consent and project selection, or Azure Lighthouse portal delegation. Connection tokens stay encrypted on the server, and execution acquires fresh credentials. A single matching verified connection automatically fills the project/subscription target; several connections require selection. Full operator configuration and permission boundaries are documented in [HOSTING.md](HOSTING.md).

For advanced operator-managed connections, configure `MAKECLOUD_CONNECTIONS_FILE` to a backend-only protected JSON file. Each entry has `id`, `label`, `owner` (account ID or email), `cloud`, target identifiers, optional `regions` and an `environment` mapping. Only the owner sees its non-secret metadata. Keep this file outside Git, restrict permissions, and populate credentials from your secret manager. For example, an AWS record has this shape with its credential environment supplied separately:

```json
[
  {
    "id": "aws-development",
    "label": "Development account",
    "owner": "person@example.com",
    "cloud": "aws",
    "account_id": "111111111111",
    "regions": ["us-east-1"],
    "environment": {}
  }
]
```

Scoped AWS connection environments accept `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`, and `AWS_DEFAULT_REGION`. Google accepts `GOOGLE_CREDENTIALS`, `GOOGLE_OAUTH_ACCESS_TOKEN`, and `GOOGLE_PROJECT`; declare matching `project_id` metadata. Azure accepts `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`, `ARM_USE_OIDC`, and `ARM_OIDC_TOKEN`; declare matching `subscription_id`. Scoped connections do not inherit the operator's cloud credential environment. AWS plans also insert `allowed_account_ids` into the executed provider configuration. IAM permissions must restrict identities to the intended account, project, region and operations.

Account cloud execution fails closed unless `MAKECLOUD_RUNNER=docker` and `MAKECLOUD_ENCRYPTION_KEY` are configured. Each Terraform command uses a separate constrained container with a read-only root, dropped capabilities, resource limits and only that project's workspace mounted. This isolates local files; it does not limit what the scoped cloud credentials are allowed to provision. Browser cloud onboarding, verified public registration, password reset and a durable single-instance job queue are implemented. Billing, distributed workers, stronger host isolation and unrestricted custom-resource support remain outside this beta. Real provider grants, actual container execution and public hosting still require configured live verification.

## Storage and hosting

Project records in SQLite and retained Terraform state, plans and logs are encrypted with Fernet. Operator mode generates a protected local encryption key if none is supplied. Hosted/account deployments must provide `MAKECLOUD_ENCRYPTION_KEY` from a secret manager separately from the data volume. Preserve that key with protected backups: losing it loses access to retained projects and state. Terraform working files are decrypted during operations and sealed before completion is exposed. This is encryption at rest, not protection against a compromised running host. Code and provider caches remain filesystem artifacts with restricted permissions.

Use **one API process, two execution slots and one backend replica per data directory**. An exclusive startup lock prevents a second process from interfering with recovery. Persist `MAKECLOUD_DATA_DIR`, protect backups, and disable API autoscaling for this release. For separate manual starts:

```bash
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 5010 --workers 1 --env-file .env
npm run dev
```

The frontend proxies allowed `/api/cloud/*` routes. Set the same random `MAKECLOUD_API_TOKEN` on both services for remote hosting; keep the backend private. Operator-mode remote frontend access also requires `MAKECLOUD_OPERATOR_PASSWORD`, with Basic username `operator`. Hosted production fails closed without authentication. `npm start` is explicitly a loopback-only production preview; for hosting, run Next with your authenticated environment and do not set `MAKECLOUD_LOCAL_ONLY=true`.

The backend Dockerfile contains Terraform and the Docker CLI; `compose.hosted.yml` supplies persistent disk, Docker runner access, the standalone frontend and Caddy HTTPS. See [HOSTING.md](HOSTING.md) for the privileged-daemon boundary and setup. Standalone Fly deployment does not supply the required hosted runner by itself. Existing Fly deployment is manual through Actions and needs a protected `makecloud_data` volume, API token and a single machine. Deploy the frontend separately. The configured Fly app name belongs to the existing installation; change it for a new deployment. No public deployment is performed by the test suite.

**Historical credential exposure:** prototype Git history contains committed AWS and Terraform Cloud credentials. Removing current files does not revoke them. Revoke/rotate the exposed credentials, audit use, and coordinate history cleanup separately.

## Optional pricing

With Infracost v2.4.2+ and `INFRACOST_CLI_AUTHENTICATION_TOKEN`, plans get a measured monthly estimate using `scan` followed by `inspect --summary --json`. The legacy `breakdown` CLI is also supported with `INFRACOST_API_KEY`. The CLI reads a temporary protected plan JSON file; MakeCloud deletes pricing input/output files afterward and exposes only the estimate. The CLI extracts pricing parameters for its service rather than sending the raw plan to the Cloud Pricing API, according to [Infracost's privacy documentation](https://www.infracost.io/docs/faq/). Missing tools, service failure or invalid prices produce an explicit unavailable result while retaining the plan. Usage, egress, taxes and unsupported resources can add charges. An estimate is not a spending cap. Current command and authentication details follow the [Infracost CLI documentation](https://www.infracost.io/docs/features/cli_commands/). Actual pricing-service requests require configured credentials and have not been verified here.

## Verification

```bash
.venv/bin/python -m pytest -q tests
npm run lint
npm run typecheck
npm run build
.venv/bin/python tests/proxy_smoke.py
# Downloads real provider schemas; does not access cloud accounts:
.venv/bin/python tests/validate_blueprints.py
```

Regression tests cover requirements and probing, explanations without mutation, malformed AI, accepted-code context, persistence and encryption, target guards, ownership, scoped connections, expiry and invalidation, budget acknowledgment, subprocess cancellation, restart recovery, redaction, exact-plan tampering, cleanup, export and archives.

The HTTP integration test starts the production frontend and API on temporary ports. It exercises all three demo clouds, controlled AI over HTTP, a simulated Terraform executable, deployment/update discussions, cleanup, cookies, cross-user denial, origin rejection, installation authentication and backend downtime. It additionally exercises public signup, one-use verification, recovery, Google OAuth callback/nonces, project selection, AWS role verification and Azure template tickets using controlled provider and mail responses. It does **not** prove live AI, cloud-account provisioning, Docker execution or browser rendering. Provider validation separately uses real AWS, Google and Azure schemas for ten reference configurations, including AWS app/database and production HTTPS variants.

Production dependency audit currently reports no vulnerabilities. Development lint tooling inherits five high-severity findings from an unpatched `braces` issue; [the advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) lists no patched version. Do not force a Next.js downgrade just to suppress this report; recheck as upstream fixes become available.
