"""Manual refresh or polling of explicitly allowed personal workspaces."""
import argparse
import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path

from .devin_local import SourceIncomplete, read_sessions
from .store import mark_source_unavailable, open_store, sync


def main():
    home = Path.home()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', action='append', required=True, type=Path, help='personal workspace to observe; repeat for more')
    p.add_argument('--source', type=Path, default=home / '.local/share/devin/cli/sessions.db')
    p.add_argument('--store', type=Path, default=home / 'Library/Application Support/Devin Task Observer/observer.sqlite3')
    p.add_argument('--poll', type=float, help='repeat at this interval in seconds, minimum 1')
    args = p.parse_args()
    if args.poll is not None and args.poll < 1:
        p.error('poll interval must be at least one second')
    try:
        db = open_store(args.store, args.source)
        lock_path = args.store.absolute().resolve().with_suffix(args.store.suffix + '.lock')
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(json.dumps({'ok': False, 'stage': 'store', 'error': type(error).__name__}), flush=True)
        return 2
    try:
        while True:
            locked = False
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                before = args.source.stat()
                snapshot = read_sessions(args.source, args.workspace)
                after = args.source.stat()
                identity = (before.st_dev, before.st_ino)
                if identity != (after.st_dev, after.st_ino):
                    raise SourceIncomplete('source replaced while reading')
                stats = {}
                count = sync(db, snapshot, args.workspace, identity, stats)
                allowed_workspaces = {str(workspace.absolute()) for workspace in args.workspace}
                stale = sum(1 for status,workspace in db.execute('SELECT status,workspace FROM sessions') if status == 'stale' and workspace in allowed_workspaces)
                print(json.dumps({'ok': True, 'sessions': len(snapshot), 'transitions': count, 'metadata_changes':stats['metadata_changes'], 'stale_sessions': stale}), flush=True)
            except BlockingIOError:
                print(json.dumps({'ok': False, 'stage': 'lock', 'error': 'busy'}), flush=True)
                if args.poll is None:
                    return 2
            except (OSError, sqlite3.Error, SourceIncomplete, ValueError) as error:
                # Preserve checkpoints; a source failure cannot be treated as fresh.
                try:
                    mark_source_unavailable(db, args.workspace)
                except sqlite3.Error:
                    pass
                print(json.dumps({'ok': False, 'stage': 'refresh', 'error': type(error).__name__}), flush=True)
                if args.poll is None:
                    return 2
            finally:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            if args.poll is None:
                return 0
            time.sleep(args.poll)
    except KeyboardInterrupt:
        return 0
    finally:
        os.close(lock_fd)
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
