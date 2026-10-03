"""Terraform policy and subprocess boundary. Never apply without a saved plan."""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import hcl2

PROVIDERS = {'aws': 'hashicorp/aws', 'google': 'hashicorp/google', 'azurerm': 'hashicorp/azurerm'}

def check_policy(code: str, provider: str):
    if not code.strip() or len(code) > 100_000:
        raise ValueError('Terraform must contain between 1 and 100,000 characters.')
    try:
        document = hcl2.load(io.StringIO(code))
    except Exception as exc:
        raise ValueError('Invalid HCL syntax. Check the Terraform editor.') from exc
    if set(document) - {'terraform', 'provider', 'resource', 'data', 'variable', 'output', 'locals'}:
        raise ValueError('Unsupported Terraform block.')
    # This is a restricted trusted-operator runner, not a sandbox for arbitrary HCL.
    forbidden_keys = {'provisioner', 'connection', 'backend', 'cloud', 'access_key', 'secret_key', 'access_token', 'credentials', 'client_secret', 'password'}
    def scan(value):
        if isinstance(value, dict):
            if set(value) & forbidden_keys:
                raise ValueError('Credentials, remote backends, provisioners, and inline passwords are not allowed. Use cloud identity outside chat.')
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)
        elif isinstance(value, str) and re.search(r'\b(file|filebase64|fileexists|fileset|templatefile|pathexpand)\s*\(', value):
            raise ValueError('Local file access is not allowed in generated Terraform.')
    scan(document)
    if len(document.get('provider', [])) != 1 or set(document['provider'][0]) != {provider}:
        raise ValueError('Use exactly one provider matching the selected cloud.')
    configs = document['provider'][0][provider]
    allowed = {'aws': {'region'}, 'google': {'region', 'project', 'zone'}, 'azurerm': {'features', 'subscription_id'}}[provider]
    if set(configs) - allowed:
        raise ValueError('Unsupported provider configuration; use environment credentials.')
    tf = document.get('terraform', [])
    required = tf[0].get('required_providers', []) if len(tf) == 1 else []
    if len(required) != 1 or set(required[0]) != {provider}:
        raise ValueError('Declare exactly the selected cloud in required_providers.')
    definition = required[0][provider]
    if definition.get('source') != PROVIDERS[provider] or not definition.get('version'):
        raise ValueError('Use a version-constrained official HashiCorp provider.')
    if not document.get('resource'):
        raise ValueError('At least one cloud resource is required.')
    for kind in ('resource', 'data'):
        for block in document.get(kind, []):
            if any(not name.startswith(provider + '_') for name in block):
                raise ValueError('Resources and data must belong to the selected cloud.')
    return document

def digest(code: str):
    return hashlib.sha256(code.encode()).hexdigest()

def command(directory: Path, *args: str):
    executable = shutil.which('terraform')
    if not executable:
        raise ValueError('Terraform is not installed on the backend. Install Terraform 1.5+ and retry.')
    environment = {**os.environ, 'TF_IN_AUTOMATION': '1', 'TF_INPUT': '0'}
    # Do not pass the AI API key or operator API token to provider plugins.
    for key in ('OPENAI_API_KEY', 'MAKECLOUD_API_TOKEN'):
        environment.pop(key, None)
    try:
        result = subprocess.run([executable, *args], cwd=directory, env=environment,
                                capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Terraform timed out. Inspect the workspace before retrying; cloud changes may have started.') from exc
    if result.returncode:
        # Raw logs may contain provider secrets. Keep them on the trusted server only.
        (directory / 'last-error.log').write_text(result.stdout + result.stderr)
        raise ValueError(f'Terraform {args[0]} failed. Inspect last-error.log in the protected workspace, fix configuration or credentials, then retry.')
    return result.stdout

def plan(directory: Path, code: str, provider: str):
    check_policy(code, provider)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    (directory / 'main.tf').write_text(code)
    (directory / 'approved.tfplan').unlink(missing_ok=True)
    command(directory, 'fmt', '-no-color')
    command(directory, 'init', '-input=false', '-no-color')
    command(directory, 'validate', '-no-color')
    command(directory, 'plan', '-input=false', '-no-color', '-out=approved.tfplan')
    details = json.loads(command(directory, 'show', '-json', 'approved.tfplan'))
    changes = [{'address': item['address'], 'actions': item['change']['actions']}
               for item in details.get('resource_changes', []) if item['change']['actions'] != ['no-op']]
    # Return only addresses/actions, never raw plan values.
    return {'changes': changes, 'creates': sum('create' in c['actions'] for c in changes),
            'updates': sum('update' in c['actions'] for c in changes),
            'deletes': sum('delete' in c['actions'] for c in changes),
            'digest': digest(code), 'plan_digest': hashlib.sha256((directory / 'approved.tfplan').read_bytes()).hexdigest()}

def apply(directory: Path, code: str, provider: str, approved: dict):
    check_policy(code, provider)
    saved = directory / 'approved.tfplan'
    if digest(code) != approved['digest'] or not saved.exists() or hashlib.sha256(saved.read_bytes()).hexdigest() != approved['plan_digest']:
        raise ValueError('The configuration or saved plan changed. Create and review a fresh plan.')
    command(directory, 'apply', '-input=false', '-no-color', 'approved.tfplan')
    saved.unlink(missing_ok=True)
