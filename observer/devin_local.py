"""Read observed Devin Local session nodes in a short read-only transaction.

This adapter deliberately preserves nodes rather than guessing which historical
branches are user-visible. It does not attach to the agent or modify its store.
"""
import hashlib
import json
import copy
import math
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


def observed_tpot_signature(message):
    """Fingerprint an assistant payload excluding the observed timing metric."""
    if message.get('role')!='assistant':return None
    metadata=message.get('metadata')
    metrics=metadata.get('metrics') if isinstance(metadata,dict) else None
    value=metrics.get('tpot_ms') if isinstance(metrics,dict) else None
    if not isinstance(value,float) or not math.isfinite(value):return None
    normalized=copy.deepcopy(message)
    normalized['metadata']['metrics'].pop('tpot_ms')
    encoded=json.dumps(normalized,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest(),value


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
            if any(isinstance(call, dict) and call.get('name') == 'todo_write' for call in message['tool_calls']):
                kinds.append('plan')
        return kinds or ['unknown']
    if role == 'tool' and isinstance(message.get('tool_call_id'), str) and message['tool_call_id']:
        return ['tool_result']
    return ['unknown']


def observed_event_keys(message):
    """Return conservative event types and tool discriminators, without text."""
    kinds = observed_kinds(message)
    result = [(k, '') for k in kinds if k not in ('tool_call', 'tool_result', 'plan')]
    if 'tool_call' in kinds:
        for call in message['tool_calls']:
            if not isinstance(call, dict) or not isinstance(call.get('id'), str) or not call['id']:
                result.append(('unknown', ''))
                continue
            result.append(('tool_call', call['id']))
            if call.get('name') == 'todo_write':
                result.append(('plan', call['id']))
    if 'tool_result' in kinds:
        result.append(('tool_result', message['tool_call_id']))
    return sorted(set(result)) or [('unknown', '')]


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
        columns={r[1] for r in db.execute('PRAGMA table_info(sessions)')}
        optional=('model','backend_type','agent_mode','created_at','last_activity_at')
        selection=','.join(name if name in columns else 'NULL AS '+name for name in optional)
        sessions = []
        for workdir in sorted({str(x.absolute()) for x in workspaces}):
            entries = db.execute('SELECT id, title, main_chain_id,'+selection+' FROM sessions WHERE working_directory = ? ORDER BY id', (workdir,)).fetchall()
            for sid, title, head, model, backend, mode, created, activity in entries:
                if not isinstance(sid, str) or not sid:
                    raise SourceIncomplete('invalid session identifier')
                if title is not None and not isinstance(title,str):
                    raise SourceIncomplete('invalid session title')
                if head is not None and not isinstance(head, int):
                    raise SourceIncomplete('invalid active chain head')
                if any(v is not None and not isinstance(v,str) for v in (model,backend,mode)) or any(v is not None and not isinstance(v,int) for v in (created,activity)):
                    raise SourceIncomplete('invalid session metadata')
                rows = db.execute('SELECT node_id, parent_node_id, chat_message FROM message_nodes WHERE session_id = ? ORDER BY node_id', (sid,))
                nodes = []
                parents = {}
                by_message = {}
                ambiguous = False
                has_rows = False
                for node_id, parent, raw in rows:
                    has_rows = True
                    if (not isinstance(node_id, int) or node_id in parents or
                        (parent is not None and not isinstance(parent, int)) or not isinstance(raw, str)):
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
                    timing=observed_tpot_signature(data)
                    if message_id in by_message:
                        previous_digest, previous_data, previous_timing = by_message[message_id]
                        timing_only=bool(timing and previous_timing and timing[0]==previous_timing[0] and timing[1]!=previous_timing[1])
                        if previous_digest != digest and not timing_only and (previous_data is None or not observed_tool_revision(previous_data, data)):
                            ambiguous = True
                    tool_candidate = data if data.get('role')=='assistant' and isinstance(data.get('tool_calls'),list) and data['tool_calls'] else None
                    by_message[message_id] = (digest, tool_candidate, timing)
                    parents[node_id] = parent
                    nodes.append({'node_id': node_id, 'parent_id': parent, 'message_id': message_id, 'role': role, 'kinds': observed_kinds(data), 'event_keys': observed_event_keys(data), 'content_hash': digest,
                        'equivalence_hash':('tpot:'+timing[0]) if timing else ('raw:'+digest)})
                cursor, seen = head, set()
                while cursor is not None and cursor in parents and cursor not in seen:
                    seen.add(cursor)
                    cursor = parents[cursor]
                if (head is None and has_rows) or cursor is not None:
                    raise SourceIncomplete('missing or cyclic active chain')
                sessions.append({'id': sid, 'title': title, 'workspace': workdir, 'head': head, 'nodes': nodes, 'ambiguous': ambiguous,
                    'metadata':{'model':model,'backend':backend,'agent_mode':mode,'source_created_raw':created,'source_activity_raw':activity}})
        db.rollback()
        return sessions
    except sqlite3.Error as error:
        raise SourceIncomplete('source read failed') from error
    finally:
        db.close()


def read_evidence(database: Path, workspace: Path, session_id: str, node_id: int, expected_hash: str):
    """Return source JSON only if its workspace, identity and hash still match.

    The caller handles sensitive data. Older versions cannot be recovered if
    the source no longer retains them; return an error instead of guessing.
    """
    db = sqlite3.connect(database.absolute().as_uri() + '?mode=ro', uri=True, timeout=0.25)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        version = db.execute('SELECT max(version) FROM refinery_schema_history').fetchone()[0]
        if version != 17:
            raise SourceIncomplete('unsupported source schema')
        row = db.execute('SELECT n.chat_message FROM message_nodes n JOIN sessions s ON s.id=n.session_id WHERE s.working_directory=? AND n.session_id=? AND n.node_id=?', (str(workspace.absolute()), session_id, node_id)).fetchone()
        if row is None or not isinstance(row[0], str):
            raise SourceIncomplete('evidence missing')
        if hashlib.sha256(row[0].encode('utf-8')).hexdigest() != expected_hash:
            raise SourceIncomplete('evidence changed')
        result = json.loads(row[0])
        if not isinstance(result, dict):
            raise SourceIncomplete('invalid evidence')
        db.rollback()
        return result
    except (sqlite3.Error, ValueError) as error:
        raise SourceIncomplete('evidence read failed') from error
    finally:
        db.close()
