#!/usr/bin/env python3
"""Install only the optional native menu bridge in a private Python environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv


def create_launcher(destination, python, config, state_root):
    """Create a py2app alias launcher using the existing Python/source in-place.

    A shell .app execs the framework Python.app and loses its bundle identity.
    Alias mode adds a small launcher only, not a bundled Python framework or CLI.
    The resulting app is local to this checkout/environment, not redistributable.
    """
    destination = Path(destination).expanduser().absolute()
    python, config, state_root = (Path(p).expanduser().absolute() for p in (python, config, state_root))
    if destination.suffix != '.app':
        raise ValueError('Launcher path must end in .app')
    if not python.is_file() or not config.is_file():
        raise ValueError('A working menu Python and existing observer config are required')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Refusing to replace an existing app bundle: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    repository = Path(__file__).resolve().parent.parent
    launcher_root = python.parent.parent / 'launchers' / hashlib.sha256(str(destination).encode()).hexdigest()[:20]
    launcher_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if launcher_root.is_symlink():
        raise ValueError('Refusing symlink launcher source directory')
    entry = launcher_root / 'Observer.py'
    arguments = ['--config', str(config), '--root', str(state_root)]
    entry.write_text('import os, sys\n' + 'sys.path.insert(0, ' + repr(str(repository)) + ')\n'
                     + 'os.chdir(' + repr(str(repository)) + ')\nfrom observer.menu import main\n'
                     + 'raise SystemExit(main(' + repr(arguments) + '))\n', encoding='utf-8')
    entry.chmod(0o600)
    info = {'CFBundleName': 'Devin Task Observer', 'CFBundleDisplayName': 'Devin Task Observer',
            'CFBundleIdentifier': 'ai.neocjmix.devin-task-observer.menu', 'CFBundleVersion': '1',
            'CFBundleShortVersionString': 'M3', 'CFBundlePackageType': 'APPL',
            'LSUIElement': True,
            'ObserverLocalLauncher': True}
    setup = launcher_root / 'setup.py'
    setup.write_text('from setuptools import setup\nsetup(app=' + repr([str(entry)])
                     + ', options=' + repr({'py2app': {'alias': True, 'plist': info}}) + ')\n', encoding='utf-8')
    setup.chmod(0o600)
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
    parser.add_argument('--app', type=Path, help='명시적으로 지정한 새 .app 경로에 얇은 로컬 실행기 생성')
    parser.add_argument('--config', type=Path, default=Path.home() / 'Library/Application Support/Devin Task Observer/config.json')
    parser.add_argument('--root', type=Path, default=Path.home() / 'Library/Application Support/Devin Task Observer/state')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('The native menu requires macOS. Core CLI/collector has no native dependency.')
    root = args.venv.expanduser().absolute()
    if root.exists() and not (root / 'pyvenv.cfg').is_file():
        parser.error('Target exists and is not a Python virtual environment; refusing to replace it.')
    if root.is_symlink():
        parser.error('Refusing a symlink environment path.')
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
                      'next': 'Use this Python with -m observer.menu --config CONFIG --root STATE from the checkout.'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
