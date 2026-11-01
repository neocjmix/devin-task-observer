"""Explicit, local-only legacy migration. Source is always SQLite read-only.

A migration publishes a new dedicated file store; it never merges into an existing
store or changes the legacy DB/configuration. Original rows remain in per-task
archives plus a consistent SQLite backup. Rollback retires only an untouched
import and restores that backup to a new path, never over the source.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid

from .cooperative import CooperativeState
from .file_store import (FileStore, StateError, MAX_RECORD_BYTES, MAX_STATE_BYTES, MAX_TRANSACTION_TASKS,
                         _encode, _hash, _decode)
from .state_schema import validate
from .views import get_view
from .registry import task_resources

MAX_BACKUP_BYTES = 128 * 1024 * 1024
MAX_ROWS = 50000


def _error(code, message, **details):
    raise StateError(code, message, details)


def _stamp(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _path(path):
    path = Path(path).expanduser().absolute()
    return path.parent.resolve() / path.name


def _outside_devin(path):
    source_root = (Path.home() / '.local' / 'share' / 'devin').resolve()
    if path.resolve().is_relative_to(source_root):
        _error('unsafe_path', 'Migration output must be outside Devin session storage', path=str(path))


def _regular(path):
    path = _path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        _error('unsafe_path', 'Migration source/backup must be an ordinary file', path=str(path))
    return path


def _snapshot(source, backup):
    source = _regular(source)
    if source.stat().st_size > MAX_BACKUP_BYTES:
        _error('migration_limit', 'Legacy database exceeds the 128 MiB backup limit')
    incoming = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=2)
    outgoing = None
    try:
        incoming.execute('PRAGMA query_only=ON')
        incoming.execute('PRAGMA trusted_schema=OFF')
        names = {r[0] for r in incoming.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'tasks', 'contributions', 'task_views', 'view_sections', 'view_items', 'checkpoints', 'source_nodes'}
        if not required <= names:
            _error('incompatible_source', 'Expected an Observer SQLite store, not a Devin source database')
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        outgoing = sqlite3.connect(backup)
        deadline = time.monotonic() + 15
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                _error('migration_busy', 'Source backup did not settle within 15 seconds; retry later')
            if total * 4096 > MAX_BACKUP_BYTES * 16:
                _error('migration_limit', 'Source backup exceeds the bounded migration size')
        incoming.backup(outgoing, pages=256, progress=progress, sleep=.01)
        outgoing.close(); outgoing = None
        if Path(backup).stat().st_size > MAX_BACKUP_BYTES:
            _error('migration_limit', 'Legacy backup exceeds 128 MiB')
    except sqlite3.Error as exc:
        _error('incompatible_source', 'Could not read a consistent Observer backup', reason=str(exc))
    finally:
        incoming.close()
        if outgoing is not None:
            outgoing.close()
    with Path(backup).open('rb') as stream:
        os.fsync(stream.fileno())
    return source


def _read_backup(backup, task_ids, all_tasks):
    db = sqlite3.connect(Path(backup).as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            _error('invalid_source', 'SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            _error('invalid_source', 'Legacy references are broken; source remains unchanged')
        if (task_ids is None) == (not all_tasks):
            _error('migration_selection', 'Supply explicit task IDs or all_tasks=True, never both')
        available = {r[0] for r in db.execute('SELECT id FROM tasks')}
        selected = sorted(available if all_tasks else set(task_ids))
        if not selected or not set(selected) <= available:
            _error('migration_selection', 'Selection must contain existing task IDs', unknown=sorted(set(selected) - available))
        if len(selected) > MAX_TRANSACTION_TASKS:
            _error('migration_limit', 'At most 32 tasks can be imported atomically; no partial import performed')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        from .store import SCHEMA, DISCOVERY_SCHEMA, PLAN_SCHEMA, TRACKING_SCHEMA
        known = set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', SCHEMA + DISCOVERY_SCHEMA + PLAN_SCHEMA + TRACKING_SCHEMA))
        if tables - known:
            _error('unsupported_schema', 'Unrecognized legacy tables require explicit migration support', tables=sorted(tables - known))
        columns = {t: [r[1] for r in db.execute('PRAGMA table_info("' + t + '")')] for t in tables}
        def rows(table, column, keys):
            if table not in tables or not keys:
                return []
            marks = ','.join('?' for _ in keys)
            found = [dict(r) for r in db.execute(f'SELECT * FROM "{table}" WHERE "{column}" IN ({marks}) LIMIT ?', (*keys, MAX_ROWS + 1))]
            if len(found) > MAX_ROWS:
                _error('migration_limit', 'Table selection exceeds 50,000 rows', table=table)
            return sorted(found, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
        exports = {}
        for task_id in selected:
            archive = {'schema_version': 1, 'source': 'observer_legacy_sqlite', 'task_id': task_id, 'tables': {}}
            records = archive['tables']
            for table in sorted(tables):
                if 'task_id' in columns[table]:
                    records[table] = rows(table, 'task_id', [task_id])
            records['tasks'] = rows('tasks', 'id', [task_id])
            groups = [('contributions', 'id', ('contribution_state', 'contribution_history', 'contribution_metadata_history'), 'contribution_id'),
                      ('resources', 'id', ('resource_state', 'resource_history'), 'resource_id'),
                      ('task_views', 'id', ('view_sections',), 'view_id'),
                      ('view_sections', 'id', ('view_items',), 'section_id'),
                      ('view_items', 'id', ('view_item_history', 'item_evidence'), 'item_id'),
                      ('tracking_items', 'id', ('tracking_history',), 'item_id'),
                      ('semantic_runs', 'id', ('semantic_proposals', 'semantic_retry_attempts', 'semantic_lock_runs', 'semantic_input_keys'), 'run_id')]
            for base, key, children, foreign in groups:
                keys = [r[key] for r in records.get(base, [])]
                for child in children:
                    records[child] = rows(child, foreign, keys)
            sids = sorted({r['session_id'] for r in records['contributions']})
            records['sessions'] = rows('sessions', 'id', sids)
            for table in ('session_metadata', 'session_metadata_history', 'checkpoints', 'session_health', 'session_validation'):
                records[table] = rows(table, 'session_id', sids)
            archive['view'] = get_view(db, task_id)
            archive['resources'] = task_resources(db, task_id, include_archived=True)
            exports[task_id] = archive
        # Fingerprint only selected logical records: later unrelated collection does
        # not invalidate an already reviewed task-state export.
        fingerprint = _hash(_encode(exports))
        return exports, fingerprint
    except (sqlite3.Error, TypeError, ValueError) as exc:
        if isinstance(exc, StateError):
            raise
        _error('incompatible_source', 'Legacy structure cannot be exported safely', reason=str(exc))
    finally:
        db.close()


def _project_unchecked(archive, migration_id):
    rows = archive['tables']; task = rows['tasks'][0]; task_id = task['id']
    link_states = {r['contribution_id']: r['active'] for r in rows['contribution_state']}
    active = [r for r in rows['contributions'] if link_states.get(r['id'], 1)]
    sids = {r['session_id'] for r in active}
    actor = {'kind': 'legacy', 'session_id': None}
    report_id = 'migration-' + migration_id
    archive_ref = 'migration/archive/' + task_id + '.json'
    state = {'schema_version': 1, 'task_id': task_id, 'title': task['title'], 'revision': 1,
             'updated_at': _stamp(task['updated_at']), 'updated_by': actor.copy(),
             'contributions': [{'session_id': r['session_id'], 'reporting': False,
                               'linked_at': _stamp(r['linked_at']), 'last_report_at': None} for r in active],
             'items': [], 'notes': [], 'view': {'markdown': ''},
             'legacy': {'migration_id': migration_id, 'source_task_id': task_id,
                        'source_revision': task['revision'], 'description': task['description'],
                        'lifecycle': task['lifecycle'], 'created_at': task['created_at'],
                        'archive_ref': archive_ref, 'archive_sha256': _hash(_encode(archive)),
                        'view': archive['view'], 'resources': archive['resources'],
                        'review_cursors': rows.get('task_session_progress', []),
                        'archived_contributions': [r for r in rows['contributions'] if not link_states.get(r['id'], 1)]}}
    def item(iid, title, content, status, pinned, stamp, evidence):
        mapped = []
        for e in evidence:
            if e.get('session_id') in sids and 'node_id' in e and 'content_hash' in e:
                mapped.append({'session_id': e['session_id'], 'kind': 'session_event',
                               'reference': 'legacy:node:' + str(e['node_id']) + ':sha256:' + e['content_hash']})
        return {'item_id': iid, 'title': title, 'value': content, 'status': status,
                'evidence': mapped, 'pinned': bool(pinned), 'archived': False,
                'created_by': actor.copy(), 'updated_by': actor.copy(), 'item_revision': 1,
                'commit_sequence': 1, 'updated_at': _stamp(stamp), 'report_id': report_id,
                'verification': 'legacy_import'}
    # Semantic current items remain actionable, but their old writer/epistemic
    # state is not guessed into a new verified claim or session identity.
    for row in rows.get('tracking_items', []):
        evidence = json.loads(row['evidence'])
        state['items'].append(item(row['id'], row['category'] + ': ' + row['item_key'], row['content'],
                                   row['status'], row['pinned'], row['updated_at'], evidence))
    # Preserve the full arbitrary View. Its human text is also accessible as notes;
    # pinned View items gain protected current IDs so reporting cannot reuse them.
    view_evidence = {}
    for row in rows.get('item_evidence', []):
        view_evidence.setdefault(row['item_id'], []).append(row)
    sections = {r['id']: r['title'] for r in rows.get('view_sections', [])}
    for row in rows.get('view_items', []):
        if row['origin'] == 'human':
            state['notes'].append({'note_id': row['id'], 'text': row['content'], 'created_at': _stamp(row['created_at'])})
        state['items'].append(item(row['id'], sections[row['section_id']], row['content'], row['status'],
                                   row['pinned'], row['updated_at'], view_evidence.get(row['id'], [])))
    try:
        validate(state, 'task'); _encode(state, MAX_STATE_BYTES)
    except (ValueError, TypeError, KeyError) as exc:
        _error('migration_incompatible', 'A legacy task exceeds or conflicts with the new schema; nothing imported', task_id=task_id, reason=str(exc))
    return state


def _project(archive, migration_id):
    try:
        return _project_unchecked(archive, migration_id)
    except StateError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        _error('migration_incompatible', 'Legacy fields cannot be projected safely; original preserved',
               task_id=archive.get('task_id'), reason=str(exc))


def _summary(exports, fingerprint):
    return {'source_fingerprint': fingerprint, 'tasks': [{'task_id': tid, 'title': a['tables']['tasks'][0]['title'],
            'linked_sessions': len(a['tables']['contributions']), 'view_items': len(a['tables']['view_items']),
            'tracking_items': len(a['tables'].get('tracking_items', []))} for tid, a in exports.items()],
            'limitations': ['reporting starts disabled', 'legacy View/resources/cursors are preserved snapshots',
                           'collector checkpoints remain in SQLite; no automatic configuration switch',
                           'only a new dedicated file-store root is supported',
                           'rollback is refused after any intervening file-store change']}


def plan_migration(source, task_ids=None, all_tasks=False):
    """Read-only dry-run; validates every projected task without creating a store."""
    with tempfile.TemporaryDirectory(prefix='observer-migration-check-') as temp:
        backup = Path(temp) / 'legacy.sqlite'; _snapshot(source, backup)
        exports, fingerprint = _read_backup(backup, task_ids, all_tasks)
        for archive in exports.values():
            _project(archive, 'dry-run')
        return {'mode': 'dry_run', **_summary(exports, fingerprint)}


def _inventory(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():
            _error('unsafe_path', 'Migration tree contains a symbolic link', path=str(path))
        if path.is_file():
            relative = str(path.relative_to(root))
            if relative not in ('.lock', '.retired.json', 'migration/manifest.json', 'migration/manifest.sha256'):
                if path.stat().st_nlink != 1:
                    _error('unsafe_path', 'Migration files must not be hard-linked', path=str(path))
                result[relative] = _file_hash(path)
    return result


def apply_migration(source, target, task_ids=None, all_tasks=False, expected_fingerprint=None):
    """Stage and publish an all-or-nothing, new store; never overwrite a target."""
    target = _path(target)
    _outside_devin(target)
    if (task_ids is None) == (not all_tasks):
        _error('migration_selection', 'Supply explicit task IDs or all_tasks=True, never both')
    if target.exists() or target.is_symlink():
        # Replaying a reviewed fingerprint does not need the source to still exist.
        # Without one, re-read the source rather than accept changed intent.
        manifest = _manifest(target)
        selection = sorted(manifest['task_ids'] if all_tasks else set(task_ids or []))
        if manifest['source'] == str(_path(source)) and selection == manifest['task_ids']:
            fingerprint = expected_fingerprint
            if fingerprint is None:
                fingerprint = plan_migration(source, task_ids, all_tasks)['source_fingerprint']
            if fingerprint != manifest['source_fingerprint']:
                _error('source_changed', 'A different source snapshot cannot replace the existing migration')
            checked = check_migration(target)
            return {'mode': 'apply', 'replayed': True, 'target': str(target), **checked}
        _error('destination_exists', 'Migration requires a new dedicated destination; existing state is unchanged')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = _path(tempfile.mkdtemp(prefix='.' + target.name + '-migration-', dir=target.parent))
    try:
        core = CooperativeState(staging)
        migration_dir = staging / 'migration'; migration_dir.mkdir(mode=0o700)
        backup = migration_dir / 'legacy.sqlite'; source = _snapshot(source, backup)
        exports, fingerprint = _read_backup(backup, task_ids, all_tasks)
        if expected_fingerprint is not None and expected_fingerprint != fingerprint:
            _error('source_changed', 'Legacy task state changed since dry-run; review a fresh plan')
        migration_id = str(uuid.uuid4())
        states = {tid: _project(a, migration_id) for tid, a in exports.items()}
        for tid, archive in exports.items():
            core.store._atomic(staging / states[tid]['legacy']['archive_ref'], _encode(archive))
        core.store.transact('migration-' + migration_id, {'action': 'migration', 'source_fingerprint': fingerprint},
                            {tid: None for tid in states}, lambda _: copy.deepcopy(states))
        manifest = {'schema_version': 1, 'migration_id': migration_id, 'source': str(source),
                    'source_fingerprint': fingerprint, 'backup_sha256': _file_hash(backup),
                    'task_ids': sorted(states), 'created_at': datetime.now(timezone.utc).isoformat(),
                    'initial_files': _inventory(staging)}
        manifest_data = _encode(manifest)
        core.store._atomic(migration_dir / 'manifest.json', manifest_data)
        core.store._atomic(migration_dir / 'manifest.sha256', (_hash(manifest_data) + '\n').encode('ascii'))
        # A normal FileStore creates nonempty directory entries immediately; POSIX
        # rename cannot replace a populated target. Recheck handles existing paths.
        if target.exists() or target.is_symlink():
            _error('destination_exists', 'Destination appeared during migration; it was not changed')
        os.rename(staging, target)
        FileStore._sync_dir(target.parent)
        return {'mode': 'apply', 'replayed': False, 'target': str(target), 'migration_id': migration_id,
                **_summary(exports, fingerprint)}
    except OSError as exc:
        _error('migration_io', 'Migration could not publish the new store; source is unchanged', reason=str(exc))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _manifest(target):
    target = _path(target)
    if target.is_symlink():
        _error('unsafe_path', 'Migration root cannot be a symlink')
    path = target / 'migration' / 'manifest.json'
    if not path.is_file() or path.is_symlink():
        _error('destination_exists', 'Destination is not a recognized migration store')
    _regular(path)
    if path.stat().st_size > MAX_RECORD_BYTES:
        _error('invalid_manifest', 'Migration manifest exceeds its size limit')
    data = path.read_bytes()
    checksum = _regular(target / 'migration' / 'manifest.sha256')
    if checksum.stat().st_size != 65 or checksum.read_text('ascii').strip() != _hash(data):
        _error('invalid_manifest', 'Migration manifest changed; original files are preserved')
    value = _decode(data)
    fields = {'schema_version', 'migration_id', 'source', 'source_fingerprint', 'backup_sha256',
              'task_ids', 'created_at', 'initial_files'}
    if not isinstance(value, dict) or set(value) != fields or value['schema_version'] != 1:
        _error('invalid_manifest', 'Migration manifest is incompatible')
    if (not isinstance(value['initial_files'], dict) or not isinstance(value['task_ids'], list)
            or not value['task_ids'] or len(value['task_ids']) > MAX_TRANSACTION_TASKS):
        _error('invalid_manifest', 'Migration manifest has invalid task/file records')
    for relative, digest in value['initial_files'].items():
        if (not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            _error('invalid_manifest', 'Migration manifest has an unsafe file record')
    return value


def check_migration(target):
    target = _path(target); manifest = _manifest(target)
    actual = _inventory(target)
    backup = _regular(target / 'migration' / 'legacy.sqlite')
    if _file_hash(backup) != manifest['backup_sha256']:
        _error('backup_changed', 'Legacy backup changed; it cannot be trusted for restoration')
    errors = []
    for relative, digest in manifest['initial_files'].items():
        if relative.startswith('migration/') and actual.get(relative) != digest:
            errors.append({'path': relative, 'code': 'archive_changed'})
    retired = (target / '.retired.json').exists()
    if not retired:
        for row in CooperativeState(target).list():
            if row['health'] != 'ok':
                errors.append({'task_id': row['task_id'], 'code': row['health']})
    return {'migration_id': manifest['migration_id'], 'task_ids': manifest['task_ids'],
            'source_fingerprint': manifest['source_fingerprint'], 'retired': retired,
            'rollback_eligible': actual == manifest['initial_files'] and not errors,
            'errors': errors}


def rollback_migration(target, restore_path):
    """Retire unchanged migration and restore backup to a NEW SQLite path.

    Existing source is never replaced. Retirement under the same store lock blocks
    already-waiting writers; a retry after retirement may finish an interrupted
    restoration. Original and imported snapshots remain available on disk.
    """
    target = _path(target); restored = _path(restore_path)
    _outside_devin(target); _outside_devin(restored)
    manifest = _manifest(target)
    if restored == target or restored.is_relative_to(target):
        _error('unsafe_path', 'Restored DB must be outside the retired store')
    store = FileStore(target)
    # A prior retirement is final for ordinary APIs. Recovery can re-enter only
    # through this explicit method and use its original stable lock inode.
    import fcntl
    lock = target / '.lock'; store._safe(lock)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _error('unsafe_path', 'Commit lock must be an ordinary file')
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _error('busy', 'File-store commit is busy; retry rollback later')
                time.sleep(.02)
        if store._recover():
            _error('rollback_conflict', 'Pending file-store recovery prevents rollback')
        if _inventory(target) != manifest['initial_files']:
            _error('rollback_conflict', 'File-store state changed after migration; rollback would lose changes')
        backup = _regular(target / 'migration' / 'legacy.sqlite')
        if _file_hash(backup) != manifest['backup_sha256']:
            _error('backup_changed', 'Backup hash changed; rollback refused')
        if restored.exists() or restored.is_symlink():
            if (target / '.retired.json').exists() and not restored.is_symlink() and _file_hash(restored) == manifest['backup_sha256']:
                return {'retired': True, 'restored': str(restored), 'replayed': True}
            _error('destination_exists', 'Restore destination must be a new file')
        restored.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        store._atomic(target / '.retired.json', _encode({'schema_version': 1, 'migration_id': manifest['migration_id'],
                       'restore_path': str(restored), 'source_unchanged': True}))
        fd_temp, name = tempfile.mkstemp(prefix='.observer-restore-', dir=restored.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd_temp, 'wb') as stream, backup.open('rb') as incoming:
                shutil.copyfileobj(incoming, stream); stream.flush(); os.fsync(stream.fileno())
            os.link(temp, restored)  # exclusive publication: never replace anything
            temp.unlink(); FileStore._sync_dir(restored.parent)
        finally:
            if temp.exists(): temp.unlink()
        return {'retired': True, 'restored': str(restored), 'replayed': False, 'source_unchanged': True}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
