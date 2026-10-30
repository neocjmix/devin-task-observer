"""File-first task/report CLI. No daemon, browser or model is required."""
import argparse
import json
import os
from pathlib import Path
import sys

from .cooperative import CooperativeState
from .file_store import StateError
from .state_schema import MAX_BYTES, validate, published

DEFAULT_ROOT = Path.home() / 'Library/Application Support/Devin Task Observer/state'


def load_json(path):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError('duplicate JSON field: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-finite JSON is unsupported')
    if str(path) == '-':
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    else:
        with open(path, 'rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('input exceeds 2 MiB')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def write_new(path, value):
    # Work copies/registration contexts are deliberately never overwritten.
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    commands = p.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--task', required=True)
    create.add_argument('--title', required=True)
    create.add_argument('--session')
    create.add_argument('--request-id')
    listing = commands.add_parser('list')
    listing.add_argument('--session')
    for name in ('read', 'history'):
        sub = commands.add_parser(name)
        sub.add_argument('--task', required=True)
    for name in ('link', 'reporting', 'pin', 'note'):
        sub = commands.add_parser(name)
        sub.add_argument('--task', required=True)
        sub.add_argument('--expected-revision', type=int, required=True)
        sub.add_argument('--request-id')
        if name in ('link', 'reporting'):
            sub.add_argument('--session', required=True)
        if name == 'reporting':
            sub.add_argument('--enabled', choices=('on', 'off'), required=True)
        if name == 'pin':
            sub.add_argument('--item', required=True)
            sub.add_argument('--pinned', choices=('on', 'off'), required=True)
        if name == 'note':
            sub.add_argument('--text', required=True)
    report = commands.add_parser('report')
    report.add_argument('--file', required=True)
    report.add_argument('--context', type=Path)
    resource = commands.add_parser('resource-put', help='explicit human resource registration/update')
    resource.add_argument('--task', required=True)
    resource.add_argument('--file', required=True)
    resource.add_argument('--expected-revision', type=int, required=True)
    resource.add_argument('--request-id')
    archive = commands.add_parser('resource-archive', help='archive/restore a registered resource without deleting history')
    archive.add_argument('--task', required=True)
    archive.add_argument('--resource', required=True)
    archive.add_argument('--archived', choices=('on', 'off'), required=True)
    archive.add_argument('--expected-revision', type=int, required=True)
    archive.add_argument('--request-id')
    export = commands.add_parser('edit-export')
    export.add_argument('--task', required=True)
    export.add_argument('--out', type=Path, required=True)
    apply = commands.add_parser('edit-apply', help='explicit human edit; agents use report')
    apply.add_argument('--file', required=True)
    apply.add_argument('--request-id')
    check = commands.add_parser('validate')
    check.add_argument('--file')
    check.add_argument('--kind', choices=('task', 'report', 'edit', 'context', 'resource'), default='task')
    check.add_argument('--task')
    schema = commands.add_parser('schema')
    schema.add_argument('--kind', choices=('task', 'report', 'edit', 'context', 'resource'), required=True)
    register = commands.add_parser('session-register')
    register.add_argument('--session', required=True)
    register.add_argument('--out', type=Path, required=True)
    who = commands.add_parser('whoami')
    who.add_argument('--context', type=Path, required=True)
    recover = commands.add_parser('recover', help='explicitly preserve raw file and restore last committed state')
    recover.add_argument('--task', required=True)
    recover.add_argument('--expected-digest', required=True, help='observed SHA256, or missing for a deleted state.json')
    facts = commands.add_parser('observations', help='read configured collector facts; never edits source or SQLite')
    facts.add_argument('--observer-config', type=Path, required=True)
    facts.add_argument('--task')
    return p


def execute(args):
    command = args.command
    if command == 'schema':
        return {'schema': published(args.kind)}
    if command == 'session-register':
        context = {'schema_version': 1, 'session_id': args.session, 'identity_source': 'explicit_registration'}
        validate(context, 'context')
        write_new(args.out, context)
        return {'context': context, 'path': str(args.out), 'identity_verified': False}
    if command == 'whoami':
        context = validate(load_json(args.context), 'context')
        return {'context': context, 'identity_verified': False}
    if command == 'validate' and args.file:
        validate(load_json(args.file), args.kind)
        return {'valid': True, 'kind': args.kind}
    root = args.root.expanduser().absolute()
    source_directory = Path.home() / '.local/share/devin'
    if root.resolve().is_relative_to(source_directory.resolve()):
        raise ValueError('Observer state must be outside the Devin source directory')
    core = CooperativeState(root)
    if command == 'create':
        return core.create(args.task, args.title, args.session, args.request_id)
    if command == 'list':
        return {'tasks': core.list(args.session)}
    if command == 'read':
        return core.read(args.task)
    if command == 'history':
        return {'history': core.store.history(args.task)}
    if command == 'link':
        return core.link(args.task, args.session, args.expected_revision, args.request_id)
    if command == 'reporting':
        return core.reporting(args.task, args.session, args.enabled == 'on', args.expected_revision, args.request_id)
    if command == 'pin':
        return core.pin(args.task, args.item, args.pinned == 'on', args.expected_revision, args.request_id)
    if command == 'note':
        return core.note(args.task, args.text, args.expected_revision, args.request_id)
    if command == 'resource-put':
        return core.resource_put(args.task, load_json(args.file), args.expected_revision, args.request_id)
    if command == 'resource-archive':
        return core.resource_archive(args.task, args.resource, args.archived == 'on', args.expected_revision, args.request_id)
    if command == 'report':
        request = validate(load_json(args.file), 'report')
        if args.context:
            context = validate(load_json(args.context), 'context')
            if context['session_id'] != request['session_id']:
                raise StateError('session_mismatch', 'report session differs from registered context')
        return core.report(request)
    if command == 'edit-export':
        value = core.export_edit(args.task)
        write_new(args.out, value)
        return {'path': str(args.out), 'expected_revision': value['expected_revision']}
    if command == 'edit-apply':
        return core.apply_edit(load_json(args.file), args.request_id)
    if command == 'recover':
        return core.store.recover(args.task, None if args.expected_digest == 'missing' else args.expected_digest)
    if command == 'validate':
        rows = [core.read(args.task)] if args.task else core.list()
        return {'valid': all(row['health'] == 'ok' for row in rows), 'tasks': rows}
    if command == 'observations':
        from .state_observations import observations
        sessions = None
        if args.task:
            row = core.read(args.task)
            if row['health'] != 'ok':
                raise StateError('unhealthy_task', 'cannot use invalid task links', row)
            sessions = [c['session_id'] for c in row['state']['contributions']]
        return observations(args.observer_config, sessions)
    raise ValueError('unsupported command')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        # Mutation receipts need not send the whole View back into agent context.
        # A replay describes the original commit, never a freshly read task state.
        if 'report_id' in result and isinstance(result.get('tasks'), dict):
            result = {**result, 'tasks': {key: {'revision': value['revision']} for key, value in result['tasks'].items()},
                      'snapshot': 'original_commit' if result.get('replayed') else 'committed'}
        if args.command == 'read' and result.get('health') == 'ok':
            result.pop('last_good', None)
        if args.command == 'list':
            compact = []
            for row in result['tasks']:
                value = row.get('state') if row['health'] == 'ok' else row.get('last_good')
                compact.append({'task_id': row['task_id'], 'health': row['health'], 'errors': row['errors'],
                                'title': value['title'] if value else None, 'revision': value['revision'] if value else None,
                                'item_count': len(value['items']) if value else None,
                                'contributions': value['contributions'] if value else [],
                                'snapshot': 'current' if row['health'] == 'ok' else 'last_good'})
            result['tasks'] = compact
        print(json.dumps({'ok': True, **result}, ensure_ascii=False, allow_nan=False))
        return 0 if result.get('valid', True) else 2
    except StateError as error:
        details = dict(error.details)
        if error.code == 'revision_conflict' and isinstance(details.get('current'), dict):
            # Healthy conflicts already contain the full current state. Do not
            # spend the working agent's context on an identical last_good copy.
            def compact(row):
                if isinstance(row, dict) and row.get('health') == 'ok':
                    return {key: value for key, value in row.items() if key != 'last_good'}
                return row
            current = details['current']
            details['current'] = (compact(current) if 'task_id' in current else
                                  {key: compact(row) for key, row in current.items()})
        print(json.dumps({'ok': False, 'error': {'code': error.code, 'message': str(error),
                                               'details': details}}, ensure_ascii=False))
        return 2
    except (ValueError, OSError, RecursionError) as error:
        code = 'io_error' if isinstance(error, OSError) else 'validation_error'
        print(json.dumps({'ok': False, 'error': {'code': code, 'message': str(error)}}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
