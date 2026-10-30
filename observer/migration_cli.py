"""Explicit legacy migration commands; never select or overwrite data implicitly."""
import argparse
import json
from pathlib import Path
import sqlite3

from .file_store import StateError
from .migration import plan_migration, apply_migration, check_migration, rollback_migration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'apply'):
        sub = commands.add_parser(name)
        sub.add_argument('--source', type=Path, required=True, help='Observer SQLite, never Devin sessions.db')
        selection = sub.add_mutually_exclusive_group(required=True)
        selection.add_argument('--task', action='append')
        selection.add_argument('--all', action='store_true')
        if name == 'apply':
            sub.add_argument('--target', type=Path, required=True, help='new dedicated state directory')
            sub.add_argument('--expected-fingerprint')
    check = commands.add_parser('check')
    check.add_argument('--target', type=Path, required=True)
    rollback = commands.add_parser('rollback')
    rollback.add_argument('--target', type=Path, required=True)
    rollback.add_argument('--restore-to', type=Path, required=True, help='new SQLite file; original source stays unchanged')
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            result = plan_migration(args.source, task_ids=args.task, all_tasks=args.all)
        elif args.command == 'apply':
            result = apply_migration(args.source, args.target, task_ids=args.task, all_tasks=args.all,
                                     expected_fingerprint=args.expected_fingerprint)
        elif args.command == 'check':
            result = check_migration(args.target)
        else:
            result = rollback_migration(args.target, args.restore_to)
        print(json.dumps({'ok': True, **result}, ensure_ascii=False, allow_nan=False))
        return 0
    except StateError as error:
        result = {'code': error.code, 'message': str(error), 'details': error.details}
    except (OSError, ValueError, sqlite3.Error) as error:
        result = {'code': 'migration_error', 'message': str(error)}
    print(json.dumps({'ok': False, 'error': result}, ensure_ascii=False))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
