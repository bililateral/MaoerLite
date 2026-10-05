"""Durable task snapshots, summaries and deletion tombstones; no graph execution."""
import hashlib
import json
import sqlite3


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, conversation TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS agent_conversation ON agent_runs(conversation)')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_deleted_conversations (id_hash TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_summaries (conversation TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()

    def get(self, run_id):
        row = self.db.execute('SELECT body FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if row is None: raise KeyError(run_id)
        return json.loads(row[0])

    def put(self, run):
        run['version'] = run.get('version', 0) + 1
        # Keep creation order stable when an older task is retried or updated.
        self.db.execute('INSERT INTO agent_runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                        (run['id'], run['conversation_id'], json.dumps(run, ensure_ascii=False)))
        self.db.commit()
        return run

    def update(self, run_id, **fields):
        return self.put({**self.get(run_id), **fields})

    def conversation(self, conversation):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT body FROM agent_runs WHERE conversation=? ORDER BY rowid', (conversation,))]

    def deleted(self, conversation):
        digest = hashlib.sha256(conversation.encode()).hexdigest()
        return self.db.execute('SELECT 1 FROM agent_deleted_conversations WHERE id_hash=?', (digest,)).fetchone() is not None

    def mark_deleted(self, conversation):
        # Keep no message content, only a fingerprint that rejects stale replays.
        digest = hashlib.sha256(conversation.encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO agent_deleted_conversations VALUES (?)', (digest,))
        self.db.commit()

    def remove_conversation(self, conversation):
        self.db.execute('DELETE FROM agent_runs WHERE conversation=?', (conversation,))
        self.db.execute('DELETE FROM agent_summaries WHERE conversation=?', (conversation,))
        self.db.commit()

    def summary(self, conversation):
        row = self.db.execute('SELECT body FROM agent_summaries WHERE conversation=?', (conversation,)).fetchone()
        return json.loads(row[0]) if row else {'data': {}, 'covered': []}

    def save_summary(self, conversation, value):
        self.db.execute('INSERT OR REPLACE INTO agent_summaries VALUES (?,?)', (conversation, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def conversations(self):
        return [r[0] for r in self.db.execute('SELECT DISTINCT conversation FROM agent_runs')]

    def runs(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM agent_runs').fetchall()]

    def close(self):
        self.db.close()

    @staticmethod
    def public(run):
        return {k: v for k, v in run.items() if k not in ('history', 'receipts', 'input', 'memory', 'batch', 'tool_ledger', 'summary_error')}
