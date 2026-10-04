"""Hosted customer journeys with controlled provider endpoints, never real grants."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
import cloud_connections as cloud
import hcl2
import run_cloud
import account_mail
from store import Store
from test_workflow import backend, draft, wait


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('MAKECLOUD_PUBLIC_URL', 'https://makecloud.example.com')
    monkeypatch.setenv('MAKECLOUD_AWS_PRINCIPAL_ARN', 'arn:aws:iam::999999999999:role/MakeCloudBroker')
    monkeypatch.setenv('MAKECLOUD_AWS_TEMPLATE_URL', 'https://bootstrap.s3.amazonaws.com/makecloud.json')
    monkeypatch.setenv('MAKECLOUD_GOOGLE_CLIENT_ID', 'client.example')
    monkeypatch.setenv('MAKECLOUD_GOOGLE_CLIENT_SECRET', 'host-google-secret')
    for key in ('TENANT_ID', 'CLIENT_ID', 'PRINCIPAL_ID'):
        monkeypatch.setenv('MAKECLOUD_AZURE_' + key, '11111111-1111-1111-1111-111111111111')
    monkeypatch.setenv('MAKECLOUD_AZURE_CLIENT_SECRET', 'host-azure-secret')


def aws_verified(api, client, profile='storage'):
    begin = client.post('/connections/begin', json={'cloud': 'aws', 'profile': profile}).json()
    cid = begin['connection']['id']
    arn = 'arn:aws:iam::123456789012:role/' + begin['role_name']
    deny = ClientError({'Error': {'Code': 'AccessDenied'}}, 'AssumeRole')
    with patch.object(cloud, 'assume', side_effect=[{'AccessKeyId': 'temp'}, deny]):
        verified = client.post('/connections/' + cid + '/verify', json={'target': arn})
    assert verified.status_code == 200, verified.text
    return cid, begin, arn


def test_aws_console_profile_verification_and_private_storage(backend, configured):
    api, client = backend
    cid, begin, arn = aws_verified(api, client)
    assert begin['parameters']['Profile'] == 'storage'
    assert 'param_ExternalId=' in begin['console_url']
    item = api.STORE.vault_get(cid, 'operator')
    assert item['role_arn'] == arn and item['account_id'] == '123456789012'
    assert item['workloads'] == ['storage']
    public = client.get('/connections').text
    assert 'external_id' not in public and 'role_arn' not in public and 'refresh_token' not in public
    assert item['external_id'].encode() not in api.STORE.path.read_bytes()
    assert client.post('/connections/' + cid + '/verify', json={'target': arn.replace('123456789012', '000000000000')}).status_code == 409
    assert client.post('/connections/not-mine/verify', json={'target': arn}).status_code == 404


def test_aws_rejects_missing_external_id_protection(backend, configured):
    _, client = backend
    result = client.post('/connections/begin', json={'cloud': 'aws'}).json()
    with patch.object(cloud, 'assume', return_value={'AccessKeyId': 'temporary'}):
        checked = client.post('/connections/' + result['connection']['id'] + '/verify', json={'target': 'arn:aws:iam::123456789012:role/' + result['role_name']})
    assert checked.status_code == 422 and 'without an external ID' in checked.text
    assert client.get('/connections').json()[0]['status'] == 'pending'


def test_aws_network_error_does_not_count_as_external_id_protection(backend, configured):
    _, client = backend
    result = client.post('/connections/begin', json={'cloud': 'aws'}).json()
    with patch.object(cloud, 'assume', side_effect=[{}, ClientError({'Error': {'Code': 'Throttling'}}, 'AssumeRole')]):
        response = client.post('/connections/' + result['connection']['id'] + '/verify', json={'target': 'arn:aws:iam::123456789012:role/' + result['role_name']})
    assert response.status_code == 422


def test_standard_boundary_injected_and_template_limits_iam(backend, configured):
    api, client = backend
    cid, begin, _ = aws_verified(api, client, 'standard')
    item = draft(client, workload='container')
    row = api.STORE.vault_get(cid, 'operator')
    guarded = run_cloud.account_guard(item['code'], 'aws', row['account_id'], row['boundary_arn'])
    doc = hcl2.loads(guarded)
    role = next(block['aws_iam_role']['execution'] for block in doc['resource'] if 'aws_iam_role' in block)
    assert role['path'] == '/makecloud/' and role['permissions_boundary'] == row['boundary_arn']
    text = json.dumps(begin['template'])
    assert 'AdministratorAccess' not in text and 'iam:PermissionsBoundary' in text and 'iam:PassedToService' in text


def google_begin(api, client):
    result = client.post('/connections/begin', json={'cloud': 'google'}).json()
    state = parse_qs(urlparse(result['authorization_url']).query)['state'][0]
    nonce = client.cookies.get('makecloud_oauth')
    return result['connection']['id'], state, nonce


def test_google_pkce_browser_binding_one_use_and_encrypted_refresh(backend, configured):
    api, client = backend
    cid, state, nonce = google_begin(api, client)
    with patch.object(cloud, 'request_json', return_value={'refresh_token': 'private-refresh-token', 'scope': 'https://www.googleapis.com/auth/cloud-platform'}) as request:
        with pytest.raises(HTTPException):
            cloud.google_callback(api.STORE, 'different-owner', state, 'code', nonce)
        with pytest.raises(HTTPException):
            cloud.google_callback(api.STORE, 'operator', state, 'code', 'wrong-browser')
        result = cloud.google_callback(api.STORE, 'operator', state, 'code', nonce)
        assert result['id'] == cid
        args = request.call_args.kwargs['data']
        assert args['code_verifier'] and args['redirect_uri'].startswith('https://')
        with pytest.raises(HTTPException):
            cloud.google_callback(api.STORE, 'operator', state, 'replayed', nonce)
    assert 'private-refresh-token' not in client.get('/connections').text
    assert b'private-refresh-token' not in api.STORE.path.read_bytes()


def test_google_callback_redirect_nonce_forwarding_contract(backend, configured):
    api, client = backend
    cid, state, nonce = google_begin(api, client)
    # TestClient calls the backend path; browser nonce has the public proxy path.
    with patch.object(cloud, 'request_json', return_value={'refresh_token': 'refresh', 'scope': 'https://www.googleapis.com/auth/cloud-platform'}):
        response = client.get('/connections/google/callback', params={'state': state, 'code': 'code'}, headers={'Cookie': 'makecloud_oauth=' + nonce}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'].endswith('#connected=' + cid)
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert response.headers['referrer-policy'] == 'no-referrer'


def test_google_offline_consent_is_required(backend, configured):
    api, client = backend
    _, state, nonce = google_begin(api, client)
    with patch.object(cloud, 'request_json', return_value={'access_token': 'short-lived', 'scope': 'https://www.googleapis.com/auth/cloud-platform'}):
        with pytest.raises(HTTPException, match='offline'):
            cloud.google_callback(api.STORE, 'operator', state, 'code', nonce)


def test_google_project_picker_pagination_permissions_and_target(backend, configured):
    api, client = backend
    cid, state, nonce = google_begin(api, client)
    with patch.object(cloud, 'request_json', return_value={'refresh_token': 'refresh', 'scope': 'https://www.googleapis.com/auth/cloud-platform'}):
        cloud.google_callback(api.STORE, 'operator', state, 'code', nonce)
    responses = [{'access_token': 'access'}, {'projects': [{'projectId': 'project-one', 'displayName': 'One', 'state': 'ACTIVE'}], 'nextPageToken': 'next'}, {'projects': [{'projectId': 'project-two', 'state': 'ACTIVE'}, {'projectId': 'deleted', 'state': 'DELETE_REQUESTED'}]}]
    with patch.object(cloud, 'request_json', side_effect=responses):
        choices = client.get('/connections/' + cid + '/projects').json()
    assert len(choices['projects']) == 2 and not choices['truncated']
    responses = [{'access_token': 'fresh'}, {'projectId': 'project-one', 'state': 'ACTIVE'}, {'permissions': ['storage.buckets.create']}]
    with patch.object(cloud, 'request_json', side_effect=responses):
        verified = client.post('/connections/' + cid + '/verify', json={'target': 'project-one'})
    assert verified.status_code == 200 and verified.json()['workloads'] == ['storage']
    assert client.post('/connections/' + cid + '/verify', json={'target': 'project-two'}).status_code == 409
    with patch.object(cloud, 'request_json', return_value={'access_token': 'fresh-for-job'}):
        env = cloud.environment(api.STORE.vault_get(cid, 'operator'))
    assert env['GOOGLE_PROJECT'] == 'project-one' and env['GOOGLE_OAUTH_ACCESS_TOKEN'] == 'fresh-for-job'
    assert 'host-google-secret' not in env.values()


def test_azure_portal_ticket_can_be_read_without_customer_auth(backend, configured, monkeypatch):
    api, client = backend
    result = client.post('/connections/begin', json={'cloud': 'azurerm'}).json()
    cid = result['connection']['id']
    ticket_url = result['console_url'].split('/uri/')[1]
    from urllib.parse import unquote
    ticket = unquote(ticket_url).split('/template/')[1]
    api.AUTH_MODE = 'accounts'
    for _ in range(2):
        template = client.get('/connections/template/' + ticket)
        assert template.status_code == 200
        assert template.json()['resources'][0]['name'] == cid
    assert client.get('/connections/' + cid + '/instructions').status_code == 401
    with api.STORE.connect() as db:
        db.execute('UPDATE challenges SET expires=0')
    assert client.get('/connections/template/' + ticket).status_code == 404


def test_azure_requires_matching_delegation_and_scopes_worker(backend, configured):
    api, client = backend
    cid = client.post('/connections/begin', json={'cloud': 'azurerm'}).json()['connection']['id']
    sub = '22222222-2222-2222-2222-222222222222'
    responses = [{'access_token': 'broker'}, {'state': 'Enabled', 'subscriptionId': sub}, {'properties': {'registrationDefinitionId': f'/subscriptions/{sub}/providers/Microsoft.ManagedServices/registrationDefinitions/{cid}'}}]
    with patch.object(cloud, 'request_json', side_effect=responses):
        result = client.post('/connections/' + cid + '/verify', json={'target': sub})
    assert result.status_code == 200
    env = cloud.environment(api.STORE.vault_get(cid, 'operator'))
    assert env['ARM_SUBSCRIPTION_ID'] == sub and env['ARM_USE_CLI'] == 'false'
    assert 'MAKECLOUD_API_TOKEN' not in env


def test_connection_cross_owner_and_disconnect_guard(backend, configured):
    api, client = backend
    cid, _, _ = aws_verified(api, client)
    item = draft(client)
    endpoint = '/sessions/' + item['id']
    assert client.put(endpoint + '/connection', json={'connection_id': cid}).status_code == 200
    current = api.get(item['id']); current['has_infrastructure'] = True; api.save(current)
    assert client.delete('/connections/' + cid).status_code == 409
    current['has_infrastructure'] = False; current.update(plan={'creates': 1}, approval='old', status='planned'); api.save(current)
    assert client.delete('/connections/' + cid).status_code == 200
    current = api.get(item['id'])
    assert current['connection_id'] is None and current['approval'] is None and current['plan'] is None
    assert api.STORE.vault_get(cid, 'operator')['status'] == 'revoked'
    assert api.STORE.vault_get(cid, 'another-owner') is None


def test_pending_connection_cannot_be_selected(backend, configured):
    _, client = backend
    cid = client.post('/connections/begin', json={'cloud': 'aws'}).json()['connection']['id']
    item = draft(client)
    assert client.put('/sessions/' + item['id'] + '/connection', json={'connection_id': cid}).status_code == 409


def test_nested_targets_and_hosted_resource_policy(backend, configured):
    api, client = backend
    item = draft(client, 'google', 'container')
    item = api.get(item['id'])
    api.target_check(item['code'], item['spec'])
    bad = item['code'] + '\nresource "google_project_service" "foreign" {\n project = "foreign-project"\n service = "run.googleapis.com"\n}'
    with pytest.raises(ValueError, match='targets'):
        api.target_check(bad, item['spec'])
    row = {'id': 'test', 'owner': 'operator', 'method': 'google_oauth', 'cloud': 'google', 'status': 'connected'}
    api.STORE.vault_save(row); item['connection_id'] = 'test'
    api.connection_policy(item)
    item['code'] += '\nresource "google_project_iam_member" "evil" {\n project = "my-project-123"\n role = "roles/owner"\n member = "user:other@example.com"\n}'
    with pytest.raises(HTTPException, match='reviewed'):
        api.connection_policy(item)


def test_durable_claim_has_one_winner_and_no_credentials(tmp_path):
    store = Store(tmp_path)
    item = {'id': 'project'}
    job = {'id': 'job', 'sid': 'project', 'account': {'id': 'owner'}, 'kind': 'plan'}
    store.enqueue(item, job)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.claim('job'), range(8)))
    assert sum(result is not None for result in results) == 1
    assert not store.queued()
    assert b'owner' not in store.path.read_bytes()


def test_queued_jobs_survive_restart_running_apply_never_replays(backend):
    api, client = backend
    item = draft(client)
    with patch.object(api.EXECUTOR, 'submit'):
        queued = api.queue(api.get(item['id']), 'plan', {'id': 'operator', 'email': 'operator'}, None, {'cloud': 'aws'})
    job = queued['job']['id']
    api.recover_startup()
    assert api.get(item['id'])['job']['status'] == 'queued' and job in api.STORE.queued()
    payload = api.STORE.claim(job); assert payload
    current = api.get(item['id']); current['job']['status'] = 'running'; current['status'] = 'applying'; api.save(current)
    api.recover_startup()
    assert api.get(item['id'])['recovery_required'] and not api.STORE.queued()


def test_public_signup_verification_recovery_and_login_revocation(backend, monkeypatch):
    api, client = backend; api.AUTH_MODE = 'accounts'
    monkeypatch.setenv('MAKECLOUD_REGISTRATION', 'public')
    monkeypatch.setenv('MAKECLOUD_SECURE_COOKIES', 'false')
    monkeypatch.setenv('MAKECLOUD_PUBLIC_URL', 'http://localhost:3000')
    for key in ('HOST', 'USER', 'PASSWORD'):
        monkeypatch.setenv('MAKECLOUD_SMTP_' + key, 'fixture')
    monkeypatch.setenv('MAKECLOUD_MAIL_FROM', 'makecloud@example.com')
    data = {'email': 'customer@example.com', 'password': 'initial-password-123'}
    with patch.object(api.account_mail, 'send') as mail:
        response = client.post('/auth/register', json=data)
        assert response.status_code == 200 and response.json()['verification_required']
        token = mail.call_args.args[1]
        assert client.post('/auth/login', json=data).status_code == 401
        assert client.post('/auth/verify', json={'token': token}).status_code == 200
        assert client.post('/auth/verify', json={'token': token}).status_code == 400
        login = client.post('/auth/login', json=data)
        assert login.status_code == 200 and 'SameSite=lax' in login.headers['set-cookie']
        assert client.get('/auth/me').status_code == 200
        assert client.post('/auth/request-link', json={'email': data['email'], 'purpose': 'reset'}).status_code == 200
        reset = mail.call_args.args[1]
        assert client.post('/auth/reset', json={'token': reset, 'password': 'new-password-1234'}).status_code == 200
        assert client.get('/auth/me').status_code == 401
        assert client.post('/auth/login', json=data).status_code == 401
        assert client.post('/auth/login', json={**data, 'password': 'new-password-1234'}).status_code == 200
        assert client.post('/auth/reset', json={'token': reset, 'password': 'another-password'}).status_code == 400
        before = mail.call_count
        result = client.post('/auth/request-link', json={'email': 'unknown@example.com', 'purpose': 'reset'})
        assert result.status_code == 200 and mail.call_count == before


def test_public_signup_fails_closed_without_email(backend, monkeypatch):
    api, client = backend; api.AUTH_MODE = 'accounts'
    monkeypatch.setenv('MAKECLOUD_REGISTRATION', 'public')
    monkeypatch.delenv('MAKECLOUD_SMTP_HOST', raising=False)
    assert client.post('/auth/register', json={'email': 'new@example.com', 'password': 'strong-password'}).status_code == 503


def test_cloud_setup_disabled_and_invalid_template_origin(backend, configured, monkeypatch):
    _, client = backend
    monkeypatch.delenv('MAKECLOUD_GOOGLE_CLIENT_SECRET')
    assert client.post('/connections/begin', json={'cloud': 'google'}).status_code == 503
    monkeypatch.setenv('MAKECLOUD_AWS_TEMPLATE_URL', 'https://attacker.example.com/template.json')
    assert client.post('/connections/begin', json={'cloud': 'aws'}).status_code == 503


def test_challenge_expiry_and_wrong_type(tmp_path):
    store = Store(tmp_path)
    token = store.challenge('owner', 'oauth', {'nonce_hash': 'value'}, lifetime=-1)
    assert store.consume(token, 'oauth', 'owner') is None
    token = store.challenge('owner', 'verify')
    assert store.consume(token, 'reset') is None
    assert store.consume(token, 'verify')[0] == 'owner'


def test_only_matching_connection_fills_project_without_typing_ids(backend):
    api, client = backend
    row = {'id': 'my-google', 'owner': 'operator', 'cloud': 'google', 'project_id': 'my-project-123', 'label': 'My project', 'status': 'connected'}
    api.STORE.vault_save(row)
    item = client.post('/sessions', json={'message': 'GCP private bucket us-central1 named myapp budget $10'}).json()
    assert item['spec']['project_id'] == row['project_id'] and item['connection_id'] == row['id'] and item['proposal']
    item = client.post('/sessions', json={'message': 'GCP private bucket us-central1 named myapp budget $10 project ID is other-project-123'}).json()
    assert item['connection_id'] is None and item['spec']['project_id'] == 'other-project-123'


def test_selecting_connection_proposes_target_change_preserving_accepted_code(backend):
    api, client = backend
    item = draft(client, 'google'); original = item['code']
    row = {'id': 'second-google', 'owner': 'operator', 'cloud': 'google', 'project_id': 'second-project', 'label': 'Second project', 'status': 'connected'}
    api.STORE.vault_save(row)
    response = client.put('/sessions/' + item['id'] + '/connection', json={'connection_id': row['id']})
    assert response.status_code == 200
    changed = response.json()
    assert changed['spec']['project_id'] == 'second-project' and changed['code'] == original and 'second-project' in changed['proposal']['code']


def test_startup_dispatches_queued_work_once(backend):
    api, client = backend
    item = draft(client)
    with patch.object(api.EXECUTOR, 'submit'):
        api.queue(api.get(item['id']), 'plan', {'id': 'operator', 'email': 'operator'}, None, {'cloud': 'aws'})
    from fastapi.testclient import TestClient
    with patch.object(run_cloud, 'plan', return_value={'changes': [], 'creates': 0, 'updates': 0, 'deletes': 0}) as operation:
        with TestClient(api.app) as resumed:
            result = wait(resumed, item['id'])
            assert result['status'] == 'planned' and result['approval']
            assert operation.call_count == 1 and not api.STORE.queued()


def test_account_mail_verifies_tls_before_authentication(configured, monkeypatch):
    for key in ('HOST', 'USER', 'PASSWORD'):
        monkeypatch.setenv('MAKECLOUD_SMTP_' + key, 'fixture')
    monkeypatch.setenv('MAKECLOUD_MAIL_FROM', 'makecloud@example.com')
    with patch.object(account_mail.smtplib, 'SMTP') as smtp:
        account_mail.send('customer@example.com', 'one-use-token', 'verify')
        server = smtp.return_value.__enter__.return_value
        context = server.starttls.call_args.kwargs['context']
        assert context.check_hostname and context.verify_mode == 2
        assert server.login.called and server.send_message.called
        message = server.send_message.call_args.args[0]
        assert '/#verify=one-use-token' in message.get_content()


def test_published_aws_template_matches_generator():
    from pathlib import Path
    template = Path(__file__).resolve().parents[1] / 'deploy/aws-bootstrap.json'
    assert json.loads(template.read_text()) == cloud.aws_template()


def test_job_acquires_credentials_only_after_queue_claim(backend, configured):
    api, client = backend
    cid, _, _ = aws_verified(api, client)
    api.MODE = 'live'
    item = draft(client)
    assert item['connection_id'] == cid
    temporary = {'PATH': '/usr/bin', 'AWS_ACCESS_KEY_ID': 'job-temporary', 'AWS_SECRET_ACCESS_KEY': 'job-secret', 'AWS_SESSION_TOKEN': 'job-session'}
    with patch.object(cloud, 'environment', return_value=temporary) as credentials, patch.object(run_cloud, 'plan', return_value={'changes': [], 'creates': 0, 'updates': 0, 'deletes': 0}) as operation:
        response = client.post('/sessions/' + item['id'] + '/plan', json={})
        assert response.status_code == 202
        result = wait(client, item['id'])
        assert result['status'] == 'planned'
        assert credentials.call_count == 1 and operation.call_args.kwargs['environment'] == temporary
    assert 'job-secret' not in client.get('/sessions/' + item['id']).text
    assert b'job-secret' not in api.STORE.path.read_bytes()


@pytest.mark.parametrize('provider', ['google', 'azurerm'])
def test_resource_location_overrides_require_requirement_change(backend, provider):
    api, client = backend
    item = draft(client, provider, 'container')
    api.target_check(item['code'], item['spec'])
    foreign = item['code'].replace('"us-central1"' if provider == 'google' else '"eastus"', '"europe-west1"' if provider == 'google' else '"westus"')
    with pytest.raises(ValueError):
        api.target_check(foreign, item['spec'])
