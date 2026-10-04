"""API-driven remote runs. HCP owns state; MakeCloud never hosts Terraform here."""
import hashlib
import io
import json
import os
import re
import tarfile
import time
from urllib.parse import quote, urlparse
import httpx
import run_cloud

API = 'https://app.terraform.io/api/v2'
TERMINAL_ERRORS = {'errored', 'canceled', 'force_canceled', 'discarded', 'policy_soft_failed'}
READY = {'planned', 'cost_estimated', 'policy_checked', 'planned_and_saved'}


def enabled():
    return os.getenv('MAKECLOUD_RUNNER') == 'hcp'


def configured():
    return all(os.getenv(key) for key in ('MAKECLOUD_HCP_TOKEN', 'MAKECLOUD_HCP_ORGANIZATION', 'MAKECLOUD_HCP_PROJECT_ID', 'MAKECLOUD_HCP_PROJECT_NAME'))


class Client:
    def __init__(self):
        if not configured():
            raise ValueError('Configure the dedicated HCP Terraform project and API token.')
        self.org = os.environ['MAKECLOUD_HCP_ORGANIZATION']
        self.project = os.environ['MAKECLOUD_HCP_PROJECT_ID']
        self.project_name = os.environ['MAKECLOUD_HCP_PROJECT_NAME']
        if not re.fullmatch(r'[\w-]{1,100}', self.org) or not re.fullmatch(r'prj-[\w-]+', self.project) or not re.fullmatch(r'[\w -]{1,100}', self.project_name):
            raise ValueError('Invalid HCP Terraform project configuration.')
        self.headers = {'Authorization': 'Bearer ' + os.environ['MAKECLOUD_HCP_TOKEN'], 'Content-Type': 'application/vnd.api+json'}

    def request(self, method, path, body=None, missing=False):
        if not path.startswith('/') or '?' in path or '..' in path:
            raise ValueError('Invalid HCP API path.')
        try:
            response = httpx.request(method, API + path, headers=self.headers, json=body, timeout=25, follow_redirects=False)
            if missing and response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise ValueError(f'HCP Terraform rejected this operation (HTTP {response.status_code}). Check project permissions, quota, and run status.')
            return response.json() if response.content else {}
        except httpx.HTTPError as exc:
            raise ValueError('HCP Terraform is temporarily unavailable. Reload to check the saved run; do not repeat an apply.') from exc

    @staticmethod
    def id(value, prefix):
        if not isinstance(value, str) or not re.fullmatch(prefix + r'-[\w-]+', value):
            raise ValueError('Invalid HCP resource identifier.')
        return value

    def workspace(self, name):
        if not re.fullmatch(r'mc-[a-f0-9]{32}', name):
            raise ValueError('Invalid MakeCloud workspace name.')
        project = self.request('GET', '/projects/' + self.project)['data']
        if project['attributes']['name'] != self.project_name:
            raise ValueError('The HCP project name changed. Update cloud trust before running Terraform.')
        path = '/organizations/' + quote(self.org, safe='') + '/workspaces/' + name
        found = self.request('GET', path, missing=True)
        if not found:
            found = self.request('POST', '/organizations/' + quote(self.org, safe='') + '/workspaces', {'data': {'type': 'workspaces', 'attributes': {'name': name, 'execution-mode': 'remote', 'auto-apply': False, 'global-remote-state': False, 'terraform-version': '1.9.8'}, 'relationships': {'project': {'data': {'id': self.project, 'type': 'projects'}}}}})
        row = found['data']
        if row['relationships']['project']['data']['id'] != self.project or row['attributes'].get('execution-mode') != 'remote' or row['attributes'].get('auto-apply'):
            raise ValueError('Workspace must belong to MakeCloud’s dedicated project, use remote execution, and require approval.')
        wid = self.id(row['id'], 'ws')
        if self.request('GET', '/workspaces/' + wid + '/varsets')['data']:
            raise ValueError('MakeCloud workspaces must not inherit variable sets or unrelated cloud credentials. Remove those assignments before running.')
        return wid

    def variables(self, workspace, values):
        workspace = self.id(workspace, 'ws')
        allowed = {'TFC_AWS_PROVIDER_AUTH', 'TFC_AWS_RUN_ROLE_ARN', 'TFC_GCP_PROVIDER_AUTH', 'TFC_GCP_PROJECT_NUMBER', 'TFC_GCP_WORKLOAD_POOL_ID', 'TFC_GCP_WORKLOAD_PROVIDER_ID', 'TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL', 'TFC_AZURE_PROVIDER_AUTH', 'TFC_AZURE_RUN_CLIENT_ID', 'ARM_SUBSCRIPTION_ID', 'ARM_TENANT_ID'}
        if set(values) - allowed:
            raise ValueError('Only workload identity settings may be stored in HCP Terraform.')
        rows = self.request('GET', '/workspaces/' + workspace + '/vars')['data']
        existing = {row['attributes']['key']: row for row in rows}
        # A generated workspace must not inherit unrelated static cloud secrets.
        if set(existing) - allowed:
            raise ValueError('This workspace contains unexpected variables. Remove unrelated credentials before connecting.')
        for key, value in values.items():
            attributes = {'key': key, 'value': str(value), 'category': 'env', 'sensitive': False, 'hcl': False}
            old = existing.get(key)
            if old and old['attributes'].get('value') == str(value):
                continue
            path = '/workspaces/' + workspace + '/vars' + ('/' + self.id(old['id'], 'var') if old else '')
            self.request('PATCH' if old else 'POST', path, {'data': {'type': 'vars', 'attributes': attributes}})

    def upload(self, workspace, code, checkpoint):
        cv = self.request('POST', '/workspaces/' + self.id(workspace, 'ws') + '/configuration-versions', {'data': {'type': 'configuration-versions', 'attributes': {'auto-queue-runs': False, 'speculative': False}}})['data']
        cid = self.id(cv['id'], 'cv')
        checkpoint(configuration_id=cid)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as tf:
            entry = tarfile.TarInfo('main.tf')
            content = code.encode()
            entry.size, entry.mode = len(content), 0o600
            tf.addfile(entry, io.BytesIO(content))
        self.transfer(cv['attributes']['upload-url'], 'PUT', archive.getvalue())
        return cid

    def transfer(self, url, method='GET', content=None):
        # These URLs come only from authenticated HCP responses. Never send the
        # API token to the signed upload/download host or follow redirects blindly.
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or not (host == 'app.terraform.io' or host.endswith(('.terraform.io', '.hashicorp.com', '.amazonaws.com'))):
            raise ValueError('HCP returned an unsupported transfer destination.')
        try:
            with httpx.stream(method, url, content=content, timeout=30, follow_redirects=False) as response:
                if response.status_code >= 400 or 300 <= response.status_code < 400:
                    raise ValueError('HCP configuration transfer failed. Check the saved run before retrying.')
                parts, total = [], 0
                for part in response.iter_bytes():
                    total += len(part)
                    if total > 10_000_000:
                        raise ValueError('This Terraform result exceeds MakeCloud’s beta size limit.')
                    parts.append(part)
                return b''.join(parts)
        except httpx.HTTPError as exc:
            raise ValueError('HCP configuration transfer is unavailable. Reload to check the saved operation.') from exc

    def create_run(self, workspace, config, marker, destroy=False, speculative=False):
        return self.request('POST', '/runs', {'data': {'type': 'runs', 'attributes': {'message': marker, 'is-destroy': destroy, 'plan-only': speculative, 'auto-apply': False}, 'relationships': {'workspace': {'data': {'type': 'workspaces', 'id': self.id(workspace, 'ws')}}, 'configuration-version': {'data': {'type': 'configuration-versions', 'id': self.id(config, 'cv')}}}}})['data']

    def run(self, rid):
        return self.request('GET', '/runs/' + self.id(rid, 'run'))['data']

    def find_run(self, workspace, config, marker):
        rows = self.request('GET', '/workspaces/' + self.id(workspace, 'ws') + '/runs')['data']
        return next((row for row in rows if row['attributes'].get('message') == marker and row['relationships']['configuration-version']['data']['id'] == config), None)

    def assert_run(self, row, workspace, config):
        rel = row['relationships']
        if rel['workspace']['data']['id'] != workspace or rel['configuration-version']['data']['id'] != config:
            raise ValueError('This run does not match the saved MakeCloud project configuration.')

    def plan_json(self, row):
        pid = self.id(row['relationships']['plan']['data']['id'], 'plan')
        try:
            response = httpx.get(API + '/plans/' + pid + '/json-output', headers=self.headers, timeout=25, follow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                return json.loads(self.transfer(response.headers['location']))
            response.raise_for_status()
            if len(response.content) > 10_000_000:
                raise ValueError('Terraform plan exceeds the beta size limit.')
            return response.json()
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError('The remote plan details are unavailable. Reload before approving.') from exc

    def outputs(self, workspace):
        data = self.request('GET', '/workspaces/' + self.id(workspace, 'ws') + '/current-state-version', missing=True)
        if not data:
            return {}
        sid = self.id(data['data']['id'], 'sv')
        rows = self.request('GET', '/state-versions/' + sid + '/outputs')['data']
        return {row['attributes']['name']: {'value': '[sensitive]' if row['attributes'].get('sensitive') or run_cloud.SECRET.search(row['attributes']['name']) else row['attributes'].get('value') if isinstance(row['attributes'].get('value'), (str, int, float, bool, type(None))) else '[complex output]', 'sensitive': bool(row['attributes'].get('sensitive') or run_cloud.SECRET.search(row['attributes']['name']))} for row in rows}


def begin_plan(item, connection, checkpoint, destroy=False):
    run_cloud.check_policy(item['code'], item['spec']['cloud'])
    code = run_cloud.account_guard(item['code'], item['spec']['cloud'], connection.get('account_id'), connection.get('boundary_arn'))
    client = Client()
    wid = client.workspace(connection['workspace_name'])
    checkpoint(workspace_id=wid, digest=run_cloud.digest(item['code']), configuration_digest=run_cloud.digest(code))
    cid = client.upload(wid, code, checkpoint)
    checkpoint(run_requested=True)
    row = client.create_run(wid, cid, 'MakeCloud job ' + item['job']['id'], destroy)
    checkpoint(run_id=client.id(row['id'], 'run'))
    return row


def review(item, row):
    remote = item['job']['remote']
    client = Client()
    client.assert_run(row, remote['workspace_id'], remote['configuration_id'])
    if run_cloud.digest(item['code']) != remote['digest']:
        raise ValueError('The configuration changed. Create and review a new plan.')
    details = client.plan_json(row)
    result = run_cloud.summarize(details)
    result.update(runner='hcp', run_id=row['id'], workspace_id=remote['workspace_id'], configuration_id=remote['configuration_id'], digest=remote['digest'], configuration_digest=remote['configuration_digest'], plan_digest=hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest(), purpose='destroy' if item['job']['kind'] == 'destroy' else 'deploy')
    return result


def apply(approved, code):
    if run_cloud.digest(code) != approved['digest']:
        raise ValueError('The configuration changed. Create a fresh plan.')
    client = Client()
    row = client.run(approved['run_id'])
    client.assert_run(row, approved['workspace_id'], approved['configuration_id'])
    if row['attributes']['status'] not in READY or not row['attributes'].get('actions', {}).get('is-confirmable'):
        raise ValueError('The HCP run is no longer available to approve. Reload and review its status.')
    details = client.plan_json(row)
    if hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest() != approved['plan_digest']:
        raise ValueError('The remote plan changed. Create and review a fresh plan.')
    client.request('POST', '/runs/' + client.id(row['id'], 'run') + '/actions/apply', {'comment': 'Approved through MakeCloud after reviewing this exact run.'})


def cancel(remote):
    client = Client()
    row = client.run(remote['run_id'])
    client.assert_run(row, remote['workspace_id'], remote['configuration_id'])
    actions = row['attributes'].get('actions', {})
    action = 'cancel' if actions.get('is-cancelable') else 'discard' if actions.get('is-discardable') else None
    if not action:
        raise ValueError('HCP cannot safely cancel this run at its current stage. Continue checking its status.')
    client.request('POST', '/runs/' + row['id'] + '/actions/' + action, {'comment': 'Cancellation requested in MakeCloud.'})
