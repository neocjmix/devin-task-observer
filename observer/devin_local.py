"""Read observed Devin Local session nodes in a short read-only transaction.

This adapter deliberately preserves nodes rather than guessing which historical
branches are user-visible. It does not attach to the agent or modify its store.
"""
import hashlib
import json
import copy
import sqlite3
from pathlib import Path


class SourceIncomplete(Exception):
    """The source is unavailable or cannot be interpreted without guessing."""


def observed_tool_revision(a, b):
    """Recognize only the additive metadata variant observed in the P0 test.

    Both node hashes remain in the store. This does not merge messages or
    interpret the tool content; every other difference remains ambiguous.
    """
    if a.get('role') != 'assistant' or b.get('role') != 'assistant' or not isinstance(a.get('tool_calls'), list) or not a['tool_calls']:
        return False
    a, b = copy.deepcopy(a), copy.deepcopy(b)
    values = []
    for item in (a, b):
        metadata = item.get('metadata')
        if not isinstance(metadata, dict):
            return False
        extensions = metadata.get('extensions')
        if extensions is None:
            values.append(None)
        elif isinstance(extensions, dict):
            values.append(extensions.pop('chisel/tool_call_content', None))
            if not extensions:
                metadata.pop('extensions', None)
        else:
            return False
    return a == b and sum(isinstance(v, dict) for v in values) == 1 and any(v is None for v in values)


def observed_kinds(message):
    """Conservative labels for shapes actually observed in the P0 test."""
    role = message.get('role')
    if role == 'user':
        metadata = message.get('metadata')
        return ['user'] if isinstance(metadata, dict) and metadata.get('is_user_input') is True else ['unknown']
    if role == 'assistant':
        kinds = []
        if isinstance(message.get('content'), str) and message['content']:
            kinds.append('assistant')
        if isinstance(message.get('tool_calls'), list) and message['tool_calls']:
            kinds.append('tool_call')
        return kinds or ['unknown']
    if role == 'tool' and isinstance(message.get('tool_call_id'), str) and message['tool_call_id']:
        return ['tool_result']
    return ['unknown']


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
            entries = db.execute('SELECT id, title, main_chain_id FROM sessions WHERE working_directory = ? ORDER BY id', (workdir,)).fetchall()
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
                    if message_id in by_message:
                        previous_digest, previous_data = by_message[message_id]
                        if previous_digest != digest and not observed_tool_revision(previous_data, data):
                            ambiguous = True
                    by_message[message_id] = (digest, data)
                    parents[node_id] = parent
                    nodes.append({'node_id': node_id, 'parent_id': parent, 'message_id': message_id, 'role': role, 'kinds': observed_kinds(data), 'content_hash': digest})
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
