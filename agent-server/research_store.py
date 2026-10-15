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

    def state(self, key):
        with self.lock:
            row = self.db.execute('SELECT body FROM research_state WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else {}

    def save_state(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO research_state VALUES(?,?)', (key, json.dumps(value)))

    def close(self):
        self.db.close()
