"""Encrypted projects, expiring login sessions and transaction-safe persistence."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

class PostgresConnection:
    """Keep the small Store API portable; always close idle Neon connections."""
    def __init__(self, url):
        import psycopg
        self.db = psycopg.connect(url, connect_timeout=15, client_encoding='UTF8')

    def execute(self, sql, params=None):
        import psycopg
        try:
            return self.db.execute(sql.replace('?', '%s'), params)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError('Database constraint violated.') from exc

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.db.rollback() if kind else self.db.commit()
        finally:
            self.db.close()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        configured = os.getenv('MAKECLOUD_ENCRYPTION_KEY')
        self.database_url = os.getenv('MAKECLOUD_DATABASE_URL')
        if self.database_url and not configured:
            raise RuntimeError('Remote storage requires a stable MAKECLOUD_ENCRYPTION_KEY.')
        key_path = self.root / 'encryption.key'
        if not configured:
            if not key_path.exists():
                try:
                    with key_path.open('xb') as file:
                        file.write(Fernet.generate_key())
                    key_path.chmod(0o600)
                except FileExistsError:
                    pass
            configured = key_path.read_bytes()
        self.cipher = Fernet(configured)
        self.path = self.root / 'sessions.db'
        with self.connect() as db:
            if not self.database_url:
                db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS logins (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, started DOUBLE PRECISION NOT NULL, count INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS challenges (token TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, sid TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)')
            if self.database_url:
                db.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS verified INTEGER NOT NULL DEFAULT 1')
            elif 'verified' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 1')

    def connect(self):
        return PostgresConnection(self.database_url) if self.database_url else sqlite3.connect(self.path, timeout=15)

    def begin(self, db):
        # Serializes short compound transactions across requests, without an
        # always-open connection that would consume free database compute.
        db.execute('SELECT pg_advisory_xact_lock(193640172)' if self.database_url else 'BEGIN IMMEDIATE')

    @staticmethod
    def upsert(db, table, columns, values, key):
        fields = ','.join(columns)
        updates = ','.join(f'{column}=excluded.{column}' for column in columns if column != key)
        db.execute(f"INSERT INTO {table} ({fields}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT ({key}) DO UPDATE SET {updates}", values)

    def encode(self, item):
        return self.cipher.encrypt(json.dumps(item).encode()).decode()

    def decode(self, data):
        if data.startswith('{'):
            return json.loads(data)  # Compatibility with the previous release.
        try:
            return json.loads(self.cipher.decrypt(data.encode()))
        except InvalidToken as exc:
            raise RuntimeError('Cannot decrypt project data. Restore the correct encryption key.') from exc

    def save(self, item):
        item['updated_at'] = time.time()
        with self.connect() as db:
            self.upsert(db, 'sessions', ('id', 'data'), (item['id'], self.encode(item)), 'id')

    def get(self, sid):
        with self.connect() as db:
            row = db.execute('SELECT data FROM sessions WHERE id=?', (sid,)).fetchone()
        return self.decode(row[0]) if row else None

    def all(self):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM sessions').fetchall()
        return [self.decode(row[0]) for row in rows]

    def has_plaintext_projects(self):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM sessions WHERE data LIKE '{%' LIMIT 1").fetchone() is not None

    def compact_migrated_pages(self):
        if self.database_url:
            return
        # Encrypted replacements alone leave old plaintext in SQLite free pages/WAL.
        with self.connect() as db:
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def limit(self, key, count, seconds):
        with self.connect() as db:
            self.begin(db)
            row = db.execute('SELECT started,count FROM rate_limits WHERE key=?', (key,)).fetchone()
            start, used = row if row and time.time() - row[0] < seconds else (time.time(), 0)
            if used >= count:
                return False
            self.upsert(db, 'rate_limits', ('key', 'started', 'count'), (key, start, used + 1), 'key')
            db.execute('DELETE FROM rate_limits WHERE started<?', (time.time() - 86400,))
        return True

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    def register(self, email, password, verified=True):
        user_id, salt = secrets.token_hex(16), secrets.token_hex(16)
        hashed = self.password_hash(password, salt)
        with self.connect() as db:
            self.begin(db)
            cap = int(os.getenv('MAKECLOUD_MAX_ACCOUNTS', '0'))
            if cap and db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= cap:
                raise ValueError('This private beta has reached its account limit.')
            db.execute('INSERT INTO users (id,email,salt,password,verified) VALUES (?,?,?,?,?)', (user_id, email, salt, hashed, int(verified)))
        return {'id': user_id, 'email': email}

    def login(self, email, password):
        with self.connect() as db:
            row = db.execute('SELECT id,email,salt,password,verified FROM users WHERE email=?', (email,)).fetchone()
        # Run scrypt for unknown emails too.
        actual = self.password_hash(password, row[2] if row else '00' * 16)
        if not row or not secrets.compare_digest(actual, row[3]) or not row[4]:
            return None
        token = secrets.token_urlsafe(48)
        with self.connect() as db:
            db.execute('DELETE FROM logins WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO logins VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row[0], time.time() + 86400))
        return token, {'id': row[0], 'email': row[1]}

    def user(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute('SELECT users.id,users.email FROM logins JOIN users ON users.id=logins.user_id WHERE token=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return {'id': row[0], 'email': row[1]} if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM logins WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def vault_save(self, item, kind='connection'):
        with self.connect() as db:
            self.upsert(db, 'vault', ('id', 'owner', 'kind', 'data'), (item['id'], item['owner'], kind, self.encode(item)), 'id')

    def vault_get(self, key, owner, kind='connection'):
        with self.connect() as db:
            row = db.execute('SELECT data FROM vault WHERE id=? AND owner=? AND kind=?', (key, owner, kind)).fetchone()
        return self.decode(row[0]) if row else None

    def claim_connection(self, cid, owner, sid):
        with self.connect() as db:
            self.begin(db)
            saved = db.execute('SELECT data FROM vault WHERE id=? AND owner=? AND kind=?', (cid, owner, 'connection')).fetchone()
            if not saved:
                return False
            row = self.decode(saved[0])
            if row.get('makecloud_project_id') not in (None, sid):
                return False
            row['makecloud_project_id'] = sid
            self.upsert(db, 'vault', ('id', 'owner', 'kind', 'data'), (cid, owner, 'connection', self.encode(row)), 'id')
        return True

    def vault_all(self, owner, kind='connection'):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM vault WHERE owner=? AND kind=?', (owner, kind)).fetchall()
        return [self.decode(row[0]) for row in rows]

    def challenge(self, owner, kind, data=None, lifetime=600):
        token = secrets.token_urlsafe(40)
        with self.connect() as db:
            db.execute('DELETE FROM challenges WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO challenges VALUES (?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), owner, kind, time.time() + lifetime, self.encode(data or {})))
        return token

    def consume(self, token, kind, owner=None, validate=None):
        with self.connect() as db:
            self.begin(db)
            key = hashlib.sha256(token.encode()).hexdigest()
            row = db.execute('SELECT owner,expires,data FROM challenges WHERE token=? AND kind=?', (key, kind)).fetchone()
            if not row or row[1] < time.time() or owner is not None and row[0] != owner:
                return None
            value = self.decode(row[2])
            if validate and not validate(value):
                return None
            db.execute('DELETE FROM challenges WHERE token=?', (key,))
        return row[0], value

    def account_by_email(self, email):
        with self.connect() as db:
            row = db.execute('SELECT id,email,verified FROM users WHERE email=?', (email,)).fetchone()
        return dict(zip(('id', 'email', 'verified'), row)) if row else None

    def verify(self, owner):
        with self.connect() as db:
            db.execute('UPDATE users SET verified=1 WHERE id=?', (owner,))

    def reset_password(self, owner, password):
        salt = secrets.token_hex(16)
        hashed = self.password_hash(password, salt)
        with self.connect() as db:
            db.execute('UPDATE users SET salt=?,password=? WHERE id=?', (salt, hashed, owner))
            db.execute('DELETE FROM logins WHERE user_id=?', (owner,))

    def enqueue(self, item, job):
        item['updated_at'] = time.time()
        with self.connect() as db:
            self.upsert(db, 'sessions', ('id', 'data'), (item['id'], self.encode(item)), 'id')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?)', (job['id'], item['id'], 'queued', self.encode(job)))

    def claim(self, job_id):
        with self.connect() as db:
            self.begin(db)
            row = db.execute("SELECT data FROM jobs WHERE id=? AND status='queued'", (job_id,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        return self.decode(row[0])

    def queued(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id")]

    def finish(self, job_id, status):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job_id))
