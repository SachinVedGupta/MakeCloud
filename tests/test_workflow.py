"""User-flow regressions with temporary data and controlled external services."""
import importlib
import json
import sys
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from domain import Spec, extract, validate_spec
from blueprints import build
import run_cloud

@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKECLOUD_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MAKECLOUD_MODE', 'demo')
    monkeypatch.setenv('MAKECLOUD_AUTH_MODE', 'operator')
    monkeypatch.delenv('MAKECLOUD_API_TOKEN', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('MAKECLOUD_CONNECTIONS_FILE', raising=False)
    sys.modules.pop('app', None)
    api = importlib.import_module('app')
    client = TestClient(api.app)
    yield api, client
    for cancel in list(api.CANCELLATIONS.values()):
        cancel.set()
    api.EXECUTOR.shutdown(wait=True, cancel_futures=True)


def draft(client, cloud='aws', workload='storage', **overrides):
    region = {'aws':'us-east-1', 'google':'us-central1', 'azurerm':'eastus'}[cloud]
    text = f"Private storage on { {'aws':'AWS','google':'GCP','azurerm':'Azure'}[cloud]} in {region} named testproject budget $100"
    if cloud=='google': text += ' project ID is my-project-123'
    if cloud=='azurerm': text += ' subscription 11111111-1111-1111-1111-111111111111'
    item = client.post('/sessions', json={'message':text}).json()
    if workload != 'storage' or overrides:
        updates={'workload':workload, **overrides}
        if workload=='container': updates={'image':'nginx:stable','port':80,'public':True,'database':False,**updates}
        if workload=='static_site': updates={'public':True,**updates}
        item=client.put('/sessions/'+item['id']+'/requirements',json={'updates':updates}).json()
        item=client.post('/sessions/'+item['id']+'/messages',json={'message':'generate draft'}).json()
    assert item.get('proposal'), item
    item=client.post('/sessions/'+item['id']+'/proposal',json={'confirm':True,'proposal_id':item['proposal']['id'],'revision':item['revision']}).json()
    assert item['status']=='draft',item
    return item


def wait(client,sid):
    for _ in range(150):
        item=client.get('/sessions/'+sid).json()
        if item['status'] not in ('planning','applying'):return item
        time.sleep(.02)
    pytest.fail('Background job did not complete')


@pytest.mark.parametrize('cloud,workload',[(c,w) for c in ['aws','google','azurerm'] for w in ['storage','container']]+[('aws','static_site'),('aws','database')])
def test_supported_designs_persist_and_demo_never_deploys(backend,cloud,workload):
    api,client=backend
    item=draft(client,cloud,workload)
    assert run_cloud.check_policy(item['code'],cloud)
    loaded=client.get('/sessions/'+item['id']).json()
    assert loaded['code']==item['code'] and loaded['revision']==1
    assert client.post('/sessions/'+item['id']+'/plan',json={}).status_code==409
    assert client.post('/sessions/'+item['id']+'/apply',json={'approval':'fake','confirm':True}).status_code in (403,409)
    raw=(api.ROOT/'sessions.db').read_bytes()
    assert b'testproject' not in raw and item['code'].encode() not in raw
    assert client.get('/sessions').json()[0]['id']==item['id']


def test_extracts_supplied_facts_and_asks_only_missing(backend):
    _,client=backend
    item=client.post('/sessions',json={'message':'Private storage on AWS us-east-1 named myapp with budget $10'}).json()
    assert item['spec']['cloud']=='aws' and item['spec']['region']=='us-east-1'
    assert item['spec']['name']=='myapp' and item['spec']['budget']==10
    assert item['missing']==[] and item['proposal']


def test_questions_during_gathering_are_answers_not_validation_failures(backend):
    _,client=backend
    item=client.post('/sessions',json={'message':'A private storage bucket'}).json()
    endpoint='/sessions/'+item['id']
    response=client.post(endpoint+'/messages',json={'message':'Why should I use AWS rather than GCP?'})
    assert response.status_code==200
    assert response.json()['spec']['cloud'] is None
    assert 'Cloud Run' in response.json()['messages'][-1]['content']
    assert response.json()['proposal'] is None


def test_natural_language_project_id_and_invalid_identifiers(backend):
    _,client=backend
    item=client.post('/sessions',json={'message':'GCP private bucket us-central1 named myapp budget $10. My project ID is actual-project-123'}).json()
    assert item['spec']['project_id']=='actual-project-123'
    assert 'actual-project-123' in item['proposal']['code']
    assert client.put('/sessions/'+item['id']+'/requirements',json={'updates':{'subscription_id':'11111111-111111111111111111111111111'}}).status_code==422
    assert client.put('/sessions/'+item['id']+'/requirements',json={'updates':{'region':'not-a-real-region'}}).status_code==422


def test_revisions_preserve_manual_edits_and_reject_stale_clients(backend):
    _,client=backend;item=draft(client);endpoint='/sessions/'+item['id']
    edited=item['code']+'\n# manually kept\n'
    saved=client.put(endpoint+'/code',json={'code':edited,'revision':1}).json()
    assert saved['revision']==2
    response=client.post(endpoint+'/messages',json={'message':'Explain this Terraform','revision':2}).json()
    assert response['code']==edited and response['revision']==2 and response['proposal'] is None
    assert client.put(endpoint+'/code',json={'code':item['code'],'revision':1}).status_code==409
    restored=client.post(endpoint+'/revisions/1/restore',json={'confirm':True,'revision':2}).json()
    assert restored['revision']==3 and restored['code']==item['code']
    assert client.get(endpoint+'/revisions/2').json()['code']==edited


def test_changes_are_proposed_and_require_current_proposal_id(backend):
    _,client=backend;item=draft(client);endpoint='/sessions/'+item['id']
    next_item=client.post(endpoint+'/messages',json={'message':'Disable versioning'}).json()
    assert next_item['code']==item['code'] and next_item['proposal']
    assert 'Suspended' in next_item['proposal']['code']
    assert client.post(endpoint+'/proposal',json={'confirm':True,'proposal_id':'stale'}).status_code==409
    accepted=client.post(endpoint+'/proposal',json={'confirm':True,'proposal_id':next_item['proposal']['id'],'revision':1}).json()
    assert accepted['revision']==2 and accepted['code']!=item['code']


@pytest.mark.parametrize('function',['file','filebase64','filemd5','filesha1','filesha256','filesha512','filebase64sha256','filebase64sha512','fileset','fileexists','templatefile','pathexpand'])
def test_local_file_functions_rejected(backend,function):
    _,client=backend;item=draft(client)
    assert client.put('/sessions/'+item['id']+'/code',json={'code':item['code']+f'\noutput "stolen" {{ value = {function}("/private/file") }}'}).status_code==422


def test_secrets_never_stored(backend):
    _,client=backend
    assert client.post('/sessions',json={'message':'storage password=not-for-chat'}).status_code==422
    assert client.get('/sessions').json()==[]


@pytest.mark.parametrize('content',['[]','null','{','{"action":"answer","reply":123}','{"action":"answer","reply":"x","updates":{"password":"x"}}'])
def test_invalid_ai_responses_are_handled(backend,content,monkeypatch):
    api,client=backend;item=draft(client);monkeypatch.setattr(api,'MODE','live');monkeypatch.setenv('OPENAI_API_KEY','fake-test-key')
    reply=httpx.Response(200,json={'choices':[{'message':{'content':content}}]},request=httpx.Request('POST','https://example.invalid'))
    with patch('assistant.request_model',return_value=reply):
        response=client.post('/sessions/'+item['id']+'/messages',json={'message':'Explain my setup'})
    assert response.status_code==200 and response.json()['error']
    assert response.json()['code']==item['code']


def test_live_context_includes_current_code_and_explanations_keep_approval(backend,monkeypatch):
    api,client=backend;item=draft(client);endpoint='/sessions/'+item['id']
    edited=item['code']+'\n# preserved manual decision\n'
    item=client.put(endpoint+'/code',json={'code':edited}).json()
    stored=api.get(item['id']);stored.update(status='planned',approval='approval',plan_created=time.time(),plan={'deletes':0});api.save(stored)
    monkeypatch.setattr(api,'MODE','live');monkeypatch.setenv('OPENAI_API_KEY','fake-test-key')
    response=httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'action':'answer','reply':'Your bucket is private.','updates':{},'terraform':''})}}]},request=httpx.Request('POST','https://example.invalid'))
    with patch('assistant.request_model',return_value=response) as call:
        result=client.post(endpoint+'/messages',json={'message':'Why is this private?'}).json()
    context=json.loads(call.call_args.kwargs['payload']['messages'][1]['content'])
    assert context['current_terraform']==edited
    assert result['approval']=='approval' and result['status']=='planned' and result['code']==edited


def test_questions_after_deployment_and_recovery(backend):
    api,client=backend;item=draft(client);stored=api.get(item['id']);stored.update(status='apply_failed',recovery_required=True,has_infrastructure=True);api.save(stored)
    endpoint='/sessions/'+item['id']
    assert client.post(endpoint+'/messages',json={'message':'What was deployed?'}).status_code==200
    assert client.put(endpoint+'/code',json={'code':item['code']}).status_code==409
    assert client.post(endpoint+'/recover',json={'confirm':False}).status_code==409
    assert client.post(endpoint+'/recover',json={'confirm':True}).json()['recovery_required'] is False


def test_async_plan_apply_updates_cleanup_and_single_use_approval(backend,monkeypatch):
    api,client=backend;item=draft(client);endpoint='/sessions/'+item['id'];monkeypatch.setattr(api,'MODE','live');monkeypatch.setenv('MAKECLOUD_ENABLE_APPLY','true')
    plan={'changes':[{'address':'aws_s3_bucket.storage','actions':['create'],'attributes':[]}],'creates':1,'updates':0,'deletes':0,'digest':'a','plan_digest':'b','purpose':'deploy'}
    def make_plan(directory,*args,**kwargs):
        directory.mkdir(parents=True,exist_ok=True);(directory/'approved.tfplan').write_bytes(b'test-plan')
        return {**plan,'purpose':'destroy' if kwargs['destroy'] else 'deploy','deletes':1 if kwargs['destroy'] else 0}
    with patch('app.run_cloud.plan',side_effect=make_plan),patch('app.run_cloud.apply',return_value={'bucket_name':{'value':'test-bucket','sensitive':False}}):
        assert client.post(endpoint+'/plan',json={}).status_code==202
        planned=wait(client,item['id']);assert planned['status']=='planned'
        token=planned['approval']
        assert client.post(endpoint+'/apply',json={'approval':token,'confirm':False}).status_code==422
        assert client.post(endpoint+'/apply',json={'approval':token,'confirm':True}).status_code==202
        applied=wait(client,item['id']);assert applied['status']=='applied' and applied['outputs']['bucket_name']['value']=='test-bucket'
        assert not (api.ROOT/item['id']/'approved.tfplan').exists()
        assert (api.ROOT/item['id']/'approved.tfplan.encrypted').exists()
        assert client.post(endpoint+'/apply',json={'approval':token,'confirm':True}).status_code==409
        assert client.post(endpoint+'/messages',json={'message':'What did we deploy?'}).json()['status']=='applied'
        assert client.post(endpoint+'/plan',json={'purpose':'destroy'}).status_code==202
        cleanup=wait(client,item['id'])
        assert client.post(endpoint+'/apply',json={'approval':cleanup['approval'],'confirm':True}).status_code==422
        assert client.post(endpoint+'/apply',json={'approval':cleanup['approval'],'confirm':True,'confirm_destructive':True}).status_code==202
        assert wait(client,item['id'])['status']=='destroyed'


def test_duplicate_operations_cancel_and_failure_recovery(backend,monkeypatch):
    api,client=backend;item=draft(client);endpoint='/sessions/'+item['id'];monkeypatch.setattr(api,'MODE','live')
    started=Event()
    def slow(*args,**kwargs):
        started.set();kwargs['cancelled'].wait(3)
        raise run_cloud.Cancelled('Canceled')
    with patch('app.run_cloud.plan',side_effect=slow):
        client.post(endpoint+'/plan',json={});assert started.wait(2)
        assert client.post(endpoint+'/plan',json={}).status_code==409
        assert client.put(endpoint+'/code',json={'code':item['code']}).status_code==409
        assert client.post(endpoint+'/messages',json={'message':'What is the current status?'}).status_code==200
        assert client.post(endpoint+'/cancel').status_code==200
        result=wait(client,item['id']);assert result['job']['status']=='canceled' and result['status']=='draft'


def test_expired_approval_and_edits_invalidate(backend,monkeypatch):
    api,client=backend;item=draft(client);monkeypatch.setattr(api,'MODE','live');monkeypatch.setenv('MAKECLOUD_ENABLE_APPLY','true')
    stored=api.get(item['id']);stored.update(status='planned',approval='reviewed',plan={'deletes':0},plan_created=time.time()-901);api.save(stored)
    endpoint='/sessions/'+item['id']
    assert client.post(endpoint+'/apply',json={'approval':'reviewed','confirm':True}).status_code==409
    result=client.put(endpoint+'/code',json={'code':item['code']+'\n# new'}).json()
    assert result['approval'] is None and result['plan'] is None


def test_redaction_and_risk_summary():
    details={'resource_changes':[{'address':'aws_db_instance.db','type':'aws_db_instance','change':{'actions':['delete','create'],'before':{'password':'never-show','instance_class':'db.small'},'after':{'password':'also-secret','instance_class':'db.large','publicly_accessible':True},'before_sensitive':{'password':True},'after_sensitive':{'password':True}}}]}
    summary=run_cloud.summarize(details)
    assert 'never-show' not in json.dumps(summary) and 'also-secret' not in json.dumps(summary)
    assert summary['deletes']==1 and summary['creates']==1
    assert any('public' in risk for risk in summary['risks'])
    assert any(attr['name']=='instance_class' for attr in summary['changes'][0]['attributes'])


def test_saved_plan_tampering_cannot_apply(tmp_path):
    code=build(Spec(cloud='aws',workload='storage',name='myapp',region='us-east-1',budget=10).model_dump())[0]
    saved=tmp_path/'approved.tfplan';saved.write_bytes(b'approved');(tmp_path/'main.tf').write_text(code)
    approved={'digest':run_cloud.digest(code),'plan_digest':run_cloud.file_digest(saved),'configuration_digest':run_cloud.file_digest(tmp_path/'main.tf')}
    saved.write_bytes(b'tampered')
    with patch('run_cloud.command') as call:
        with pytest.raises(ValueError):run_cloud.apply(tmp_path,code,'aws',approved)
        call.assert_not_called()


def test_archive_is_reversible_and_export_omits_identity(backend):
    _,client=backend;item=draft(client);endpoint='/sessions/'+item['id']
    assert client.put(endpoint+'/archive',json={'archived':True}).json()['archived']
    assert client.get('/sessions').json()==[]
    assert client.get('/sessions?archived=true').json()[0]['id']==item['id']
    assert client.put(endpoint+'/archive',json={'archived':False}).json()['archived'] is False
    exported=client.get(endpoint+'/export').json()
    assert exported['code']==item['code'] and 'approval' not in exported and 'owner' not in exported


def test_accounts_cannot_read_or_mutate_other_projects(backend,monkeypatch):
    api,_=backend;monkeypatch.setattr(api,'AUTH_MODE','accounts');monkeypatch.setenv('MAKECLOUD_INVITE_CODE','test-invite');monkeypatch.setenv('MAKECLOUD_SECURE_COOKIES','false')
    first,second=TestClient(api.app),TestClient(api.app)
    for client,address in [(first,'one@example.com'),(second,'two@example.com')]:
        assert client.post('/auth/register',json={'email':address,'password':'long-test-password','invite':'test-invite'}).status_code==200
        assert client.post('/auth/login',json={'email':address,'password':'long-test-password'}).status_code==200
    item=draft(first);endpoint='/sessions/'+item['id']
    assert second.get('/sessions').json()==[]
    for method,path,data in [('GET',endpoint,None),('PUT',endpoint+'/code',{'code':item['code']}),('GET',endpoint+'/export',None),('POST',endpoint+'/messages',{'message':'Explain'})]:
        assert second.request(method,path,json=data).status_code==404
    assert first.get('/auth/me').status_code==200
    assert first.post('/auth/logout').status_code==200
    assert first.get(endpoint).status_code==401


def test_accounts_fail_closed_without_execution_isolation(backend,monkeypatch):
    api,client=backend;item=draft(client);monkeypatch.setattr(api,'MODE','live');monkeypatch.setattr(api,'AUTH_MODE','accounts')
    account={'id':'operator','email':'test@example.com'}
    api.app.dependency_overrides[api.user]=lambda:account
    assert client.post('/sessions/'+item['id']+'/plan',json={}).status_code==403


def test_backend_token_and_actual_body_limit(backend,monkeypatch):
    api,client=backend;monkeypatch.setattr(api,'TOKEN','test-token')
    assert client.get('/health').status_code==401
    assert client.get('/health',headers={'Authorization':'Bearer test-token'}).status_code==200
    assert client.post('/sessions',content='x'*160001,headers={'Authorization':'Bearer test-token','Content-Length':'1'}).status_code==413


def test_startup_lock_precedes_recovery_and_reseals_interrupted_state(backend):
    import asyncio
    import fcntl
    api,client=backend
    item=draft(client)
    stored=api.get(item['id']);stored.update(status='applying',approval='old',plan={'deletes':0},job={'id':'interrupted','status':'running'})
    api.save(stored)
    directory=api.ROOT/item['id'];directory.mkdir()
    (directory/'terraform.tfstate').write_text('sensitive-state')
    held=(api.ROOT/'server.lock').open('a');fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
    async def denied():
        with pytest.raises(RuntimeError,match='Only one API process'):
            async with api.lifespan(api.app): pass
    asyncio.run(denied())
    assert api.STORE.get(item['id'])['status']=='applying'
    assert (directory/'terraform.tfstate').exists()
    fcntl.flock(held,fcntl.LOCK_UN);held.close()
    async def recovered():
        async with api.lifespan(api.app):
            restored=api.STORE.get(item['id'])
            assert restored['status']=='interrupted' and restored['recovery_required']
            assert restored['approval'] is None and restored['plan'] is None
            assert not (directory/'terraform.tfstate').exists()
            assert api.STORE.cipher.decrypt((directory/'terraform.tfstate.encrypted').read_bytes())==b'sensitive-state'
    asyncio.run(recovered())


@pytest.mark.parametrize('monthly',['NaN','Infinity','-1',None])
def test_invalid_pricing_is_unavailable_and_private_files_removed(tmp_path,monthly):
    def pricing(*args,**kwargs):
        (tmp_path/'pricing-output.private.json').write_text(json.dumps({'totalMonthlyCost':monthly}))
        return type('Result',(),{'returncode':0})()
    with patch('run_cloud.shutil.which',return_value='/fake/infracost'),patch.dict('os.environ',{'INFRACOST_API_KEY':'fixture-pricing-key'}),patch('run_cloud.subprocess.run',side_effect=pricing):
        assert run_cloud.estimate(tmp_path,{})['status']=='unavailable'
    assert not list(tmp_path.glob('pricing-*.private.json'))


def test_budget_exceeded_requires_separate_acknowledgment(backend,monkeypatch):
    api,client=backend;item=draft(client);monkeypatch.setattr(api,'MODE','live');monkeypatch.setenv('MAKECLOUD_ENABLE_APPLY','true')
    stored=api.get(item['id']);stored.update(status='planned',approval='budget-review',plan={'deletes':0,'cost':{'monthly':101}},plan_created=time.time());api.save(stored)
    endpoint='/sessions/'+item['id']
    assert client.post(endpoint+'/apply',json={'approval':'budget-review','confirm':True}).status_code==422
    assert api.get(item['id'])['approval']=='budget-review'
    with patch('app.run_cloud.apply',return_value={}):
        assert client.post(endpoint+'/apply',json={'approval':'budget-review','confirm':True,'confirm_budget':True}).status_code==202
        assert wait(client,item['id'])['status']=='applied'


def test_code_target_cannot_drift_from_requirements(backend):
    _,client=backend;item=draft(client);endpoint='/sessions/'+item['id']
    response=client.put(endpoint+'/code',json={'code':item['code'].replace('us-east-1','eu-west-1'),'revision':item['revision']})
    assert response.status_code==422
    assert client.get(endpoint).json()['code']==item['code']


def test_scoped_connection_account_and_region_guards(backend,tmp_path,monkeypatch):
    api,client=backend;item=draft(client)
    path=tmp_path/'connections.private.json'
    row={'id':'aws-one','owner':'operator','cloud':'aws','label':'Test account','account_id':'111111111111','regions':['eu-west-1'],'environment':{'AWS_ACCESS_KEY_ID':'fixture-key'}}
    path.write_text(json.dumps([row]));monkeypatch.setenv('MAKECLOUD_CONNECTIONS_FILE',str(path))
    assert 'environment' not in client.get('/connections').text and 'fixture-key' not in client.get('/connections').text
    assert client.put('/sessions/'+item['id']+'/connection',json={'connection_id':'aws-one'}).status_code==200
    with pytest.raises(Exception,match='region is not authorized'):api.identity(api.get(item['id']),{'id':'operator','email':'operator'})
    row['regions']=['us-east-1'];row['account_id']='invalid';path.write_text(json.dumps([row]))
    with pytest.raises(Exception,match='expected account ID'):api.identity(api.get(item['id']),{'id':'operator','email':'operator'})
    row['account_id']='111111111111';path.write_text(json.dumps([row]))
    environment,target=api.identity(api.get(item['id']),{'id':'operator','email':'operator'})
    assert environment['AWS_ACCESS_KEY_ID']=='fixture-key' and target['account_id']=='111111111111'
    guarded=run_cloud.account_guard(item['code'],'aws',target['account_id'])
    import hcl2
    assert hcl2.loads(guarded)['provider'][0]['aws']['allowed_account_ids']==['111111111111']
    row['environment']['OPENAI_API_KEY']='forbidden';path.write_text(json.dumps([row]))
    with pytest.raises(Exception,match='environment is invalid'):api.identity(api.get(item['id']),{'id':'operator','email':'operator'})


def test_real_subprocess_cancellation_terminates_runner(tmp_path,monkeypatch):
    import threading
    executable=tmp_path/'terraform'
    executable.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(60)\n');executable.chmod(0o700)
    monkeypatch.setenv('PATH',str(tmp_path));monkeypatch.setenv('MAKECLOUD_RUNNER','local')
    canceled=Event();timer=threading.Timer(.3,canceled.set);timer.start()
    started=time.monotonic()
    try:
        with pytest.raises(run_cloud.Cancelled):run_cloud.command(tmp_path,'plan',cancelled=canceled)
    finally:timer.cancel()
    assert time.monotonic()-started<5


@pytest.mark.parametrize('text',['Can you provision a private bucket?','Could you create a container app?','Would you please generate Terraform?'])
def test_polite_change_requests_are_not_readonly_questions(text):
    from domain import is_question
    assert not is_question(text)


def test_modern_pricing_is_scoped_ephemeral_and_does_not_inherit_cloud_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('INFRACOST_CLI_AUTHENTICATION_TOKEN','pricing-test-token')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','cloud-test-secret')
    def execute(argv,**kwargs):
        assert kwargs['env']['INFRACOST_CLI_AUTHENTICATION_TOKEN']=='pricing-test-token'
        assert 'AWS_SECRET_ACCESS_KEY' not in kwargs['env']
        assert Path(kwargs['env']['HOME']).is_relative_to(tmp_path)
        if argv[1]=='scan':
            assert '--currency' in argv and 'USD' in argv
            assert Path(argv[2]).exists()
            return type('Result',(),{'returncode':0,'stdout':''})()
        assert argv[1]=='inspect'
        return type('Result',(),{'returncode':0,'stdout':json.dumps({'monthly_cost':12.34})})()
    with patch('run_cloud.shutil.which',return_value='/fake/infracost'),patch('run_cloud.subprocess.run',side_effect=execute):
        estimate=run_cloud.estimate(tmp_path,{'planned_values':{}})
        assert estimate['status']=='estimated' and estimate['monthly']==12.34
    assert list(tmp_path.iterdir())==[]


def test_legacy_project_migration_removes_plaintext_pages(backend):
    api,client=backend;item=draft(client)
    stored=api.get(item['id']);stored['requirements']='legacy-plaintext-marker'
    with api.STORE.connect() as db:
        db.execute('UPDATE sessions SET data=? WHERE id=?',(json.dumps(stored),item['id']))
    assert api.STORE.has_plaintext_projects()
    api.recover_startup()
    assert not api.STORE.has_plaintext_projects()
    assert api.STORE.get(item['id'])['requirements']=='legacy-plaintext-marker'
    for path in api.ROOT.glob('sessions.db*'):
        assert b'legacy-plaintext-marker' not in path.read_bytes()


def test_container_asks_actual_listening_port_instead_of_assuming(backend):
    _,client=backend
    item=client.post('/sessions',json={'message':'Public container app on AWS us-east-1 named myapp budget $100 image nginx:stable without a database'}).json()
    assert item['missing']==['port'] and item['proposal'] is None
    assert 'nginx uses 80' in item['messages'][-1]['content']
    answered=client.post('/sessions/'+item['id']+'/messages',json={'message':'80'}).json()
    assert answered['spec']['port']==80 and answered['proposal']


@pytest.mark.parametrize('image',['https://registry.example.com/image','user:password@registry.example.com/image','image@sha256:incomplete'])
def test_container_image_rejects_credentials_and_schemes(image):
    with pytest.raises(ValueError):Spec(image=image)


def test_local_cost_answer_uses_the_measured_plan(backend):
    api,client=backend;item=draft(client)
    stored=api.get(item['id']);stored['plan']={'cost':{'status':'estimated','monthly':12.34,'currency':'USD'}};api.save(stored)
    response=client.post('/sessions/'+item['id']+'/messages',json={'message':'What will this cost?'}).json()
    assert '12.34' in response['messages'][-1]['content']
    assert response['plan']==stored['plan']


def test_https_requires_matching_region_and_domain_and_outputs_real_hostname(backend):
    from domain import missing
    spec=Spec(cloud='aws',workload='container',region='us-east-1',name='myapp',budget=100,public=True,image='nginx:stable',port=80,database=False,environment='production',certificate_arn='arn:aws:acm:us-east-1:111111111111:certificate/11111111-1111-1111-1111-111111111111').model_dump()
    assert missing(spec)==['domain_name']
    with pytest.raises(ValueError,match='selected AWS region'):validate_spec({**spec,'region':'eu-west-1'})
    code,summary=build({**spec,'domain_name':'app.example.com'})
    assert 'https://app.example.com' in code and 'load_balancer_dns_name' in code
    assert 'DNS provider' in summary


def test_polite_explanations_remain_available_during_recovery(backend):
    api,client=backend;item=draft(client);stored=api.get(item['id']);stored.update(status='apply_failed',recovery_required=True);api.save(stored)
    response=client.post('/sessions/'+item['id']+'/messages',json={'message':'Could you please explain what happened'})
    assert response.status_code==200 and response.json()['error'] is None
    assert response.json()['recovery_required']


@pytest.mark.parametrize('status',['applied','apply_failed'])
def test_legacy_deployment_migration_preserves_recovery_and_cleanup(backend,status):
    api,_=backend
    legacy={'id':'legacy','status':status,'provider':'aws','answers':['AWS','us-east-1','oldproject'],'code':'old-configuration','updated_at':1}
    migrated=api.migrate(legacy)
    assert migrated['has_infrastructure'] and migrated['recovery_required']==(status=='apply_failed')
    assert migrated['plan'] is None and migrated['approval'] is None
    assert migrated['revisions'][0]['code']=='old-configuration'


def test_deployed_target_cannot_change_through_requirements_chat_or_restore(backend):
    api,client=backend;item=draft(client);endpoint='/sessions/'+item['id']
    stored=api.get(item['id']);stored.update(status='applied',has_infrastructure=True)
    prior={**stored['revisions'][0],'revision':99,'spec':{**stored['spec'],'region':'eu-west-1'}}
    stored['revisions'].append(prior);api.save(stored)
    assert client.put(endpoint+'/requirements',json={'updates':{'region':'eu-west-1'}}).status_code==422
    result=client.post(endpoint+'/messages',json={'message':'Move to eu-west-1'}).json()
    assert result['spec']['region']=='us-east-1' and 'retain its cloud, region' in result['error']
    assert client.post(endpoint+'/revisions/99/restore',json={'confirm':True}).status_code==409
    assert client.get(endpoint).json()['code']==item['code']


@pytest.mark.parametrize('text',['{"client_secret":"fixture-sensitive"}','{"AWS_SECRET_ACCESS_KEY":"fixture-sensitive"}','password = fixture-sensitive'])
def test_credential_fields_rejected_before_storage_or_ai(backend,text):
    api,client=backend
    with patch('assistant.request_model') as request:
        assert client.post('/sessions',json={'message':text}).status_code==422
        request.assert_not_called()
    assert api.STORE.all()==[]
