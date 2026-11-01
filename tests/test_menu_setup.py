import importlib.util
import contextlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('menu_setup', Path(__file__).resolve().parent.parent / 'scripts/setup_menu.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


class MenuSetupTests(unittest.TestCase):
    def test_launcher_quotes_paths_and_does_not_start_or_register_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / 'venv/bin/special " $(touch never) python'
            python.parent.mkdir(parents=True)
            python.touch()
            config = root / "config 'special'.json"
            config.write_text('{}')
            state = root / 'state $(touch NEVER)'
            def build(command, **kwargs):
                self.assertEqual(command[0], str(python))
                self.assertIn('--alias', command)
                directory = Path(command[command.index('--dist-dir') + 1]) / 'Devin Task Observer.app/Contents'
                directory.mkdir(parents=True)
                (directory / 'Info.plist').write_bytes(plistlib.dumps({'LSUIElement': True}))
            with patch.object(SETUP.subprocess, 'run', side_effect=build):
                app = SETUP.create_launcher(root / 'Observer Test.app', python, config, state)
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            self.assertTrue(info['LSUIElement'])
            entry = next((root / 'venv/launchers').glob('*/Observer.py'))
            # Generated Python literals preserve arbitrary paths without a shell.
            with patch('observer.desktop.main', return_value=0) as main:
                with self.assertRaises(SystemExit):
                    exec(compile(entry.read_text(), str(entry), 'exec'), {})
                main.assert_called_once_with(['--config', str(config), '--root', str(state)])
            self.assertEqual(entry.stat().st_mode & 0o777, 0o600)
            self.assertFalse(state.exists())
            self.assertFalse((root / 'Library/LaunchAgents').exists())
            with self.assertRaises(FileExistsError):
                SETUP.create_launcher(app, python, config, state)
            self.assertEqual(plistlib.loads((app / 'Contents/Info.plist').read_bytes()), info)

    def test_launcher_requires_explicit_config_and_does_not_replace_foreign_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                SETUP.create_launcher(root / 'app.txt', root / 'python', root / 'missing', root / 'state')
            self.assertEqual(list(root.iterdir()), [])

    def managed_alias(self, root):
        python = root / 'venv/bin/python'
        python.parent.mkdir(parents=True)
        python.touch()
        (root / 'venv/pyvenv.cfg').write_text('synthetic test environment')
        config = root / "config 'quoted'.json"
        config.write_text('{}')
        state = root / 'state $(touch NEVER)'
        app = root / 'Observer Test.app'
        _, _, _, _, repo, launcher_root = SETUP._launcher_paths(app, python, config, state)
        launcher_root.mkdir(parents=True)
        entry = launcher_root / 'Observer.py'
        entry.write_bytes(SETUP._entry_source(repo, config, state, 'observer.menu'))
        resources = app / 'Contents/Resources'
        resources.mkdir(parents=True)
        info = {'ObserverLocalLauncher': True, 'CFBundleIdentifier': SETUP.BUNDLE_ID,
                'PyOptions': {'alias': True}, 'PythonInfoDict': {'PythonExecutable': str(python),
                                                               'py2app': {'alias': True}}}
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        boot = ('_path_inject(' + repr([str(launcher_root)]) + ')\n'
                + 'DEFAULT_SCRIPT=' + repr(str(entry)) + '\nSCRIPT_MAP={}\n')
        (resources / '__boot__.py').write_text(boot)
        return app, python, config, state, entry

    def test_managed_upgrade_backs_up_only_entry_and_keeps_bundle_environment_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python, config, state, entry = self.managed_alias(Path(directory))
            original = entry.read_bytes()
            bundle = {str(p): p.read_bytes() for p in app.rglob('*') if p.is_file()}
            with patch.object(SETUP.subprocess, 'run', side_effect=AssertionError('must not install or launch')):
                result = SETUP.upgrade_launcher(app, python, config, state)
            self.assertTrue(result['upgraded'])
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            self.assertEqual(Path(result['backup']).stat().st_mode & 0o777, 0o600)
            self.assertEqual(entry.stat().st_mode & 0o777, 0o600)
            self.assertEqual({str(p): p.read_bytes() for p in app.rglob('*') if p.is_file()}, bundle)
            self.assertFalse(state.exists())
            with patch('observer.desktop.main', return_value=0) as main:
                with self.assertRaises(SystemExit):
                    exec(compile(entry.read_text(), str(entry), 'exec'), {})
                main.assert_called_once_with(['--config', str(config), '--root', str(state)])
            again = SETUP.upgrade_launcher(app, python, config, state)
            self.assertFalse(again['upgraded'])
            self.assertIsNone(again['backup'])
            self.assertEqual(len(list(entry.parent.glob('*.bak'))), 1)

    def test_upgrade_rejects_foreign_bundle_marker_identifier_alias_or_python(self):
        for field in ('marker', 'bundle', 'alias', 'python'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                app, python, config, state, entry = self.managed_alias(Path(directory))
                info_path = app / 'Contents/Info.plist'
                info = plistlib.loads(info_path.read_bytes())
                if field == 'marker':
                    info['ObserverLocalLauncher'] = False
                elif field == 'bundle':
                    info['CFBundleIdentifier'] = 'someone.else.app'
                elif field == 'alias':
                    info['PyOptions']['alias'] = False
                else:
                    info['PythonInfoDict']['PythonExecutable'] = '/foreign/python'
                info_path.write_bytes(plistlib.dumps(info))
                original = entry.read_bytes()
                with self.assertRaises(ValueError):
                    SETUP.upgrade_launcher(app, python, config, state)
                self.assertEqual(entry.read_bytes(), original)
                self.assertEqual(list(entry.parent.glob('*.bak')), [])

    def test_upgrade_rejects_modified_entry_or_different_config_root_repository(self):
        for changed in ('entry', 'config', 'root', 'repository'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                app, python, config, state, entry = self.managed_alias(Path(directory))
                if changed == 'entry':
                    entry.write_bytes(entry.read_bytes() + b'# edited by user\n')
                elif changed == 'config':
                    config = config.with_name('other.json')
                    config.write_text('{}')
                elif changed == 'root':
                    state = state.with_name('other-state')
                else:
                    source = entry.read_text().replace(str(Path(__file__).resolve().parents[1]), '/foreign/checkout')
                    entry.write_text(source)
                original = entry.read_bytes()
                with self.assertRaises(ValueError):
                    SETUP.upgrade_launcher(app, python, config, state)
                self.assertEqual(entry.read_bytes(), original)
                self.assertEqual(list(entry.parent.glob('*.bak')), [])

    def test_upgrade_rejects_foreign_boot_binding_and_symlink_entry(self):
        for changed in ('boot', 'symlink'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                app, python, config, state, entry = self.managed_alias(Path(directory))
                original = entry.read_bytes()
                if changed == 'boot':
                    boot = app / 'Contents/Resources/__boot__.py'
                    boot.write_text(boot.read_text().replace(str(entry), '/foreign/Observer.py'))
                else:
                    target = entry.with_name('foreign.py')
                    entry.rename(target)
                    entry.symlink_to(target)
                with self.assertRaises((ValueError, OSError)):
                    SETUP.upgrade_launcher(app, python, config, state)
                self.assertEqual(entry.read_bytes(), original)
                self.assertEqual(list(entry.parent.glob('*.bak')), [])

    def test_explicit_rebind_preserves_old_entry_and_is_idempotent(self):
        for module in ('observer.menu', 'observer.desktop'):
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                app, python, config, state, entry = self.managed_alias(root)
                repository = Path(__file__).resolve().parents[1]
                entry.write_bytes(SETUP._entry_source(repository, config, state, module))
                original = entry.read_bytes()
                new_config = root / '.devin-task-observer/config.json'
                new_config.parent.mkdir()
                new_config.write_text('{}')
                new_state = new_config.parent / 'state'
                result = SETUP.upgrade_launcher(app, python, new_config, new_state,
                                                previous_config=config, previous_root=state)
                self.assertTrue(result['upgraded'])
                self.assertEqual(Path(result['backup']).read_bytes(), original)
                self.assertEqual(entry.read_bytes(), SETUP._entry_source(repository, new_config, new_state))
                again = SETUP.upgrade_launcher(app, python, new_config, new_state,
                                               previous_config=config, previous_root=state)
                self.assertFalse(again['upgraded'])
                self.assertEqual(len(list(entry.parent.glob('*.bak'))), 1)
                self.assertFalse(new_state.exists())

    def test_rebind_rejects_unpaired_or_wrong_old_paths_without_changing_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, python, config, state, entry = self.managed_alias(root)
            original = entry.read_bytes()
            new_config = root / 'new-config.json'
            new_config.write_text('{}')
            for previous in ({'previous_config': config}, {'previous_root': state},
                             {'previous_config': root / 'wrong.json', 'previous_root': state},
                             {'previous_config': config, 'previous_root': root / 'wrong-state'}):
                with self.subTest(previous=previous), self.assertRaises(ValueError):
                    SETUP.upgrade_launcher(app, python, new_config, root / 'new-state', **previous)
                self.assertEqual(entry.read_bytes(), original)
                self.assertEqual(list(entry.parent.glob('*.bak')), [])

    def test_previous_path_cli_flags_require_upgrade_and_pair(self):
        for options in (['--previous-config', '/old/config'], ['--previous-root', '/old/state'],
                        ['--previous-config', '/old/config', '--previous-root', '/old/state']):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(SETUP.subprocess, 'run', side_effect=AssertionError('no install or launch')), \
                    self.assertRaises(SystemExit) as error:
                SETUP.main(options)
            self.assertEqual(error.exception.code, 2)

    def test_replacement_failure_retains_old_entry_and_durable_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python, config, state, entry = self.managed_alias(Path(directory))
            original = entry.read_bytes()
            with patch.object(SETUP.os, 'replace', side_effect=OSError('synthetic replacement failure')):
                with self.assertRaises(OSError):
                    SETUP.upgrade_launcher(app, python, config, state)
            self.assertEqual(entry.read_bytes(), original)
            backups = list(entry.parent.glob('*.bak'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertEqual(list(entry.parent.glob('*.tmp')), [])

    def test_upgrade_cli_does_not_install_dependencies_start_app_or_enable_service(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python, config, state, entry = self.managed_alias(Path(directory))
            output = io.StringIO()
            with patch.object(SETUP.sys, 'platform', 'darwin'), contextlib.redirect_stdout(output), \
                    patch.object(SETUP.subprocess, 'run', side_effect=AssertionError('no install or launch')), \
                    patch.object(SETUP.venv.EnvBuilder, 'create', side_effect=AssertionError('no environment creation')):
                self.assertEqual(SETUP.main(['--venv', str(python.parent.parent), '--upgrade-app', str(app),
                                            '--config', str(config), '--root', str(state)]), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result['upgraded'])
            self.assertFalse(result['auto_start_enabled'])
            self.assertFalse(state.exists())
            self.assertFalse((Path(directory) / 'Library/LaunchAgents').exists())


if __name__ == '__main__':
    unittest.main()
