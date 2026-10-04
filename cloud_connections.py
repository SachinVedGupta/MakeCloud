"""Customer delegated access. Provider tokens never cross the public API."""
import base64
import hashlib
import os
import re
import secrets
import time
import uuid
from urllib.parse import urlencode, urlparse, quote
import httpx
from fastapi import HTTPException

PUBLIC_FIELDS = ('id', 'label', 'cloud', 'account_id', 'project_id', 'subscription_id', 'regions', 'status', 'method', 'verified_at', 'workloads', 'error')
UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
GOOGLE_TOKEN = 'https://oauth2.googleapis.com/token'
GCP_API = 'https://cloudresourcemanager.googleapis.com/v3'


def public(row):
    return {key: row.get(key) for key in PUBLIC_FIELDS}


def base_url():
    value = os.getenv('MAKECLOUD_PUBLIC_URL', '').rstrip('/')
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or not parsed.hostname:
        raise HTTPException(503, 'Configure the public MakeCloud URL before enabling cloud onboarding.')
    if parsed.scheme != 'https' and not (os.getenv('MAKECLOUD_SECURE_COOKIES') == 'false' and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        raise HTTPException(503, 'Cloud onboarding requires HTTPS.')
    return value


def capabilities():
    import hcp_runner
    if hcp_runner.enabled():
        return {cloud: hcp_runner.configured() for cloud in ('aws', 'google', 'azurerm')}
    return {
        'aws': bool(os.getenv('MAKECLOUD_AWS_PRINCIPAL_ARN')),
        'google': bool(os.getenv('MAKECLOUD_GOOGLE_CLIENT_ID') and os.getenv('MAKECLOUD_GOOGLE_CLIENT_SECRET')),
        'azurerm': bool(os.getenv('MAKECLOUD_AZURE_TENANT_ID') and os.getenv('MAKECLOUD_AZURE_PRINCIPAL_ID') and os.getenv('MAKECLOUD_AZURE_CLIENT_ID') and os.getenv('MAKECLOUD_AZURE_CLIENT_SECRET')),
    }


def request_json(method, url, **kwargs):
    # Every URL here is a fixed provider endpoint; no user-controlled host/redirect.
    try:
        response = httpx.request(method, url, timeout=25, follow_redirects=False, **kwargs)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Cloud access could not be verified. Check permissions and consent, then try again.') from exc


def begin(store, owner, cloud, label, profile='standard'):
    import hcp_runner
    if hcp_runner.enabled():
        import hcp_connections
        return hcp_connections.begin(store, owner, cloud, label, profile)
    if cloud not in capabilities() or not capabilities()[cloud]:
        raise HTTPException(503, 'This cloud connection method has not been enabled by the MakeCloud operator.')
    row = {'id': str(uuid.uuid4()), 'owner': owner, 'cloud': cloud, 'label': label.strip() or {'aws': 'AWS account', 'google': 'Google Cloud project', 'azurerm': 'Azure subscription'}[cloud], 'status': 'pending', 'created_at': time.time()}
    if cloud == 'aws':
        if profile not in ('storage', 'standard'):
            raise HTTPException(422, 'Choose storage or standard access.')
        principal = os.environ['MAKECLOUD_AWS_PRINCIPAL_ARN']
        if not re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/[\w+=,.@/-]+', principal):
            raise HTTPException(503, 'Configure a dedicated MakeCloud AWS role principal.')
        row.update(method='aws_role', external_id=secrets.token_urlsafe(32), role_name='MakeCloud-' + row['id'], profile=profile, workloads=['storage'] if profile == 'storage' else ['storage', 'container', 'static_site', 'database', 'custom'])
    elif cloud == 'azurerm':
        for field in ('MAKECLOUD_AZURE_TENANT_ID', 'MAKECLOUD_AZURE_PRINCIPAL_ID', 'MAKECLOUD_AZURE_CLIENT_ID'):
            if not re.fullmatch(UUID, os.environ[field]):
                raise HTTPException(503, 'The Azure broker identity is not configured correctly.')
        row.update(method='azure_lighthouse', workloads=['storage', 'container', 'custom'])
    else:
        base_url()
        verifier = secrets.token_urlsafe(48)
        nonce = secrets.token_urlsafe(32)
        state = store.challenge(owner, 'google_oauth', {'id': row['id'], 'verifier': verifier, 'nonce_hash': hashlib.sha256(nonce.encode()).hexdigest()})
        query = urlencode({'client_id': os.environ['MAKECLOUD_GOOGLE_CLIENT_ID'], 'redirect_uri': base_url() + '/api/cloud/connections/google/callback', 'response_type': 'code', 'scope': 'https://www.googleapis.com/auth/cloud-platform', 'access_type': 'offline', 'prompt': 'consent', 'state': state, 'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='), 'code_challenge_method': 'S256'})
        row.update(method='google_oauth', workloads=['storage', 'container', 'custom'])
        store.vault_save(row)
        return {'connection': public(row), 'authorization_url': 'https://accounts.google.com/o/oauth2/v2/auth?' + query, 'nonce': nonce}
    store.vault_save(row)
    return instructions(row)


def aws_template():
    sub = lambda value: {'Fn::Sub': value}
    runtime = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': ['ecr:GetAuthorizationToken', 'ecr:BatchCheckLayerAvailability', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchGetImage', 'logs:CreateLogStream', 'logs:PutLogEvents'], 'Resource': '*'}]}
    storage = [{'Effect': 'Allow', 'Action': ['s3:ListAllMyBuckets', 's3:GetBucketLocation'], 'Resource': '*'}, {'Effect': 'Allow', 'Action': 's3:*', 'Resource': ['arn:aws:s3:::makecloud-*', 'arn:aws:s3:::makecloud-*/*']}]
    standard = storage + [
        {'Effect': 'Allow', 'Action': ['ec2:*', 'ecs:*', 'elasticloadbalancing:*', 'rds:*', 'cloudfront:*', 'logs:*', 'acm:DescribeCertificate', 'acm:ListCertificates'], 'Resource': '*'},
        {'Effect': 'Allow', 'Action': ['secretsmanager:CreateSecret', 'secretsmanager:TagResource'], 'Resource': sub('arn:aws:secretsmanager:*:${AWS::AccountId}:secret:rds!db-*')},
        {'Effect': 'Allow', 'Action': 'kms:DescribeKey', 'Resource': sub('arn:aws:kms:*:${AWS::AccountId}:key/*')},
        {'Effect': 'Allow', 'Action': ['iam:CreateRole', 'iam:PutRolePermissionsBoundary'], 'Resource': sub('arn:aws:iam::${AWS::AccountId}:role/makecloud/*'), 'Condition': {'StringEquals': {'iam:PermissionsBoundary': sub('arn:aws:iam::${AWS::AccountId}:policy/MakeCloudRuntimeBoundary-${ConnectionId}')}}},
        {'Effect': 'Allow', 'Action': ['iam:GetRole', 'iam:DeleteRole', 'iam:ListRolePolicies', 'iam:ListAttachedRolePolicies', 'iam:ListInstanceProfilesForRole', 'iam:TagRole', 'iam:UntagRole'], 'Resource': sub('arn:aws:iam::${AWS::AccountId}:role/makecloud/*')},
        {'Effect': 'Allow', 'Action': ['iam:AttachRolePolicy', 'iam:DetachRolePolicy'], 'Resource': sub('arn:aws:iam::${AWS::AccountId}:role/makecloud/*'), 'Condition': {'ArnEquals': {'iam:PolicyARN': 'arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy'}}},
        {'Effect': 'Allow', 'Action': 'iam:PassRole', 'Resource': sub('arn:aws:iam::${AWS::AccountId}:role/makecloud/*'), 'Condition': {'StringEquals': {'iam:PassedToService': 'ecs-tasks.amazonaws.com'}}},
        {'Effect': 'Allow', 'Action': 'iam:CreateServiceLinkedRole', 'Resource': '*', 'Condition': {'StringEquals': {'iam:AWSServiceName': ['ecs.amazonaws.com', 'elasticloadbalancing.amazonaws.com', 'rds.amazonaws.com']}}},
    ]
    return {'AWSTemplateFormatVersion': '2010-09-09', 'Description': 'MakeCloud delegated provisioning. Storage profile is restricted to makecloud-* buckets. Standard grants broad infrastructure control in this account; review before approving.', 'Parameters': {
        'PrincipalArn': {'Type': 'String', 'AllowedPattern': r'arn:aws:iam::[0-9]{12}:role/[\w+=,.@/-]+'}, 'ExternalId': {'Type': 'String', 'MinLength': 20}, 'ConnectionId': {'Type': 'String', 'AllowedPattern': UUID}, 'Profile': {'Type': 'String', 'Default': 'storage', 'AllowedValues': ['storage', 'standard']}},
        'Conditions': {'Standard': {'Fn::Equals': [{'Ref': 'Profile'}, 'standard']}},
        'Resources': {'Boundary': {'Type': 'AWS::IAM::ManagedPolicy', 'Condition': 'Standard', 'Properties': {'ManagedPolicyName': sub('MakeCloudRuntimeBoundary-${ConnectionId}'), 'PolicyDocument': runtime}}, 'Role': {'Type': 'AWS::IAM::Role', 'Properties': {'RoleName': sub('MakeCloud-${ConnectionId}'), 'MaxSessionDuration': 3600, 'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'AWS': {'Ref': 'PrincipalArn'}}, 'Action': 'sts:AssumeRole', 'Condition': {'StringEquals': {'sts:ExternalId': {'Ref': 'ExternalId'}}}}]}, 'Policies': [{'PolicyName': 'Provision', 'PolicyDocument': {'Version': '2012-10-17', 'Statement': {'Fn::If': ['Standard', standard, storage]}}}]}}},
        'Outputs': {'RoleArn': {'Value': {'Fn::GetAtt': ['Role', 'Arn']}}}}


def azure_template(row):
    return {'$schema': 'https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#', 'contentVersion': '1.0.0.0', 'resources': [
        {'type': 'Microsoft.ManagedServices/registrationDefinitions', 'apiVersion': '2022-10-01', 'name': row['id'], 'properties': {'registrationDefinitionName': row['label'], 'description': 'MakeCloud infrastructure provisioning. Contributor access to this subscription. Remove delegation in Service providers to revoke.', 'managedByTenantId': os.environ['MAKECLOUD_AZURE_TENANT_ID'], 'authorizations': [{'principalId': os.environ['MAKECLOUD_AZURE_PRINCIPAL_ID'], 'principalIdDisplayName': 'MakeCloud provisioning', 'roleDefinitionId': 'b24988ac-6180-42a0-ab88-20f7382dd24c'}]}},
        {'type': 'Microsoft.ManagedServices/registrationAssignments', 'apiVersion': '2022-10-01', 'name': row['id'], 'dependsOn': ["[subscriptionResourceId('Microsoft.ManagedServices/registrationDefinitions', '" + row['id'] + "')]"], 'properties': {'registrationDefinitionId': "[subscriptionResourceId('Microsoft.ManagedServices/registrationDefinitions', '" + row['id'] + "')]"}},
    ]}


def instructions(row):
    if row.get('method') == 'hcp_oidc':
        import hcp_connections
        return hcp_connections.instructions(row)
    output = {'connection': public(row)}
    if row['cloud'] == 'aws':
        template_url = os.getenv('MAKECLOUD_AWS_TEMPLATE_URL', '')
        parsed = urlparse(template_url)
        valid_s3 = parsed.hostname and (parsed.hostname == 's3.amazonaws.com' or parsed.hostname.endswith('.s3.amazonaws.com') or re.fullmatch(r'[a-z0-9.-]+\.s3[.-][a-z0-9-]+\.amazonaws\.com', parsed.hostname))
        output.update(template=aws_template(), external_id=row['external_id'], role_name=row['role_name'], permissions='Only makecloud-* S3 buckets.' if row['profile'] == 'storage' else 'Broad control of EC2, ECS, load balancers, RDS, CloudFront and logs in this account. IAM creation is limited to bounded ECS execution roles. Use a dedicated account.')
        if template_url and (parsed.scheme != 'https' or not valid_s3 or parsed.username or parsed.password):
            raise HTTPException(503, 'The AWS bootstrap template must be hosted at an HTTPS S3 URL.')
        if template_url:
            output['console_url'] = 'https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?' + urlencode({'templateURL': template_url, 'stackName': row['role_name'], 'param_PrincipalArn': os.environ['MAKECLOUD_AWS_PRINCIPAL_ARN'], 'param_ExternalId': row['external_id'], 'param_ConnectionId': row['id'], 'param_Profile': row['profile']})
        output['parameters'] = {'PrincipalArn': os.environ['MAKECLOUD_AWS_PRINCIPAL_ARN'], 'ExternalId': row['external_id'], 'ConnectionId': row['id'], 'Profile': row['profile']}
    elif row['cloud'] == 'azurerm':
        output.update(template=azure_template(row), permissions='Contributor access to the entire selected subscription through Azure Lighthouse. It cannot assign access roles. Use a dedicated subscription.')
        # Public template uses an opaque short-lived bearer ticket, not account cookies.
        output['needs_template_ticket'] = True
    return output


def sts():
    import boto3
    from botocore.config import Config
    return boto3.client('sts', region_name=os.getenv('MAKECLOUD_AWS_BROKER_REGION', 'us-east-1'), config=Config(connect_timeout=10, read_timeout=20, retries={'max_attempts': 2}))


def assume(row, external=True):
    args = {'RoleArn': row['role_arn'], 'RoleSessionName': 'MakeCloud-' + row['id'][:12], 'DurationSeconds': 3600}
    if external:
        args['ExternalId'] = row['external_id']
    return sts().assume_role(**args)['Credentials']


def google_refresh(row):
    return request_json('POST', GOOGLE_TOKEN, data={'grant_type': 'refresh_token', 'refresh_token': row['refresh_token'], 'client_id': os.environ['MAKECLOUD_GOOGLE_CLIENT_ID'], 'client_secret': os.environ['MAKECLOUD_GOOGLE_CLIENT_SECRET']})['access_token']


def azure_token():
    tenant = os.environ['MAKECLOUD_AZURE_TENANT_ID']
    if not re.fullmatch(UUID, tenant):
        raise HTTPException(503, 'Invalid Azure broker tenant.')
    return request_json('POST', f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', data={'grant_type': 'client_credentials', 'client_id': os.environ['MAKECLOUD_AZURE_CLIENT_ID'], 'client_secret': os.environ['MAKECLOUD_AZURE_CLIENT_SECRET'], 'scope': 'https://management.azure.com/.default'})['access_token']


def verify(store, row, target):
    if row.get('method') == 'hcp_oidc':
        import hcp_connections
        return hcp_connections.verify(store, row, target)
    if row['status'] == 'revoked':
        raise HTTPException(409, 'Start a new connection after revoking access.')
    if row['cloud'] == 'aws':
        matched = re.fullmatch(r'arn:aws:iam::([0-9]{12}):role/' + re.escape(row['role_name']), target)
        if not matched:
            raise HTTPException(422, 'Paste the RoleArn output from this connection’s CloudFormation stack.')
        row.update(role_arn=target, account_id=matched[1])
        from botocore.exceptions import ClientError, BotoCoreError
        try:
            assume(row)
            try:
                assume(row, external=False)
            except ClientError as exc:
                if exc.response.get('Error', {}).get('Code') != 'AccessDenied':
                    raise
            else:
                raise HTTPException(422, 'This role accepts access without an external ID. Correct its trust policy before connecting.')
        except (ClientError, BotoCoreError) as exc:
            raise HTTPException(422, 'MakeCloud could not assume this role. Check its trust policy and stack parameters.') from exc
        if row['profile'] == 'standard':
            row['boundary_arn'] = f"arn:aws:iam::{row['account_id']}:policy/MakeCloudRuntimeBoundary-{row['id']}"
    elif row['cloud'] == 'google':
        if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', target) or not row.get('refresh_token'):
            raise HTTPException(422, 'Authorize Google Cloud and choose a valid project ID.')
        token = google_refresh(row)
        headers = {'Authorization': 'Bearer ' + token}
        result = request_json('GET', GCP_API + '/projects/' + target, headers=headers)
        if result.get('state') != 'ACTIVE' or result.get('projectId') != target:
            raise HTTPException(422, 'Choose an active Google Cloud project.')
        allowed = request_json('POST', 'https://cloudresourcemanager.googleapis.com/v1/projects/' + target + ':testIamPermissions', headers=headers, json={'permissions': ['storage.buckets.create', 'run.services.create', 'serviceusage.services.enable']}).get('permissions', [])
        if 'storage.buckets.create' not in allowed:
            raise HTTPException(422, 'Your Google account needs permission to create storage buckets in this project.')
        row.update(project_id=target, workloads=['storage', 'container', 'custom'] if all(x in allowed for x in ('run.services.create', 'serviceusage.services.enable')) else ['storage'])
    else:
        if not re.fullmatch(UUID, target):
            raise HTTPException(422, 'Enter the Azure subscription ID shown in the portal.')
        headers = {'Authorization': 'Bearer ' + azure_token()}
        url = 'https://management.azure.com/subscriptions/' + target
        result = request_json('GET', url + '?api-version=2022-12-01', headers=headers)
        if result.get('state') != 'Enabled' or str(result.get('subscriptionId', '')).lower() != target.lower():
            raise HTTPException(422, 'Choose an enabled Azure subscription.')
        # Verify this connection’s registration, not just access through another delegation.
        assignment = request_json('GET', url + '/providers/Microsoft.ManagedServices/registrationAssignments/' + row['id'] + '?api-version=2022-10-01', headers=headers)
        expected = '/subscriptions/' + target + '/providers/Microsoft.ManagedServices/registrationDefinitions/' + row['id']
        if assignment.get('properties', {}).get('registrationDefinitionId', '').lower() != expected.lower():
            raise HTTPException(422, 'Approve this connection’s Lighthouse delegation first.')
        row.update(subscription_id=target.lower())
    row.update(status='connected', verified_at=time.time())
    store.vault_save(row)
    return public(row)


def google_callback(store, owner, state, code, nonce):
    consumed = store.consume(state, 'google_oauth', owner, lambda data: secrets.compare_digest(data['nonce_hash'], hashlib.sha256(nonce.encode()).hexdigest()))
    if not consumed:
        raise HTTPException(400, 'Cloud sign-in expired or did not start in this browser. Start again.')
    row = store.vault_get(consumed[1]['id'], owner)
    if not row or row['status'] != 'pending':
        raise HTTPException(409, 'This connection is no longer pending.')
    tokens = request_json('POST', GOOGLE_TOKEN, data={'code': code, 'grant_type': 'authorization_code', 'redirect_uri': base_url() + '/api/cloud/connections/google/callback', 'code_verifier': consumed[1]['verifier'], 'client_id': os.environ['MAKECLOUD_GOOGLE_CLIENT_ID'], 'client_secret': os.environ['MAKECLOUD_GOOGLE_CLIENT_SECRET']})
    if not tokens.get('refresh_token') or 'https://www.googleapis.com/auth/cloud-platform' not in tokens.get('scope', '').split():
        raise HTTPException(422, 'Google Cloud consent needs offline cloud access. Start again and grant the requested permission.')
    row['refresh_token'] = tokens['refresh_token']
    store.vault_save(row)
    return public(row)


def google_projects(row):
    headers = {'Authorization': 'Bearer ' + google_refresh(row)}
    projects, page = [], None
    # Paginate up to 1000 active projects without returning any provider tokens.
    for _ in range(10):
        data = request_json('GET', GCP_API + '/projects:search', headers=headers, params={'pageSize': 100, **({'pageToken': page} if page else {})})
        projects.extend({'id': item['projectId'], 'label': item.get('displayName', item['projectId'])} for item in data.get('projects', []) if item.get('state') == 'ACTIVE')
        page = data.get('nextPageToken')
        if not page:
            break
    return {'projects': projects, 'truncated': bool(page)}


def environment(row):
    if row['status'] != 'connected':
        raise HTTPException(409, 'Verify this cloud connection before planning.')
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SSL_CERT_FILE') if key in os.environ}
    if row['cloud'] == 'aws':
        try:
            token = assume(row)
        except Exception as exc:
            raise HTTPException(409, 'AWS access expired or was revoked. Verify the connection again.') from exc
        env.update(AWS_ACCESS_KEY_ID=token['AccessKeyId'], AWS_SECRET_ACCESS_KEY=token['SecretAccessKey'], AWS_SESSION_TOKEN=token['SessionToken'])
    elif row['cloud'] == 'google':
        env.update(GOOGLE_OAUTH_ACCESS_TOKEN=google_refresh(row), GOOGLE_PROJECT=row['project_id'])
    else:
        env.update(ARM_CLIENT_ID=os.environ['MAKECLOUD_AZURE_CLIENT_ID'], ARM_CLIENT_SECRET=os.environ['MAKECLOUD_AZURE_CLIENT_SECRET'], ARM_TENANT_ID=os.environ['MAKECLOUD_AZURE_TENANT_ID'], ARM_SUBSCRIPTION_ID=row['subscription_id'], ARM_USE_CLI='false', ARM_USE_MSI='false', ARM_USE_OIDC='false')
    return env
