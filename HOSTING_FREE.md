# MakeCloud private beta on free hosting

Customers use the website and their cloud console. They do not clone MakeCloud or install Terraform. This setup targets an unpaid personal beta with at most ten accounts. AI calls and customer cloud resources have separate costs.

## Architecture

| Component | Service | Responsibility |
| --- | --- | --- |
| Website | Vercel Hobby | Next.js chat, cookies, same-origin API proxy |
| API | Render free web service, or an available Koyeb free slot | Requirements, accounts, encrypted records, HCP API calls |
| Database | Neon Free PostgreSQL | Durable conversations, connections, jobs and approvals |
| Terraform | HCP Terraform Free | Remote plans/applies, workspace locking and retained state |
| AI | OpenAI API | Live reasoning and custom proposals, billed separately |

HCP runs continue while the backend sleeps. Remote identifiers are checkpointed before advancing, then reconciled when the user returns. An ambiguous response never causes an automatic apply replay. Each connection belongs to one MakeCloud project and one HCP workspace; customer ownership is checked on selection and execution.

`Dockerfile.hcp` contains no Terraform CLI or Docker socket. `MAKECLOUD_EPHEMERAL_HOST=true` requires account authentication, stable encryption, an installation token, remote PostgreSQL and the HCP runner. SQLite on ephemeral hosting is rejected. The retained self-hosted alternative is in [HOSTING.md](HOSTING.md).

## Free tier limits

- Vercel Hobby permits personal non-commercial use. A commercial launch needs an eligible plan or another frontend host; ten users alone does not determine eligibility. [Vercel policy](https://vercel.com/docs/plans/hobby).
- Render sleeps after 15 idle minutes and can take about a minute to wake. Free hours are shared across the workspace. Failed reads are retried when waking; writes are never retried. Render's free database expires after 30 days, so use Neon. [Render limits](https://render.com/docs/free).
- Koyeb has one free instance per organization with no durable volume. Do not replace another app to free the slot without approval. [Koyeb instances](https://www.koyeb.com/docs/reference/instances).
- HCP Free currently provides 500 managed resources per organization and one concurrent run, shared with existing workspaces. Review usage before invitations; never upgrade automatically. [HCP plans](https://developer.hashicorp.com/terraform/cloud-docs/overview).
- Neon has storage and compute quotas. Back up the encryption key separately: losing it makes encrypted records unreadable even if the database survives. [Neon pricing](https://neon.com/pricing).
- OpenAI API billing is separate from ChatGPT. Without a key, reference blueprints still work; custom live AI is unavailable. Customer cloud bills remain theirs.

## Operator setup

1. Create a dedicated HCP project named `MakeCloud`. Its name is part of the cloud trust subject, so keep it stable. Existing unrelated workspaces are not migrated.
2. Configure a user or team token capable of creating workspaces, managing variables, reading plan JSON and confirming runs. Organization tokens cannot create runs. HCP Free does not support custom team permissions: a user token inherits that user's rights, including unrelated projects. Explicitly approve that access or use a separate automation account. The app's project checks do not scope the actual token. [Token permissions](https://developer.hashicorp.com/terraform/cloud-docs/users-teams-organizations/api-tokens).
3. Create a dedicated Neon database and use a connection string with `sslmode=require` or stronger. Copy `.env.hcp.example` to ignored `.env.hcp`; configure the database URL, a stable Fernet encryption key, cryptographically random installation token and invitation code. Keep all secrets in service settings, never in source or chat.
4. Deploy the API from `makecloud-product-build` with `Dockerfile.hcp` and the free plan. `render.yaml` prepares the service. Keep apply disabled during setup. `/readyz` is a generic host check; useful endpoints require the installation token and, where appropriate, customer login.
5. Deploy Next.js on Vercel with Fluid compute enabled. Server-only settings are `MAKECLOUD_BACKEND_URL`, the same `MAKECLOUD_API_TOKEN`, and `MAKECLOUD_PUBLIC_URL`. The backend uses that exact HTTPS public URL too. Never use `NEXT_PUBLIC_` for secrets. The API proxy has a 300-second maximum and 270-second upstream timeout, allowing a sleeping backend to wake before an AI response or remote submission. Verify the intended branch before enabling automatic production deployments. [Function limits](https://vercel.com/docs/functions/limitations).
6. Start with invite registration and `MAKECLOUD_MAX_ACCOUNTS=10`. Public signup requires verified email. Password reset also requires mail. Render blocks common SMTP ports, so optional Resend HTTPS delivery is implemented; verify a sender/domain first.
7. Configure live OpenAI through the secure API-key workflow. Do not buy credits or upgrade a service without explicit approval. Configure usage limits before opening access.

## Customer cloud connection

HCP gets temporary credentials tied to organization, project, workspace and plan/apply phase. No customer cloud key or client secret is stored. Auto-apply and global state sharing are disabled; inherited variable sets and unexpected workspace variables are rejected.

- **AWS:** review the downloaded CloudFormation template in the browser and create the permission stack. Reuse an existing `app.terraform.io` IAM OIDC provider when present. Paste the RoleArn output. Storage access covers `makecloud-*` buckets; broad infrastructure access is labeled and enforces a runtime IAM boundary. [AWS credentials](https://developer.hashicorp.com/terraform/cloud-docs/dynamic-provider-credentials/aws-configuration/quick-setup).
- **Google Cloud:** review/run the generated script in browser Cloud Shell. It creates a workload identity pool/provider and service account restricted to the exact plan/apply subjects. Paste project ID and number. Storage or storage plus Cloud Run permissions are displayed. [Google credentials](https://developer.hashicorp.com/terraform/cloud-docs/dynamic-provider-credentials/gcp-configuration).
- **Azure:** review/run the Bash script in Azure Cloud Shell. It creates an Entra app and two exact federated credentials, one for each phase. Paste client, tenant and subscription IDs. Storage grants Storage Account Contributor plus Resource Group Contributor; broad setup grants Contributor. These permissions cover the subscription. [Azure credentials](https://developer.hashicorp.com/terraform/cloud-docs/dynamic-provider-credentials/azure-configuration).

Verification is a read-only HCP plan comparing returned identity to the requested target. A connected label proves identity; the actual workload plan checks permissions, provider availability and organization policies. The one-time browser setup requires the customer's permission to grant IAM access.

Plans display changes, target and cost availability. Apply requires a single-use, unexpired approval for that exact configuration and run. Deletions require an extra acknowledgment. Failed applies preserve recovery state and require inspection before another plan. Terraform completion does not establish application connectivity.

## Release gates

Run regressions, real disposable PostgreSQL tests, production lint/type/build checks, HTTP proxy journeys, real provider-schema validation and CloudFormation validation. HCP contract tests simulate its API and do not prove live execution. Live gates additionally require an authenticated HCP plan, actual customer OIDC setup, reviewed cloud apply, functional check, update/destroy and cleanup evidence in dedicated test accounts. Keep provisioning disabled until those gates pass.

The expanded CI definition is prepared in `deploy/ci-hosted.example.yml`. Installing it at `.github/workflows/ci.yml` requires a GitHub credential with workflow write permission; the current CLI credential lacks that scope. Existing CI is retained. Real PostgreSQL, provider-schema and CloudFormation checks were also run locally; a skipped PostgreSQL CI fixture is not equivalent to those checks.
