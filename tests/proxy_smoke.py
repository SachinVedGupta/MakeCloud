"""Production frontend + API + controlled AI/CLI fixtures over real HTTP.
These checks never use real AI/cloud credentials or provision cloud resources.
Real provider schema checks are separate in validate_blueprints.py.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from cryptography.fernet import Fernet
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints import build
from domain import is_question

ROOT=Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1',0))
        return connection.getsockname()[1]

class FakeAI(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        context=json.loads(payload['messages'][1]['content'])
        text=context['message']
        if 'invalid ai response' in text.lower():
            result=[]
        elif is_question(text):
            result={'action':'answer','reply':'Your accepted code is unchanged. Private storage blocks public access.','updates':{},'terraform':''}
        elif context['missing_fields']:
            result={'action':'clarify','reply':'Please supply: '+context['missing_fields'][0],'updates':{},'terraform':''}
        else:
            code,summary=build(context['requirements'])
            if context['current_terraform']:
                code=context['current_terraform']+'\n# controlled AI revision preserves the current configuration\n'
            result={'action':'propose','reply':summary,'updates':{},'terraform':code}
        body=json.dumps({'choices':[{'message':{'content':json.dumps(result)}}]}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

FAKE_TERRAFORM=r'''import json,sys,pathlib,time
import hcl2
root=pathlib.Path('.')
command=sys.argv[1]
if command=='validate':
    hcl2.loads((root/'main.tf').read_text())
elif command=='plan':
    config=hcl2.loads((root/'main.tf').read_text())
    destroyed='-destroy' in sys.argv
    (root/'approved.tfplan').write_text(json.dumps({'configuration':config,'destroy':destroyed}))
elif command=='show':
    saved=json.loads((root/'approved.tfplan').read_text())
    exists=(root/'terraform.tfstate').exists()
    changes=[]
    for entry in saved['configuration']['resource']:
        for kind,items in entry.items():
            for name in items:
                changes.append({'address':kind+'.'+name,'type':kind,'change':{'actions':['delete'] if saved['destroy'] else ['update'] if exists else ['create'],'before':{'name':'old'} if exists else None,'after':None if saved['destroy'] else {'name':'created'},'before_sensitive':{},'after_sensitive':{}}})
    print(json.dumps({'resource_changes':changes}))
elif command=='apply':
    saved=json.loads((root/'approved.tfplan').read_text())
    if saved['destroy']:
        (root/'terraform.tfstate').write_text(json.dumps({'resources':[],'outputs':{}}))
    else:
        (root/'terraform.tfstate').write_text(json.dumps({'resources':[{'fixture':True}],'outputs':{'bucket_name':{'value':'controlled-test-bucket','sensitive':False},'database_password':{'value':'must-never-reach-browser','sensitive':True}}}))
elif command=='output':
    print(json.dumps(json.loads((root/'terraform.tfstate').read_text())['outputs']))
'''

with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryFile(mode='w+') as logs:
    directory=Path(temporary);binary=directory/'bin';binary.mkdir()
    script=binary/'terraform';script.write_text('#!'+sys.executable+'\n'+FAKE_TERRAFORM);script.chmod(0o755)
    backend_port,frontend_port=free_port(),free_port()
    ai=ThreadingHTTPServer(('127.0.0.1',0),FakeAI)
    threading.Thread(target=ai.serve_forever,daemon=True).start()
    data=directory/'data'
    environment={**os.environ,'PATH':str(binary)+os.pathsep+os.environ['PATH'],'MAKECLOUD_MODE':'demo','MAKECLOUD_AUTH_MODE':'operator','MAKECLOUD_RUNNER':'local','MAKECLOUD_DATA_DIR':str(data),'MAKECLOUD_BACKEND_URL':f'http://127.0.0.1:{backend_port}','MAKECLOUD_API_TOKEN':'smoke-installation-token','MAKECLOUD_OPERATOR_PASSWORD':'','MAKECLOUD_LOCAL_ONLY':'true','OPENAI_API_KEY':'','INFRACOST_API_KEY':'','MAKECLOUD_ENABLE_APPLY':'false','MAKECLOUD_CONNECTIONS_FILE':''}
    processes=[]
    def launch_backend(module='app:app'):
        process=subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(backend_port),'--no-access-log'],cwd=ROOT,env=environment,stdout=logs,stderr=logs);processes.append(process);return process
    def launch_frontend():
        process=subprocess.Popen(['node','scripts/start-web.mjs'],cwd=ROOT,env={**environment,'PORT':str(frontend_port)},stdout=logs,stderr=logs);processes.append(process);return process
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    base=f'http://127.0.0.1:{frontend_port}'
    with httpx.Client(timeout=30,trust_env=False) as client:
        def ready(path='/api/cloud/health',status=200,auth=None):
            for _ in range(150):
                try:
                    if client.get(base+path,auth=auth).status_code==status:return
                except httpx.HTTPError:pass
                time.sleep(.1)
            logs.seek(0);raise AssertionError('Servers failed to start: '+logs.read())
        def request(path,method='GET',data=None,expected=200):
            response=client.request(method,base+'/api/cloud/'+path,json=data)
            assert response.status_code==expected,(response.status_code,response.text,path)
            return response.json()
        def settle(sid):
            for _ in range(200):
                item=request('sessions/'+sid)
                if item['status'] not in ('planning','applying'):return item
                time.sleep(.05)
            raise AssertionError('Job did not complete')
        try:
            backend=launch_backend();frontend=launch_frontend();ready()
            assert client.get(base).status_code==200
            for cloud,extra,region in [('AWS','','us-east-1'),('GCP','project ID is my-project-123','us-central1'),('Azure','subscription 11111111-1111-1111-1111-111111111111','eastus')]:
                item=request('sessions','POST',{'message':f'Private storage on {cloud} {region} named smokeapp budget $10 {extra}'})
                assert item['proposal'] and item['missing']==[]
                endpoint='sessions/'+item['id']
                item=request(endpoint+'/proposal','POST',{'confirm':True,'proposal_id':item['proposal']['id'],'revision':0})
                assert item['status']=='draft'
                edited=item['code']+'\n# manual edit kept after reload\n'
                item=request(endpoint+'/code','PUT',{'code':edited,'revision':1})
                explained=request(endpoint+'/messages','POST',{'message':'Why is this private?'})
                assert explained['code']==edited and explained['revision']==2
                assert request(endpoint)['code']==edited
                request(endpoint+'/plan','POST',{},409)
                exported=request(endpoint+'/export');assert exported['code']==edited
                request(endpoint+'/archive','PUT',{'archived':True})
                assert any(row['id']==item['id'] for row in request('sessions?archived=true'))
                request(endpoint+'/archive','PUT',{'archived':False})
                print(cloud+': real HTTP requirements -> proposal -> acceptance -> edit -> question -> reload -> export -> archive PASS',flush=True)
            assert client.post(base+'/api/cloud/sessions',headers={'Origin':'https://attacker.example'},json={'message':'storage'}).status_code==403
            assert client.post(base+'/api/cloud/sessions',headers={'Origin':'not-a-url'},json={'message':'storage'}).status_code==403
            assert client.get(base+'/api/cloud/not-allowed').status_code==404
            assert client.get(base,headers={'Host':'untrusted.example'}).status_code==401
            assert client.get(f'http://127.0.0.1:{backend_port}/sessions').status_code==401
            stop(backend);assert client.get(base+'/api/cloud/health').status_code==503
            environment.update(MAKECLOUD_MODE='live',OPENAI_API_KEY='controlled-smoke-key',OPENAI_BASE_URL=f'http://127.0.0.1:{ai.server_port}/v1',MAKECLOUD_ENABLE_APPLY='true')
            backend=launch_backend();ready()
            item=request('sessions','POST',{'message':'Private storage on AWS us-east-1 named liveapp budget $10'})
            endpoint='sessions/'+item['id']
            item=request(endpoint+'/proposal','POST',{'confirm':True,'proposal_id':item['proposal']['id'],'revision':0})
            item=request(endpoint+'/code','PUT',{'code':item['code']+'\n# operator manual edit\n','revision':1})
            proposal=request(endpoint+'/messages','POST',{'message':'Add a documented revision'})
            assert '# operator manual edit' in proposal['proposal']['code']
            item=request(endpoint+'/proposal','POST',{'confirm':True,'proposal_id':proposal['proposal']['id'],'revision':2})
            request(endpoint+'/plan','POST',{'revision':3},202)
            planned=settle(item['id']);assert planned['status']=='planned' and planned['approval']
            token=planned['approval']
            answer=request(endpoint+'/messages','POST',{'message':'Explain the planned infrastructure?'})
            assert answer['approval']==token and answer['code']==item['code']
            invalid=request(endpoint+'/messages','POST',{'message':'Explain an invalid AI response?'})
            assert invalid['error'] and invalid['approval']==token and invalid['code']==item['code']
            request(endpoint+'/apply','POST',{'approval':token,'confirm':True,'revision':3},202)
            applied=settle(item['id']);assert applied['status']=='applied'
            assert applied['outputs']['bucket_name']['value']=='controlled-test-bucket'
            assert 'must-never-reach-browser' not in json.dumps(applied)
            assert (data/item['id']/'terraform.tfstate.encrypted').exists()
            assert not (data/item['id']/'terraform.tfstate').exists()
            request(endpoint+'/apply','POST',{'approval':token,'confirm':True},409)
            after=request(endpoint+'/messages','POST',{'message':'What did we deploy?'})
            assert after['status']=='applied' and after['code']==item['code']
            request(endpoint+'/plan','POST',{'purpose':'destroy'},202)
            cleanup=settle(item['id'])
            request(endpoint+'/apply','POST',{'approval':cleanup['approval'],'confirm':True},422)
            request(endpoint+'/apply','POST',{'approval':cleanup['approval'],'confirm':True,'confirm_destructive':True},202)
            assert settle(item['id'])['status']=='destroyed'
            print('Controlled AI + Terraform fixtures: preserved edits -> plan -> explanation -> malformed AI -> exact approval -> apply -> redaction -> encrypted state -> discussion -> reviewed cleanup PASS',flush=True)
            stop(frontend)
            environment['MAKECLOUD_OPERATOR_PASSWORD']='smoke-only-password'
            frontend=launch_frontend();ready('/',401)
            assert client.get(base,auth=('operator','wrong')).status_code==401
            assert client.get(base,auth=('operator','smoke-only-password')).status_code==200
            stop(frontend);environment['MAKECLOUD_OPERATOR_PASSWORD']='';environment['MAKECLOUD_LOCAL_ONLY']='false'
            frontend=launch_frontend();ready('/',401)
            assert client.get(base,headers={'Host':'localhost'}).status_code==401
            print('Origin rejection, installation token, backend downtime and remote fail-closed hosting PASS',flush=True)
            stop(frontend);stop(backend)
            environment.update(MAKECLOUD_AUTH_MODE='accounts',MAKECLOUD_DATA_DIR=str(directory/'accounts'),MAKECLOUD_ENCRYPTION_KEY=Fernet.generate_key().decode(),MAKECLOUD_INVITE_CODE='smoke-invite',MAKECLOUD_SECURE_COOKIES='false')
            backend=launch_backend();frontend=launch_frontend();ready()
            request('sessions',expected=401)
            for address in ['one@example.com','two@example.com']:
                request('auth/register','POST',{'email':address,'password':'smoke-account-password','invite':'smoke-invite'})
            request('auth/login','POST',{'email':'one@example.com','password':'smoke-account-password'})
            owned=request('sessions','POST',{'message':'Private storage on AWS us-east-1 named ownedapp budget $10'})
            request('auth/logout','POST')
            request('auth/login','POST',{'email':'two@example.com','password':'smoke-account-password'})
            assert request('sessions')==[]
            request('sessions/'+owned['id'],expected=404)
            request('sessions/'+owned['id']+'/export',expected=404)
            request('sessions/'+owned['id']+'/messages','POST',{'message':'Explain'},404)
            request('auth/logout','POST');request('auth/me',expected=401)
            print('Real HTTP account registration, login cookies, logout, project ownership and cross-user access rejection PASS',flush=True)
            stop(frontend);stop(backend)
            # Fixture server replaces external provider calls only; all HTTP,
            # account cookies, nonce routing, persistence and proxy code are real.
            (directory/'hosted_fixture.py').write_text('''from app import app
import os,json
from pathlib import Path
import cloud_connections as cloud
import account_mail
from botocore.exceptions import ClientError
def provider(method,url,**kw):
    if 'oauth2.googleapis.com/token' in url:return {'refresh_token':'private-google-refresh','access_token':'fixture-google-access','scope':'https://www.googleapis.com/auth/cloud-platform'}
    if 'projects:search' in url:return {'projects':[{'projectId':'my-project-123','displayName':'Fixture project','state':'ACTIVE'}]}
    if ':testIamPermissions' in url:return {'permissions':['storage.buckets.create','run.services.create','serviceusage.services.enable']}
    if 'cloudresourcemanager' in url:return {'projectId':'my-project-123','state':'ACTIVE'}
    if 'login.microsoftonline.com' in url:return {'access_token':'fixture-azure-access'}
    if '/registrationAssignments/' in url:return {'properties':{'registrationDefinitionId':url.split('?')[0].replace('registrationAssignments','registrationDefinitions').replace('https://management.azure.com','')}}
    if 'management.azure.com/subscriptions/' in url:return {'subscriptionId':'11111111-1111-1111-1111-111111111111','state':'Enabled'}
    raise AssertionError('Unexpected provider endpoint')
def assume(row,external=True):
    if not external:raise ClientError({'Error':{'Code':'AccessDenied'}},'AssumeRole')
    return {'AccessKeyId':'fixture-temporary','SecretAccessKey':'fixture-secret','SessionToken':'fixture-session'}
def mail(address,token,kind):
    Path(os.environ['FIXTURE_MAIL_FILE']).write_text(json.dumps({'email':address,'token':token,'kind':kind}))
cloud.request_json=provider
cloud.assume=assume
account_mail.send=mail
''')
            environment.update(PYTHONPATH=str(directory)+os.pathsep+str(ROOT),MAKECLOUD_PUBLIC_URL=base,MAKECLOUD_REGISTRATION='public',MAKECLOUD_SMTP_HOST='fixture',MAKECLOUD_SMTP_USER='fixture',MAKECLOUD_SMTP_PASSWORD='fixture',MAKECLOUD_MAIL_FROM='makecloud@example.com',FIXTURE_MAIL_FILE=str(directory/'mail.json'),MAKECLOUD_GOOGLE_CLIENT_ID='fixture-client',MAKECLOUD_GOOGLE_CLIENT_SECRET='fixture-secret',MAKECLOUD_AWS_PRINCIPAL_ARN='arn:aws:iam::999999999999:role/MakeCloudBroker',MAKECLOUD_AWS_TEMPLATE_URL='https://bootstrap.s3.amazonaws.com/makecloud.json',MAKECLOUD_AZURE_TENANT_ID='11111111-1111-1111-1111-111111111111',MAKECLOUD_AZURE_CLIENT_ID='11111111-1111-1111-1111-111111111111',MAKECLOUD_AZURE_PRINCIPAL_ID='11111111-1111-1111-1111-111111111111',MAKECLOUD_AZURE_CLIENT_SECRET='fixture-secret')
            backend=launch_backend('hosted_fixture:app');frontend=launch_frontend();ready()
            registered=request('auth/register','POST',{'email':'public@example.com','password':'public-account-password'})
            assert registered['verification_required']
            token=json.loads((directory/'mail.json').read_text())['token']
            request('auth/login','POST',{'email':'public@example.com','password':'public-account-password'},401)
            request('auth/verify','POST',{'token':token});request('auth/verify','POST',{'token':token},400)
            request('auth/login','POST',{'email':'public@example.com','password':'public-account-password'})
            google=request('connections/begin','POST',{'cloud':'google','label':'My Google project'},201)
            from urllib.parse import parse_qs,urlparse
            state=parse_qs(urlparse(google['authorization_url']).query)['state'][0]
            nonce=client.cookies.get('makecloud_oauth');assert nonce
            callback=client.get(base+'/api/cloud/connections/google/callback',params={'state':state,'code':'controlled-code'})
            assert callback.status_code==303 and callback.headers['location']==base+'/#connected='+google['connection']['id']
            assert not client.cookies.get('makecloud_oauth')
            cid=google['connection']['id']
            assert request('connections/'+cid+'/projects')['projects'][0]['id']=='my-project-123'
            request('connections/'+cid+'/verify','POST',{'target':'my-project-123'})
            aws=request('connections/begin','POST',{'cloud':'aws','profile':'storage'},201)
            request('connections/'+aws['connection']['id']+'/verify','POST',{'target':'arn:aws:iam::123456789012:role/'+aws['role_name']})
            azure=request('connections/begin','POST',{'cloud':'azurerm'},201)
            from urllib.parse import unquote
            ticket=unquote(azure['console_url'].split('/uri/')[1]).split('/template/')[1]
            with httpx.Client(timeout=30) as anonymous:
                template=anonymous.get(base+'/api/cloud/connections/template/'+ticket)
                assert template.status_code==200 and template.json()['resources']
            request('connections/'+azure['connection']['id']+'/verify','POST',{'target':'11111111-1111-1111-1111-111111111111'})
            visible=request('connections');assert sum(row['status']=='connected' for row in visible)==3
            assert 'private-google-refresh' not in json.dumps(visible)
            request('auth/request-link','POST',{'email':'public@example.com','purpose':'reset'})
            token=json.loads((directory/'mail.json').read_text())['token']
            request('auth/reset','POST',{'token':token,'password':'replacement-password-123'})
            request('auth/me',expected=401)
            request('auth/login','POST',{'email':'public@example.com','password':'replacement-password-123'})
            print('Real HTTP hosted signup -> email verification -> Google browser callback/cookies/project picker -> AWS role -> Azure portal template -> three scoped connections -> password recovery PASS; cloud and mail services simulated',flush=True)
        finally:
            for process in reversed(processes):stop(process)
            ai.shutdown();ai.server_close()
print('HTTP integration complete. External AI and cloud services were simulated; browser rendering was not tested.')
