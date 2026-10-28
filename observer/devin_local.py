"""Read observed Devin Local session nodes in a short read-only transaction.

This adapter deliberately preserves nodes rather than guessing which historical
branches are user-visible. It does not attach to the agent or modify its store.
"""
import hashlib
import json
import sqlite3
from pathlib import Path


class SourceIncomplete(Exception):
    """The source is unavailable or cannot be interpreted without guessing."""


def read_sessions(database: Path, workspaces: list[Path]):
    if not workspaces:
        raise ValueError('at least one explicit personal workspace is required')
    db = sqlite3.connect(database.absolute().as_uri() + '?mode=ro', uri=True, timeout=0.25)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        version = db.execute('SELECT max(version) FROM refinery_schema_history').fetchone()[0]
        if version != 17:
            raise SourceIncomplete('unsupported source schema')
        sessions = []
        for workdir in sorted({str(x.absolute()) for x in workspaces}):
            entries = db.execute('SELECT id, title, main_chain_id FROM sessions WHERE working_directory = ?', (workdir,)).fetchall()
            for sid, title, head in entries:
                if not isinstance(sid, str) or not sid:
                    raise SourceIncomplete('invalid session identifier')
                rows = db.execute('SELECT node_id, parent_node_id, chat_message FROM message_nodes WHERE session_id = ? ORDER BY node_id', (sid,)).fetchall()
                nodes = []
                parents = {}
                by_message = {}
                ambiguous = False
                for node_id, parent, raw in rows:
                    if not isinstance(node_id, int) or not isinstance(raw, str):
                        raise SourceIncomplete('invalid node')
                    try:
                        data = json.loads(raw)
                    except (ValueError, TypeError) as error:
                        raise SourceIncomplete('malformed message') from error
                    if not isinstance(data, dict):
                        raise SourceIncomplete('unexpected message shape')
                    message_id = data.get('message_id')
                    if not isinstance(message_id, str) or not message_id:
                        raise SourceIncomplete('missing message identifier')
                    role = data.get('role') if data.get('role') in ('user', 'assistant', 'system', 'tool') else 'unknown'
                    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
                    if message_id in by_message and by_message[message_id] != digest:
                        ambiguous = True
                    by_message[message_id] = digest
                    parents[node_id] = parent
                    nodes.append({'node_id': node_id, 'parent_id': parent, 'message_id': message_id, 'role': role, 'content_hash': digest})
                cursor, seen = head, set()
                while cursor is not None and cursor in parents and cursor not in seen:
                    seen.add(cursor)
                    cursor = parents[cursor]
                if (head is None and rows) or cursor is not None:
                    raise SourceIncomplete('missing or cyclic active chain')
                sessions.append({'id': sid, 'title': title, 'workspace': workdir, 'head': head, 'nodes': nodes, 'ambiguous': ambiguous})
        db.rollback()
        return sessions
    except sqlite3.Error as error:
        raise SourceIncomplete('source read failed') from error
    finally:
        db.close()
