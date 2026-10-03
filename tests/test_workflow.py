import importlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKECLOUD_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MAKECLOUD_MODE', 'demo')
    monkeypatch.delenv('MAKECLOUD_API_TOKEN', raising=False)
    sys.modules.pop('app', None)
    api = importlib.import_module('app')
    return api, TestClient(api.app)

def draft(client, provider='AWS', extra='none'):
    item = client.post('/sessions', json={'message':'Private storage bucket for my app'}).json()
    for answer in [provider, {'AWS':'us-east-1','GCP':'us-central1','Azure':'eastus'}[provider], 'testproject', 'Budget $10; private storage', extra]:
        response = client.post(f"/sessions/{item['id']}/messages", json={'message':answer})
        assert response.status_code == 200, response.text
        item = response.json()
    return item

@pytest.mark.parametrize('provider,extra,resource', [('AWS','none','aws_s3_bucket'),('GCP','my-project-123','google_storage_bucket'),('Azure','11111111-1111-1111-1111-111111111111','azurerm_storage_account')])
def test_complete_demo_and_persistence(backend, provider, extra, resource):
    api, client = backend
    item = draft(client, provider, extra)
    assert item['status'] == 'draft', item
    assert resource in item['code']
    assert len(item['messages']) == 12
    assert item['approval'] is None
    assert client.get('/sessions/'+item['id']).json()['code'] == item['code']
    assert client.get('/sessions').json()[0]['id'] == item['id']
    assert client.post('/sessions/'+item['id']+'/plan').status_code == 409
    assert client.post('/sessions/'+item['id']+'/apply', json={'approval':'fake','confirm':True}).status_code == 403
    api = importlib.reload(api)
    assert api.get(item['id'])['code'] == item['code']

def test_input_retry_and_missing_project(backend):
    _, client = backend
    item = client.post('/sessions',json={'message':'storage'}).json()
    url='/sessions/'+item['id']+'/messages'
    assert client.post(url,json={'message':'nonsense'}).status_code == 422
    for answer in ['GCP','us-central1','testproject','$5','none']:
        item=client.post(url,json={'message':answer}).json()
    assert item['error'] and item['status']=='gathering'
    assert client.post(url,json={'message':'my-cloud-project'}).json()['status']=='draft'

def test_save_invalidates_plan_and_policy(backend):
    api, client=backend
    item=draft(client)
    original=item['code']
    item=api.get(item['id'])
    item.update(status='planned', approval='approval',plan={'deletes':0})
    api.save(item)
    response=client.put('/sessions/'+item['id']+'/code',json={'code':original+'\n# edit'})
    assert response.json()['approval'] is None
    assert response.json()['plan'] is None
    for code in [original+'\nresource "null_resource" "bad" {}', original.replace('hashicorp/aws','evil/aws'),original+'\nlocals { stolen = file("/etc/passwd") }',original+'\nresource "aws_instance" "bad" { provisioner "local-exec" { command = "id" } }',original.replace('region = "us-east-1"','region = "us-east-1"\n access_key = "fake"'), 'not HCL']:
        assert client.put('/sessions/'+item['id']+'/code',json={'code':code}).status_code==422

def test_live_plan_approval_expiration_and_exact_apply(backend,monkeypatch):
    api,client=backend
    item=draft(client)
    monkeypatch.setattr(api,'MODE','live')
    monkeypatch.setenv('MAKECLOUD_ENABLE_APPLY','true')
    endpoint='/sessions/'+item['id']
    assert client.post(endpoint+'/apply',json={'approval':'fake','confirm':True}).status_code==409
    proposed={'changes':[{'address':'aws_s3_bucket.storage','actions':['delete','create']}], 'creates':1,'updates':0,'deletes':1,'digest':'x','plan_digest':'y'}
    with patch.object(api,'plan',return_value=proposed):
        planned=client.post(endpoint+'/plan').json()
    assert planned['status']=='planned'
    assert client.post(endpoint+'/apply',json={'approval':planned['approval'],'confirm':True}).status_code==422
    expired=api.get(item['id']);expired['plan_created']=time.time()-901;api.save(expired)
    assert client.post(endpoint+'/apply',json={'approval':planned['approval'],'confirm':True,'confirm_destructive':True}).status_code==409
    expired['plan_created']=time.time();api.save(expired)
    with patch.object(api,'apply') as execute:
        result=client.post(endpoint+'/apply',json={'approval':planned['approval'],'confirm':True,'confirm_destructive':True}).json()
        assert result['status']=='applied'
        execute.assert_called_once()
        assert execute.call_args.args[1]==item['code']
    assert client.post(endpoint+'/apply',json={'approval':planned['approval'],'confirm':True,'confirm_destructive':True}).status_code==409
    assert client.put(endpoint+'/code',json={'code':item['code']}).status_code==409

def test_terraform_failure_not_success(backend,monkeypatch):
    api,client=backend; item=draft(client);monkeypatch.setattr(api,'MODE','live')
    with patch.object(api,'plan',side_effect=ValueError('Terraform is not installed')):
        failed=client.post('/sessions/'+item['id']+'/plan').json()
    assert failed['status']=='draft' and failed['error'] and not failed['approval']

def test_auth_and_interrupted_restart(backend,monkeypatch):
    api,client=backend;item=draft(client)
    item=api.get(item['id']);item.update(status='applying',approval='unsafe');api.save(item)
    api=importlib.reload(api)
    assert api.get(item['id'])['status']=='interrupted'
    assert not api.get(item['id'])['approval']
    monkeypatch.setattr(api,'TOKEN','test-token')
    assert client.get('/health').status_code==401
    assert client.get('/health',headers={'Authorization':'Bearer test-token'}).status_code==200

def test_runner_subprocess_saved_plan_and_tampering(tmp_path):
    import run_cloud
    code='example'; saved=tmp_path/'approved.tfplan';saved.write_bytes(b'reviewed')
    approved={'digest':run_cloud.digest(code),'plan_digest':__import__('hashlib').sha256(b'reviewed').hexdigest()}
    with patch.object(run_cloud,'check_policy'),patch.object(run_cloud,'command') as execute:
        run_cloud.apply(tmp_path,code,'aws',approved)
        assert execute.call_args.args==(tmp_path,'apply','-input=false','-no-color','approved.tfplan')
        assert not saved.exists()
        saved.write_bytes(b'changed')
        with pytest.raises(ValueError):run_cloud.apply(tmp_path,code,'aws',approved)

def test_live_ai_service_and_missing_config(backend,monkeypatch):
    api,client=backend;item=draft(client);original=item['code'];monkeypatch.setattr(api,'MODE','live')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    response=client.post('/sessions/'+item['id']+'/messages',json={'message':'Add versioning'})
    assert 'not configured' in response.json()['error']
    monkeypatch.setenv('OPENAI_API_KEY','fake-test-key')
    result={'choices':[{'message':{'content':json.dumps({'terraform':original,'summary':'Private storage with versioning.'})}}]}
    with patch.object(api.httpx,'post',return_value=__import__('httpx').Response(200,json=result,request=__import__('httpx').Request('POST','https://example.com'))):
        response=client.post('/sessions/'+item['id']+'/messages',json={'message':'Add versioning'})
    assert response.json()['error'] is None
    assert response.json()['summary']=='Private storage with versioning.'

def test_secrets_rejected_before_storage(backend):
    _, client = backend
    response = client.post('/sessions', json={'message': 'storage password=please-do-not-store'})
    assert response.status_code == 422
    assert client.get('/sessions').json() == []

def test_unexpected_plan_failure_recovers(backend, monkeypatch):
    api, client = backend
    item = draft(client)
    monkeypatch.setattr(api, 'MODE', 'live')
    with patch.object(api, 'plan', side_effect=RuntimeError('unexpected internal detail')):
        result = client.post('/sessions/'+item['id']+'/plan').json()
    assert result['status'] == 'draft'
    assert 'unexpected internal detail' not in result['error']
    assert result['approval'] is None

def test_operation_lock_rejects_duplicate_plan(backend, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    api, client = backend
    item = draft(client)
    monkeypatch.setattr(api, 'MODE', 'live')
    started, finish = Event(), Event()
    def slow_plan(*args):
        started.set()
        assert finish.wait(3)
        return {'changes': [], 'creates': 0, 'updates': 0, 'deletes': 0, 'digest': 'x', 'plan_digest': 'y'}
    endpoint = '/sessions/'+item['id']
    with patch.object(api, 'plan', side_effect=slow_plan), ThreadPoolExecutor() as executor:
        future = executor.submit(client.post, endpoint+'/plan')
        assert started.wait(2)
        try:
            assert client.post(endpoint+'/plan').status_code == 409
            assert client.put(endpoint+'/code', json={'code': item['code']}).status_code == 409
        finally:
            finish.set()
        assert future.result().json()['status'] == 'planned'
