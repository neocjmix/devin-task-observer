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
CREATE INDEX IF NOT EXISTS observations_identity ON observations(session_id,message_id,content_hash,id);
CREATE INDEX IF NOT EXISTS observations_sequence ON observations(session_id,id);
CREATE INDEX IF NOT EXISTS observations_node_version ON observations(session_id,node_id,content_hash,id);
CREATE TABLE IF NOT EXISTS checkpoints (
 session_id TEXT PRIMARY KEY, source_fingerprint TEXT NOT NULL, committed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS node_kinds (
 session_id TEXT NOT NULL, node_id INTEGER NOT NULL, content_hash TEXT NOT NULL,
 kinds TEXT NOT NULL, PRIMARY KEY(session_id, node_id, content_hash)
);
CREATE TABLE IF NOT EXISTS events (
 session_id TEXT NOT NULL, message_id TEXT NOT NULL, kind TEXT NOT NULL,
 discriminator TEXT NOT NULL, first_observed_at INTEGER NOT NULL,
 PRIMARY KEY(session_id, message_id, kind, discriminator)
);
CREATE TABLE IF NOT EXISTS event_evidence (
 session_id TEXT NOT NULL, message_id TEXT NOT NULL, kind TEXT NOT NULL,
 discriminator TEXT NOT NULL, node_id INTEGER NOT NULL, content_hash TEXT NOT NULL,
 PRIMARY KEY(session_id, message_id, kind, discriminator, node_id, content_hash)
);
CREATE TABLE IF NOT EXISTS session_health (
 session_id TEXT PRIMARY KEY, structural_stale INTEGER NOT NULL, source_unavailable INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_validation (
 session_id TEXT PRIMARY KEY, ambiguous INTEGER NOT NULL, present INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_metadata (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id), model TEXT, backend TEXT, agent_mode TEXT,
 source_created_raw INTEGER, source_activity_raw INTEGER
);
CREATE TABLE IF NOT EXISTS session_metadata_history (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), model TEXT, backend TEXT,
 agent_mode TEXT, source_created_raw INTEGER, source_activity_raw INTEGER, observed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_history (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
 missing_nodes INTEGER NOT NULL, acknowledged_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source_identity (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), device INTEGER NOT NULL, inode INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
 lifecycle TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS contributions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL,
 scope TEXT NOT NULL, linked_by TEXT NOT NULL, confidence REAL,
 linked_at INTEGER NOT NULL, UNIQUE(task_id,session_id)
);
CREATE TABLE IF NOT EXISTS resources (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL,
 uri TEXT NOT NULL, external_status TEXT NOT NULL, verified_at INTEGER, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contribution_state (
 contribution_id TEXT PRIMARY KEY REFERENCES contributions(id), active INTEGER NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contribution_history (
 id INTEGER PRIMARY KEY, contribution_id TEXT NOT NULL REFERENCES contributions(id),
 active INTEGER NOT NULL, revision INTEGER NOT NULL, saved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contribution_metadata_history (
 id INTEGER PRIMARY KEY, contribution_id TEXT NOT NULL REFERENCES contributions(id),
 role TEXT NOT NULL, scope TEXT NOT NULL, confidence REAL, revision INTEGER NOT NULL, saved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_session_progress (
 task_id TEXT NOT NULL REFERENCES tasks(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 last_observation_id INTEGER NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(task_id,session_id)
);
CREATE TABLE IF NOT EXISTS task_progress_history (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 last_observation_id INTEGER NOT NULL, revision INTEGER NOT NULL, acknowledged_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_state (
 resource_id TEXT PRIMARY KEY REFERENCES resources(id), active INTEGER NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_history (
 id INTEGER PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES resources(id), kind TEXT NOT NULL,
 uri TEXT NOT NULL, active INTEGER NOT NULL, revision INTEGER NOT NULL, saved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_views (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id), revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS view_sections (
 id TEXT PRIMARY KEY, view_id TEXT NOT NULL REFERENCES task_views(id), title TEXT NOT NULL,
 position INTEGER NOT NULL, UNIQUE(view_id,position)
);
CREATE TABLE IF NOT EXISTS view_items (
 id TEXT PRIMARY KEY, section_id TEXT NOT NULL REFERENCES view_sections(id),
 content TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL, pinned INTEGER NOT NULL,
 position INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(section_id,position)
);
CREATE TABLE IF NOT EXISTS view_item_history (
 id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES view_items(id), content TEXT NOT NULL,
 status TEXT NOT NULL, pinned INTEGER NOT NULL, saved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS item_evidence (
 item_id TEXT NOT NULL REFERENCES view_items(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 node_id INTEGER NOT NULL, content_hash TEXT NOT NULL,
 PRIMARY KEY(item_id,session_id,node_id,content_hash)
);
CREATE TABLE IF NOT EXISTS semantic_runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 node_id INTEGER NOT NULL, content_hash TEXT NOT NULL, model TEXT NOT NULL, prompt_version INTEGER NOT NULL,
 budget_day TEXT NOT NULL, reserved_tokens INTEGER NOT NULL, prompt_tokens INTEGER, output_tokens INTEGER,
 status TEXT NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(task_id,session_id,node_id,content_hash,model,prompt_version)
);
CREATE TABLE IF NOT EXISTS semantic_proposals (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES semantic_runs(id),
 content TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, accepted_item_id TEXT
);
CREATE TABLE IF NOT EXISTS semantic_retry_attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES semantic_runs(id), budget_day TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL, prompt_tokens INTEGER, output_tokens INTEGER,
 status TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_lock_runs (
 run_id TEXT PRIMARY KEY REFERENCES semantic_runs(id)
);
CREATE TABLE IF NOT EXISTS semantic_input_keys (
 run_id TEXT PRIMARY KEY REFERENCES semantic_runs(id), message_id TEXT NOT NULL, input_hash TEXT NOT NULL
);
'''


def open_store(path: Path, source: Path):
    target = path.absolute().resolve()
    source = source.absolute().resolve()
    if target == source or target.is_relative_to(source.parent):
        raise ValueError('observer store must be outside the source directory')
    if target.exists() and source.exists() and os.path.samefile(target, source):
        raise ValueError('observer store must be distinct from source')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    db = sqlite3.connect(target)
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    db.execute('BEGIN IMMEDIATE')
    try:
        columns={r[1] for r in db.execute('PRAGMA table_info(session_validation)')}
        if 'present' not in columns:
            db.execute('ALTER TABLE session_validation ADD COLUMN present INTEGER NOT NULL DEFAULT 0')
        db.execute("INSERT OR IGNORE INTO session_health SELECT id, CASE WHEN status='stale' THEN 1 ELSE 0 END, 0 FROM sessions")
        db.commit()
    except Exception:
        db.rollback()
        db.close()
        raise
    return db


def sync(db, snapshots, workspaces, source_identity=None):
    """Atomically apply a complete source snapshot; retain missing nodes as stale."""
    now = time.time_ns()
    changes = 0
    with db:
        if source_identity is not None:
            previous = db.execute('SELECT device,inode FROM source_identity WHERE singleton=1').fetchone()
            if previous is not None and tuple(previous) != tuple(source_identity):
                db.execute("UPDATE sessions SET status='stale'")
                db.execute('INSERT INTO session_health SELECT id,1,0 FROM sessions WHERE true ON CONFLICT(session_id) DO UPDATE SET structural_stale=1')
            db.execute('INSERT INTO source_identity VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET device=excluded.device,inode=excluded.inode', source_identity)
        ids = set()
        for session in snapshots:
            sid = session['id']
            ids.add(sid)
            existing = {r[0]: r[1:] for r in db.execute('SELECT node_id,parent_id,message_id,role,content_hash,present FROM source_nodes WHERE session_id=?', (sid,))}
            current = {n['node_id']: n for n in session['nodes']}
            missing = set(existing) - set(current)
            identity_change = any(n in existing and (existing[n][0] != v['parent_id'] or existing[n][1] != v['message_id']) for n, v in current.items())
            previous_health = db.execute('SELECT structural_stale FROM session_health WHERE session_id=?', (sid,)).fetchone()
            newly_missing = any(existing[n][4] for n in missing)
            structural_stale = bool(newly_missing or session['ambiguous'] or identity_change or (previous_health and previous_health[0]))
            status = 'stale' if structural_stale else 'pending' if session['head'] is None else 'current'
            db.execute('INSERT INTO session_health VALUES (?,?,0) ON CONFLICT(session_id) DO UPDATE SET structural_stale=excluded.structural_stale,source_unavailable=0', (sid,int(structural_stale)))
            db.execute('INSERT INTO session_validation VALUES (?,?,1) ON CONFLICT(session_id) DO UPDATE SET ambiguous=excluded.ambiguous,present=1',(sid,int(session['ambiguous'])))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, workspace=excluded.workspace, head=excluded.head, status=excluded.status, last_seen=excluded.last_seen', (sid, session['title'], session['workspace'], session['head'], status, now))
            metadata=session.get('metadata',{})
            fields=tuple(metadata.get(k) for k in ('model','backend','agent_mode','source_created_raw','source_activity_raw'))
            previous=db.execute('SELECT model,backend,agent_mode,source_created_raw,source_activity_raw FROM session_metadata WHERE session_id=?',(sid,)).fetchone()
            if previous!=fields:
                db.execute('INSERT INTO session_metadata_history(session_id,model,backend,agent_mode,source_created_raw,source_activity_raw,observed_at) VALUES (?,?,?,?,?,?,?)',(sid,*fields,now))
                db.execute('INSERT INTO session_metadata VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET model=excluded.model,backend=excluded.backend,agent_mode=excluded.agent_mode,source_created_raw=excluded.source_created_raw,source_activity_raw=excluded.source_activity_raw',(sid,*fields))
            for node_id, node in current.items():
                db.execute('INSERT INTO node_kinds VALUES (?,?,?,?) ON CONFLICT(session_id,node_id,content_hash) DO UPDATE SET kinds=excluded.kinds', (sid,node_id,node['content_hash'],json.dumps(node['kinds'])))
                for kind, discriminator in node['event_keys']:
                    db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)', (sid,node['message_id'],kind,discriminator,now))
                    db.execute('INSERT OR IGNORE INTO event_evidence VALUES (?,?,?,?,?,?)', (sid,node['message_id'],kind,discriminator,node_id,node['content_hash']))
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
                    db.execute('INSERT INTO session_health VALUES (?,1,0) ON CONFLICT(session_id) DO UPDATE SET structural_stale=1', (sid,))
                    db.execute('UPDATE session_validation SET present=0 WHERE session_id=?',(sid,))
    return changes


def mark_source_unavailable(db, workspaces):
    """Keep prior checkpoints but flag them until a complete snapshot returns."""
    with db:
        for workspace in set(str(Path(p).absolute()) for p in workspaces):
            ids = [r[0] for r in db.execute('SELECT id FROM sessions WHERE workspace=?', (workspace,))]
            for sid in ids:
                db.execute('INSERT INTO session_health VALUES (?,0,1) ON CONFLICT(session_id) DO UPDATE SET source_unavailable=1', (sid,))
                db.execute("UPDATE sessions SET status='stale' WHERE id=?", (sid,))


def acknowledge_structural_change(db, session_id, expected_fingerprint):
    """Accept a freshly inspected complete snapshot without removing history."""
    if not isinstance(expected_fingerprint,str) or len(expected_fingerprint)!=64:
        raise ValueError('checkpoint required')
    db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute('''SELECT h.structural_stale,h.source_unavailable,v.ambiguous,c.source_fingerprint,s.head,v.present
          FROM sessions s JOIN session_health h ON h.session_id=s.id
          JOIN session_validation v ON v.session_id=s.id JOIN checkpoints c ON c.session_id=s.id
          WHERE s.id=?''',(session_id,)).fetchone()
        if not row or not row[0] or row[1] or row[2] or row[4] is None or not row[5]:
            raise ValueError('snapshot is not eligible for recovery')
        if row[3]!=expected_fingerprint:
            raise ValueError('snapshot changed; inspect it again')
        missing=db.execute('SELECT COUNT(*) FROM source_nodes WHERE session_id=? AND present=0',(session_id,)).fetchone()[0]
        db.execute('INSERT INTO recovery_history(session_id,source_fingerprint,missing_nodes,acknowledged_at) VALUES (?,?,?,?)',(session_id,expected_fingerprint,missing,time.time_ns()))
        db.execute('UPDATE session_health SET structural_stale=0 WHERE session_id=?',(session_id,))
        db.execute("UPDATE sessions SET status='current' WHERE id=?",(session_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
