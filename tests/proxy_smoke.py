"""Exercise the built Next.js app and real backend together, without cloud access."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as data, tempfile.TemporaryFile(mode='w+') as logs:
    environment = {**os.environ, 'MAKECLOUD_MODE': 'demo', 'MAKECLOUD_DATA_DIR': data,
                   'MAKECLOUD_BACKEND_URL': 'http://127.0.0.1:5101',
                   'MAKECLOUD_API_TOKEN': '', 'MAKECLOUD_OPERATOR_PASSWORD': '', 'MAKECLOUD_LOCAL_ONLY': 'true'}
    backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '5101'], cwd=ROOT, env=environment, stdout=logs, stderr=logs)
    frontend = subprocess.Popen(['node', 'node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '3101'], cwd=ROOT, env=environment, stdout=logs, stderr=logs)
    try:
        base = 'http://127.0.0.1:3101'
        with httpx.Client(timeout=30, trust_env=False) as client:
            for attempt in range(100):
                try:
                    if client.get(base+'/api/cloud/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
            else:
                logs.seek(0)
                raise AssertionError('Servers failed to start: '+logs.read())
            response = client.get(base)
            assert response.status_code == 200 and 'MakeCloud' in response.text
            for provider, region, extra, resource in [
                ('AWS', 'us-east-1', 'none', 'aws_s3_bucket'),
                ('Azure', 'eastus', '11111111-1111-1111-1111-111111111111', 'azurerm_storage_account'),
                ('GCP', 'us-central1', 'my-cloud-project', 'google_storage_bucket')]:
                item = client.post(base+'/api/cloud/sessions', json={'message': 'Private storage bucket'}).json()
                endpoint = base+'/api/cloud/sessions/'+item['id']
                for answer in [provider, region, 'testproject', 'Budget $10; private', extra]:
                    response = client.post(endpoint+'/messages', json={'message': answer})
                    assert response.status_code == 200, response.text
                    item = response.json()
                assert item['status'] == 'draft' and resource in item['code'], item
                assert client.get(endpoint).json()['code'] == item['code']
                assert client.put(endpoint+'/code', json={'code': item['code']+'\n# reviewed'}).json()['status'] == 'draft'
                assert client.post(endpoint+'/plan').status_code == 409
                print(provider+': Next.js proxy -> chat -> Terraform -> edit -> reload -> deployment block PASS')
            assert client.post(base+'/api/cloud/sessions', headers={'Origin': 'https://attacker.example'}, json={'message': 'storage'}).status_code == 403
            assert client.get(base+'/api/cloud/not-allowed').status_code == 404
            assert client.get(base, headers={'Host': 'untrusted.example'}).status_code == 401
            backend.terminate()
            backend.wait(timeout=10)
            assert client.get(base+'/api/cloud/health').status_code == 503
            print('Home render, CSRF rejection, route allowlist, remote auth, backend downtime PASS')
            frontend.terminate()
            frontend.wait(timeout=10)
            environment['MAKECLOUD_OPERATOR_PASSWORD'] = 'smoke-only-test-password'
            frontend = subprocess.Popen(['node', 'node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '3101'], cwd=ROOT, env=environment, stdout=logs, stderr=logs)
            for attempt in range(100):
                try:
                    if client.get(base).status_code == 401:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
            else:
                raise AssertionError('Protected frontend did not start.')
            assert client.get(base, auth=('operator', 'wrong')).status_code == 401
            assert client.get(base, auth=('operator', 'smoke-only-test-password')).status_code == 200
            print('Operator password: missing/wrong credentials blocked; correct credentials accepted PASS')
            frontend.terminate()
            frontend.wait(timeout=10)
            environment['MAKECLOUD_OPERATOR_PASSWORD'] = ''
            environment['MAKECLOUD_LOCAL_ONLY'] = 'false'
            frontend = subprocess.Popen(['node', 'node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '3101'], cwd=ROOT, env=environment, stdout=logs, stderr=logs)
            for attempt in range(100):
                try:
                    if client.get(base).status_code == 401:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
            else:
                raise AssertionError('Remote-mode frontend did not fail closed.')
            assert client.get(base, headers={'Host': 'localhost'}).status_code == 401
            print('Remote hosting without password fails closed, including spoofed localhost Host PASS')


    finally:
        for process in (frontend, backend):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
