import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from observer.app import save_config
from observer.cooperative import CooperativeState
from observer.popover import ProjectionController, acquire, wire_value


class PopoverTests(unittest.TestCase):
    def test_wire_preserves_nanoseconds_and_does_not_rewrite_input(self):
        original = {'time': 1793447700000000001, 'rows': [True, 4, None, 'text']}
        before = copy.deepcopy(original)
        wire = json.loads(json.dumps(wire_value(original)))
        self.assertEqual(wire['time'], '1793447700000000001')
        self.assertEqual(wire['rows'], original['rows'])
        self.assertEqual(original, before)

    def test_fixture_cli_has_no_native_or_collector_dependency(self):
        result = subprocess.run([sys.executable, '-m', 'observer.popover', '--fixture',
                                 'tests/fixtures/ui_v2/s0.json', '--snapshot'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(len(value['tasks']), 7)
        self.assertEqual(value['tasks']['task-external']['display_origin'], 'last_good')
        self.assertIsInstance(value['health']['runtime']['collector']['last_success'], str)

    def test_acquisition_reports_missing_collector_and_preserves_task_state(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'personal').mkdir()
            (work / 'different').mkdir()
            core = CooperativeState(work / 'state')
            core.create('task', 'Local task', 'synthetic-session')
            config = {'version': 1, 'source': str(work / 'source' / 'sessions.db'),
                      'store': str(work / 'observations.db'), 'workspaces': [str(work / 'personal')], 'port': 8765}
            config_path = work / 'config.json'
            save_config(config_path, config)
            before = (work / 'state/tasks/task/state.json').read_bytes()
            value = acquire(work / 'state', config, config_path)
            self.assertEqual(value['tasks']['task']['display_origin'], 'current')
            self.assertEqual(value['health']['runtime']['collector']['last_error'], 'collector_unavailable_or_incompatible')
            self.assertTrue(value['health']['observations']['warnings'])
            self.assertEqual((work / 'state/tasks/task/state.json').read_bytes(), before)
            self.assertFalse(Path(config['source']).exists())
            self.assertFalse(Path(config['store']).exists())
            changed = {**config, 'workspaces': [str(work / 'different')]}
            save_config(config_path, changed)
            value = acquire(work / 'state', config, config_path)
            self.assertIsNone(value['health']['observations'])
            self.assertEqual(value['health']['state_errors'][0]['code'], 'configuration_changed')

    def test_background_reader_recovers_after_bad_snapshot_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixture.json'
            fixture.write_text('{')
            controller = ProjectionController(fixture=fixture, interval=.02)
            controller.start()
            try:
                deadline = time.monotonic() + 3
                while controller.latest() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(controller.latest()['health']['state_errors'][0]['code'], 'projection_unavailable')
                fixture.write_text(json.dumps({'task_snapshots': []}))
                controller.refresh()
                deadline = time.monotonic() + 3
                while controller.latest()['health']['state_errors'] and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(controller.latest()['health']['state_errors'], [])
                value = controller.latest()
                value['tasks']['changed'] = True
                self.assertEqual(controller.latest()['tasks'], {})
            finally:
                controller.stop()
            self.assertFalse(controller._thread.is_alive())

    def test_total_inflight_is_bounded_before_worker_and_deep_input_is_rejected(self):
        controller = ProjectionController(fixture='tests/fixtures/ui_v2/s0.json')
        body = {'action': 'note', 'request_id': 'bounded'}
        for _ in range(32):
            self.assertIsNone(controller.submit(body))
        for _ in range(1000):
            self.assertEqual(controller.submit(body)['error']['code'], 'busy')
        self.assertEqual(controller._actions.qsize(), 32)
        self.assertEqual(controller._results.qsize(), 0)
        deep = []
        for _ in range(1500):
            deep = [deep]
        self.assertEqual(controller.submit(dict(body, deep=deep))['error']['code'], 'invalid_action')


if __name__ == '__main__':
    unittest.main()
