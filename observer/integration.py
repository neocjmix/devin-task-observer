"""Explicit, reversible project hooks for cooperative reporting.

Only installed workspace configuration is written. Hook payload text is never
persisted. Hooks are diagnostic/context helpers, not report generators or gates.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import uuid

from .file_store import FileStore, StateError, _decode, _encode, _hash
from .state_cli import DEFAULT_ROOT
from .state_schema import validate

REPOSITORY = Path(__file__).resolve().parents[1]
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostCompaction', 'Stop', 'SessionEnd')
HOOKS_FILE = '.devin/hooks.v1.json'
SKILL_DIRECTORY = '.devin/skills/observer-reporting'
MAX_HOOK_BYTES = 2 * 1024 * 1024


def _workspace(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError('workspace must be an existing directory')
    if path == Path.home().resolve():
        raise ValueError('choose a project workspace, not the home directory')
    if path.is_relative_to((Path.home() / '.local/share/devin').resolve()):
        raise ValueError('integration cannot modify Devin session storage')
    return path


def _store(root, *, hook=False):
    path = Path(root).expanduser().absolute()
    if path.resolve().is_relative_to((Path.home() / '.local/share/devin').resolve()):
        raise ValueError('Observer state must be outside the Devin source directory')
    return FileStore(path, lock_timeout=.2 if hook else 2)


def _manifest_path(store, workspace):
    return store.root / 'integrations' / _hash(str(workspace).encode()) / 'manifest.json'


def _path(workspace, relative):
    path = workspace / relative
    path.relative_to(workspace)
    current = workspace
    for part in Path(relative).parts:
        if part in ('.', '..') or Path(relative).is_absolute():
            raise ValueError('invalid integration path')
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise StateError('unsafe_path', 'integration paths must not be symlinks', {'path': str(current)})
    return path


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StateError('unsafe_path', 'integration files must be ordinary files')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_HOOK_BYTES + 1)
        if len(raw) > MAX_HOOK_BYTES:
            raise ValueError('integration file exceeds 2 MiB')
        return raw
    finally:
        os.close(fd)


def _write(workspace, relative, raw):
    path = _path(workspace, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    _path(workspace, relative)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        _path(workspace, relative)
        os.replace(temporary, path)
        FileStore._sync_dir(path.parent)
    finally:
        os.close(fd)
        if temporary.exists():
            temporary.unlink()


def _hooks(raw):
    value = {} if raw is None else _decode(raw)
    if not isinstance(value, dict) or any(not isinstance(rows, list) for rows in value.values()):
        raise ValueError('.devin/hooks.v1.json must map events to hook arrays')
    if any(not isinstance(row, dict) or not isinstance(row.get('hooks'), list) or
           any(not isinstance(hook, dict) for hook in row['hooks'])
           for rows in value.values() for row in rows):
        raise ValueError('.devin/hooks.v1.json contains an invalid hook group')
    return value


def _load_manifest(store, workspace):
    raw = store._read_bytes(_manifest_path(store, workspace))
    if raw is None:
        return None
    value = _decode(raw)
    if (not isinstance(value, dict) or value.get('version') != 1 or
            value.get('workspace') != str(workspace) or value.get('root') != str(store.root) or
            value.get('phase') not in ('installing', 'installed', 'removing', 'removed')):
        raise StateError('invalid_installation', 'integration manifest is invalid; preserve it for recovery')
    # A local manifest is not an authority to delete arbitrary workspace paths.
    expected = {SKILL_DIRECTORY + '/SKILL.md', SKILL_DIRECTORY + '/references/cli.md'}
    if set(value.get('files', {})) != expected or set(value.get('hooks', {})) != set(EVENTS):
        raise StateError('invalid_installation', 'integration ownership record is invalid')
    for entry in value['files'].values():
        if not isinstance(entry, dict) or not isinstance(entry.get('content'), str) or _hash(entry['content'].encode()) != entry.get('sha256'):
            raise StateError('invalid_installation', 'integration file checksum is invalid')
    return value


def _save_manifest(store, workspace, value):
    store._atomic(_manifest_path(store, workspace), _encode(value))


def _owned_hook_count(hooks, event, entry):
    return sum(row == entry for row in hooks.get(event, []))


def _check_files(workspace, manifest, *, allow_missing=False):
    errors = []
    for relative, entry in manifest['files'].items():
        raw = _read(_path(workspace, relative))
        if raw is None and allow_missing:
            continue
        if raw is None or _hash(raw) != entry['sha256']:
            errors.append(relative)
    if errors:
        raise StateError('modified_integration', 'owned files changed; preserve edits before reinstall/remove', {'paths': errors})


def install(workspace, root, *, cli=None, python=None):
    workspace = _workspace(workspace)
    store = _store(root)
    cli = Path(cli or REPOSITORY / 'observerctl').resolve(strict=True)
    python = Path(python or sys.executable).resolve(strict=True)
    if not cli.is_file() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('integration requires an existing observerctl and executable Python')
    with store._locked():
        manifest = _load_manifest(store, workspace)
        if manifest and manifest['phase'] == 'installed':
            _check_files(workspace, manifest)
            hooks = _hooks(_read(_path(workspace, HOOKS_FILE)))
            if any(_owned_hook_count(hooks, event, entry) != 1 for event, entry in manifest['hooks'].items()):
                raise StateError('modified_integration', 'owned hooks changed; preserve configuration before reinstall')
            return {'installed': True, 'unchanged': True, 'workspace': str(workspace), 'root': str(store.root)}
        if manifest and manifest['phase'] == 'removing':
            raise StateError('incomplete_removal', 'finish integration remove before reinstall')
        if manifest is None or manifest['phase'] == 'removed':
            folder = _path(workspace, SKILL_DIRECTORY)
            if folder.exists():
                raise StateError('existing_skill', 'observer-reporting skill already exists; preserve it before installing')
            raw = _read(_path(workspace, HOOKS_FILE))
            _hooks(raw)
            command = shlex.join([str(python), str(cli), 'integration', 'hook', '--workspace', str(workspace), '--root', str(store.root)])
            hook = {'hooks': [{'type': 'command', 'command': command, 'timeout': 3}]}
            skill = (REPOSITORY / 'skills/observer-reporting/SKILL.md').read_text()
            skill = skill.replace('./observerctl', shlex.quote(str(cli)))
            binding = ('\n## Installed workspace binding\n\n'
                       'Use these installation-specific paths with the context path supplied by the Devin hook.\n\n'
                       f'- CLI: `{cli}`\n- State root: `{store.root}`\n- Workspace: `{workspace}`\n'
                       'The hook-provided session context identifies this session; do not select another context from the directory.\n')
            skill += binding
            files = {
                SKILL_DIRECTORY + '/SKILL.md': skill,
                SKILL_DIRECTORY + '/references/cli.md': (REPOSITORY / 'skills/observer-reporting/references/cli.md').read_text().replace('./observerctl', shlex.quote(str(cli))),
            }
            manifest = {'version': 1, 'phase': 'installing', 'workspace': str(workspace), 'root': str(store.root),
                        'cli': str(cli), 'python': str(python), 'hooks': {event: hook for event in EVENTS},
                        'original_hooks': None if raw is None else base64.b64encode(raw).decode(),
                        'files': {name: {'content': text, 'sha256': _hash(text.encode())} for name, text in files.items()}}
            # Durable recovery record comes before any workspace mutation.
            _save_manifest(store, workspace, manifest)
        _check_files(workspace, manifest, allow_missing=True)
        current = _hooks(_read(_path(workspace, HOOKS_FILE)))
        for event, entry in manifest['hooks'].items():
            count = _owned_hook_count(current, event, entry)
            if count > 1:
                raise StateError('modified_integration', 'duplicate owned hook; preserve configuration before reinstall')
            if not count:
                current.setdefault(event, []).append(entry)
        for relative, entry in manifest['files'].items():
            _write(workspace, relative, entry['content'].encode())
        _write(workspace, HOOKS_FILE, _encode(current))
        manifest['phase'] = 'installed'
        _save_manifest(store, workspace, manifest)
        return {'installed': True, 'unchanged': False, 'workspace': str(workspace), 'root': str(store.root),
                'skill': str(workspace / SKILL_DIRECTORY / 'SKILL.md'), 'events': list(EVENTS)}


def remove(workspace, root):
    workspace = _workspace(workspace)
    store = _store(root)
    with store._locked():
        manifest = _load_manifest(store, workspace)
        if manifest is None or manifest['phase'] == 'removed':
            return {'installed': False, 'unchanged': True}
        _check_files(workspace, manifest, allow_missing=True)
        raw = _read(_path(workspace, HOOKS_FILE))
        current = _hooks(raw)
        original = None if manifest['original_hooks'] is None else base64.b64decode(manifest['original_hooks'], validate=True)
        original_value = _hooks(original)
        for event, entry in manifest['hooks'].items():
            # Refuse modified copies carrying our command instead of guessing ownership.
            command = entry['hooks'][0]['command']
            for candidate_event, candidates in current.items():
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    commands = [h.get('command') for h in candidate.get('hooks', []) if isinstance(h, dict)]
                    if command in commands and (candidate_event != event or candidate != entry):
                        # Each event intentionally has the same hook command. An exact
                        # owned entry under another expected event is ordinary.
                        if candidate_event in manifest['hooks'] and candidate == manifest['hooks'][candidate_event]:
                            continue
                        raise StateError('modified_integration', 'owned hook was edited or moved; preserve it before removing')
            count = _owned_hook_count(current, event, entry)
            if count > 1 or (manifest['phase'] == 'installed' and count != 1):
                raise StateError('modified_integration', 'owned hook changed or disappeared; preserve configuration before removing')
            current[event] = [row for row in current.get(event, []) if row != entry]
            if not current[event] and event not in original_value:
                current.pop(event, None)
        manifest['phase'] = 'removing'
        _save_manifest(store, workspace, manifest)
        if current == original_value:
            if original is None:
                if raw is not None:
                    _path(workspace, HOOKS_FILE).unlink()
                    FileStore._sync_dir(workspace / '.devin')
            else:
                _write(workspace, HOOKS_FILE, original)
        else:
            _write(workspace, HOOKS_FILE, _encode(current))
        for relative in manifest['files']:
            path = _path(workspace, relative)
            if path.exists():
                path.unlink()
                FileStore._sync_dir(path.parent)
        # Never recursively remove user additions, or unrelated .devin settings.
        for relative in (SKILL_DIRECTORY + '/references', SKILL_DIRECTORY):
            try:
                _path(workspace, relative).rmdir()
            except (FileNotFoundError, OSError):
                pass
        manifest['phase'] = 'removed'
        _save_manifest(store, workspace, manifest)
        return {'installed': False, 'unchanged': False, 'history_retained': True}


def status(workspace, root):
    workspace = _workspace(workspace)
    if not Path(root).expanduser().exists():
        return {'installed': False, 'health': 'not_installed', 'workspace': str(workspace)}
    store = _store(root)
    with store._locked():
        manifest = _load_manifest(store, workspace)
        if not manifest or manifest['phase'] == 'removed':
            return {'installed': False, 'health': 'not_installed', 'workspace': str(workspace)}
        errors = []
        if not Path(manifest.get('cli', '')).is_file() or not os.access(manifest.get('python', ''), os.X_OK):
            errors.append('installed CLI or Python path is unavailable; preserve configuration and reinstall')
        try:
            _check_files(workspace, manifest)
            current = _hooks(_read(_path(workspace, HOOKS_FILE)))
            for event, entry in manifest['hooks'].items():
                if _owned_hook_count(current, event, entry) != 1:
                    errors.append(event + ' hook missing or duplicated')
        except (ValueError, OSError) as error:
            errors.append(str(error))
        return {'installed': manifest['phase'] == 'installed', 'health': 'ok' if not errors and manifest['phase'] == 'installed' else 'attention',
                'phase': manifest['phase'], 'workspace': str(workspace), 'root': str(store.root), 'errors': errors,
                'context_delivery': 'hook_observation_required', 'skill': str(workspace / SKILL_DIRECTORY / 'SKILL.md')}



def session_activity(root, session_id):
    """Bounded diagnostic read for the menu. Hook identity is not authentication."""
    try:
        expected = {'schema_version': 1, 'session_id': session_id, 'identity_source': 'devin_hook'}
        validate(expected, 'context')
        if not Path(root).expanduser().exists():
            return {'health': 'not_seen', 'session_id': session_id}
        store = _store(root, hook=True)
        with store._locked():
            directory = store.root / 'sessions' / _hash(session_id.encode())
            context = store._read_bytes(directory / 'context.json', limit=8192)
            raw = store._read_bytes(directory / 'activity.json', limit=8192)
            if context is None and raw is None:
                return {'health': 'not_seen', 'session_id': session_id}
            if context is None or raw is None or _decode(context) != expected:
                raise ValueError('session activity context is incomplete or inconsistent')
            value = _decode(raw)
            if (not isinstance(value, dict) or value.get('schema_version') != 1 or
                    value.get('session_id') != session_id or value.get('last_event') not in EVENTS or
                    not isinstance(value.get('workspace'), str) or
                    type(value.get('needs_context')) is not bool):
                raise ValueError('session activity metadata is invalid')
            for field in ('last_seen', 'last_context_at'):
                if field == 'last_context_at' and value.get(field) is None:
                    continue
                if not isinstance(value.get(field), str):
                    raise ValueError('session activity timestamp is invalid')
                datetime.fromisoformat(value[field])
            return {'health': 'ok', 'identity_source': 'devin_hook',
                    **{key: value[key] for key in ('session_id', 'workspace', 'last_seen', 'last_event', 'last_context_at', 'needs_context')}}
    except (ValueError, OSError, KeyError, TypeError):
        return {'health': 'attention', 'session_id': session_id, 'error': 'session_activity_unavailable'}

def hook(workspace, root, payload):
    """Return context only; ignore transcript/prompt/summary/tool fields entirely."""
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in EVENTS:
        return {}
    context = {'schema_version': 1, 'session_id': payload.get('session_id'), 'identity_source': 'devin_hook'}
    validate(context, 'context')
    workspace = _workspace(workspace)
    if not Path(root).expanduser().exists():
        return {}
    store = _store(root, hook=True)
    event = payload['hook_event_name']
    with store._locked():
        manifest = _load_manifest(store, workspace)
        if manifest is None or manifest['phase'] != 'installed':
            return {}
        directory = store.root / 'sessions' / _hash(context['session_id'].encode())
        context_path = directory / 'context.json'
        existing = store._read_bytes(context_path)
        if existing is not None and _decode(existing) != context:
            raise StateError('context_conflict', 'existing session identity context differs; preserve it')
        if existing is None:
            store._atomic(context_path, _encode(context))
        activity_path = directory / 'activity.json'
        activity_raw = store._read_bytes(activity_path)
        old = {} if activity_raw is None else _decode(activity_raw)
        if not isinstance(old, dict):
            raise StateError('invalid_activity', 'activity metadata is damaged')
        stamp = datetime.now(timezone.utc).isoformat()
        deliver = event == 'SessionStart' or (event == 'UserPromptSubmit' and
                  (existing is None or old.get('needs_context', True) or old.get('last_event') == 'SessionEnd'))
        activity = {'schema_version': 1, 'session_id': context['session_id'], 'workspace': str(workspace),
                    'last_seen': stamp, 'last_event': event,
                    'last_context_at': stamp if deliver else old.get('last_context_at'),
                    'needs_context': event == 'PostCompaction' or (not deliver and old.get('needs_context', True))}
        store._atomic(activity_path, _encode(activity))
        if not deliver:
            return {}
        text = ('Observer cooperative reporting is available. This session context is ' + str(context_path) +
                '. Read it with ' + shlex.join([manifest['python'], manifest['cli'], 'state', '--root', str(store.root), 'whoami', '--context', str(context_path)]) +
                '. Use the observer-reporting skill (' + str(workspace / SKILL_DIRECTORY / 'SKILL.md') +
                ') to start/stop reporting when requested, or to continue previously enabled reporting. '
                'Read linked tasks and their reporting flags. While ON, report meaningful changes from your existing context as ordinary work proceeds. '
                'Do not invent session identity, query other work sessions, or block the original task for Observer errors.')
        return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'remove', 'status', 'hook'))
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    if args.action == 'hook':
        # Never return Devin's blocking exit code, or print untrusted error payloads.
        try:
            raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
            value = {} if len(raw) > MAX_HOOK_BYTES else hook(args.workspace, args.root, _decode(raw))
            print(json.dumps(value, ensure_ascii=False))
        except Exception:
            print('{}')
        return 0
    try:
        value = globals()[args.action](args.workspace, args.root)
        print(json.dumps({'ok': True, **value}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({'ok': False, 'error': {'code': getattr(error, 'code', 'integration_error'), 'message': str(error), 'details': getattr(error, 'details', {})}}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
