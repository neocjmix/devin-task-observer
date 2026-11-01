import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from observer.ui_native import checked_target, finish, perform


class NativeActionsTests(unittest.TestCase):
    def test_protocols_and_commands_are_not_executed(self):
        for target in ('javascript:alert(1)', 'file:///tmp/test.md', 'devin://acp?prompt=x',
                       'https://user:secret@example.com/', 'https://example.com/a b', 'https://'):
            with self.assertRaises(ValueError):
                checked_target({'kind': 'url', 'target': target})
        self.assertEqual(checked_target({'kind': 'url', 'target': 'https://example.com/path'}), ('url', 'https://example.com/path'))
        self.assertEqual(checked_target({'kind': 'copy', 'target': 'feature/task'}), ('copy', 'feature/task'))

    def test_files_are_explicit_existing_documents_and_missing_paths_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('deliverable.md', 'run.command', 'run.sh'):
                (root / name).write_text('synthetic')
            self.assertEqual(checked_target({'kind': 'file', 'target': str(root / 'deliverable.md')})[0], 'file')
            for name in ('run.command', 'run.sh', 'missing.md'):
                with self.assertRaises(ValueError):
                    checked_target({'kind': 'file', 'target': str(root / name)})

    def test_os_failure_is_not_reported_as_success(self):
        dispatched = {'request_id': 'open', 'ok': True, 'result': {'native_action': {'kind': 'url', 'target': 'https://example.com'}}}
        def fail(_):
            raise ValueError('open failed')
        result = finish(dispatched, fail)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'open_failed')
        self.assertNotIn('native_action', result)
        self.assertTrue(finish(dispatched, lambda _: {'opened': True})['result']['opened'])

    def test_finder_reveal_selects_broken_json_and_reports_os_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text('{broken')
            appkit, foundation = Mock(), Mock()
            workspace = appkit.NSWorkspace.sharedWorkspace.return_value
            action = {'kind': 'reveal', 'target': str(path)}
            with patch.dict('sys.modules', AppKit=appkit, Foundation=foundation):
                workspace.selectFile_inFileViewerRootedAtPath_.return_value = True
                self.assertEqual(perform(action), {'revealed': True})
                workspace.selectFile_inFileViewerRootedAtPath_.assert_called_once_with(str(path), directory)
                workspace.openURL_.assert_not_called()
                workspace.selectFile_inFileViewerRootedAtPath_.return_value = False
                result = finish({'ok': True, 'request_id': 'reveal', 'result': {'native_action': action}})
                self.assertEqual(result['error']['code'], 'open_failed')
                self.assertEqual(perform({'kind': 'directory', 'target': directory}), {'opened': True})
                foundation.NSURL.fileURLWithPath_.assert_called_once_with(directory)
            path.unlink()
            with self.assertRaises(ValueError):
                checked_target(action)
            self.assertEqual(checked_target({'kind': 'directory', 'target': directory}), ('directory', directory))
            with self.assertRaises(ValueError):
                checked_target({'kind': 'directory', 'target': str(path)})

    def test_activation_only_uses_an_already_running_devin_bundle(self):
        from unittest.mock import Mock
        from observer.ui_native import activate_existing
        other, devin = Mock(), Mock()
        other.bundleURL.return_value.lastPathComponent.return_value = 'Windsurf.app'
        devin.bundleURL.return_value.lastPathComponent.return_value = 'Devin.app'
        devin.activateWithOptions_.return_value = True
        self.assertEqual(activate_existing([other, devin], 2), {'activated': True})
        other.activateWithOptions_.assert_not_called()
        devin.activateWithOptions_.assert_called_once_with(2)
        with self.assertRaises(ValueError):
            activate_existing([], 2)
        with self.assertRaises(ValueError):
            activate_existing([devin, devin], 2)
        devin.activateWithOptions_.return_value = False
        with self.assertRaises(ValueError):
            activate_existing([devin], 2)
