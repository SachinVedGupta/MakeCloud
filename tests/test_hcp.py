"""Remote execution contracts, restart recovery, and customer isolation."""
import io
import json
import tarfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import httpx
import pytest
from cryptography.fernet import Fernet
import hcp_connections
import hcp_runner
from store import Store
from test_workflow import backend, draft


@pytest.fixture
def remote(backend, monkeypatch):
    api, browser = backend
    api.MODE = 'live'
    monkeypatch.setenv('MAKECLOUD_RUNNER', 'hcp')
    monkeypatch.setenv('MAKECLOUD_HCP_TOKEN', 'test-server-token')
    monkeypatch.setenv('MAKECLOUD_HCP_ORGANIZATION', 'test-org')
    monkeypatch.setenv('MAKECLOUD_HCP_PROJECT_ID', 'prj-test')
    monkeypatch.setenv('MAKECLOUD_HCP_PROJECT_NAME', 'MakeCloud')
    monkeypatch.setenv('MAKECLOUD_ENABLE_APPLY', 'true')
    state = {'workspace': None, 'runs': {}, 'variables': {}, 'calls': [], 'transfers': [], 'plan': {'resource_changes': [{'address': 'aws_s3_bucket.main', 'type': 'aws_s3_bucket', 'mode': 'managed', 'change': {'actions': ['create'], 'before': None, 'after': {'id': 'bucket', 'secret': 'never-show'}}}]}, 'apply_timeout': False}

    def response(url, code=200, value=None):
        return httpx.Response(code, json=value or {}, request=httpx.Request('GET', url))

    def request(method, url, **kwargs):
        assert url.startswith(hcp_runner.API)
        assert kwargs['headers']['Authorization'] == 'Bearer test-server-token'
        path = url.removeprefix(hcp_runner.API)
        payload = kwargs.get('json') or {}
        state['calls'].append((method, path, payload))
        if path == '/projects/prj-test':
            data = {'attributes': {'name': 'MakeCloud'}}
        elif path.startswith('/organizations/test-org/workspaces'):
            if method == 'GET' and not state['workspace']:
                return response(url, 404)
            if method == 'POST':
                attributes = payload['data']['attributes']
                assert not attributes['auto-apply'] and not attributes['global-remote-state']
                state['workspace'] = {'id': 'ws-test', 'attributes': attributes, 'relationships': {'project': {'data': {'id': 'prj-test'}}}}
            data = state['workspace']
        elif path == '/workspaces/ws-test/varsets':
            data = state.get('varsets', [])
        elif path == '/workspaces/ws-test/vars':
            if method == 'POST':
                value = payload['data']['attributes']
                state['variables'][value['key']] = {'id': 'var-' + str(len(state['variables'])), 'attributes': value}
            data = list(state['variables'].values())
        elif path == '/workspaces/ws-test/configuration-versions':
            assert payload['data']['attributes']['auto-queue-runs'] is False
            data = {'id': 'cv-test', 'attributes': {'upload-url': 'https://archivist.terraform.io/upload/test'}}
        elif path == '/runs' and method == 'POST':
            attrs = payload['data']['attributes']
            assert not attrs['auto-apply']
            rid = 'run-' + str(len(state['runs']) + 1)
            data = {'id': rid, 'attributes': {**attrs, 'status': 'planning', 'actions': {'is-confirmable': False, 'is-cancelable': True}}, 'relationships': {**payload['data']['relationships'], 'plan': {'data': {'id': 'plan-test'}}}}
            state['runs'][rid] = data
        elif path == '/workspaces/ws-test/runs':
            data = list(state['runs'].values())
        elif path.startswith('/runs/'):
            rid = path.split('/')[2]
            data = state['runs'][rid]
            if method == 'POST':
                action = path.split('/')[-1]
                data['attributes']['status'] = 'apply_queued' if action == 'apply' else 'canceled'
                data['attributes']['actions']['is-confirmable'] = False
                if action == 'apply' and state['apply_timeout']:
                    raise httpx.ReadTimeout('simulated response loss')
        elif path == '/workspaces/ws-test/current-state-version':
            data = {'id': 'sv-test'}
        elif path == '/state-versions/sv-test/outputs':
            data = [{'attributes': {'name': 'bucket_name', 'value': 'mybucket', 'sensitive': False}}, {'attributes': {'name': 'secret_value', 'value': 'private', 'sensitive': False}}]
        else:
            pytest.fail('Unexpected HCP call: ' + method + ' ' + path)
        return response(url, value={'data': data})

    @contextmanager
    def transfer(method, url, **kwargs):
        assert 'headers' not in kwargs
        state['transfers'].append((method, url, kwargs.get('content')))
        yield response(url, value=state['plan'])

    def plan_get(url, **kwargs):
        assert url == hcp_runner.API + '/plans/plan-test/json-output'
        return httpx.Response(307, headers={'location': 'https://archivist.terraform.io/plan/test'}, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'request', request)
    monkeypatch.setattr(httpx, 'stream', transfer)
    monkeypatch.setattr(httpx, 'get', plan_get)
    return api, browser, state


def linked(remote):
    api, browser, state = remote
    item = draft(browser)
    cid = str(uuid.uuid4())
    row = {'id': cid, 'owner': 'operator', 'cloud': 'aws', 'label': 'My AWS', 'status': 'connected', 'method': 'hcp_oidc', 'account_id': '123456789012', 'workspace_name': 'mc-' + cid.replace('-', ''), 'workloads': ['storage'], 'hcp_org': 'test-org', 'hcp_project_id': 'prj-test', 'hcp_project_name': 'MakeCloud'}
    api.STORE.vault_save(row)
    result = browser.put('/sessions/' + item['id'] + '/connection', json={'connection_id': cid})
    assert result.status_code == 200, result.text
    return result.json(), row


def planned(remote):
    _, browser, state = remote
    item, row = linked(remote)
    result = browser.post('/sessions/' + item['id'] + '/plan', json={'revision': item['revision']})
    assert result.status_code == 202, result.text
    assert result.json()['status'] == 'planning'
    run = next(iter(state['runs'].values()))
    run['attributes'].update(status='planned', actions={'is-confirmable': True, 'is-discardable': True})
    result = browser.get('/sessions/' + item['id']).json()
    assert result['status'] == 'planned', result
    return result, row


def test_hcp_plan_apply_and_redaction(remote):
    _, browser, state = remote
    item, _ = planned(remote)
    assert item['plan']['creates'] == 1
    assert 'never-show' not in json.dumps(item)
    assert not any(path.endswith('/actions/apply') for _, path, _ in state['calls'])
    upload = next(content for method, _, content in state['transfers'] if method == 'PUT')
    with tarfile.open(fileobj=io.BytesIO(upload), mode='r:gz') as archive:
        assert archive.getnames() == ['main.tf']
        assert b'allowed_account_ids' in archive.extractfile('main.tf').read()
    result = browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True, 'revision': item['revision']})
    assert result.status_code == 202, result.text
    assert browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True}).status_code == 409
    state['runs'][item['plan']['run_id']]['attributes']['status'] = 'applied'
    applied = browser.get('/sessions/' + item['id']).json()
    assert applied['status'] == 'applied' and applied['has_infrastructure']
    assert applied['outputs']['secret_value']['value'] == '[sensitive]'
    assert sum(path.endswith('/actions/apply') for _, path, _ in state['calls']) == 1


def test_remote_apply_survives_restart_without_replay(remote):
    api, browser, state = remote
    item, _ = planned(remote)
    state['apply_timeout'] = True
    result = browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True})
    assert result.status_code == 202
    api.recover_startup()
    assert api.get(item['id'])['status'] == 'applying'
    state['runs'][item['plan']['run_id']]['attributes']['status'] = 'applied'
    assert browser.get('/sessions/' + item['id']).json()['status'] == 'applied'
    assert sum(path.endswith('/actions/apply') for _, path, _ in state['calls']) == 1


def test_remote_changed_plan_cannot_apply(remote):
    _, browser, state = remote
    item, _ = planned(remote)
    state['plan']['resource_changes'][0]['change']['after']['id'] = 'different'
    result = browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True})
    assert result.status_code == 202
    assert not any(path.endswith('/actions/apply') for _, path, _ in state['calls'])
    assert 'remote plan changed' in result.json()['error'].lower()


def test_connection_cannot_share_state_across_projects(remote):
    _, browser, _ = remote
    _, row = linked(remote)
    other = draft(browser)
    result = browser.put('/sessions/' + other['id'] + '/connection', json={'connection_id': row['id']})
    assert result.status_code == 409
    assert other['connection_id'] is None


def test_remote_cancel_and_partial_failure(remote):
    _, browser, state = remote
    item, _ = linked(remote)
    browser.post('/sessions/' + item['id'] + '/plan')
    assert browser.post('/sessions/' + item['id'] + '/cancel').status_code == 200
    assert browser.get('/sessions/' + item['id']).json()['status'] == 'draft'


def test_remote_submission_reconciles_missing_response(remote):
    api, browser, state = remote
    item, _ = linked(remote)
    browser.post('/sessions/' + item['id'] + '/plan')
    saved = api.get(item['id'])
    saved['job']['remote'].pop('run_id')
    api.save(saved)
    run = next(iter(state['runs'].values()))
    run['attributes'].update(status='planned', actions={'is-confirmable': True})
    assert browser.get('/sessions/' + item['id']).json()['status'] == 'planned'
    assert len(state['runs']) == 1


@pytest.mark.parametrize('cloud', ['aws', 'google', 'azurerm'])
def test_workspace_bound_setup_contains_no_cloud_secrets(remote, cloud):
    api, _, _ = remote
    result = hcp_connections.begin(api.STORE, 'operator', cloud, 'Test', 'storage')
    row = api.STORE.vault_get(result['connection']['id'], 'operator')
    text = json.dumps(result)
    assert hcp_connections.subject(row, 'plan') in (json.dumps(result['template']) if cloud == 'aws' else result['setup_script'])
    assert hcp_connections.subject(row, 'apply') in (json.dumps(result['template']) if cloud == 'aws' else result['setup_script'])
    assert result['target_fields']
    assert 'client_secret' not in text and 'AWS_SECRET_ACCESS_KEY' not in text
    if cloud == 'azurerm':
        assert 'Resource Group Contributor' in result['setup_script']


def test_verification_checks_exact_identity(remote):
    api, _, state = remote
    result = hcp_connections.begin(api.STORE, 'operator', 'aws', 'Test', 'storage')
    row = api.STORE.vault_get(result['connection']['id'], 'operator')
    verified = hcp_connections.verify(api.STORE, row, json.dumps({'role_arn': 'arn:aws:iam::123456789012:role/MakeCloud-' + row['id']}))
    assert verified['status'] == 'verifying'
    state['plan'] = {'output_changes': {'identity': {'after': '000000000000'}}}
    next(iter(state['runs'].values()))['attributes']['status'] = 'planned_and_finished'
    row = api.STORE.vault_get(row['id'], 'operator')
    assert hcp_connections.refresh(api.STORE, row)['status'] == 'pending'
    assert not any(path.endswith('/actions/apply') for _, path, _ in state['calls'])


def test_free_beta_account_cap_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKECLOUD_MAX_ACCOUNTS', '1')
    store = Store(tmp_path)
    store.register('one@example.com', 'a-long-test-password')
    with pytest.raises(ValueError, match='account limit'):
        store.register('two@example.com', 'a-long-test-password')


def test_external_database_requires_stable_key(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKECLOUD_DATABASE_URL', 'postgresql://invalid')
    monkeypatch.delenv('MAKECLOUD_ENCRYPTION_KEY', raising=False)
    with pytest.raises(RuntimeError, match='stable'):
        Store(tmp_path)


def test_transfer_rejects_untrusted_hosts(remote):
    client = hcp_runner.Client()
    for url in ['http://app.terraform.io/file', 'https://127.0.0.1/file', 'https://app.terraform.io.evil.test/file', 'https://user:password@app.terraform.io/file']:
        with pytest.raises(ValueError, match='unsupported'):
            client.transfer(url)


def test_completed_apply_is_preserved_when_outputs_unavailable(remote, monkeypatch):
    _, browser, state = remote
    item, _ = planned(remote)
    browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True})
    state['runs'][item['plan']['run_id']]['attributes']['status'] = 'applied'
    def unavailable(*args):
        raise ValueError('temporary state outage')
    monkeypatch.setattr(hcp_runner.Client, 'outputs', unavailable)
    result = browser.get('/sessions/' + item['id']).json()
    assert result['status'] == 'applied' and result['has_infrastructure']
    assert result['job']['status'] == 'succeeded'
    assert result['approval'] is None
    assert 'outputs could not be retrieved' in result['error']


def test_remote_expired_plan_cannot_apply(remote):
    api, browser, state = remote
    item, _ = planned(remote)
    saved = api.get(item['id'])
    saved['plan_created'] = time.time() - 901
    api.save(saved)
    response = browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True})
    assert response.status_code == 409
    assert not any(path.endswith('/actions/apply') for _, path, _ in state['calls'])


def test_inherited_credentials_prevent_remote_submission(remote):
    _, browser, state = remote
    item, _ = linked(remote)
    state['varsets'] = [{'id': 'varset-other-project'}]
    result = browser.post('/sessions/' + item['id'] + '/plan').json()
    assert result['status'] == 'draft'
    assert 'inherit variable sets' in result['error']
    assert not state['runs']


def test_failed_apply_requires_recovery_and_cleanup_acknowledgment(remote):
    _, browser, state = remote
    item, _ = planned(remote)
    browser.post('/sessions/' + item['id'] + '/apply', json={'approval': item['approval'], 'confirm': True})
    state['runs'][item['plan']['run_id']]['attributes']['status'] = 'errored'
    failed = browser.get('/sessions/' + item['id']).json()
    assert failed['status'] == 'apply_failed' and failed['recovery_required']
    assert browser.post('/sessions/' + item['id'] + '/plan').status_code == 409
    assert browser.post('/sessions/' + item['id'] + '/recover', json={'confirm': False}).status_code == 409
    recovered = browser.post('/sessions/' + item['id'] + '/recover', json={'confirm': True}).json()
    assert recovered['has_infrastructure'] and not recovered['recovery_required']
    state['plan']['resource_changes'][0]['change']['actions'] = ['delete']
    browser.post('/sessions/' + item['id'] + '/plan', json={'purpose': 'destroy'})
    run = list(state['runs'].values())[-1]
    assert run['attributes']['is-destroy']
    run['attributes'].update(status='planned', actions={'is-confirmable': True})
    cleanup = browser.get('/sessions/' + item['id']).json()
    assert cleanup['plan']['purpose'] == 'destroy'
    assert browser.post('/sessions/' + item['id'] + '/apply', json={'approval': cleanup['approval'], 'confirm': True}).status_code == 422
    assert browser.post('/sessions/' + item['id'] + '/apply', json={'approval': cleanup['approval'], 'confirm': True, 'confirm_destructive': True}).status_code == 202
    run['attributes']['status'] = 'applied'
    assert browser.get('/sessions/' + item['id']).json()['status'] == 'destroyed'
