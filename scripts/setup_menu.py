#!/usr/bin/env python3
"""Install optional native UI bridges or upgrade a managed local app launcher."""
import argparse
import ast
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import venv
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observer.paths import DEFAULT_CONFIG, DEFAULT_ROOT


BUNDLE_ID = 'ai.neocjmix.devin-task-observer.menu'


def _launcher_paths(destination, python, config, state_root):
    destination = Path(destination).expanduser().absolute()
    python, config, state_root = (Path(p).expanduser().absolute() for p in (python, config, state_root))
    if destination.suffix != '.app':
        raise ValueError('Launcher path must end in .app')
    if not python.is_file() or not config.is_file():
        raise ValueError('A working menu Python and existing observer config are required')
    repository = Path(__file__).resolve().parent.parent
    launcher_root = python.parent.parent / 'launchers' / hashlib.sha256(str(destination).encode()).hexdigest()[:20]
    return destination, python, config, state_root, repository, launcher_root


def _entry_source(repository, config, state_root, module='observer.desktop'):
    arguments = ['--config', str(config), '--root', str(state_root)]
    return ('import os, sys\n' + 'sys.path.insert(0, ' + repr(str(repository)) + ')\n'
            + 'os.chdir(' + repr(str(repository)) + ')\nfrom ' + module + ' import main\n'
            + 'raise SystemExit(main(' + repr(arguments) + '))\n').encode('utf-8')


def _plain_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
            raise ValueError('Managed launcher file must be a small ordinary file: ' + str(path))
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            return handle.read(2 * 1024 * 1024 + 1)
    finally:
        os.close(fd)


def _write_exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _new_or_equal(path, raw):
    if path.exists() or path.is_symlink():
        if _plain_bytes(path) != raw:
            raise FileExistsError('Refusing to replace modified launcher source: ' + str(path))
    else:
        _write_exclusive(path, raw)


def _plain_directories(paths):
    for path in paths:
        if path.is_symlink() or not path.is_dir():
            raise ValueError('Managed launcher directory is missing or symbolic: ' + str(path))


def _sync_directory(path):
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def upgrade_launcher(destination, python, config, state_root, *, previous_config=None, previous_root=None):
    """Replace only an exact managed entry, keeping its original private backup.

    No package installation, app launch, bundle rebuild or service registration.
    py2app alias boot files are parsed as data; they are never executed here.
    Rebinding requires both explicitly supplied previous paths. It does not move
    any state files or infer paths from an unknown launcher.
    """
    if (previous_config is None) != (previous_root is None):
        raise ValueError('Previous config and state root must be provided together')
    destination, python, config, state_root, repository, launcher_root = _launcher_paths(
        destination, python, config, state_root)
    contents = destination / 'Contents'
    resources = contents / 'Resources'
    _plain_directories((destination, contents, resources, python.parent.parent,
                        launcher_root.parent, launcher_root))
    info_path, boot_path, entry = contents / 'Info.plist', resources / '__boot__.py', launcher_root / 'Observer.py'
    info_raw, boot_raw, original = (_plain_bytes(path) for path in (info_path, boot_path, entry))
    info = plistlib.loads(info_raw)
    if not isinstance(info, dict):
        raise ValueError('Existing app metadata is not a managed Observer property list')
    python_info = info.get('PythonInfoDict', {})
    if (not isinstance(python_info, dict) or not isinstance(info.get('PyOptions'), dict) or
            not isinstance(python_info.get('py2app'), dict) or
            info.get('ObserverLocalLauncher') is not True or info.get('CFBundleIdentifier') != BUNDLE_ID or
            info.get('PyOptions', {}).get('alias') is not True or
            python_info.get('PythonExecutable') != str(python) or
            python_info.get('py2app', {}).get('alias') is not True):
        raise ValueError('Existing app is not the managed Observer alias for this Python environment')
    tree = ast.parse(boot_raw.decode('utf-8'))
    bindings, injects = {}, []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in ('DEFAULT_SCRIPT', 'SCRIPT_MAP'):
                    if target.id in bindings:
                        raise ValueError('Ambiguous alias launcher script binding')
                    bindings[target.id] = ast.literal_eval(statement.value)
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Name) and call.func.id == '_path_inject':
                injects.append(ast.literal_eval(call.args[0]) if len(call.args) == 1 and not call.keywords else None)
    if bindings != {'DEFAULT_SCRIPT': str(entry), 'SCRIPT_MAP': {}} or injects != [[str(launcher_root)]]:
        raise ValueError('Alias boot script does not target the expected managed launcher path')
    desired = _entry_source(repository, config, state_root)
    old_config, old_root = ((Path(previous_config).expanduser().absolute(), Path(previous_root).expanduser().absolute())
                            if previous_config is not None else (config, state_root))
    accepted = (desired, _entry_source(repository, old_config, old_root),
                _entry_source(repository, old_config, old_root, 'observer.menu'))
    if original not in accepted:
        raise ValueError('Managed entry was modified or uses different repository/config/state paths; preserving it')
    if original == desired:
        return {'app': str(destination), 'entry': str(entry), 'upgraded': False, 'backup': None}
    lock = launcher_root / '.upgrade.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    temporary = launcher_root / ('.Observer.' + uuid.uuid4().hex + '.tmp')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = launcher_root / ('Observer.py.before-ui-v2-' + stamp + '-' + hashlib.sha256(original).hexdigest()[:12] + '.bak')
        if any(_plain_bytes(path) != raw for path, raw in ((info_path, info_raw), (boot_path, boot_raw), (entry, original))):
            raise ValueError('Launcher changed during upgrade validation; preserving current files')
        _write_exclusive(backup, original)
        _sync_directory(launcher_root)
        _write_exclusive(temporary, desired)
        if any(_plain_bytes(path) != raw for path, raw in ((info_path, info_raw), (boot_path, boot_raw), (entry, original))):
            raise ValueError('Launcher changed before replacement; preserving current entry and backup')
        os.replace(temporary, entry)
        _sync_directory(launcher_root)
        return {'app': str(destination), 'entry': str(entry), 'upgraded': True, 'backup': str(backup)}
    finally:
        if temporary.exists():
            temporary.unlink()
        os.close(fd)


def create_launcher(destination, python, config, state_root):
    """Create a py2app alias launcher using the existing Python/source in-place.

    A shell .app execs the framework Python.app and loses its bundle identity.
    Alias mode adds a small launcher only, not a bundled Python framework or CLI.
    The resulting app is local to this checkout/environment, not redistributable.
    """
    destination, python, config, state_root, repository, launcher_root = _launcher_paths(
        destination, python, config, state_root)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Refusing to replace an existing app bundle: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if launcher_root.parent.is_symlink() or launcher_root.is_symlink():
        raise ValueError('Refusing symlink launcher source directory')
    launcher_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _plain_directories((python.parent.parent, launcher_root.parent, launcher_root))
    entry = launcher_root / 'Observer.py'
    _new_or_equal(entry, _entry_source(repository, config, state_root))
    info = {'CFBundleName': 'Devin Task Observer', 'CFBundleDisplayName': 'Devin Task Observer',
            'CFBundleIdentifier': BUNDLE_ID, 'CFBundleVersion': '1',
            'CFBundleShortVersionString': 'M3', 'CFBundlePackageType': 'APPL',
            'LSUIElement': True,
            'ObserverLocalLauncher': True}
    setup = launcher_root / 'setup.py'
    _new_or_equal(setup, ('from setuptools import setup\nsetup(app=' + repr([str(entry)])
                         + ', options=' + repr({'py2app': {'alias': True, 'plist': info}}) + ')\n').encode())
    with tempfile.TemporaryDirectory(prefix='observer-menu-build-') as directory:
        build = Path(directory)
        subprocess.run([str(python), str(setup), 'py2app', '--alias', '--dist-dir', str(build / 'dist'),
                        '--bdist-base', str(build / 'build')], cwd=launcher_root, check=True)
        bundle = build / 'dist/Devin Task Observer.app'
        if not (bundle / 'Contents/Info.plist').is_file():
            raise ValueError('py2app did not create the expected local launcher')
        # copytree rejects a concurrently created destination; never overwrite it.
        shutil.copytree(bundle, destination, symlinks=True)
        destination.chmod(0o700)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', type=Path, default=Path.home() / 'Library/Application Support/Devin Task Observer/menu-runtime')
    app_options = parser.add_mutually_exclusive_group()
    app_options.add_argument('--app', type=Path, help='명시적으로 지정한 새 .app 경로에 얇은 로컬 실행기 생성')
    app_options.add_argument('--upgrade-app', type=Path, help='검증된 기존 로컬 alias 실행기만 백업 후 popover 기본 경로로 전환')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--previous-config', type=Path, help='명시적 경로 변경 시 기존 실행기의 정확한 config 경로')
    parser.add_argument('--previous-root', type=Path, help='명시적 경로 변경 시 기존 실행기의 정확한 state 경로')
    args = parser.parse_args(argv)
    if (args.previous_config is None) != (args.previous_root is None):
        parser.error('--previous-config and --previous-root are required together.')
    if args.previous_config is not None and not args.upgrade_app:
        parser.error('Previous paths are only accepted with --upgrade-app.')
    if sys.platform != 'darwin':
        parser.error('The native menu requires macOS. Core CLI/collector has no native dependency.')
    root = args.venv.expanduser().absolute()
    if root.exists() and not (root / 'pyvenv.cfg').is_file():
        parser.error('Target exists and is not a Python virtual environment; refusing to replace it.')
    if root.is_symlink():
        parser.error('Refusing a symlink environment path.')
    if args.upgrade_app:
        if not root.is_dir():
            parser.error('An existing managed Python environment is required for upgrade.')
        result = upgrade_launcher(args.upgrade_app, root / 'bin/python', args.config, args.root,
                                  previous_config=args.previous_config, previous_root=args.previous_root)
        print(json.dumps({**result, 'python': str(root / 'bin/python'), 'auto_start_enabled': False}))
        return 0
    root.parent.mkdir(parents=True, exist_ok=True)
    if not root.exists():
        venv.EnvBuilder(with_pip=True).create(root)
    os.chmod(root, 0o700)
    python = root / 'bin/python'
    # Thin Cocoa/WebKit bridges only; no all-framework bundle or browser runtime.
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    'rumps==0.4.0', 'pyobjc-framework-Cocoa==12.2.2',
                    'pyobjc-framework-WebKit==12.2.2'], check=True)
    subprocess.run([str(python), '-c', 'import rumps, AppKit, WebKit; print("Native UI imports OK")'], check=True)
    if args.app:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        'py2app==0.28.10', 'setuptools==84.2.1'], check=True)
    app = create_launcher(args.app, python, args.config, args.root) if args.app else None
    print(json.dumps({'python': str(python), 'app': str(app) if app else None, 'auto_start_enabled': False,
                      'next': 'Use this Python with -m observer.desktop --config CONFIG --root STATE; add --legacy-menu for NSMenu fallback.'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
