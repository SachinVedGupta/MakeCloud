# MakeCloud hosted product plan and research decisions

**Current decision:** the small unpaid beta uses Vercel, a sleeping free API host, Neon PostgreSQL and HCP Terraform remote execution. See [HOSTING_FREE.md](HOSTING_FREE.md) for implementation and release gates. The original persistent-server design below is retained as an alternative; its local queue and broker setup are not prerequisites for HCP mode.

## Product goal

A person opens MakeCloud, connects a cloud account in a browser, describes the application, answers useful follow-up questions, reviews a readable infrastructure plan and deploys. The cloud bill and resources belong to the person. MakeCloud retains session context, accepted Terraform and state for explanations, updates and explicit cleanup. Customers need no local installation.

“Platform agnostic” means the same product workflow and requirements model can generate appropriate provider-specific infrastructure. AWS, Google Cloud and Azure do not share one universal Terraform resource schema or one permission-consent mechanism. The reference catalog must be explicit about which combinations are supported, and custom AI output must pass policy, provider validation and planning before approval.

## Decisions from research

| Decision | Why this approach | Alternative and trade-off |
| --- | --- | --- |
| AWS role plus unique external ID | Standard temporary third-party access; customer reviews one bootstrap stack | Personal access keys are simpler to collect but longer-lived and poor hosted UX; ordinary OAuth sign-in does not grant infrastructure permissions |
| Google web OAuth and project picker | Few customer steps, no key files or local CLI | Workload federation/service-account impersonation offers tighter credentials but requires additional project IAM setup; a future advanced onboarding path |
| Azure Lighthouse | Portal approval delegates a subscription to the broker service principal; Terraform can authenticate non-interactively | Microsoft user OAuth alone does not directly supply the AzureRM provider’s supported broker authentication; manual per-customer service principal secrets add friction |
| Existing account auth plus verification/recovery | Builds on the current ownership model and works independently of cloud login | Managed identity providers reduce auth maintenance but require a vendor installation and migration; add when choosing the actual host/business stack |
| One persistent Linux host, private API, HTTPS frontend | Smallest working operational model with local state, sibling Terraform containers and simple deployment | Serverless-only hosting cannot provide this persistent Terraform execution model; distributed Postgres/object storage/queues require lease and workspace redesign |
| Durable SQLite queue with single API process | Atomic enqueue/claim and restart recovery without another managed service | Separate autoscaling workers need distributed locks and state storage; do not add replicas to this build |
| Explicit broad-access labels and workload policies | Accurately describes what customers approve and limits shared-broker/GCP-user credential exposure | Pretending Contributor/cloud-platform/standard AWS access is universally least-privilege would be misleading |

Research sources: [AWS external IDs](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html), [CloudFormation quick create](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cfn-console-create-stacks-quick-create-links.html), [Google server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Google token expiration and publishing](https://developers.google.com/identity/protocols/oauth2), [Google Terraform authentication](https://docs.cloud.google.com/docs/terraform/authentication), [Azure Lighthouse](https://learn.microsoft.com/en-us/azure/lighthouse/how-to/onboard-customer), [Azure broker authentication](https://learn.microsoft.com/en-us/azure/developer/terraform/authenticate/authenticate-to-azure-with-service-principle), [Docker persistent mounts](https://docs.docker.com/engine/storage/bind-mounts/), [Caddy forwarded-header behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

## Implemented stages

1. **Requirements and sessions:** explicit requirements, adaptive probing, explanations without mutation, accepted-code context, proposals/diffs, manual edits, revision history, archives, export, cloud-target stability after deployment.
2. **Reviewed execution:** real Terraform init/validate/plan, redacted changes, exact saved-plan/hash approval, expiry, extra destructive/budget acknowledgment, cancellation, recovery, retained state and explicit cleanup.
3. **Self-service accounts:** public registration with verified email, invitations for early rollout, password recovery, expiring cookies, account ownership and rate limits.
4. **Browser cloud onboarding:** AWS role bootstrap and trust verification, Google PKCE/nonce/state and project picker, Azure per-connection Lighthouse template/portal link and verification. Sensitive connection material stays encrypted on the server. Disconnect clears drafts’ approvals and blocks while retained deployments need cleanup; users can revoke permissions in their cloud consoles at any time.
5. **Hosted operations:** production web/backend images, HTTPS Compose stack, persistent encrypted volume, atomic durable queue, fresh credentials on execution, resumable queued work, interrupted-operation review, installation token and private backend.

## Supported reference catalog

| Requirement | AWS | Google Cloud | Azure |
| --- | --- | --- | --- |
| Private versioned object storage | S3 | Cloud Storage | Blob Storage |
| Container application | Fargate/ALB/CloudWatch | Cloud Run v2 | Container Apps |
| Static site/CDN | S3/CloudFront | No reference design yet | No reference design yet |
| Private PostgreSQL | RDS, optionally connected to AWS app | No reference design yet | No reference design yet |
| Public production TLS | Existing ACM certificate plus DNS for ALB; CloudFront managed domain | Cloud Run managed endpoint | Container Apps managed endpoint |
| Advanced custom resources | Depends on scoped IAM and policy | OAuth path restricted to reviewed project-scoped types | Lighthouse path restricted to reviewed storage/container types |

Application data upload, container build/push, database migrations, DNS registration, automatic cloud-account creation, guaranteed monthly spending caps and arbitrary resource compatibility are not implemented by provisioning these reference designs. Requirements and outputs explain the necessary follow-up setup.

## Validation and rollout

The code is checked in layers: pure requirements/security regressions; account, connection, recovery and job integration using controlled provider responses; production frontend-to-API HTTP journeys; real Terraform validation against all three providers’ schemas; offline CloudFormation validation; production build/type/lint checks; Compose configuration validation. Tests never substitute mock success for real cloud provisioning.

The next concrete release gate is a configured Linux installation with a real Docker daemon, HTTPS domain, email transport, live AI and cloud broker identities. Run actual provider onboarding, create/update/destroy and revocation checks in dedicated test accounts, and record resource IDs, operation results and cleanup evidence. Only then call the hosted experience verified.

## Next expansion priorities

1. Run the live release gates and fix provider/organization-policy differences exposed by real accounts.
2. Move cloud execution to a dedicated sandbox service; introduce workload federation and narrower per-customer identities before opening a broad custom-resource catalog.
3. Add operational alerts, encrypted off-host backup/restore drills, quotas and pricing-service verification. Budget estimates remain estimates; spending controls need provider-specific enforcement.
4. Expand each workload as a tested vertical slice: networking, secrets, databases, migrations, DNS and readiness checks together with probing questions, plan presentation, failure recovery and cleanup.
5. Add distributed durable storage/leases and managed auth/billing when actual scale and business requirements justify them.
