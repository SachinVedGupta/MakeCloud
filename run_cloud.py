"""Restricted Terraform execution, redacted review, and exact saved-plan apply."""
import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
import tempfile
import uuid
from pathlib import Path
import hcl2

PROVIDERS = {'aws': 'hashicorp/aws', 'google': 'hashicorp/google', 'azurerm': 'hashicorp/azurerm'}
SECRET = re.compile(r'password|secret|credential|token|private.?key|connection.?string|access.?key', re.I)
SAFE_ATTRIBUTES = {'name', 'id', 'arn', 'region', 'location', 'project', 'subscription_id', 'instance_class', 'engine', 'engine_version', 'allocated_storage', 'max_allocated_storage', 'storage_encrypted', 'publicly_accessible', 'multi_az', 'backup_retention_period', 'deletion_protection', 'skip_final_snapshot', 'force_destroy', 'tags', 'labels', 'cidr_block', 'cidr_blocks', 'from_port', 'to_port', 'protocol', 'ingress', 'egress', 'public_access_prevention', 'uniform_bucket_level_access', 'versioning', 'versioning_configuration', 'status', 'enabled', 'block_public_acls', 'block_public_policy', 'ignore_public_acls', 'restrict_public_buckets', 'desired_count', 'image', 'cpu', 'memory', 'port', 'target_port', 'external_enabled', 'allow_insecure_connections', 'min_replicas', 'max_replicas', 'member', 'role', 'internal', 'viewer_protocol_policy', 'account_replication_type', 'min_tls_version', 'shared_access_key_enabled', 'allow_nested_items_to_be_public'}


def check_policy(code, provider):
    if provider not in PROVIDERS:
        raise ValueError('Select AWS, Azure or Google Cloud.')
    if not code.strip() or len(code) > 100000:
        raise ValueError('Terraform must contain between 1 and 100,000 characters.')
    try:
        document = hcl2.load(io.StringIO(code))
    except Exception as exc:
        raise ValueError('Invalid HCL syntax. Check the Terraform editor.') from exc
    if set(document) - {'terraform', 'provider', 'resource', 'data', 'variable', 'output', 'locals'}:
        raise ValueError('Unsupported Terraform block; modules and external execution are not allowed.')
    forbidden = {'provisioner', 'connection', 'backend', 'cloud', 'access_key', 'secret_key', 'access_token', 'credentials', 'client_secret', 'password', 'master_password', 'administrator_login_password', 'private_key', 'private_key_pem', 'secret_access_key'}
    def scan(value):
        if isinstance(value, dict):
            if set(value) & forbidden:
                raise ValueError('Credentials, password arguments, remote backends and provisioners are not allowed. Use managed identity and secrets.')
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)
        elif isinstance(value, str) and re.search(r'\b(?:file[a-z0-9_]*|templatefile|pathexpand)\s*\(', value, re.I):
            raise ValueError('Local file access is not allowed in generated Terraform.')
    scan(document)
    providers = document.get('provider', [])
    if len(providers) != 1 or set(providers[0]) != {provider}:
        raise ValueError('Use exactly one provider matching the selected cloud.')
    allowed = {'aws': {'region'}, 'google': {'region', 'project', 'zone'}, 'azurerm': {'features', 'subscription_id'}}[provider]
    if set(providers[0][provider]) - allowed:
        raise ValueError('Unsupported provider configuration; configure cloud identity outside Terraform.')
    tf = document.get('terraform', [])
    required = tf[0].get('required_providers', []) if len(tf) == 1 else []
    if len(required) != 1 or set(required[0]) != {provider}:
        raise ValueError('Declare exactly the selected cloud in required_providers.')
    definition = required[0][provider]
    if not isinstance(definition, dict) or definition.get('source') != PROVIDERS[provider] or not isinstance(definition.get('version'), str) or not definition['version']:
        raise ValueError('Use a version-constrained official HashiCorp provider.')
    if not tf[0].get('required_version'):
        raise ValueError('Declare the supported Terraform version.')
    if not document.get('resource'):
        raise ValueError('At least one cloud resource is required.')
    for kind in ('resource', 'data'):
        for block in document.get(kind, []):
            if any(not name.startswith(provider + '_') for name in block):
                raise ValueError('Resources and data must belong to the selected cloud.')
    return document


def digest(code):
    return hashlib.sha256(code.encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def account_guard(code, provider, account_id, boundary=None):
    if provider != 'aws' or not account_id:
        return code
    if not re.fullmatch(r'[0-9]{12}', account_id):
        raise ValueError('The AWS connection must declare a valid account ID.')
    document = hcl2.loads(code, with_meta=True)
    document['provider'][0]['aws']['allowed_account_ids'] = [account_id]
    if boundary:
        for block in document.get('resource', []):
            for role in block.get('aws_iam_role', {}).values():
                role['path'] = '/makecloud/'
                role['permissions_boundary'] = boundary
    return hcl2.writes(hcl2.reverse_transform(document))


class Cancelled(ValueError):
    pass


def command(directory, *args, environment=None, progress=None, cancelled=None):
    if progress:
        progress('terraform_' + args[0])
    env = dict(os.environ if environment is None else environment)
    env.update(TF_IN_AUTOMATION='1', TF_INPUT='0')
    for key in list(env):
        if key.startswith(('OPENAI_', 'MAKECLOUD_')) or key.startswith('INFRACOST_'):
            env.pop(key, None)
    runner = os.getenv('MAKECLOUD_RUNNER', 'local')
    container_name = None
    if runner == 'docker':
        executable = shutil.which('docker')
        if not executable:
            raise ValueError('The isolated Docker runner is unavailable.')
        container_name = 'makecloud-' + uuid.uuid4().hex
        argv = [executable, 'run', '--name', container_name, '--rm', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128', '--memory=1g', '--cpus=1', '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--mount', f'type=bind,src={directory.resolve()},dst=/workspace', '--workdir', '/workspace']
        for key in env:
            if key.startswith(('AWS_', 'ARM_', 'GOOGLE_', 'TF_')):
                argv += ['--env', key]
        argv += [os.getenv('MAKECLOUD_TERRAFORM_IMAGE', 'hashicorp/terraform:1.9.8'), *args]
    else:
        executable = shutil.which('terraform')
        if not executable:
            raise ValueError('Terraform is not installed on the backend.')
        argv = [executable, *args]
    process = subprocess.Popen(argv, cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    started = time.monotonic()
    try:
        while True:
            if cancelled and cancelled.is_set():
                raise Cancelled('Operation canceled. An apply may have created resources; inspect state before continuing.')
            if time.monotonic() - started > int(os.getenv('MAKECLOUD_COMMAND_TIMEOUT', '300')):
                raise ValueError('Terraform timed out. Inspect retained state before continuing.')
            try:
                stdout, stderr = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        if container_name:
            subprocess.run([executable, 'rm', '-f', container_name], capture_output=True, timeout=15)
        raise
    if process.returncode:
        (directory / 'last-error.log').write_text(stdout + stderr)
        raise ValueError(f'Terraform {args[0]} failed. Inspect the protected workspace log, correct identity or configuration, then retry.')
    return stdout


def redact(value, sensitive=False):
    if sensitive is True:
        return '[sensitive]'
    if isinstance(value, dict):
        masks = sensitive if isinstance(sensitive, dict) else {}
        return {k: '[sensitive]' if SECRET.search(k) else redact(v, masks.get(k, False)) for k, v in value.items() if k in SAFE_ATTRIBUTES or isinstance(sensitive, dict) and k in sensitive}
    if isinstance(value, list):
        masks = sensitive if isinstance(sensitive, list) else []
        return [redact(v, masks[i] if i < len(masks) else False) for i, v in enumerate(value[:30])]
    if isinstance(value, str):
        return value[:500]
    return value


def summarize(details):
    changes, risks = [], []
    for item in details.get('resource_changes', []):
        change = item['change']
        if change['actions'] in (['no-op'], ['read']):
            continue
        before = redact(change.get('before') or {}, change.get('before_sensitive') or {})
        after = redact(change.get('after') or {}, change.get('after_sensitive') or {})
        keys = set(before) | set(after)
        attributes = [{'name': k, 'before': before.get(k), 'after': after.get(k)} for k in sorted(keys) if before.get(k) != after.get(k)]
        changes.append({'address': item['address'], 'type': item.get('type', ''), 'actions': change['actions'], 'attributes': attributes[:40]})
        if 'delete' in change['actions']:
            risks.append('Resources will be deleted or replaced. Back up data and review retention settings.')
        if item.get('type', '').startswith(('aws_iam_', 'google_project_iam_', 'azurerm_role_')):
            risks.append('Identity or permissions will change. Review scope and least privilege.')
        raw = json.dumps(change.get('after') or {})
        if '0.0.0.0/0' in raw or '::/0' in raw or 'allUsers' in raw or (change.get('after') or {}).get('publicly_accessible'):
            risks.append('This plan contains public network access or public principals. Review the affected resources.')
    return {'changes': changes, 'creates': sum('create' in c['actions'] for c in changes), 'updates': sum('update' in c['actions'] for c in changes), 'deletes': sum('delete' in c['actions'] for c in changes), 'risks': sorted(set(risks)), 'cost': {'status': 'unavailable', 'monthly': None, 'currency': 'USD', 'message': 'No measured pricing estimate is available. Inspect provider pricing before approving.'}}


def plan(directory, code, provider, *, destroy=False, environment=None, progress=None, cancelled=None, expected_account=None, expected_boundary=None):
    check_policy(code, provider)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    (directory / 'main.tf').write_text(account_guard(code, provider, expected_account, expected_boundary))
    (directory / 'approved.tfplan').unlink(missing_ok=True)
    options = dict(environment=environment, progress=progress, cancelled=cancelled)
    command(directory, 'fmt', '-no-color', **options)
    command(directory, 'init', '-input=false', '-no-color', **options)
    command(directory, 'validate', '-no-color', **options)
    args = ['plan', '-input=false', '-no-color', '-out=approved.tfplan']
    if destroy:
        args.append('-destroy')
    try:
        command(directory, *args, **options)
        details = json.loads(command(directory, 'show', '-json', 'approved.tfplan', **options))
        result = summarize(details)
        result['cost'] = estimate(directory, details)
        result.update(digest=digest(code), configuration_digest=file_digest(directory / 'main.tf'), plan_digest=file_digest(directory / 'approved.tfplan'), purpose='destroy' if destroy else 'deploy')
        return result
    except Exception:
        (directory / 'approved.tfplan').unlink(missing_ok=True)
        raise


def estimate(directory, details):
    unavailable = {'status': 'unavailable', 'monthly': None, 'currency': 'USD', 'message': 'No measured pricing estimate is available. Inspect provider pricing before approving.'}
    executable = shutil.which('infracost')
    key = os.getenv('INFRACOST_API_KEY')
    modern_token = os.getenv('INFRACOST_CLI_AUTHENTICATION_TOKEN')
    if executable and modern_token:
        return modern_estimate(executable, modern_token, directory, details, unavailable)
    if not executable or not key:
        return unavailable
    source, destination = directory / 'pricing-input.private.json', directory / 'pricing-output.private.json'
    try:
        source.write_text(json.dumps(details))
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'SSL_CERT_FILE') if k in os.environ}
        env['INFRACOST_API_KEY'] = key
        result = subprocess.run([executable, 'breakdown', '--path', str(source), '--format', 'json', '--out-file', str(destination), '--no-color'], env=env, capture_output=True, text=True, timeout=90)
        if result.returncode:
            return {**unavailable, 'message': 'Pricing estimation failed. The Terraform plan is retained; inspect provider pricing before approving.'}
        report = json.loads(destination.read_text())
        monthly = float(report['totalMonthlyCost'])
        if not math.isfinite(monthly) or monthly < 0 or monthly > 1e12:
            raise ValueError()
        return {'status': 'estimated', 'monthly': monthly, 'currency': report.get('currency', 'USD'), 'message': 'Infracost estimate for configured resources. Usage, egress, taxes and unsupported resources may add charges; this is not a spending cap.'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.TimeoutExpired):
        return unavailable
    finally:
        source.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)


def modern_estimate(executable, token, directory, details, unavailable):
    # CLI v2 caches scan data; keep HOME, cache, input and report in an ephemeral directory.
    try:
        with tempfile.TemporaryDirectory(prefix='pricing-private-', dir=directory) as temporary:
            root = Path(temporary)
            source = root / 'plan.json'
            source.write_text(json.dumps(details))
            env = {k: os.environ[k] for k in ('PATH', 'LANG', 'SSL_CERT_FILE') if k in os.environ}
            env.update(HOME=str(root), XDG_CACHE_HOME=str(root / 'cache'), INFRACOST_CLI_AUTHENTICATION_TOKEN=token)
            scan = subprocess.run([executable, 'scan', str(source), '--currency', 'USD', '--no-color'], cwd=root, env=env, capture_output=True, text=True, timeout=90)
            if scan.returncode:
                return {**unavailable, 'message': 'Pricing estimation failed. Inspect provider pricing before approving.'}
            result = subprocess.run([executable, 'inspect', '--summary', '--json', '--no-color'], cwd=root, env=env, capture_output=True, text=True, timeout=30)
            if result.returncode:
                return unavailable
            report = json.loads(result.stdout)
            monthly = float(report['monthly_cost'])
            if not math.isfinite(monthly) or monthly < 0 or monthly > 1e12:
                return unavailable
            return {'status': 'estimated', 'monthly': monthly, 'currency': 'USD', 'message': 'Infracost estimate for configured resources. Usage, egress, taxes and unsupported resources may add charges; this is not a spending cap.'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.TimeoutExpired):
        return unavailable


def apply(directory, code, provider, approved, *, environment=None, progress=None, cancelled=None):
    check_policy(code, provider)
    saved = directory / 'approved.tfplan'
    if digest(code) != approved['digest'] or not saved.exists() or file_digest(saved) != approved['plan_digest'] or approved.get('configuration_digest') and file_digest(directory / 'main.tf') != approved['configuration_digest']:
        raise ValueError('The configuration or saved plan changed. Create and review a new plan.')
    command(directory, 'apply', '-input=false', '-no-color', 'approved.tfplan', environment=environment, progress=progress, cancelled=cancelled)
    saved.unlink(missing_ok=True)
    try:
        outputs = json.loads(command(directory, 'output', '-json', environment=environment, progress=progress, cancelled=cancelled))
        return {name: {'value': '[sensitive]' if item.get('sensitive') or SECRET.search(name) else (item.get('value') if isinstance(item.get('value'), (str, int, float, bool, type(None))) else '[complex output]'), 'sensitive': bool(item.get('sensitive') or SECRET.search(name))} for name, item in outputs.items()}
    except ValueError:
        return {}  # Apply succeeded; output retrieval failure is not a failed deployment.


def seal(directory, cipher):
    if not directory.exists():
        return
    for pattern in ('*.tfstate', '*.tfstate.backup', '*.tfplan', '*.log'):
        for path in directory.glob(pattern):
            target = path.with_name(path.name + '.encrypted')
            temporary = target.with_name(target.name + '.next')
            temporary.write_bytes(cipher.encrypt(path.read_bytes()))
            os.replace(temporary, target)
            path.unlink()


def unseal(directory, cipher):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in directory.glob('*.encrypted'):
        target = path.with_name(path.name.removesuffix('.encrypted'))
        target.write_bytes(cipher.decrypt(path.read_bytes()))
