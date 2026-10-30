"""Public-source cache and incremental embeddings, separate from conversation data."""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ResearchStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS feeds(id TEXT PRIMARY KEY, fetched INTEGER, body TEXT);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY, value BLOB);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, month TEXT, credits INTEGER);
            CREATE TABLE IF NOT EXISTS research_state(id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS research_state_scopes(scope TEXT, state_id TEXT, PRIMARY KEY(scope,state_id));
            CREATE INDEX IF NOT EXISTS research_state_scope_keys ON research_state_scopes(state_id);
        ''')

    def document(self, text):
        key = digest(text)
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO documents VALUES(?,?)', (key, text))
        return key

    def read(self, key):
        with self.lock:
            row = self.db.execute('SELECT body FROM documents WHERE id=?', (key,)).fetchone()
            return row[0] if row else ''

    def feed(self, key):
        with self.lock:
            row = self.db.execute('SELECT fetched,body FROM feeds WHERE id=?', (key,)).fetchone()
            return (row[0], json.loads(row[1])) if row else (0, [])

    def save_feed(self, key, fetched, rows):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO feeds VALUES(?,?,?)', (key, fetched, json.dumps(rows, ensure_ascii=False)))

    def vectors(self, keys):
        with self.lock:
            return {key: row[0] for key in keys if (row := self.db.execute('SELECT value FROM vectors WHERE id=?', (key,)).fetchone())}

    def save_vectors(self, pairs):
        with self.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO vectors VALUES(?,?)', pairs)

    def reserve(self, request_id, month, limit):
        # Reserve a full credit for either basic search or single-page extract;
        # unknown outcomes stay reserved. Checkpoints/retries cannot spend twice.
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM requests WHERE id=?', (request_id,)).fetchone(): return False
            used = self.db.execute('SELECT COALESCE(SUM(credits),0) FROM requests WHERE month=?', (month,)).fetchone()[0]
            if used >= limit: return False
            self.db.execute('INSERT INTO requests VALUES(?,?,1)', (request_id, month))
            return True

    def credits(self, month):
        with self.lock:
            return self.db.execute('SELECT COALESCE(SUM(credits),0) FROM requests WHERE month=?', (month,)).fetchone()[0]

    def reserved(self, request_id):
        with self.lock:
            return self.db.execute('SELECT 1 FROM requests WHERE id=?', (request_id,)).fetchone() is not None

    def state(self, key):
        with self.lock:
            row = self.db.execute('SELECT body FROM research_state WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else {}

    def save_state(self, key, value, *, scope=''):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO research_state VALUES(?,?)', (key, json.dumps(value)))
            if scope:
                self.db.execute('INSERT OR IGNORE INTO research_state_scopes VALUES(?,?)', (scope, key))

    def retain_state(self, key, scope):
        if not scope: return
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO research_state_scopes SELECT ?,id FROM research_state WHERE id=?', (scope, key))

    def state_scopes(self):
        with self.lock:
            return {row[0] for row in self.db.execute('SELECT DISTINCT scope FROM research_state_scopes')}

    def release_scope(self, scope):
        with self.lock, self.db:
            # Shared snapshots remain until their last owning conversation ends.
            self.db.execute('''DELETE FROM research_state WHERE id IN
                (SELECT state_id FROM research_state_scopes WHERE scope=?) AND id NOT IN
                (SELECT state_id FROM research_state_scopes WHERE scope<>?)''', (scope, scope))
            self.db.execute('DELETE FROM research_state_scopes WHERE scope=?', (scope,))

    def state_items(self, prefix):
        with self.lock:
            for key, body in self.db.execute('SELECT id,body FROM research_state WHERE substr(id,1,?)=?', (len(prefix), prefix)):
                yield key, json.loads(body)

    def remove_unowned_states(self, prefix):
        with self.lock, self.db:
            self.db.execute('''DELETE FROM research_state WHERE substr(id,1,?)=? AND id NOT IN
                (SELECT state_id FROM research_state_scopes)''', (len(prefix), prefix))

    def close(self):
        self.db.close()
