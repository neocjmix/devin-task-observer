"""The app entry uses the new popover unless legacy NSMenu is explicit."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from observer import desktop


class DesktopEntryTests(unittest.TestCase):
    def test_default_routes_popover_and_shows_manual_app_launch(self):
        arguments = ['--config', '/synthetic/config with space.json', '--root', '/synthetic/state']
        with patch('observer.popover.main', return_value=17) as popover, patch('observer.menu.main') as menu:
            self.assertEqual(desktop.main(arguments), 17)
        popover.assert_called_once_with(arguments + ['--show'])
        menu.assert_not_called()
        self.assertNotIn('--show', arguments)

    def test_explicit_show_and_headless_diagnostics_are_preserved(self):
        for arguments in (['--show'], ['--snapshot'], ['--help'], ['-h']):
            with self.subTest(arguments=arguments), patch('observer.popover.main', return_value=0) as popover:
                self.assertEqual(desktop.main(arguments), 0)
                popover.assert_called_once_with(arguments)

    def test_explicit_fallback_strips_only_legacy_flag_and_does_not_launch_popover(self):
        arguments = ['--config', '/synthetic/config.json', '--legacy-menu', '--root', '/synthetic/state', '--snapshot']
        with patch('observer.popover.main') as popover, patch('observer.menu.main', return_value=9) as menu:
            self.assertEqual(desktop.main(arguments), 9)
        menu.assert_called_once_with(['--config', '/synthetic/config.json', '--root', '/synthetic/state', '--snapshot'])
        popover.assert_not_called()
        self.assertIn('--legacy-menu', arguments)

    def test_sys_argv_route_and_lazy_import_do_not_require_native_dependencies(self):
        with patch.object(desktop.sys, 'argv', ['observer.desktop', '--legacy-menu', '--snapshot']), \
                patch('observer.menu.main', return_value=0) as menu:
            self.assertEqual(desktop.main(), 0)
            menu.assert_called_once_with(['--snapshot'])
        result = subprocess.run([sys.executable, '-c',
            'import sys; import observer.desktop; assert "AppKit" not in sys.modules; assert "observer.popover" not in sys.modules; assert "observer.menu" not in sys.modules'],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_default_module_snapshot_works_with_synthetic_fixture(self):
        repository = Path(__file__).resolve().parents[1]
        fixture = repository / 'tests/fixtures/ui_v2/s0.json'
        result = subprocess.run([sys.executable, '-m', 'observer.desktop', '--fixture', str(fixture), '--snapshot'],
                                cwd=repository, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        output = json.loads(result.stdout)
        self.assertIn('tasks', output)
        self.assertIn('task-auth', output['tasks'])


if __name__ == '__main__':
    unittest.main()
