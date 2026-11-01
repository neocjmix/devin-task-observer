#!/usr/bin/env python3
"""Inspect a verified Devin Local SQLite schema without retaining message bodies.

This is a P0 diagnostic, not an event collector. Restrict it to a controlled
synthetic workspace. SQLite read transactions may briefly hold read locks and
use SQLite's WAL shared-memory coordination even with a read-only connection.
"""
import argparse
import collections
import hashlib
import hmac
import json
import secrets
import sqlite3
from pathlib import Path


def inspect(database: Path, workspace: Path, key: bytes):
    uri = database.absolute().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=0.25)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        sessions = db.execute(
            'SELECT id, main_chain_id FROM sessions WHERE working_directory = ?',
            (str(workspace.absolute()),),
        ).fetchall()
        results = []
        for session_id, head in sessions:
            rows = db.execute(
                'SELECT node_id, parent_node_id, chat_message FROM message_nodes WHERE session_id = ? ORDER BY node_id',
                (session_id,),
            ).fetchall()
            fingerprint = hmac.new(key, digestmod=hashlib.sha256)
            def add_part(value):
                encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
                fingerprint.update(len(encoded).to_bytes(8, 'big'))
                fingerprint.update(encoded)
            add_part(head)
            nodes = {}
            roles = collections.Counter()
            ids = []
            malformed = 0
            user_inputs = 0
            other_user_nodes = 0
            for node, parent, raw in rows:
                add_part([node, parent, raw])
                nodes[node] = parent
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError('expected object')
                    role = message.get('role')
                    roles[role if role in ('user', 'assistant', 'system', 'tool') else 'unknown'] += 1
                    if role == 'user':
                        metadata = message.get('metadata')
                        if isinstance(metadata, dict) and metadata.get('is_user_input') is True:
                            user_inputs += 1
                        else:
                            other_user_nodes += 1
                    identifier = message.get('message_id')
                    if isinstance(identifier, str) and identifier:
                        ids.append(identifier)
                except (ValueError, TypeError):
                    malformed += 1
            seen = set()
            cursor = head
            while cursor is not None and cursor in nodes and cursor not in seen:
                seen.add(cursor)
                cursor = nodes[cursor]
            results.append({
                'session_ref': hmac.new(key, session_id.encode(), hashlib.sha256).hexdigest(),
                'content_fingerprint': fingerprint.hexdigest(),
                'nodes': len(rows), 'roles': dict(sorted(roles.items())),
                'messages_with_id': len(ids), 'distinct_message_ids': len(set(ids)),
                'user_input_flagged_nodes': user_inputs, 'other_user_role_nodes': other_user_nodes,
                'malformed_messages': malformed, 'active_chain_nodes': len(seen),
                'active_chain_complete': head is not None and cursor is None,
            })
        db.rollback()
        complete = all(not item['malformed_messages'] and item['active_chain_complete'] for item in results)
        return {'session_count': len(results), 'sessions': results, 'snapshot_complete': complete}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--key-file', type=Path, help='private random key for stable pseudonyms; otherwise use an ephemeral key')
    args = parser.parse_args()
    try:
        key = args.key_file.read_bytes() if args.key_file else secrets.token_bytes(32)
        if len(key) < 32:
            raise ValueError('key too short')
        result = inspect(args.database, args.workspace, key)
        result['stable_pseudonyms'] = args.key_file is not None
        print(json.dumps(result, sort_keys=True))
        if not result['snapshot_complete']:
            raise SystemExit(2)
    except (OSError, sqlite3.Error, ValueError) as error:
        # No exception text: paths and database content can be sensitive.
        print(json.dumps({'error': type(error).__name__, 'snapshot_complete': False}))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
