#!/usr/bin/env python3
"""Local metadata-only comparison for a controlled Devin Desktop P0 experiment.

This is a filesystem probe, not a Devin session adapter. Run it only on directories
created by the experiment. It never writes to the observed roots or emits contents.
"""
import argparse
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path

CHUNK = 1024 * 1024
MAX_FILE = 1024 * 1024 * 1024


def scan(root: Path, salt: bytes):
    """Hash regular files without following symlinks; flag concurrent changes."""
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("root must be an existing non-symlink directory")
    result = {}
    errors = 0
    def onerror(_error):
        nonlocal errors
        errors += 1

    for current, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            key = hashlib.sha256(salt + os.fsencode(str(path.relative_to(root)))).hexdigest()
            try:
                if path.is_symlink():
                    continue
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                fd = os.open(path, flags)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode):
                        continue
                    if before.st_size > MAX_FILE:
                        errors += 1
                        continue
                    digest = hashlib.sha256()
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        for block in iter(lambda: stream.read(CHUNK), b''):
                            digest.update(block)
                    after = os.fstat(fd)
                    path_after = os.stat(path, follow_symlinks=False)
                    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                    if signature(before) != signature(after) or signature(after) != signature(path_after):
                        errors += 1
                        continue
                    result[key] = {'size': after.st_size, 'digest': digest.hexdigest()}
                finally:
                    os.close(fd)
            except OSError:
                errors += 1
    return result, errors


def compare(previous, current):
    old, new = set(previous), set(current)
    return {'added': len(new - old), 'removed': len(old - new),
            'changed': sum(previous[k] != current[k] for k in old & new),
            'unchanged': sum(previous[k] == current[k] for k in old & new)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='controlled test directory only')
    parser.add_argument('state', type=Path, help='private local snapshot file outside root')
    args = parser.parse_args()
    if args.root.is_symlink():
        parser.error('root must not be a symlink')
    root = args.root.resolve(strict=True)
    state = args.state.absolute()
    if state.resolve().is_relative_to(root):
        parser.error('snapshot must be outside the observed root')
    if state.exists():
        saved = json.loads(state.read_text())
        if saved['root'] != str(root):
            parser.error('snapshot belongs to a different root')
        salt = bytes.fromhex(saved['salt'])
        previous = saved['files']
    else:
        salt = secrets.token_bytes(32)
        previous = {}
    current, errors = scan(root, salt)
    print(json.dumps({'files': len(current), 'comparison': compare(previous, current),
                      'unreadable_or_unstable': errors, 'snapshot_committed': errors == 0}))
    if errors:
        raise SystemExit(2)
    # Replace atomically with restrictive permissions; state contains hashes, not content.
    temporary = state.with_name(state.name + '.' + secrets.token_hex(8) + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'root': str(root), 'salt': salt.hex(), 'files': current}, stream)
        os.replace(temporary, state)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == '__main__':
    main()
