# Hosted MakeCloud

For the current free-tier HCP beta, use [HOSTING_FREE.md](HOSTING_FREE.md). The instructions below describe the retained self-hosted Docker alternative.

Customers use the website. They do not clone this repository, install Terraform, run Docker, or give MakeCloud their personal cloud access keys. The operator installs and configures MakeCloud once on a Linux server.

The release targets one persistent server with one API process, two execution slots, a production Next.js frontend, and Caddy for HTTPS. This is a deployable architecture for a controlled public beta. A real public deployment and real customer-cloud lifecycle still need verification.

## Customer experience

1. Create an account, verify email, and sign in. Invitation registration is available while the operator checks the installation.
2. Open **Your cloud accounts** and approve access using the appropriate cloud console or Google consent screen.
3. Describe the application. MakeCloud asks for missing requirements and retains the conversation, accepted code and deployment history.
4. A single matching verified connection supplies the project/subscription automatically. With several connections, choose one in the project’s Connection panel. The plan displays the actual target again before approval.
5. Accept the proposed code, create a real plan, review changes and possible costs, and explicitly approve deployment. Ask questions and make reviewed changes later. Cleanup also requires a reviewed destroy plan.

## Connection methods and operator setup

| Cloud | Customer action | Operator configuration | Credentials used by execution |
| --- | --- | --- | --- |
| AWS | Approve a CloudFormation stack, paste its RoleArn output, verify | Dedicated broker role, broker permission to assume the generated customer roles, published S3 bootstrap template | STS credentials obtained when the job starts; customer-specific external ID |
| Google Cloud | Consent in browser, choose an active project, verify permissions | Web OAuth client, exact callback URI, consent configuration, enabled Resource Manager API in the OAuth project | Fresh OAuth access token per job; encrypted refresh token retained by MakeCloud |
| Azure | Subscription owner approves Lighthouse template, enters subscription ID, verifies | Managing Entra tenant, dedicated service principal object/client IDs and broker secret | Broker service principal, selected delegated subscription; no customer secret |

### AWS

1. Create a dedicated MakeCloud broker role in the operator’s AWS account. Its runtime credentials must be available to the API, preferably through the host’s IAM role. The role needs `sts:AssumeRole` for approved customer `MakeCloud-*` roles. Do not give customer roles trust in the entire operator account: use the specific broker role ARN.
2. Publish the version-controlled [bootstrap template](deploy/aws-bootstrap.json) in an HTTPS S3 location. Configure `MAKECLOUD_AWS_TEMPLATE_URL` and `MAKECLOUD_AWS_PRINCIPAL_ARN`. CloudFormation’s quick-create link requires an S3 template URL; the operator publishes this once, rather than asking each customer to install tools. [AWS quick-create documentation](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cfn-console-create-stacks-quick-create-links.html).
3. The storage profile only grants bucket operations on `makecloud-*` buckets, plus bucket discovery. New reference buckets use this prefix. The standard profile grants broad EC2/ECS/load-balancer/RDS/CloudFront/logging control in the chosen account. It is intended for a dedicated account, not a least-privilege production account containing unrelated resources. Customers can inspect the complete policy before approving.
4. Standard execution roles are forced into `/makecloud/` with the bootstrap’s permissions boundary. Attachments and `PassRole` are limited to ECS execution. The boundary does not limit infrastructure spending or substitute for customer approval.
   The standard policy also permits creating/tagging RDS-managed database secrets and reading KMS key metadata, as required by the managed-master-password reference design. It does not grant secret-value retrieval. [RDS managed-password permissions](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html).
5. Verification tries the role with its unique external ID and also without it. Access without the external ID is rejected, following [AWS’s third-party access guidance](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html). Executed provider configuration includes the expected account ID.

### Google Cloud

1. Register a confidential web OAuth client and configure its client ID/secret. Its authorized redirect URI must be exactly `https://YOUR_HOST/api/cloud/connections/google/callback`. Configure `MAKECLOUD_PUBLIC_URL` to that same HTTPS origin.
2. Enable the Resource Manager API for project discovery. Configure the OAuth consent screen and prepare the appropriate publishing/verification requirements for a public app. An external app in Testing normally receives refresh tokens expiring after seven days for this scope, so testing configuration is unsuitable for a lasting public connection. [Google OAuth documentation](https://developers.google.com/identity/protocols/oauth2), [production readiness](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview).
3. The flow uses PKCE, a one-use state challenge bound to the account and browser nonce, explicit consent, offline access and encrypted token retention. A public project picker returns active projects without exposing tokens. [Google web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server).
4. Cloud-platform consent uses the Google user’s existing IAM rights; it is not a credential cryptographically restricted to one project. This release therefore restricts hosted OAuth execution to reviewed storage and Cloud Run resource types, denies data blocks, and checks explicit project overrides and known local references. Use a dedicated project and an account with suitable permissions. Additional resource types require a reviewed policy or a separately configured dedicated service-account connection.
5. Verification checks project identity and representative creation permissions. It does not prove every workload permission, billing status or organization policy. The real Terraform plan remains the authoritative workload check. Disconnect locally and remove the OAuth grant in Google to revoke cloud access.

### Azure

1. Create a dedicated service principal in the managing tenant. Configure its tenant ID, object ID, client ID and operator-owned secret. The object ID used by Lighthouse and client ID used for authentication are different values.
2. The app generates a per-connection subscription-level Lighthouse template. The customer’s subscription owner reviews Contributor delegation in the portal. The template is served using an opaque bearer ticket that expires after 30 minutes and can be fetched repeatedly by Azure; account cookies are not required for template retrieval.
3. Verification checks an enabled subscription and this specific connection’s registration assignment. Terraform authenticates with the broker service principal in the managing tenant and the selected customer subscription. A matching provider subscription and reviewed storage/Container Apps resource types are required; arbitrary external environment/account references are rejected.
4. Contributor access covers the subscription and cannot assign access roles. Use a dedicated subscription. Existing providers may need registration or organization-policy approval by the customer. Do not claim that signing into Microsoft alone grants deployment access. [Azure Lighthouse onboarding](https://learn.microsoft.com/en-us/azure/lighthouse/how-to/onboard-customer), [Terraform service-principal authentication](https://learn.microsoft.com/en-us/azure/developer/terraform/authenticate/authenticate-to-azure-with-service-principle).

## Install the hosted stack

Use a dedicated Linux host with Docker Engine/Compose, DNS pointing at the host, and inbound TCP 80/443. The API and frontend have no host-exposed ports; Caddy is the entrypoint. SQLite and project workspaces require local persistent disk, not a network filesystem or ephemeral serverless storage.

```bash
cp .env.hosted.example .env.hosted
chmod 600 .env.hosted
# Configure hostname, strong random installation token, a Fernet encryption key,
# cloud broker/OAuth settings, SMTP, and the actual Docker socket group ID.
sudo install -d -m 700 -o 1000 -g 1000 /var/lib/makecloud
docker compose --env-file .env.hosted -f compose.hosted.yml config --quiet
docker compose --env-file .env.hosted -f compose.hosted.yml up --build -d
```

Generate the installation token and encryption key with a secret manager or a cryptographically secure generator. Keep the encryption key backed up separately from the data directory; losing it makes retained state and connections unusable. Do not use the example hostname, blank secrets or fixture values. `MAKECLOUD_DOCKER_GID` must match the host socket’s group. The backend image’s MakeCloud user is UID 1000; the persistent directory must be writable by it. If changing the data path, use the same absolute path inside and outside the API container, because sibling Terraform containers mount it using the host Docker daemon.

The API controls the host Docker socket. Treat it as a privileged service and use a dedicated host. Terraform containers have constrained resources, a read-only root, dropped capabilities, only their project workspace mounted, and only execution credentials. These controls are not a claim of isolation from a compromised API, kernel or Docker daemon. A production expansion should move execution to a dedicated sandbox service with stronger tenant boundaries.

Start with `MAKECLOUD_REGISTRATION=invite` and `MAKECLOUD_ENABLE_APPLY=false`. Confirm HTTPS, cookies, storage persistence, mail delivery, account isolation, onboarding and a real cloud plan. Then enable deployment, run create/update/destroy checks in dedicated test accounts, and switch registration to `public` when SMTP verification and recovery work. Public signup fails closed if mail is unconfigured.

The sample Compose stack keeps operator configuration in a protected environment file for simplicity. For a production installation, inject secrets from the chosen hosting platform’s secret manager. Image tags should be pinned to reviewed digests before a production rollout. [Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/), [bind-mount behavior](https://docs.docker.com/engine/storage/bind-mounts/).

## Persistence, restarts and backups

Project data, connection tokens, challenges and job payloads are encrypted in SQLite. Login tokens and challenge lookup tokens are hashed. State, saved plans and protected logs are sealed before completed job status is published. Execution temporarily decrypts working state on the protected volume.

Enqueue atomically stores the project operation and durable job. Workers atomically claim a job once and obtain fresh connection credentials at execution time. A restart resumes jobs that never started; any operation that started and did not finish is marked interrupted and requires review. An apply is never automatically replayed. A queued apply also checks plan expiry before touching the cloud. There is one API process and two execution slots; additional replicas sharing the volume are deliberately unsupported.

For a consistent backup, temporarily stop the API gracefully, confirm no Terraform runner remains, and back up the entire data directory together with SQLite and encrypted workspaces. Keep the encryption key separately. Restart the API after the snapshot. Test restoration on a separate host with deployment disabled; inspect any interrupted operations before replanning. Do not copy a workspace mid-apply or treat a database-only snapshot as a complete state backup. Add encrypted off-host scheduled backups and disk monitoring before public use.

## Live release verification still required

- Build and run the Compose images on Linux with a working Docker daemon; this machine’s daemon is unavailable.
- Verify public HTTPS/DNS, account email delivery, cookies, frontend interaction and recovery on the actual host. Browser automation was blocked in this session; HTTP tests do not substitute for visual verification.
- Configure operator cloud identities and Google OAuth publishing, then verify actual console consent and create/update/destroy in each dedicated test account. Validate expiration, revocation, interrupted apply and cleanup of billable resources.
- Configure the live AI key and run a provider-backed conversation evaluation. No OpenAI key was created in this session: its destination approval was declined.
- Add monitoring, protected off-host backups, provider permission review and host hardening. Billing/subscriptions, distributed execution, workload identity federation and arbitrary cloud-resource support are outside this implemented beta.

No cloud resources were created, emails delivered to customers, credentials generated, or public deployment published by the automated verification suite.
