"""Read-only bridge to the existing, explicitly configured observation store."""
import json
import sqlite3
from pathlib import Path


def observations(config_path, session_ids=None):
    from .app import load_config
    config = load_config(config_path)
    result = {'origin': 'deterministic_collector', 'collector': None, 'sessions': [],
              'unobserved_session_ids': [], 'warnings': [], 'identity_verified': False}
    try:
        db = sqlite3.connect(Path(config['store']).as_uri() + '?mode=ro', uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        with db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            row = db.execute('SELECT last_success,last_attempt,last_error FROM collector_status WHERE singleton=1').fetchone()
            result['collector'] = dict(row) if row else None
            marks = ','.join('?' for _ in config['workspaces'])
            rows = db.execute(f'SELECT id,title,workspace,status,last_seen FROM sessions WHERE workspace IN ({marks}) ORDER BY id', config['workspaces'])
            for row in rows:
                sid = row['id']
                if session_ids is not None and sid not in session_ids:
                    continue
                entry = dict(row)
                entry['identity_verified'] = False
                entry['observation_count'] = db.execute('SELECT count(*) FROM observations WHERE session_id=?', (sid,)).fetchone()[0]
                resources = db.execute('''SELECT d.kind,d.value,d.verification,d.source_ref,d.node_id,d.source_hash
                    FROM discovered_resources d JOIN source_nodes n ON n.session_id=d.session_id
                    AND n.node_id=d.node_id AND n.content_hash=d.source_hash AND n.present=1
                    WHERE d.session_id=? AND d.workspace=? ORDER BY d.id LIMIT 101''', (sid, row['workspace'])).fetchall()
                entry['resource_mentions'] = [dict(r) for r in resources[:100]]
                entry['resources_truncated'] = len(resources) > 100
                plans = db.execute('''SELECT p.node_id,p.message_id,p.call_id,p.source_hash,p.items
                    FROM plan_records p JOIN source_nodes n ON n.session_id=p.session_id AND n.node_id=p.node_id
                    AND n.content_hash=p.source_hash AND n.present=1
                    WHERE p.session_id=? ORDER BY p.node_id,p.call_id LIMIT 101''', (sid,)).fetchall()
                entry['plan_snapshots'] = [{**dict(r), 'items': json.loads(r['items']) if r['items'] else None,
                                            'verification': 'agent_reported', 'ordering': 'not_inferred'} for r in plans[:100]]
                entry['plans_truncated'] = len(plans) > 100
                result['sessions'].append(entry)
            if session_ids is not None:
                result['unobserved_session_ids'] = sorted(set(session_ids) - {r['id'] for r in result['sessions']})
    except (OSError, sqlite3.Error, ValueError):
        result['warnings'].append('collector_unavailable_or_incompatible; reported task state is unchanged')
    finally:
        if 'db' in locals():
            db.close()
    return result
