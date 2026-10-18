"""SQLite is the authority for v2 sessions and accepted conversation context."""
from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())


class BusyError(Exception): pass
class MissingError(Exception): pass


class ChatStore:
    def __init__(self, filename):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(filename), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript('''
                PRAGMA foreign_keys=ON;
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT 'local', workspace_id TEXT NOT NULL DEFAULT 'local',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    parent_id TEXT, replaces_id TEXT, active INTEGER NOT NULL DEFAULT 1, request_id TEXT,
                    payload TEXT NOT NULL DEFAULT '{}', vote INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                    copy_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request ON requests(session_id) WHERE state='pending';
                PRAGMA user_version=1;
            ''')
            # An interrupted worker cannot resume an upstream token stream after restart.
            self.db.execute("UPDATE requests SET state='error' WHERE state='pending'")
            for row in self.db.execute("SELECT id,payload FROM messages WHERE status='pending'").fetchall():
                data = json.loads(row['payload']); data.update(error='服务重启中断了此回答，请重新生成。', outcome='error')
                self.db.execute("UPDATE messages SET status='error',payload=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row['id']))

    def close(self): self.db.close()

    def create(self):
        identifier, timestamp = uid(), now()
        with self.lock, self.db:
            self.db.execute('INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)', (identifier, '新会话', timestamp, timestamp))
        return self.session(identifier)

    def list(self, query=''):
        with self.lock:
            return [dict(row) for row in self.db.execute('''SELECT * FROM sessions s WHERE title LIKE ? OR EXISTS
                (SELECT 1 FROM messages m WHERE m.session_id=s.id AND m.role='user' AND m.content LIKE ?)
                ORDER BY updated_at DESC''', (f'%{query}%', f'%{query}%'))]

    def session(self, identifier):
        with self.lock:
            row = self.db.execute('SELECT * FROM sessions WHERE id=?', (identifier,)).fetchone()
            if not row: raise MissingError('会话不存在')
            return dict(row)

    def messages(self, identifier):
        self.session(identifier)
        with self.lock:
            rows = self.db.execute('SELECT * FROM messages WHERE session_id=? ORDER BY rowid', (identifier,)).fetchall()
            return [self._message(row) for row in rows]

    @staticmethod
    def _message(row):
        result = dict(row); result.update(json.loads(result.pop('payload')))
        result['active'] = bool(result['active'])
        result.setdefault('citations', []); result.setdefault('candidates', [])
        return result

    def message(self, identifier):
        with self.lock:
            row = self.db.execute('SELECT * FROM messages WHERE id=?', (identifier,)).fetchone()
            if not row: raise MissingError('消息不存在')
            return self._message(row)

    def request(self, identifier):
        with self.lock:
            row = self.db.execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone()
            if not row: raise MissingError('请求尚未被接受或已移除')
            return {**dict(row), 'message': self.message(row['message_id'])}

    def _idle(self, identifier):
        self.session(identifier)
        if self.db.execute("SELECT 1 FROM requests WHERE session_id=? AND state='pending'", (identifier,)).fetchone():
            raise BusyError('此会话已有请求正在进行，请先停止')

    def rename(self, identifier, title):
        with self.lock, self.db:
            self._idle(identifier)
            self.db.execute('UPDATE sessions SET title=?,updated_at=? WHERE id=?', (title, now(), identifier))
        return self.session(identifier)

    def clear(self, identifier, delete=False):
        with self.lock, self.db:
            self._idle(identifier)
            self.db.execute('DELETE FROM requests WHERE session_id=?', (identifier,))
            self.db.execute('DELETE FROM messages WHERE session_id=?', (identifier,))
            if delete: self.db.execute('DELETE FROM sessions WHERE id=?', (identifier,))
            else: self.db.execute("UPDATE sessions SET title='新会话',updated_at=? WHERE id=?", (now(), identifier))

    def begin(self, session_id, request_id, query=None, regenerate=None):
        with self.lock, self.db:
            self._idle(session_id)
            if self.db.execute('SELECT 1 FROM requests WHERE id=?', (request_id,)).fetchone():
                raise BusyError('request_id 已使用，请刷新消息后重试')
            replaces, parent = None, None
            if regenerate:
                target = self.message(regenerate)
                last = self.db.execute("SELECT id FROM messages WHERE session_id=? AND role='assistant' AND active=1 ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
                if target['session_id'] != session_id or target['role'] != 'assistant' or not last or target['id'] != last['id']:
                    raise BusyError('仅可重新生成此会话最近一轮的当前回答')
                parent, replaces = target['parent_id'], target['id']
                query = self.message(parent)['content']
                self.db.execute('UPDATE messages SET active=0 WHERE id=?', (replaces,))
            else:
                parent = uid()
                self.db.execute('INSERT INTO messages(id,session_id,role,content,status,created_at) VALUES(?,?,?,?,?,?)',
                    (parent, session_id, 'user', query, 'complete', now()))
                count = self.db.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND role='user'", (session_id,)).fetchone()[0]
                if count == 1: self.db.execute('UPDATE sessions SET title=? WHERE id=?', (query[:32], session_id))
            message_id = uid()
            payload = json.dumps(dict(query=query), ensure_ascii=False)
            self.db.execute('''INSERT INTO messages(id,session_id,role,status,parent_id,replaces_id,request_id,payload,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''', (message_id, session_id, 'assistant', 'pending', parent, replaces, request_id, payload, now()))
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?)', (request_id, session_id, message_id, 'pending', now()))
            self.db.execute('UPDATE sessions SET updated_at=? WHERE id=?', (now(), session_id))
            return self.message(parent), self.message(message_id)

    def finish(self, request_id, state, payload):
        with self.lock, self.db:
            row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row or row['state'] != 'pending': return False
            message = self.message(row['message_id'])
            data = {k: message[k] for k in ('query',) if k in message}
            data.update(payload)
            self.db.execute('UPDATE messages SET status=?,content=?,payload=? WHERE id=?',
                (state, data.get('content', ''), json.dumps(data, ensure_ascii=False), row['message_id']))
            self.db.execute('UPDATE requests SET state=? WHERE id=?', (state, request_id))
            self.db.execute('UPDATE sessions SET updated_at=? WHERE id=?', (now(), row['session_id']))
            return True

    def context(self, identifier, before_parent=None):
        messages = self.messages(identifier)
        users = {m['id']: m for m in messages if m['role'] == 'user'}
        pairs = []
        for message in messages:
            if message['role'] == 'assistant' and message['active'] and message['status'] == 'complete' and message.get('outcome') != 'citation_failed':
                if message['parent_id'] == before_parent: continue
                parent = users[message['parent_id']]
                pairs.append((parent['content'], message['content']))
        return pairs[-10:]

    def feedback(self, identifier, vote, reason):
        with self.lock, self.db:
            item = self.message(identifier)
            if item['role'] != 'assistant' or item['status'] != 'complete': raise BusyError('此消息尚未完成')
            self.db.execute('UPDATE messages SET vote=?,reason=? WHERE id=?', (vote, reason, identifier))
        return self.message(identifier)

    def copied(self, identifier):
        with self.lock, self.db:
            self.message(identifier)
            self.db.execute('UPDATE messages SET copy_count=copy_count+1 WHERE id=?', (identifier,))
