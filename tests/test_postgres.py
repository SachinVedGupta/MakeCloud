"""Real PostgreSQL persistence and concurrency, using a disposable local cluster."""
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
import psycopg
import pytest
from cryptography.fernet import Fernet
from store import Store


@pytest.fixture(scope='module')
def postgres_cluster():
    bindir = Path(os.getenv('MAKECLOUD_TEST_PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
    if not (bindir / 'initdb').exists():
        pytest.skip('A PostgreSQL server is required for real database tests.')
    with tempfile.TemporaryDirectory(prefix='mc-pg-', dir='/tmp') as tmp:
        root = Path(tmp)
        data, socket = root / 'data', root / 'socket'
        socket.mkdir(mode=0o700)
        subprocess.run([str(bindir / 'initdb'), '-D', str(data), '-U', 'makecloud', '-A', 'trust', '--no-locale', '-E', 'UTF8'], check=True, capture_output=True)
        options = f"-k {socket} -c listen_addresses='' -c unix_socket_permissions=0700"
        subprocess.run([str(bindir / 'pg_ctl'), '-D', str(data), '-l', str(root / 'server.log'), '-o', options, 'start', '-w'], check=True, capture_output=True)
        url = 'postgresql:///postgres?' + urlencode({'host': str(socket), 'user': 'makecloud'})
        try:
            yield url
        finally:
            subprocess.run([str(bindir / 'pg_ctl'), '-D', str(data), 'stop', '-m', 'fast', '-w'], check=True, capture_output=True)


@pytest.fixture
def pg_store(postgres_cluster, monkeypatch, tmp_path):
    name = 'test_' + uuid.uuid4().hex
    with psycopg.connect(postgres_cluster, autocommit=True) as db:
        db.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
    url = postgres_cluster.replace('/postgres?', '/' + name + '?')
    monkeypatch.setenv('MAKECLOUD_DATABASE_URL', url)
    monkeypatch.setenv('MAKECLOUD_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.delenv('MAKECLOUD_MAX_ACCOUNTS', raising=False)
    return Store(tmp_path), url


def test_encrypted_state_survives_loss_of_local_disk(pg_store, tmp_path):
    store, _ = pg_store
    item = {'id': 'project', 'owner': 'one', 'messages': [{'content': 'private conversation'}]}
    store.save(item)
    store.vault_save({'id': 'connection', 'owner': 'one', 'refresh_token': 'private-token'})
    second = Store(tmp_path / 'new-ephemeral-disk')
    assert second.get('project')['messages'] == item['messages']
    assert second.vault_get('connection', 'one')['refresh_token'] == 'private-token'
    with second.connect() as db:
        raw = db.execute('SELECT data FROM sessions').fetchone()[0]
        assert 'private conversation' not in raw
        assert db.execute('SELECT data FROM vault').fetchone()[0].startswith('gAAAA')


def test_single_winner_for_challenges_and_jobs(pg_store):
    store, _ = pg_store
    token = store.challenge('one', 'verify')
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: store.consume(token, 'verify', 'one'), range(5)))
    assert sum(value is not None for value in results) == 1
    store.enqueue({'id': 'project'}, {'id': 'job', 'sid': 'project', 'kind': 'plan'})
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: store.claim('job'), range(5)))
    assert sum(value is not None for value in results) == 1
    assert store.queued() == []


def test_rate_limits_and_connection_claims_are_atomic(pg_store):
    store, _ = pg_store
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.limit('key', 3, 600), range(8)))
    assert sum(results) == 3
    store.vault_save({'id': 'connection', 'owner': 'one'})
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda i: store.claim_connection('connection', 'one', 'project-' + str(i)), range(5)))
    assert sum(results) == 1
    assert not store.claim_connection('connection', 'another-owner', 'project')


def test_account_cap_and_login_revocation(pg_store, monkeypatch):
    store, _ = pg_store
    monkeypatch.setenv('MAKECLOUD_MAX_ACCOUNTS', '2')
    def register(i):
        try:
            return store.register(f'user{i}@example.com', 'long-test-password')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        accounts = [value for value in pool.map(register, range(5)) if value]
    assert len(accounts) == 2
    account = accounts[0]
    token, _ = store.login(account['email'], 'long-test-password')
    assert store.user(token)['id'] == account['id']
    store.reset_password(account['id'], 'new-long-test-password')
    assert store.user(token) is None
    assert store.login(account['email'], 'long-test-password') is None
    assert store.login(account['email'], 'new-long-test-password')


def test_duplicate_account_is_reported_without_database_details(pg_store):
    import sqlite3
    store, _ = pg_store
    store.register('one@example.com', 'long-test-password')
    with pytest.raises(sqlite3.IntegrityError, match='constraint'):
        store.register('one@example.com', 'long-test-password')
