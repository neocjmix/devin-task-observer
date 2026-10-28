"""Atomic local observation and checkpoint store (no model calls)."""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, title TEXT, workspace TEXT NOT NULL, head INTEGER,
 status TEXT NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source_nodes (
 session_id TEXT NOT NULL, node_id INTEGER NOT NULL, parent_id INTEGER,
 message_id TEXT NOT NULL, role TEXT NOT NULL, content_hash TEXT NOT NULL,
 present INTEGER NOT NULL, PRIMARY KEY(session_id, node_id)
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, node_id INTEGER NOT NULL,
 transition TEXT NOT NULL, message_id TEXT NOT NULL, role TEXT NOT NULL,
 content_hash TEXT NOT NULL, observed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
 session_id TEXT PRIMARY KEY, source_fingerprint TEXT NOT NULL, committed_at INTEGER NOT NULL
);
'''


def open_store(path: Path, source: Path):
    target = path.absolute().resolve()
    source = source.absolute().resolve()
    if target == source or target.is_relative_to(source.parent):
        raise ValueError('observer store must be outside the source directory')
    if target.exists() and os.path.samefile(target, source):
        raise ValueError('observer store must be distinct from source')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    db = sqlite3.connect(target)
    db.executescript(SCHEMA)
    return db


def sync(db, snapshots, workspaces):
    """Atomically apply a complete source snapshot; retain missing nodes as stale."""
    now = time.time_ns()
    changes = 0
    with db:
        ids = set()
        for session in snapshots:
            sid = session['id']
            ids.add(sid)
            existing = {r[0]: r[1:] for r in db.execute('SELECT node_id,parent_id,message_id,role,content_hash,present FROM source_nodes WHERE session_id=?', (sid,))}
            current = {n['node_id']: n for n in session['nodes']}
            missing = set(existing) - set(current)
            identity_change = any(n in existing and (existing[n][0] != v['parent_id'] or existing[n][1] != v['message_id']) for n, v in current.items())
            previous_status = db.execute('SELECT status FROM sessions WHERE id=?', (sid,)).fetchone()
            status = 'stale' if missing or session['ambiguous'] or identity_change or (previous_status and previous_status[0] == 'stale') else 'pending' if session['head'] is None else 'current'
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, workspace=excluded.workspace, head=excluded.head, status=excluded.status, last_seen=excluded.last_seen', (sid, session['title'], session['workspace'], session['head'], status, now))
            for node_id, node in current.items():
                old = existing.get(node_id)
                fresh = (node['parent_id'], node['message_id'], node['role'], node['content_hash'], 1)
                if old != fresh:
                    transition = 'added' if old is None else 'restored' if not old[4] else 'modified'
                    db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)', (sid,node_id,transition,node['message_id'],node['role'],node['content_hash'],now))
                    db.execute('INSERT INTO source_nodes VALUES (?,?,?,?,?,?,1) ON CONFLICT(session_id,node_id) DO UPDATE SET parent_id=excluded.parent_id,message_id=excluded.message_id,role=excluded.role,content_hash=excluded.content_hash,present=1', (sid,node_id,node['parent_id'],node['message_id'],node['role'],node['content_hash']))
                    changes += 1
            for node_id in missing:
                old = existing[node_id]
                if old[4]:
                    db.execute('UPDATE source_nodes SET present=0 WHERE session_id=? AND node_id=?', (sid,node_id))
                    db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)', (sid,node_id,'missing',old[1],old[2],old[3],now))
                    changes += 1
            encoded = json.dumps([session['head'], session['nodes']], sort_keys=True, separators=(',', ':')).encode()
            fingerprint = hashlib.sha256(encoded).hexdigest()
            db.execute('INSERT INTO checkpoints VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET source_fingerprint=excluded.source_fingerprint,committed_at=excluded.committed_at', (sid,fingerprint,now))
        for workspace in set(str(Path(p).absolute()) for p in workspaces):
            for (sid,) in db.execute('SELECT id FROM sessions WHERE workspace=?', (workspace,)).fetchall():
                if sid not in ids:
                    db.execute("UPDATE sessions SET status='stale' WHERE id=?", (sid,))
    return changes
