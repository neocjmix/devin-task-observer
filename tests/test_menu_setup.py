import importlib.util
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
            with patch('observer.menu.main', return_value=0) as main:
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


if __name__ == '__main__':
    unittest.main()
