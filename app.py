"""Persistent MakeCloud API. One API process; background operations per project."""
import difflib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
import fcntl
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError
import assistant
from blueprints import CATALOG, build
from domain import Spec, extract, missing, question, validate_spec, is_question, explain
from store import Store
import run_cloud
import cloud_connections
import account_mail
import hcp_runner

os.umask(0o077)
ROOT = Path(os.getenv('MAKECLOUD_DATA_DIR', '.makecloud')).resolve()
MODE = os.getenv('MAKECLOUD_MODE', 'demo')
AUTH_MODE = os.getenv('MAKECLOUD_AUTH_MODE', 'operator')
if MODE not in ('demo', 'live') or AUTH_MODE not in ('operator', 'accounts'):
    raise RuntimeError('Invalid MakeCloud mode or authentication mode.')
if os.getenv('MAKECLOUD_EPHEMERAL_HOST') == 'true' and (not os.getenv('MAKECLOUD_DATABASE_URL') or not os.getenv('MAKECLOUD_ENCRYPTION_KEY') or not os.getenv('MAKECLOUD_API_TOKEN') or AUTH_MODE != 'accounts' or not hcp_runner.enabled()):
    raise RuntimeError('Ephemeral hosting requires remote database, stable encryption key, API token, account authentication, and HCP execution.')
TOKEN = os.getenv('MAKECLOUD_API_TOKEN', '')
STORE = Store(ROOT)
LOCK = threading.RLock()
PROJECT_LOCKS = {}
CANCELLATIONS = {}
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='makecloud')
RUNNING = {'planning', 'applying'}
RECOVERY = {'apply_failed', 'interrupted', 'canceled_apply'}


@contextmanager
def project_lock(sid):
    with LOCK:
        local = PROJECT_LOCKS.setdefault(sid, threading.RLock())
    with local:
        if STORE.database_url:
            with STORE.connect() as db:
                key = int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8], 'big', signed=True)
                db.execute('SELECT pg_advisory_xact_lock(?)', (key,))
                yield
        else:
            yield


def migrate(item):
    if 'spec' not in item:
        spec = Spec().model_dump()
        answers = item.get('answers', [])
        if len(answers) >= 3:
            spec.update(cloud=item.get('provider'), region=answers[1], name=answers[2], workload='storage', budget_unknown=True)
        if len(answers) >= 5:
            try:
                spec.update(extract(answers[4], spec, 'project_id' if spec['cloud'] == 'google' else 'subscription_id'))
            except ValueError:
                pass
        item.update(spec=spec, revision=1 if item.get('code') else 0, owner='operator', proposal=None, job=None, revisions=[], audit=[], outputs={}, archived=False, checks=[], plan=None, approval=None)
        if item['status'] == 'planned':
            item['status'] = 'draft'
        if item.get('code'):
            item['revisions'].append({'revision': 1, 'code': item['code'], 'spec': spec.copy(), 'summary': 'Imported accepted configuration from the previous release.', 'at': item.get('updated_at', time.time())})
    for key, value in {'proposal': None, 'job': None, 'revisions': [], 'audit': [], 'outputs': {}, 'archived': False, 'checks': [], 'connection_id': None, 'recovery_required': item['status'] in RECOVERY, 'has_infrastructure': item['status'] in {'applied', 'applying', 'apply_failed', 'interrupted', 'canceled_apply'}}.items():
        item.setdefault(key, value)
    item['missing'] = missing(item['spec'], MODE)
    return item


def recover_startup():
    if hcp_runner.enabled():
        # A replacement web instance must not overwrite a live instance's
        # checkpoint. Remote operations reconcile under project locks on reads.
        return
    legacy = STORE.has_plaintext_projects()
    for existing in STORE.all():
        migrate(existing)
        if existing['status'] in RUNNING:
            if hcp_runner.enabled() and existing.get('job', {}).get('remote'):
                # HCP continues independently. Reconcile on the next browser
                # request, without replaying an apply or keeping free hosts awake.
                STORE.save(existing)
                continue
            job = existing.get('job') or {}
            if job.get('status') == 'queued' and job.get('id') in STORE.queued():
                STORE.save(existing)
                continue
            existing.update(status='interrupted', approval=None, plan=None, recovery_required=True, error='Server restarted during an operation. Review retained state and acknowledge recovery before replanning.')
            if existing.get('job'):
                existing['job'].update(status='interrupted', finished_at=time.time())
                STORE.finish(existing['job']['id'], 'interrupted')
        run_cloud.seal(ROOT / existing['id'], STORE.cipher)
        STORE.save(existing)
    if legacy:
        STORE.compact_migrated_pages()


@asynccontextmanager
async def lifespan(app):
    lock_file = (ROOT / 'server.lock').open('a')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError('Only one API process may use this data directory.') from exc
    try:
        recover_startup()
        if not hcp_runner.enabled():
            for job_id in STORE.queued():
                CANCELLATIONS[job_id] = threading.Event()
                EXECUTOR.submit(dispatch, job_id)
        yield
    finally:
        for cancel in list(CANCELLATIONS.values()):
            cancel.set()
        EXECUTOR.shutdown(wait=True, cancel_futures=True)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


app = FastAPI(title='MakeCloud', docs_url=None, redoc_url=None, lifespan=lifespan)

class Message(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    revision: int | None = None

class Code(BaseModel):
    code: str = Field(min_length=1, max_length=100000)
    revision: int | None = None

class Confirm(BaseModel):
    confirm: bool = False
    revision: int | None = None
    proposal_id: str | None = None

class Approval(BaseModel):
    approval: str = Field(min_length=1, max_length=200)
    confirm: bool = False
    confirm_destructive: bool = False
    revision: int | None = None
    confirm_budget: bool = False

class Operation(BaseModel):
    purpose: str = 'deploy'
    revision: int | None = None

class Requirements(BaseModel):
    updates: dict
    revision: int | None = None

class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    invite: str = Field(default='', max_length=200)

class Connection(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)

class Archive(BaseModel):
    archived: bool

class BeginConnection(BaseModel):
    cloud: str
    label: str = Field(default='', max_length=100)
    profile: str = 'storage'

class VerifyConnection(BaseModel):
    target: str = Field(min_length=1, max_length=2000)

class MailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    purpose: str = 'reset'

class AccountToken(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str | None = Field(default=None, min_length=12, max_length=128)


def save(item):
    item['missing'] = missing(item['spec'], MODE)
    STORE.save(item)


def get(sid, owner=None):
    item = STORE.get(sid)
    if item is None or owner is not None and item.get('owner', 'operator') != owner:
        raise HTTPException(404, 'Project not found.')
    return migrate(item)


def event(item, action, detail=''):
    item['audit'].append({'id': str(uuid.uuid4()), 'action': action, 'detail': detail[:300], 'at': time.time()})
    item['audit'] = item['audit'][-500:]


def revision_check(item, expected):
    if expected is not None and expected != item['revision']:
        raise HTTPException(409, 'This project changed in another tab. Reload before changing it.')


def writable(item):
    if item['status'] in RUNNING:
        raise HTTPException(409, 'An operation is running. Questions are available; changes must wait.')
    if item.get('recovery_required') or item['status'] in RECOVERY:
        raise HTTPException(409, 'A previous operation may have changed infrastructure. Review state and acknowledge recovery first.')
    if item['archived']:
        raise HTTPException(409, 'Restore this archived project before changing it.')


def public(item):
    result = {k: v for k, v in item.items() if k not in ('owner', 'answers', 'requirements', 'plan_created', 'request_cache')}
    result['provider'] = item['spec'].get('cloud')
    result['revisions'] = [{k: v for k, v in rev.items() if k not in ('code', 'spec')} for rev in item['revisions']]
    result['mode'] = MODE
    result['approval_expires_at'] = item.get('plan_created', 0) + 900 if item.get('approval') else None
    return result


def user(request: Request):
    if AUTH_MODE == 'operator':
        return {'id': 'operator', 'email': 'Local operator'}
    account = STORE.user(request.cookies.get('makecloud_auth'))
    if not account:
        raise HTTPException(401, 'Sign in to your MakeCloud account.')
    return account


@app.middleware('http')
async def protect(request: Request, call_next):
    if request.url.path == '/readyz' and request.method == 'GET':
        return JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
    if TOKEN:
        if not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + TOKEN):
            return JSONResponse({'detail': 'Backend authentication required.'}, status_code=401)
    elif request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        return JSONResponse({'detail': 'Remote access requires an installation API token.'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('origin') and request.headers.get('origin').split('://')[-1] != request.headers.get('host'):
            return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
    # Bound actual bytes as well as Content-Length, including chunked requests.
    try:
        if int(request.headers.get('content-length', '0')) > 160000:
            return JSONResponse({'detail': 'Request too large.'}, status_code=413)
    except ValueError:
        return JSONResponse({'detail': 'Invalid request size.'}, status_code=400)
    body = await request.body()
    if len(body) > 160000:
        return JSONResponse({'detail': 'Request too large.'}, status_code=413)
    response = await call_next(request)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return response


def reject_secrets(content):
    if re.search('(?:AKIA|ASIA)[0-9A-Z]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|-----BEGIN .*PRIVATE KEY|\\b(?:secret_key|access_key|client_secret|password|private_key|aws_secret_access_key|openai_api_key|google_credentials)["\']?\\s*[:=]', content, re.I):
        raise HTTPException(422, 'Keep credentials outside chat. Use a configured cloud connection and managed secrets.')


@app.get('/health')
def health():
    return {'mode': MODE, 'auth_mode': AUTH_MODE, 'ai_configured': bool(os.getenv('OPENAI_API_KEY')), 'terraform_installed': bool(shutil.which('terraform')), 'runner': os.getenv('MAKECLOUD_RUNNER', 'local'), 'apply_enabled': MODE == 'live' and os.getenv('MAKECLOUD_ENABLE_APPLY') == 'true', 'encrypted_storage': True, 'onboarding': cloud_connections.capabilities(), 'registration': os.getenv('MAKECLOUD_REGISTRATION', 'invite'), 'scope': 'Hosted accounts with delegated cloud access. A reviewed plan is required for deployment.'}


@app.get('/catalog')
def catalog():
    return CATALOG


@app.get('/auth/me')
def me(account=Depends(user)):
    return account


def email(body):
    value = body.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise HTTPException(422, 'Enter a valid email address.')
    return value


def client_address(request):
    import ipaddress
    forwarded = request.headers.get('x-makecloud-client-ip') if TOKEN else None
    if forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return request.client.host


@app.post('/auth/register')
def register(body: Credentials, request: Request):
    if AUTH_MODE != 'accounts':
        raise HTTPException(409, 'Account registration is disabled in operator mode.')
    expected = os.getenv('MAKECLOUD_INVITE_CODE', '')
    public_signup = os.getenv('MAKECLOUD_REGISTRATION', 'invite') == 'public'
    if public_signup and not account_mail.ready():
        raise HTTPException(503, 'Public registration requires account email to be configured.')
    if not public_signup and (not expected or not secrets.compare_digest(body.invite, expected)):
        raise HTTPException(403, 'A valid invitation code is required.')
    if not STORE.limit('register:' + client_address(request), 5, 3600):
        raise HTTPException(429, 'Too many registration attempts. Try again later.')
    try:
        account = STORE.register(email(body), body.password, verified=not public_signup)
    except sqlite3.IntegrityError as exc:
        if public_signup:
            return {'verification_required': True, 'message': 'Check your email or request a new verification link.'}
        raise HTTPException(409, 'An account already exists with that email.') from exc
    except ValueError as exc:
        raise HTTPException(403, safe_error(exc)) from exc
    if public_signup:
        token = STORE.challenge(account['id'], 'verify', lifetime=1800)
        account_mail.send(account['email'], token, 'verify')
        return {'verification_required': True, 'message': 'Check your email to verify this account before signing in.'}
    return {'id': account['id'], 'email': account['email']}


@app.post('/auth/request-link')
def request_link(body: MailRequest, request: Request):
    if AUTH_MODE != 'accounts' or body.purpose not in ('verify', 'reset'):
        raise HTTPException(422, 'Choose email verification or password reset.')
    if not account_mail.ready():
        raise HTTPException(503, 'Account email is not configured.')
    if not STORE.limit('mail:' + client_address(request), 5, 600) or not STORE.limit('mail-account:' + email(body), 3, 600):
        raise HTTPException(429, 'Too many email requests. Try again later.')
    account = STORE.account_by_email(email(body))
    if account and (body.purpose != 'verify' or not account['verified']):
        account_mail.send(account['email'], STORE.challenge(account['id'], body.purpose, lifetime=1800), body.purpose)
    return {'message': 'If this account needs the requested link, it has been sent.'}


@app.post('/auth/verify')
def verify_email(body: AccountToken):
    result = STORE.consume(body.token, 'verify')
    if not result:
        raise HTTPException(400, 'This verification link expired or was already used.')
    STORE.verify(result[0])
    return {'message': 'Email verified. You can sign in now.'}


@app.post('/auth/reset')
def reset_password(body: AccountToken):
    if not body.password:
        raise HTTPException(422, 'Enter a new password with at least 12 characters.')
    result = STORE.consume(body.token, 'reset')
    if not result:
        raise HTTPException(400, 'This reset link expired or was already used.')
    STORE.reset_password(result[0], body.password)
    return {'message': 'Password reset. Sign in again with your new password.'}


@app.post('/auth/login')
def login(body: Credentials, request: Request, response: Response):
    if AUTH_MODE != 'accounts':
        raise HTTPException(409, 'Sign-in is disabled in operator mode.')
    if not STORE.limit('login:' + client_address(request), 20, 600) or not STORE.limit('login-account:' + email(body), 20, 600):
        raise HTTPException(429, 'Too many sign-in attempts. Try again later.')
    result = STORE.login(email(body), body.password)
    if not result:
        raise HTTPException(401, 'Email or password is incorrect.')
    token, account = result
    response.set_cookie('makecloud_auth', token, httponly=True, secure=os.getenv('MAKECLOUD_SECURE_COOKIES', 'true') != 'false', samesite='lax', max_age=86400, path='/')
    return account


@app.post('/auth/logout')
def logout(request: Request, response: Response):
    STORE.logout(request.cookies.get('makecloud_auth', ''))
    response.delete_cookie('makecloud_auth', path='/')
    return {'ok': True}


def connections_for(account, include_identity=False):
    saved = STORE.vault_all(account['id'])
    if hcp_runner.enabled():
        import hcp_connections
        saved = [hcp_connections.refresh(STORE, row) if row.get('method') == 'hcp_oidc' else row for row in saved]
    configured = os.getenv('MAKECLOUD_CONNECTIONS_FILE')
    if not configured:
        return saved if include_identity else [cloud_connections.public(row) for row in saved]
    try:
        rows = json.loads(Path(configured).read_text())
        if not isinstance(rows, list):
            raise ValueError()
        selected = [row for row in rows if row.get('owner') in (account['id'], account['email'])]
        if include_identity:
            return selected + saved
        return [{k: row.get(k) for k in ('id', 'label', 'cloud', 'account_id', 'project_id', 'subscription_id', 'regions')} for row in selected] + [cloud_connections.public(row) for row in saved]
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(503, 'Cloud connections are not configured correctly on the backend.') from exc


@app.get('/connections')
def connections(account=Depends(user)):
    return connections_for(account)


@app.post('/connections/begin', status_code=201)
def begin_connection(body: BeginConnection, response: Response, account=Depends(user)):
    if not STORE.limit('connect:' + account['id'], 10, 600):
        raise HTTPException(429, 'Too many connection attempts. Try again later.')
    result = cloud_connections.begin(STORE, account['id'], body.cloud, body.label, body.profile)
    nonce = result.pop('nonce', None)
    if nonce:
        response.set_cookie('makecloud_oauth', nonce, httponly=True, secure=os.getenv('MAKECLOUD_SECURE_COOKIES', 'true') != 'false', samesite='lax', max_age=600, path='/api/cloud/connections/google/callback')
    return add_template_ticket(result, account)


def owned_connection(cid, account):
    row = STORE.vault_get(cid, account['id'])
    if not row:
        raise HTTPException(404, 'Cloud connection not found.')
    return row


def add_template_ticket(result, account):
    if result.pop('needs_template_ticket', False):
        ticket = STORE.challenge(account['id'], 'azure_template', {'template': result['template']}, lifetime=1800)
        url = cloud_connections.base_url() + '/api/cloud/connections/template/' + ticket
        result['console_url'] = 'https://portal.azure.com/#create/Microsoft.Template/uri/' + cloud_connections.quote(url, safe='')
    return result


@app.get('/connections/template/{ticket}')
def connection_template(ticket: str):
    # Azure may retrieve the template more than once. Do not consume it on reads.
    import hashlib
    with STORE.connect() as db:
        row = db.execute('SELECT data FROM challenges WHERE token=? AND kind=? AND expires>?', (hashlib.sha256(ticket.encode()).hexdigest(), 'azure_template', time.time())).fetchone()
    if not row:
        raise HTTPException(404, 'Template link expired. Reopen connection instructions.')
    return STORE.decode(row[0])['template']


@app.get('/connections/google/callback')
def google_callback(request: Request, state: str = '', code: str = '', error: str = '', account=Depends(user)):
    try:
        if error or not code or len(code) > 4096 or len(state) > 200:
            raise HTTPException(400, 'Google Cloud consent was canceled. Start again when ready.')
        row = cloud_connections.google_callback(STORE, account['id'], state, code, request.cookies.get('makecloud_oauth', ''))
        target = '/#connected=' + row['id']
    except HTTPException as exc:
        target = '/#connection_error=' + cloud_connections.quote(str(exc.detail), safe='')
    response = RedirectResponse(cloud_connections.base_url() + target, status_code=303)
    response.delete_cookie('makecloud_oauth', path='/api/cloud/connections/google/callback')
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/connections/{cid}/instructions')
def connection_instructions(cid: str, account=Depends(user)):
    return add_template_ticket(cloud_connections.instructions(owned_connection(cid, account)), account)


@app.get('/connections/{cid}/projects')
def connection_projects(cid: str, account=Depends(user)):
    row = owned_connection(cid, account)
    if row['cloud'] != 'google' or not row.get('refresh_token') or row['status'] == 'revoked':
        raise HTTPException(409, 'Authorize a Google Cloud connection first.')
    return cloud_connections.google_projects(row)


@app.post('/connections/{cid}/verify')
def verify_connection(cid: str, body: VerifyConnection, account=Depends(user)):
    row = owned_connection(cid, account)
    if row.get('method') == 'hcp_oidc':
        return cloud_connections.verify(STORE, row, body.target)
    if row['status'] == 'connected' and body.target != next((row[x] for x in ('role_arn', 'project_id', 'subscription_id') if row.get(x)), None):
        raise HTTPException(409, 'Use a new connection to change cloud accounts.')
    if not STORE.limit('verify:' + account['id'], 20, 600):
        raise HTTPException(429, 'Too many access checks. Try again later.')
    return cloud_connections.verify(STORE, row, body.target)


@app.delete('/connections/{cid}')
def disconnect(cid: str, account=Depends(user)):
    row = owned_connection(cid, account)
    items = [x for x in STORE.all() if x.get('owner') == account['id'] and x.get('connection_id') == cid]
    # One API process; API mutations and jobs share these project locks.
    from contextlib import ExitStack
    with ExitStack() as stack:
        for item in sorted(items, key=lambda x: x['id']):
            stack.enter_context(project_lock(item['id']))
        items = [get(x['id'], account['id']) for x in items]
        if any(x.get('has_infrastructure') or x.get('recovery_required') or x['status'] in RUNNING for x in items):
            raise HTTPException(409, 'Clean up deployments and resolve recovery before disconnecting. You can revoke access directly in your cloud console at any time.')
        row = {key: value for key, value in row.items() if key in cloud_connections.PUBLIC_FIELDS or key == 'owner'}
        row['status'] = 'revoked'
        STORE.vault_save(row)
        for item in items:
            item.update(connection_id=None, plan=None, approval=None, status='draft' if item['code'] else item['status'])
            event(item, 'connection_removed')
            save(item)
    return {'ok': True, 'message': 'Removed from MakeCloud. Also revoke the role, OAuth grant or Lighthouse delegation in your cloud console.'}


@app.get('/sessions')
def sessions(archived: bool = False, account=Depends(user)):
    rows = [migrate(x) for x in STORE.all() if x.get('owner', 'operator') == account['id'] and x.get('archived', False) == archived]
    return [{'id': x['id'], 'title': x['spec'].get('name') or x['requirements'][:70], 'status': x['status'], 'cloud': x['spec'].get('cloud'), 'workload': x['spec'].get('workload'), 'updated_at': x['updated_at']} for x in sorted(rows, key=lambda x: x['updated_at'], reverse=True)[:100]]


def propose(item, code, summary, spec=None):
    target_check(code, spec or item['spec'])
    reject_secrets(code)
    item['proposal'] = {'id': str(uuid.uuid4()), 'code': code, 'summary': summary, 'spec': spec or item['spec'].copy(), 'base_revision': item['revision'], 'diff': ''.join(difflib.unified_diff(item['code'].splitlines(True), code.splitlines(True), fromfile='current/main.tf', tofile='proposed/main.tf'))}
    if not item['code']:
        item['status'] = 'proposal'
    event(item, 'proposal_created', summary)


def target_check(code, spec):
    document = run_cloud.check_policy(code, spec['cloud'])
    config = document['provider'][0][spec['cloud']]
    fields = ['region'] if spec['cloud'] == 'aws' else ['region', 'project'] if spec['cloud'] == 'google' else ['subscription_id']
    expected = {'region': spec['region'], 'project': spec.get('project_id'), 'subscription_id': spec.get('subscription_id')}
    local_projects = {f'${{google_cloud_run_v2_service.{name}.project}}' for block in document.get('resource', []) for name, attrs in block.get('google_cloud_run_v2_service', {}).items() if attrs.get('project', expected['project']) == expected['project']}
    location_refs = {f'${{{kind}.{name}.location}}' for block in document.get('resource', []) for kind, instances in block.items() for name, attrs in instances.items() if isinstance(attrs.get('location'), str) and attrs['location'].replace(' ', '').lower() == spec['region'].replace(' ', '').lower()}
    if any(config.get(field) != expected[field] for field in fields):
        raise ValueError('Terraform provider target must match the project region and account requirements. Update requirements before changing the target.')
    def check(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('labels', 'tags'):
                    continue
                if (spec['cloud'] == 'google' and key in ('project', 'project_id') or spec['cloud'] == 'azurerm' and key == 'subscription_id') and child != expected.get('project' if key == 'project_id' else key) and not (isinstance(child, str) and child in local_projects):
                    raise ValueError('Resource and data targets must use the selected project or subscription without overrides.')
                if spec['cloud'] in ('google', 'azurerm') and key in ('region', 'location') and (not isinstance(child, str) or child not in location_refs and child.replace(' ', '').lower() != spec['region'].replace(' ', '').lower()):
                    raise ValueError('Resource locations must match the selected region or reference a resource in that region.')
                if isinstance(child, str) and spec['cloud'] == 'azurerm' and '/subscriptions/' in child.lower() and not child.lower().startswith('/subscriptions/' + str(spec.get('subscription_id')).lower() + '/'):
                    raise ValueError('Azure resource IDs must stay in the selected subscription.')
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    for kind in ('resource', 'data'):
        check(document.get(kind, []))


def ensure_target_stable(item, candidate):
    if item.get('has_infrastructure') and any(candidate.get(field) != item['spec'].get(field) for field in ('cloud', 'region', 'project_id', 'subscription_id')):
        raise ValueError('A deployed project must retain its cloud, region and account target. Use a new project for migration.')


def handle_turn(item, text):
    if is_question(text):
        if MODE == 'live' and os.getenv('OPENAI_API_KEY'):
            result = assistant.turn(item, text)
            if result['action'] != 'answer':
                raise ValueError('A question must not change your infrastructure. Ask for a specific change separately.')
            return result['reply']
        return explain(text, item)
    writable(item)
    updates = extract(text, item['spec'], item.get('awaiting'))
    if updates.get('cloud') and updates['cloud'] != item['spec'].get('cloud') and item.get('has_infrastructure'):
        raise ValueError('Changing cloud after deployment requires a new project. This project retains its existing state.')
    candidate = validate_spec({**item['spec'], **updates})
    ensure_target_stable(item, candidate)
    if candidate['cloud'] != item['spec']['cloud']:
        item['connection_id'] = None
    if not item.get('connection_id') and not item.get('has_infrastructure'):
        available = [row for row in STORE.vault_all(item['owner']) if row.get('status') == 'connected' and row.get('makecloud_project_id') in (None, item['id']) and row['cloud'] == candidate['cloud'] and all(not candidate.get(field) or candidate[field] == row.get(field) for field in ('project_id', 'subscription_id'))]
        if len(available) == 1:
            row = available[0]
            if row.get('method') == 'hcp_oidc' and not STORE.claim_connection(row['id'], item['owner'], item['id']):
                raise ValueError('This cloud connection was selected in another project. Create a new connection.')
            candidate = validate_spec({**candidate, **{key: row[key] for key in ('project_id', 'subscription_id') if row.get(key)}})
            item['connection_id'] = row['id']
            event(item, 'connection_selected', 'Defaulted to the only matching verified connection: ' + row['label'])
    if candidate != item['spec']:
        item.update(spec=candidate, approval=None, plan=None, proposal=None)
        if item['status'] == 'planned':
            item['status'] = 'draft'
    item['missing'] = missing(item['spec'], MODE)
    if MODE == 'live' and os.getenv('OPENAI_API_KEY'):
        result = assistant.turn(item, text)
        updated = validate_spec({**item['spec'], **result['updates']})
        if updated.get('cloud') != item['spec'].get('cloud') and item.get('has_infrastructure'):
            raise ValueError('Changing cloud after deployment requires a new project.')
        ensure_target_stable(item, updated)
        if updated != item['spec']:
            item.update(spec=updated, plan=None, approval=None, proposal=None)
        item['missing'] = missing(item['spec'], MODE)
        if result['action'] == 'propose':
            if item['missing'] or not result['terraform'].strip():
                raise ValueError('The AI proposal is missing requirements or Terraform. Your accepted configuration is unchanged.')
            propose(item, result['terraform'], result['reply'])
        return result['reply']
    if item['missing']:
        field = item['missing'][0]
        item['awaiting'] = field
        return question(field, item['spec'])
    item['awaiting'] = None
    # Blueprint generation must not silently replace accepted manual edits.
    if item['code'] and not updates and text.lower().strip() not in ('generate', 'generate draft', 'regenerate', 'create draft'):
        return 'Tell me a specific requirement change or edit the Terraform directly. I keep accepted code unchanged. The local assistant can explain this design; arbitrary code revisions need live AI.'
    code, summary = build(item['spec'])
    propose(item, code, summary)
    return summary + '\nReview the proposed Terraform and accept it before planning. Demo mode never deploys.' if MODE == 'demo' else summary + '\nReview and accept this proposal before planning.'


@app.post('/sessions')
def create(body: Message, account=Depends(user)):
    text = body.message.strip()
    if not text:
        raise HTTPException(422, 'Describe what you want to build.')
    reject_secrets(text)
    if len([x for x in STORE.all() if x.get('owner', 'operator') == account['id'] and not x.get('archived')]) >= 50:
        raise HTTPException(429, 'Project limit reached. Archive unused projects first.')
    item = {'id': str(uuid.uuid4()), 'owner': account['id'], 'requirements': text, 'spec': Spec().model_dump(), 'revision': 0, 'status': 'gathering', 'messages': [{'role': 'user', 'content': text}], 'code': '', 'summary': '', 'plan': None, 'approval': None, 'proposal': None, 'error': None, 'mode': MODE, 'job': None, 'revisions': [], 'audit': [], 'outputs': {}, 'checks': [], 'archived': False, 'has_infrastructure': False, 'recovery_required': False, 'connection_id': None, 'missing': []}
    event(item, 'project_created')
    try:
        reply = handle_turn(item, text)
    except (ValueError, ValidationError) as exc:
        item['error'] = safe_error(exc)
        reply = item['error']
    item['messages'].append({'role': 'assistant', 'content': reply})
    save(item)
    return public(item)


def safe_error(exc):
    if isinstance(exc, ValidationError):
        return '; '.join(error['msg'].removeprefix('Value error, ') for error in exc.errors(include_input=False))[:1500]
    return str(exc)[:1500]


@app.get('/sessions/{sid}')
def read(sid: str, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        if hcp_runner.enabled():
            refresh_remote(item)
        return public(item)


@app.post('/sessions/{sid}/messages')
def message(sid: str, body: Message, account=Depends(user)):
    text = body.message.strip()
    if not text:
        raise HTTPException(422, 'Message cannot be empty.')
    reject_secrets(text)
    if not STORE.limit('messages:' + account['id'], 60, 60):
        raise HTTPException(429, 'Too many messages. Please wait a moment.')
    with project_lock(sid):
        item = get(sid, account['id'])
        revision_check(item, body.revision)
        if len(item['messages']) >= 500:
            raise HTTPException(409, 'This conversation reached its limit. Export it and start a new project.')
        item['messages'].append({'role': 'user', 'content': text})
        item['error'] = None
        try:
            reply = handle_turn(item, text)
        except (ValueError, ValidationError) as exc:
            item['error'] = safe_error(exc)
            reply = item['error']
        item['messages'].append({'role': 'assistant', 'content': reply})
        save(item)
        return public(item)


@app.put('/sessions/{sid}/requirements')
def requirements(sid: str, body: Requirements, account=Depends(user)):
    reject_secrets(json.dumps(body.updates))
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        revision_check(item, body.revision)
        try:
            updated = validate_spec({**item['spec'], **body.updates})
            ensure_target_stable(item, updated)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, safe_error(exc)) from exc
        if item.get('has_infrastructure') and updated['cloud'] != item['spec']['cloud']:
            raise HTTPException(409, 'Use a new project to change cloud after deployment.')
        item.update(spec=updated, plan=None, approval=None, proposal=None, error=None)
        if item['status'] == 'planned':
            item['status'] = 'draft'
        event(item, 'requirements_updated')
        item['messages'].append({'role': 'assistant', 'content': 'Requirements updated. Ask me to generate a proposal when you are ready.'})
        save(item)
        return public(item)


def accept_code(item, code, summary, spec=None):
    if item['revision'] >= 100:
        raise HTTPException(409, 'Configuration revision limit reached. Export the project before continuing.')
    item['revision'] += 1
    item.update(code=code, summary=summary, spec=spec or item['spec'], status='draft', plan=None, approval=None, proposal=None, error=None)
    item['revisions'].append({'revision': item['revision'], 'code': code, 'spec': item['spec'].copy(), 'summary': summary, 'at': time.time()})
    event(item, 'configuration_accepted', f"Revision {item['revision']}")


@app.post('/sessions/{sid}/proposal')
def accept_proposal(sid: str, body: Confirm, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        revision_check(item, body.revision)
        proposal = item.get('proposal')
        if not body.confirm or not proposal or body.proposal_id != proposal['id'] or proposal['base_revision'] != item['revision']:
            raise HTTPException(409, 'Review and confirm the current proposal before accepting it.')
        accept_code(item, proposal['code'], proposal['summary'], proposal['spec'])
        item['messages'].append({'role': 'assistant', 'content': f"Configuration revision {item['revision']} accepted. You can edit it or create a Terraform plan."})
        save(item)
        return public(item)


@app.delete('/sessions/{sid}/proposal')
def discard_proposal(sid: str, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        item['proposal'] = None
        item['status'] = 'draft' if item['code'] else 'gathering'
        if item['revisions']:
            item['spec'] = item['revisions'][-1]['spec'].copy()
        event(item, 'proposal_discarded')
        save(item)
        return public(item)


@app.put('/sessions/{sid}/code')
def edit(sid: str, body: Code, account=Depends(user)):
    reject_secrets(body.code)
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        revision_check(item, body.revision)
        if not item['spec'].get('cloud'):
            raise HTTPException(409, 'Select a cloud first.')
        try:
            target_check(body.code, item['spec'])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        accept_code(item, body.code, 'Manual Terraform edit. Review a new plan before applying.')
        save(item)
        return public(item)


@app.get('/sessions/{sid}/revisions/{number}')
def read_revision(sid: str, number: int, account=Depends(user)):
    item = get(sid, account['id'])
    revision = next((x for x in item['revisions'] if x['revision'] == number), None)
    if not revision:
        raise HTTPException(404, 'Revision not found.')
    return revision


@app.post('/sessions/{sid}/revisions/{number}/restore')
def restore_revision(sid: str, number: int, body: Confirm, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        revision_check(item, body.revision)
        prior = next((x for x in item['revisions'] if x['revision'] == number), None)
        if not body.confirm or not prior:
            raise HTTPException(422, 'Confirm an existing revision to restore it.')
        try:
            ensure_target_stable(item, prior['spec'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        accept_code(item, prior['code'], f'Restored configuration from revision {number}.', prior['spec'])
        save(item)
        return public(item)


@app.put('/sessions/{sid}/connection')
def attach_connection(sid: str, body: Connection, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        match = next((x for x in connections_for(account) if x['id'] == body.connection_id), None)
        if not match or match['cloud'] != item['spec']['cloud']:
            raise HTTPException(422, 'Choose an authorized connection for this cloud.')
        if match.get('status') and match['status'] != 'connected':
            raise HTTPException(409, 'Verify this connection before selecting it.')
        connection_row = STORE.vault_get(body.connection_id, account['id'])
        if connection_row and connection_row.get('method') == 'hcp_oidc':
            if connection_row.get('makecloud_project_id') not in (None, sid):
                raise HTTPException(409, 'This HCP connection already belongs to another project. Create another connection for separate Terraform state.')
            if any(x['id'] != sid and x.get('connection_id') == body.connection_id for x in STORE.all()):
                raise HTTPException(409, 'Each HCP connection is dedicated to one MakeCloud project.')
        if item.get('has_infrastructure') and item.get('connection_id') != body.connection_id:
            raise HTTPException(409, 'A deployed project must retain its original cloud connection.')
        candidate = validate_spec({**item['spec'], **{key: match[key] for key in ('project_id', 'subscription_id') if match.get(key)}})
        try:
            ensure_target_stable(item, candidate)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if candidate != item['spec']:
            item.update(spec=candidate, proposal=None, status='draft' if item['code'] else 'gathering')
            if not missing(candidate, MODE) and candidate['workload'] != 'custom':
                code, summary = build(candidate)
                propose(item, code, summary)
        item.update(connection_id=body.connection_id, plan=None, approval=None)
        if connection_row and connection_row.get('method') == 'hcp_oidc':
            if not STORE.claim_connection(body.connection_id, account['id'], sid):
                raise HTTPException(409, 'This cloud connection was selected in another project. Create another connection.')
        event(item, 'connection_selected', match.get('label') or match['id'])
        save(item)
        return public(item)


def identity(item, account, resolve=True):
    if not item.get('connection_id'):
        if AUTH_MODE == 'accounts':
            raise HTTPException(409, 'Select a scoped cloud connection for this project.')
        return None, {'label': 'Operator environment', 'cloud': item['spec']['cloud']}
    row = next((x for x in connections_for(account, True) if x['id'] == item['connection_id']), None)
    if not row or row.get('cloud') != item['spec']['cloud']:
        raise HTTPException(409, 'Cloud connection is unavailable or belongs to another cloud.')
    for field in ('project_id', 'subscription_id'):
        if row.get(field) and row[field] != item['spec'].get(field):
            raise HTTPException(409, 'Project requirements do not match the selected cloud account.')
    if row.get('regions') and item['spec']['region'] not in row['regions']:
        raise HTTPException(409, 'This region is not authorized for the cloud connection.')
    if row['cloud'] == 'aws' and not re.fullmatch(r'[0-9]{12}', str(row.get('account_id', ''))):
        raise HTTPException(503, 'The AWS connection must specify the expected account ID.')
    if row.get('workloads') and item['spec']['workload'] not in row['workloads']:
        raise HTTPException(409, 'This connection does not grant access for this workload. Connect with the required permissions.')
    if row.get('method'):
        if row['method'] == 'hcp_oidc':
            if not hcp_runner.enabled() or row.get('status') != 'connected':
                raise HTTPException(409, 'This connection requires HCP Terraform and verified workload identity.')
            return None, {**cloud_connections.public(row), 'boundary_arn': row.get('boundary_arn')}
        return cloud_connections.environment(row) if resolve else None, {**cloud_connections.public(row), 'boundary_arn': row.get('boundary_arn')}
    allowed = {'aws': {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_REGION', 'AWS_DEFAULT_REGION'}, 'google': {'GOOGLE_CREDENTIALS', 'GOOGLE_OAUTH_ACCESS_TOKEN', 'GOOGLE_PROJECT'}, 'azurerm': {'ARM_CLIENT_ID', 'ARM_CLIENT_SECRET', 'ARM_TENANT_ID', 'ARM_SUBSCRIPTION_ID', 'ARM_USE_OIDC', 'ARM_OIDC_TOKEN'}}[row['cloud']]
    supplied = row.get('environment', {})
    if not isinstance(supplied, dict) or set(supplied) - allowed or any(not isinstance(v, str) for v in supplied.values()):
        raise HTTPException(503, 'The cloud connection environment is invalid.')
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'SSL_CERT_FILE') if k in os.environ}
    env.update(supplied)
    return env, {k: row.get(k) for k in ('id', 'label', 'cloud', 'account_id', 'project_id', 'subscription_id')}


def update_job(sid, job_id, **changes):
    with project_lock(sid):
        item = get(sid)
        if item.get('job', {}).get('id') != job_id:
            return
        item['job'].update(changes)
        save(item)


def execute_job(sid, job_id, kind, account, environment):
    cancel = CANCELLATIONS[job_id]
    directory = ROOT / sid
    update_job(sid, job_id, status='running', started_at=time.time())
    def progress(stage):
        update_job(sid, job_id, stage=stage)
    try:
        if cancel.is_set():
            raise run_cloud.Cancelled('Operation canceled before cloud execution.')
        if kind == 'apply' and time.time() - get(sid).get('plan_created', 0) > 900:
            raise ValueError('The reviewed plan expired while queued. Inspect state and create a fresh plan.')
        # Acquire fresh credentials when the durable job actually starts.
        if environment is None and get(sid).get('connection_id'):
            environment, _ = identity(get(sid), account)
        run_cloud.unseal(directory, STORE.cipher)
        item = get(sid)
        if kind in ('plan', 'destroy'):
            result = run_cloud.plan(directory, item['code'], item['spec']['cloud'], destroy=kind == 'destroy', environment=environment, progress=progress, cancelled=cancel, expected_account=item['job']['identity'].get('account_id'), expected_boundary=item['job']['identity'].get('boundary_arn'))
            if cancel.is_set():
                raise run_cloud.Cancelled('Planning canceled.')
            run_cloud.seal(directory, STORE.cipher)
            with project_lock(sid):
                current = get(sid)
                current.update(status='planned', plan=result, approval=secrets.token_urlsafe(32), plan_created=time.time(), error=None)
                current['plan']['identity'] = current['job']['identity']
                current['plan']['revision'] = current['revision']
                current['job'].update(status='succeeded', stage='review_ready', finished_at=time.time())
                event(current, 'plan_created', 'Cleanup plan' if kind == 'destroy' else 'Deployment plan')
                save(current)
        else:
            outputs = run_cloud.apply(directory, item['code'], item['spec']['cloud'], item['plan'], environment=environment, progress=progress, cancelled=cancel)
            run_cloud.seal(directory, STORE.cipher)
            with project_lock(sid):
                current = get(sid)
                destroying = current['plan'].get('purpose') == 'destroy'
                current.update(status='destroyed' if destroying else 'applied', outputs=outputs, has_infrastructure=not destroying, recovery_required=False, error=None)
                current['job'].update(status='succeeded', stage='complete', finished_at=time.time())
                current['messages'].append({'role': 'assistant', 'content': 'Cleanup applied successfully. State and audit history are retained.' if destroying else 'The reviewed plan was applied. Outputs are available in the deployment panel. Review application setup and test connectivity before treating it as ready.'})
                current['checks'] = [{'name': 'Terraform apply', 'status': 'passed'}, {'name': 'Application connectivity', 'status': 'not_run', 'message': 'Use the resource outputs to verify your application. No arbitrary endpoint requests are made by the backend.'}]
                event(current, 'cleanup_applied' if destroying else 'deployment_applied')
                save(current)
    except Exception as exc:
        logging.exception('MakeCloud operation failed: %s', kind)
        run_cloud.seal(directory, STORE.cipher)
        with project_lock(sid):
            current = get(sid)
            unsafe = kind == 'apply'
            current.update(status='canceled_apply' if unsafe and isinstance(exc, run_cloud.Cancelled) else 'apply_failed' if unsafe else 'draft', approval=None, plan=None, recovery_required=unsafe, error=str(exc.detail) if isinstance(exc, HTTPException) else safe_error(exc) if isinstance(exc, ValueError) else 'Operation failed unexpectedly. Inspect the protected workspace and retained state.')
            current['job'].update(status='canceled' if isinstance(exc, run_cloud.Cancelled) else 'failed', stage='stopped', finished_at=time.time())
            event(current, 'operation_failed', current['error'])
            save(current)
    finally:
        STORE.finish(job_id, (get(sid).get('job') or {}).get('status', 'failed'))
        CANCELLATIONS.pop(job_id, None)


def dispatch(job_id):
    payload = STORE.claim(job_id)
    if not payload:
        return
    item = get(payload['sid'])
    if item.get('job', {}).get('stage') == 'cancel_requested':
        CANCELLATIONS[job_id].set()
    execute_job(payload['sid'], job_id, payload['kind'], payload['account'], None)


def queue(item, kind, account, environment, identity_info):
    if hcp_runner.enabled():
        return queue_remote(item, kind, account, identity_info)
    job_id = str(uuid.uuid4())
    item.update(status='applying' if kind == 'apply' else 'planning', error=None)
    item['job'] = {'id': job_id, 'kind': kind, 'status': 'queued', 'stage': 'queued', 'created_at': time.time(), 'identity': identity_info}
    CANCELLATIONS[job_id] = threading.Event()
    event(item, 'operation_queued', kind)
    item['missing'] = missing(item['spec'], MODE)
    STORE.enqueue(item, {'id': job_id, 'sid': item['id'], 'kind': kind, 'account': account})
    EXECUTOR.submit(dispatch, job_id)
    return public(item)


def execution_ready():
    if MODE != 'live':
        raise HTTPException(409, 'Demo mode never accesses cloud accounts. Export the blueprint or configure live mode.')
    if AUTH_MODE == 'accounts' and (os.getenv('MAKECLOUD_RUNNER') not in ('docker', 'hcp') or not os.getenv('MAKECLOUD_ENCRYPTION_KEY')):
        raise HTTPException(403, 'Account cloud execution requires an isolated Docker or HCP runner and a separately configured encryption key.')
    if hcp_runner.enabled() and not hcp_runner.configured():
        raise HTTPException(503, 'HCP Terraform execution has not been configured.')


def queue_remote(item, kind, account, selected):
    row = STORE.vault_get(item.get('connection_id'), account['id'])
    if not row or row.get('method') != 'hcp_oidc' or row.get('makecloud_project_id') != item['id']:
        raise HTTPException(409, 'Select a verified HCP cloud connection dedicated to this project.')
    old = (item.get('job') or {}).get('remote')
    if kind != 'apply' and old and old.get('run_id'):
        previous = hcp_runner.Client().run(old['run_id'])
        if previous['attributes']['status'] in hcp_runner.READY:
            hcp_runner.cancel(old)
    jid = str(uuid.uuid4())
    item.update(status='applying' if kind == 'apply' else 'planning', error=None)
    remote = {key: item['plan'][key] for key in ('run_id', 'workspace_id', 'configuration_id', 'digest', 'configuration_digest')} if kind == 'apply' else {}
    item['job'] = {'id': jid, 'kind': kind, 'status': 'running', 'stage': 'submitting', 'created_at': time.time(), 'identity': selected, 'remote': remote}
    event(item, 'operation_queued', kind)
    save(item)
    def checkpoint(**values):
        item['job']['remote'].update(values)
        save(item)
    try:
        if kind == 'apply':
            checkpoint(apply_requested=True)
            hcp_runner.apply(item['plan'], item['code'])
        else:
            hcp_runner.begin_plan(item, row, checkpoint, destroy=kind == 'destroy')
        item['job']['stage'] = 'remote_queued'
    except ValueError as exc:
        item['error'] = safe_error(exc)
        # A timeout can occur after HCP accepted the request. Preserve its IDs
        # and reconcile; never submit a replacement apply automatically.
        if not remote.get('run_id') and not remote.get('run_requested'):
            item.update(status='draft', plan=None, approval=None)
            item['job'].update(status='failed', stage='stopped', finished_at=time.time())
        else:
            item['job']['stage'] = 'checking_remote_request'
    save(item)
    return public(item)


def refresh_remote(item):
    if item['status'] not in RUNNING or 'remote' not in (item.get('job') or {}):
        return
    job, client = item['job'], hcp_runner.Client()
    remote = job['remote']
    try:
        if not remote.get('run_id'):
            if remote.get('run_requested'):
                row = client.find_run(remote['workspace_id'], remote['configuration_id'], 'MakeCloud job ' + job['id'])
                if row:
                    remote['run_id'] = row['id']
                elif time.time() - job['created_at'] < 60:
                    return
                else:
                    raise ValueError('Remote submission could not be confirmed. Inspect the HCP workspace before creating another plan.')
            else:
                if time.time() - job['created_at'] < 60:
                    return
                raise ValueError('The backend stopped before submitting the plan. Create a new plan.')
        row = client.run(remote['run_id'])
        client.assert_run(row, remote['workspace_id'], remote['configuration_id'])
        status = row['attributes']['status']
        job['stage'] = 'hcp_' + status
        if status in hcp_runner.TERMINAL_ERRORS:
            unsafe = job['kind'] == 'apply'
            item.update(status='apply_failed' if unsafe else 'draft', plan=None, approval=None, recovery_required=unsafe, error='The HCP Terraform run ended with status ' + status + '. Review its protected logs and any partial resources.')
            job.update(status='canceled' if status in ('canceled', 'force_canceled', 'discarded') else 'failed', finished_at=time.time())
        elif job['kind'] != 'apply' and status in hcp_runner.READY and row['attributes'].get('actions', {}).get('is-confirmable'):
            result = hcp_runner.review(item, row)
            result.update(identity=job['identity'], revision=item['revision'])
            item.update(status='planned', plan=result, approval=secrets.token_urlsafe(32), plan_created=time.time(), error=None)
            job.update(status='succeeded', stage='review_ready', finished_at=time.time())
            event(item, 'plan_created', 'Remote cleanup plan' if job['kind'] == 'destroy' else 'Remote deployment plan')
        elif status in ('applied', 'planned_and_finished'):
            destroying = job['kind'] == 'destroy' or (item.get('plan') or {}).get('purpose') == 'destroy'
            output_error = None
            try:
                outputs = client.outputs(remote['workspace_id'])
            except ValueError:
                outputs = item.get('outputs', {})
                output_error = 'HCP completed the run, but resource outputs could not be retrieved. View them in the HCP workspace.'
            changed = status == 'applied'
            if changed:
                item.update(status='destroyed' if destroying else 'applied', has_infrastructure=not destroying, recovery_required=False)
                item['messages'].append({'role': 'assistant', 'content': 'The reviewed changes were applied by HCP Terraform. Review outputs and test your application.'})
                event(item, 'cleanup_applied' if destroying else 'deployment_applied')
            else:
                item['status'] = 'applied' if item.get('has_infrastructure') else 'draft'
                item['messages'].append({'role': 'assistant', 'content': 'The remote plan completed with no infrastructure changes to apply.'})
            item.update(outputs=outputs, approval=None, plan=None, error=output_error)
            job.update(status='succeeded', stage='complete', finished_at=time.time())
            item['checks'] = [{'name': 'Terraform remote run', 'status': 'passed'}, {'name': 'Application connectivity', 'status': 'not_run'}]
        elif job['kind'] == 'apply' and status in hcp_runner.READY and time.time() - job['created_at'] > 60:
            item.update(status='apply_failed', approval=None, plan=None, recovery_required=True, error='HCP has not confirmed this apply request. Inspect its run before recovery; MakeCloud will not repeat it automatically.')
            job.update(status='failed', finished_at=time.time())
        save(item)
    except ValueError as exc:
        item['error'] = safe_error(exc)
        if not remote.get('run_id') and time.time() - job['created_at'] > 60:
            item.update(status='draft', approval=None, plan=None)
            job.update(status='failed', finished_at=time.time())
        save(item)


def connection_policy(item):
    row = STORE.vault_get(item.get('connection_id'), item['owner'])
    if row and row.get('method') in ('google_oauth', 'azure_lighthouse'):
        # User OAuth tokens carry all the user's IAM rights. Only reviewed,
        # project-scoped resource types may use this hosted connection method.
        allowed = {'google_storage_bucket', 'google_cloud_run_v2_service', 'google_cloud_run_v2_service_iam_member', 'google_project_service'} if row['cloud'] == 'google' else {'azurerm_resource_group', 'azurerm_storage_account', 'azurerm_storage_container', 'azurerm_container_app_environment', 'azurerm_container_app'}
        doc = run_cloud.check_policy(item['code'], row['cloud'])
        if doc.get('data') or any(set(block) - allowed for block in doc.get('resource', [])):
            raise HTTPException(422, 'This hosted connection supports reviewed storage and container resource types. Additional resource types require a separately reviewed connection policy.')
        if row['cloud'] == 'azurerm':
            refs = {f'${{{kind}.{name}.id}}' for block in doc.get('resource', []) for kind, instances in block.items() for name in instances}
            def check_ids(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in ('storage_account_id', 'container_app_environment_id') and child not in refs:
                            raise HTTPException(422, 'Azure account and environment IDs must reference resources in this configuration.')
                        if key in ('identity', 'identity_ids'):
                            raise HTTPException(422, 'Additional Azure identities need a reviewed connection policy.')
                        check_ids(child)
                elif isinstance(value, list):
                    for child in value:
                        check_ids(child)
            check_ids(doc.get('resource', []))


@app.post('/sessions/{sid}/plan', status_code=202)
def create_plan(sid: str, body: Operation = Operation(), account=Depends(user)):
    execution_ready()
    with project_lock(sid):
        item = get(sid, account['id'])
        writable(item)
        revision_check(item, body.revision)
        if not item['code'] or item['proposal']:
            raise HTTPException(409, 'Accept or discard the current proposal before planning.')
        try:
            target_check(item['code'], item['spec'])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        connection_policy(item)
        if body.purpose not in ('deploy', 'destroy'):
            raise HTTPException(422, 'Plan purpose must be deploy or destroy.')
        if body.purpose == 'destroy' and not item.get('has_infrastructure'):
            raise HTTPException(409, 'This project has no confirmed deployment to clean up.')
        if not STORE.limit('operations:' + account['id'], 20, 3600):
            raise HTTPException(429, 'Operation limit reached. Try again later.')
        environment, selected = identity(item, account, resolve=False)
        item.update(plan=None, approval=None)
        return queue(item, 'destroy' if body.purpose == 'destroy' else 'plan', account, environment, selected)


@app.post('/sessions/{sid}/apply', status_code=202)
def deploy(sid: str, body: Approval, account=Depends(user)):
    execution_ready()
    if os.getenv('MAKECLOUD_ENABLE_APPLY') != 'true':
        raise HTTPException(403, 'Deployment is disabled on this backend.')
    with project_lock(sid):
        item = get(sid, account['id'])
        revision_check(item, body.revision)
        if item['status'] != 'planned' or item['proposal'] or not item.get('approval') or not secrets.compare_digest(body.approval, item['approval']):
            raise HTTPException(409, 'A current reviewed plan is required.')
        if time.time() - item['plan_created'] > 900:
            raise HTTPException(409, 'This plan expired. Create and review a new one.')
        if not body.confirm or (item['plan']['deletes'] or item['plan'].get('purpose') == 'destroy') and not body.confirm_destructive:
            raise HTTPException(422, 'Confirm the plan and explicitly acknowledge deletions or replacements.')
        monthly = (item['plan'].get('cost') or {}).get('monthly')
        if item['plan'].get('purpose') != 'destroy' and monthly is not None and item['spec']['budget'] is not None and monthly > item['spec']['budget'] and not body.confirm_budget:
            raise HTTPException(422, 'This estimate exceeds your budget. Explicitly acknowledge it or revise the design.')
        environment, selected = identity(item, account, resolve=False)
        item['approval'] = None
        return queue(item, 'apply', account, environment, selected)


@app.post('/sessions/{sid}/cancel')
def cancel_job(sid: str, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        job = item.get('job')
        if hcp_runner.enabled() and job and job.get('remote', {}).get('run_id') and item['status'] in RUNNING:
            try:
                hcp_runner.cancel(job['remote'])
            except ValueError as exc:
                raise HTTPException(409, safe_error(exc)) from exc
            job['stage'] = 'cancel_requested'
            event(item, 'cancel_requested')
            save(item)
            return public(item)
        cancel = CANCELLATIONS.get(job['id']) if job else None
        if not cancel:
            raise HTTPException(409, 'No running operation is available to cancel.')
        cancel.set()
        item['job']['stage'] = 'cancel_requested'
        event(item, 'cancel_requested')
        save(item)
        return public(item)


@app.post('/sessions/{sid}/recover')
def recover(sid: str, body: Confirm, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        if item['status'] in RUNNING or not item.get('recovery_required') or not body.confirm:
            raise HTTPException(409, 'Acknowledge that you inspected retained state and possible partial cloud changes before recovery.')
        item.update(status='draft', recovery_required=False, plan=None, approval=None, error=None, has_infrastructure=True)
        event(item, 'recovery_acknowledged')
        save(item)
        return public(item)


@app.put('/sessions/{sid}/archive')
def archive(sid: str, body: Archive, account=Depends(user)):
    with project_lock(sid):
        item = get(sid, account['id'])
        if item['status'] in RUNNING:
            raise HTTPException(409, 'Wait for the operation before archiving.')
        item.update(archived=body.archived, approval=None, plan=None)
        if item['status'] == 'planned':
            item['status'] = 'draft'
        event(item, 'project_archived' if body.archived else 'project_restored')
        save(item)
        return public(item)


@app.get('/sessions/{sid}/export')
def export_project(sid: str, account=Depends(user)):
    item = get(sid, account['id'])
    return {'name': item['spec'].get('name'), 'spec': item['spec'], 'code': item['code'], 'messages': item['messages'], 'revisions': item['revisions'], 'audit': item['audit'], 'outputs': item['outputs'], 'status': item['status'], 'exported_at': time.time(), 'note': 'Terraform state, plans, login tokens and cloud identity are not included.'}
