"""Customer-owned OIDC grants bound to one project workspace, without cloud keys."""
import json
import os
import re
import shlex
import time
import uuid
from fastapi import HTTPException
import hcp_runner

UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


def subject(row, phase):
    return f"organization:{row['hcp_org']}:project:{row['hcp_project_name']}:workspace:{row['workspace_name']}:run_phase:{phase}"


def begin(store, owner, cloud, label, profile):
    if cloud not in ('aws', 'google', 'azurerm') or profile not in ('storage', 'standard'):
        raise HTTPException(422, 'Choose a supported cloud and permission profile.')
    if not hcp_runner.configured():
        raise HTTPException(503, 'HCP Terraform onboarding has not been enabled.')
    client = hcp_runner.Client()
    cid = str(uuid.uuid4())
    row = {'id': cid, 'owner': owner, 'cloud': cloud, 'label': label.strip() or {'aws': 'AWS account', 'google': 'Google Cloud project', 'azurerm': 'Azure subscription'}[cloud], 'status': 'pending', 'method': 'hcp_oidc', 'profile': profile, 'workspace_name': 'mc-' + cid.replace('-', ''), 'hcp_org': client.org, 'hcp_project_id': client.project, 'hcp_project_name': client.project_name, 'created_at': time.time(), 'workloads': ['storage'] if profile == 'storage' else (['storage', 'container', 'static_site', 'database', 'custom'] if cloud == 'aws' else ['storage', 'container', 'custom'])}
    store.vault_save(row)
    return instructions(row)


def instructions(row):
    from cloud_connections import public, aws_template
    result = {'connection': public(row), 'workspace_name': row['workspace_name'], 'permissions': 'This grants temporary credentials to one HCP workspace for one MakeCloud project. Cloud resources may have usage charges. Use a dedicated development cloud account or project.', 'setup_note': 'Review the permission setup in your cloud console. No cloud access keys or client secrets are needed.'}
    if row['cloud'] == 'aws':
        template = aws_template()
        del template['Parameters']['PrincipalArn'], template['Parameters']['ExternalId']
        template['Parameters']['ConnectionId']['Default'] = row['id']
        template['Parameters']['Profile']['Default'] = row['profile']
        template['Parameters']['ExistingOidcProviderArn'] = {'Type': 'String', 'Default': '', 'Description': 'Leave blank to create the app.terraform.io OIDC provider. If it already exists in IAM Identity providers, paste its ARN.'}
        template['Conditions']['CreateOidc'] = {'Fn::Equals': [{'Ref': 'ExistingOidcProviderArn'}, '']}
        template['Resources']['OidcProvider'] = {'Type': 'AWS::IAM::OIDCProvider', 'Condition': 'CreateOidc', 'Properties': {'Url': 'https://app.terraform.io', 'ClientIdList': ['aws.workload.identity']}}
        template['Resources']['Role']['Properties']['AssumeRolePolicyDocument']['Statement'] = [{'Effect': 'Allow', 'Principal': {'Federated': {'Fn::If': ['CreateOidc', {'Ref': 'OidcProvider'}, {'Ref': 'ExistingOidcProviderArn'}]}}, 'Action': 'sts:AssumeRoleWithWebIdentity', 'Condition': {'StringEquals': {'app.terraform.io:aud': 'aws.workload.identity', 'app.terraform.io:sub': [subject(row, 'plan'), subject(row, 'apply')]}}}]
        template['Description'] = 'MakeCloud HCP Terraform workspace access. Review the selected storage or broad infrastructure permissions before approving.'
        result.update(template=template, console_url='https://console.aws.amazon.com/cloudformation/home#/stacks/create', target_fields=[{'key': 'role_arn', 'label': 'Role ARN', 'placeholder': 'arn:aws:iam::123456789012:role/MakeCloud-' + row['id']}], setup_note='Upload this template in CloudFormation, review permissions, and create the stack. Copy its RoleArn output. If app.terraform.io already exists in IAM Identity providers, use ExistingOidcProviderArn instead of creating a duplicate.')
    elif row['cloud'] == 'google':
        pool = 'mc-' + row['id'].replace('-', '')[:20]
        condition = 'assertion.sub in ' + json.dumps([subject(row, 'plan'), subject(row, 'apply')])
        roles = ['roles/storage.admin'] + (['roles/run.admin', 'roles/iam.serviceAccountUser', 'roles/serviceusage.serviceUsageAdmin'] if row['profile'] == 'standard' else [])
        script = '\n'.join([
            'set -euo pipefail', 'read -r -p "Google Cloud project ID: " PROJECT_ID',
            '[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || { echo "Invalid project ID"; exit 1; }',
            f'POOL={shlex.quote(pool)}', 'NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")',
            'gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com --project="$PROJECT_ID"',
            'gcloud iam workload-identity-pools create "$POOL" --project="$PROJECT_ID" --location=global --display-name="MakeCloud"',
            'gcloud iam workload-identity-pools providers create-oidc terraform --project="$PROJECT_ID" --location=global --workload-identity-pool="$POOL" --issuer-uri=https://app.terraform.io --allowed-audiences=gcp.workload.identity --attribute-mapping=google.subject=assertion.sub --attribute-condition=' + shlex.quote(condition),
            'gcloud iam service-accounts create "$POOL" --project="$PROJECT_ID" --display-name="MakeCloud Terraform"',
            'EMAIL="$POOL@$PROJECT_ID.iam.gserviceaccount.com"',
            'gcloud iam service-accounts add-iam-policy-binding "$EMAIL" --project="$PROJECT_ID" --role=roles/iam.workloadIdentityUser --member="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/*"',
            *['gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$EMAIL" --role=' + role for role in roles],
            'printf "Project ID: %s\\nProject number: %s\\n" "$PROJECT_ID" "$NUMBER"',
        ])
        result.update(console_url='https://shell.cloud.google.com/', setup_script=script, setup_note='Open Google Cloud Shell in the browser. Review this script before running it; it creates a workspace-bound identity pool and service account, then grants the selected permissions in your project. This is a one-time setup.', target_fields=[{'key': 'project_id', 'label': 'Project ID', 'placeholder': 'my-project-123'}, {'key': 'project_number', 'label': 'Project number', 'placeholder': '123456789012'}])
    else:
        roles = 'Contributor' if row['profile'] == 'standard' else 'Storage Account Contributor'
        credentials = [json.dumps({'name': 'makecloud-' + phase, 'issuer': 'https://app.terraform.io', 'subject': subject(row, phase), 'audiences': ['api://AzureADTokenExchange']}) for phase in ('plan', 'apply')]
        script = '\n'.join([
            'set -euo pipefail', 'read -r -p "Azure subscription ID: " SUBSCRIPTION',
            '[[ "$SUBSCRIPTION" =~ ^[0-9a-fA-F-]{36}$ ]] || { echo "Invalid subscription ID"; exit 1; }',
            'az account set --subscription "$SUBSCRIPTION"',
            'TENANT=$(az account show --query tenantId -o tsv)',
            'APP_ID=$(az ad app create --display-name ' + shlex.quote('MakeCloud-' + row['id']) + ' --query appId -o tsv)',
            'OBJECT_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)',
            *['az ad app federated-credential create --id "$APP_ID" --parameters ' + shlex.quote(value) for value in credentials],
            'az role assignment create --assignee-object-id "$OBJECT_ID" --assignee-principal-type ServicePrincipal --role ' + shlex.quote(roles) + ' --scope "/subscriptions/$SUBSCRIPTION"',
            *(['az role assignment create --assignee-object-id "$OBJECT_ID" --assignee-principal-type ServicePrincipal --role "Resource Group Contributor" --scope "/subscriptions/$SUBSCRIPTION"'] if row['profile'] == 'storage' else []),
            'printf "Client ID: %s\\nTenant ID: %s\\nSubscription ID: %s\\n" "$APP_ID" "$TENANT" "$SUBSCRIPTION"',
        ])
        permission_roles = roles + (' and Resource Group Contributor' if row['profile'] == 'storage' else '')
        result.update(console_url='https://shell.azure.com/', setup_script=script, permissions=result['permissions'] + ' The selected Azure roles are ' + permission_roles + ' across the subscription.', setup_note='Open Azure Cloud Shell and choose Bash. Review this script before running it; it creates an application with two workspace-bound federated credentials and grants the displayed roles. It creates no client secret.', target_fields=[{'key': 'client_id', 'label': 'Client ID', 'placeholder': 'Application UUID'}, {'key': 'tenant_id', 'label': 'Tenant ID', 'placeholder': 'Directory UUID'}, {'key': 'subscription_id', 'label': 'Subscription ID', 'placeholder': 'Subscription UUID'}])
    return result


def parse_target(row, target):
    try:
        fields = json.loads(target)
        if not isinstance(fields, dict) or any(not isinstance(value, str) for value in fields.values()):
            raise ValueError()
        if row['cloud'] == 'aws':
            match = re.fullmatch(r'arn:aws:iam::([0-9]{12}):role/MakeCloud-' + re.escape(row['id']), fields.get('role_arn', ''))
            if set(fields) != {'role_arn'} or not match:
                raise ValueError()
            return {'role_arn': fields['role_arn'], 'account_id': match[1]}
        if row['cloud'] == 'google':
            if set(fields) != {'project_id', 'project_number'} or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', fields['project_id']) or not re.fullmatch(r'[0-9]{6,20}', fields['project_number']):
                raise ValueError()
        elif set(fields) != {'client_id', 'tenant_id', 'subscription_id'} or any(not re.fullmatch(UUID, value) for value in fields.values()):
            raise ValueError()
        return fields
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, 'Enter the cloud setup outputs exactly as shown. Do not enter keys or secrets.') from exc


def variables(row):
    if row['cloud'] == 'aws':
        return {'TFC_AWS_PROVIDER_AUTH': 'true', 'TFC_AWS_RUN_ROLE_ARN': row['role_arn']}
    if row['cloud'] == 'google':
        pool = 'mc-' + row['id'].replace('-', '')[:20]
        return {'TFC_GCP_PROVIDER_AUTH': 'true', 'TFC_GCP_PROJECT_NUMBER': row['project_number'], 'TFC_GCP_WORKLOAD_POOL_ID': pool, 'TFC_GCP_WORKLOAD_PROVIDER_ID': 'terraform', 'TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL': f"{pool}@{row['project_id']}.iam.gserviceaccount.com"}
    return {'TFC_AZURE_PROVIDER_AUTH': 'true', 'TFC_AZURE_RUN_CLIENT_ID': row['client_id'], 'ARM_TENANT_ID': row['tenant_id'], 'ARM_SUBSCRIPTION_ID': row['subscription_id']}


def verify(store, row, target):
    from cloud_connections import public
    if row['status'] == 'revoked':
        raise HTTPException(409, 'Start a new connection after revoking access.')
    fields = parse_target(row, target)
    if row.get('verification_target') and fields != row['verification_target']:
        raise HTTPException(409, 'A connection retains its original cloud identity. Create another connection to change accounts.')
    if row['status'] == 'connected':
        return public(row)
    if row['status'] == 'verifying':
        return public(refresh(store, row))
    client = hcp_runner.Client()
    if (client.org, client.project, client.project_name) != (row['hcp_org'], row['hcp_project_id'], row['hcp_project_name']):
        raise HTTPException(409, 'HCP settings changed. Create a fresh cloud connection.')
    row.update(fields)
    row['verification_target'] = fields
    if row['cloud'] == 'aws' and row['profile'] == 'standard':
        row['boundary_arn'] = f"arn:aws:iam::{row['account_id']}:policy/MakeCloudRuntimeBoundary-{row['id']}"
    try:
        wid = client.workspace(row['workspace_name'])
        client.variables(wid, variables(row))
        provider = row['cloud']
        source = {'aws': 'hashicorp/aws', 'google': 'hashicorp/google', 'azurerm': 'hashicorp/azurerm'}[provider]
        version = {'aws': '~> 5.0', 'google': '~> 6.0', 'azurerm': '~> 4.0'}[provider]
        provider_args = 'region = "us-east-1"' if provider == 'aws' else 'project = ' + json.dumps(row['project_id']) if provider == 'google' else 'features {}\nsubscription_id = ' + json.dumps(row['subscription_id'])
        data_type, expression = {'aws': ('aws_caller_identity', 'account_id'), 'google': ('google_project', 'project_id'), 'azurerm': ('azurerm_client_config', 'subscription_id')}[provider]
        code = f'terraform {{\n required_version = ">= 1.9, < 2.0"\n required_providers {{\n {provider} = {{ source = "{source}", version = "{version}" }}\n }}\n}}\nprovider "{provider}" {{\n {provider_args}\n}}\ndata "{data_type}" "identity" {{}}\noutput "identity" {{ value = data.{data_type}.identity.{expression} }}\n'
        def checkpoint(**values):
            row.setdefault('verification', {}).update(values)
            store.vault_save(row)
        checkpoint(workspace_id=wid)
        cid = client.upload(wid, code, checkpoint)
        run = client.create_run(wid, cid, 'MakeCloud identity check ' + row['id'], speculative=True)
        checkpoint(run_id=run['id'])
        row.update(status='verifying', verification_started=time.time(), error=None)
        store.vault_save(row)
        return public(row)
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc


def refresh(store, row):
    if row['status'] != 'verifying':
        return row
    try:
        client = hcp_runner.Client()
        remote = row['verification']
        run = client.run(remote['run_id'])
        client.assert_run(run, remote['workspace_id'], remote['configuration_id'])
        status = run['attributes']['status']
        if status in hcp_runner.TERMINAL_ERRORS:
            row.update(status='pending', error='Identity verification failed. Check the cloud trust and permissions, then try again.')
        elif status == 'planned_and_finished':
            details = client.plan_json(run)
            actual = details.get('output_changes', {}).get('identity', {}).get('after')
            expected = row.get('account_id') or row.get('project_id') or row.get('subscription_id')
            if actual != expected or any(value.get('mode') != 'data' or value['change']['actions'] not in (['read'], ['no-op']) for value in details.get('resource_changes', [])):
                row.update(status='pending', error='The cloud identity did not match the selected account.')
            else:
                row.update(status='connected', verified_at=time.time(), error=None)
        store.vault_save(row)
    except ValueError:
        # Preserve IDs so a sleeping backend or temporary API outage is resumable.
        pass
    return row
