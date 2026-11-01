"""Exercise the disposable S8 demo through its real CLI and collector boundary."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from observer.app import load_config
from observer.cooperative import CooperativeState
from observer.popover import acquire
from observer.runtime import CollectorRuntime
from observer.session_usage import UsageCache


class UiV2DemoTests(unittest.TestCase):
    def test_isolated_demo_collects_projects_preserves_controls_and_refuses_overwrite(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'synthetic acceptance'
            command = [sys.executable, str(repository / 'scripts/ui_v2_demo.py'), str(root)]
            created = subprocess.run(command, cwd=repository, capture_output=True, text=True, timeout=10)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(json.loads(created.stdout), {
                'config': str(root / 'config.json'), 'root': str(root / 'state'), 'synthetic': True})
            config = load_config(root / 'config.json')
            source = Path(config['source'])
            self.assertTrue(source.is_relative_to(root))
            self.assertTrue(Path(config['store']).is_relative_to(root))
            self.assertEqual(config['workspaces'], [str(root / 'workspace')])
            source_before = source.read_bytes()
            with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 8)
                self.assertEqual(db.execute('SELECT count(*) FROM message_nodes').fetchone()[0], 8)

            core = CooperativeState(root / 'state')
            original_tasks = core.list()
            self.assertEqual(len(original_tasks), 7)
            runtime = CollectorRuntime(config)
            try:
                runtime.acquire()
                collected = runtime.refresh_once()
                self.assertTrue(collected['ok'], collected)
                view = acquire(root / 'state', config, root / 'config.json', runtime, UsageCache())
                self.assertEqual(view['health']['state_errors'], [])
                self.assertEqual(view['health']['observations']['warnings'], [])
                self.assertIsNone(view['health']['read_cursor_error'])
                self.assertEqual(len(view['tasks']), 7)
                self.assertEqual(len(view['sessions']), 8)
                self.assertTrue(all(s['usage']['status'] == 'available' for s in view['sessions'].values()))
                self.assertTrue(all(s['observation']['observation_count'] == 1 for s in view['sessions'].values()))
                self.assertEqual(view['tasks']['task-1']['session_ids'], ['ui-session-1', 'ui-session-8'])
                self.assertEqual(view['sessions']['ui-session-1']['task_ids'], ['task-1', 'task-2'])
                self.assertTrue(view['contributions']['task-1']['ui-session-1']['reporting'])
                self.assertFalse(view['contributions']['task-2']['ui-session-1']['reporting'])
                self.assertTrue(view['items']['task-1']['next']['pinned'])
                metadata = view['tasks']['task-1']['metadata']
                self.assertEqual(metadata['notes'][0]['text'], '기존 사용자 메모를 보존합니다.')
                resource = metadata['resources'][0]
                self.assertEqual(resource['target'], str(root / 'workspace/report.md'))
                self.assertEqual(resource['open_mode'], 'file')
                self.assertEqual(resource['created_by']['kind'], 'human')
                self.assertTrue(Path(resource['target']).is_file())
                self.assertFalse(view['tasks']['task-4']['effective_observation_enabled'])
                self.assertEqual(view['tasks']['task-5']['management'], {
                    'lifecycle': 'completed', 'observation_enabled': False,
                    'previous_observation_enabled': False})
                self.assertEqual(view['tasks']['task-2']['read_state']['status'], 'read')
                self.assertEqual(view['tasks']['task-3']['read_state']['status'], 'read')
                self.assertEqual(view['tasks']['task-1']['read_state']['status'], 'unread')
                self.assertEqual(core.list(), original_tasks)
                self.assertEqual(source.read_bytes(), source_before)
            finally:
                runtime.stop()

            completed = core.read('task-5')['state']
            core.lifecycle('task-5', False, completed['revision'])
            restored = core.read('task-5')['state']['management']
            self.assertEqual(restored['lifecycle'], 'active')
            self.assertFalse(restored['observation_enabled'])
            before_retry = {str(path.relative_to(root)): path.read_bytes()
                            for path in root.rglob('*') if path.is_file()}
            repeated = subprocess.run(command, cwd=repository, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual({str(path.relative_to(root)): path.read_bytes()
                              for path in root.rglob('*') if path.is_file()}, before_retry)


if __name__ == '__main__':
    unittest.main()
