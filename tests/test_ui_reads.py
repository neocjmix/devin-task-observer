"""Cursor writes share task commit serialization without semantic revision bumps."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.state_schema import validate
from observer.ui_reads import ReadCursors, published


class ReadCursorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'
        self.core = CooperativeState(self.root)
        self.reads = ReadCursors(self.root)
        self.serial = 0
        for task in ('task-a', 'task-b'):
            self.core.create(task, task, 'shared-session')
            self.core.reporting(task, 'shared-session', True, self.state(task)['revision'])

    def state(self, task='task-a'):
        return self.core.read(task)['state']

    def request(self, task='task-a'):
        self.serial += 1
        return {'schema_version': 1, 'report_id': f'cursor-report-{self.serial}',
                'session_id': 'shared-session', 'expected_revisions': {task: self.state(task)['revision']},
                'changes': {task: [{'op': 'define', 'item_id': f'item-{self.serial}', 'title': 'Status',
                                    'value': 'Synthetic report', 'status': 'reported', 'evidence': []}]}}

    def mark(self, task='task-a', request_id=None):
        state = self.state(task)
        return self.reads.mark(task, state['revision'], state['last_report_revision'], request_id)

    def test_snapshot_and_legacy_unknown_do_not_create_cursors(self):
        self.assertEqual(self.reads.snapshot(), {'schema_version': 1, 'revision': 0, 'cursors': {}})
        self.assertFalse(self.reads.path.exists())
        self.assertNotIn('last_report_revision', self.state())
        with self.assertRaises(StateError) as caught:
            self.reads.mark('task-a', self.state()['revision'], 1)
        self.assertEqual(caught.exception.code, 'report_position_unknown')
        self.assertFalse(self.reads.path.exists())

    def test_only_accepted_report_changes_last_report_position(self):
        request = self.request()
        self.core.report(request)
        state = self.state()
        position = state['last_report_revision']
        self.assertEqual(position, state['revision'])
        item_id = state['items'][0]['item_id']
        self.core.note('task-a', 'Human note', self.state()['revision'])
        self.core.pin('task-a', item_id, True, self.state()['revision'])
        self.core.observation('task-a', False, self.state()['revision'])
        self.core.lifecycle('task-a', True, self.state()['revision'])
        self.assertEqual(self.state()['last_report_revision'], position)
        self.assertTrue(self.core.report(request)['replayed'])
        self.assertEqual(self.state()['last_report_revision'], position)
        with self.assertRaises(StateError):
            self.core.report(self.request())
        self.assertEqual(self.state()['last_report_revision'], position)

    def test_explicit_mark_never_changes_task_revision_history_or_report_state(self):
        self.core.report(self.request())
        before = self.state()
        history = self.core.store.history('task-a')
        result = self.mark(request_id='mark-one')
        self.assertTrue(result['changed'])
        self.assertEqual(result['request_id'], 'mark-one')
        self.assertEqual(result['cursor']['through_report_revision'], before['last_report_revision'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.core.store.history('task-a'), history)
        self.assertEqual(result['cursor_revision'], 1)

    def test_shared_session_tasks_have_independent_persistent_positions(self):
        self.core.report(self.request())
        self.core.report(self.request('task-b'))
        self.mark()
        value = ReadCursors(self.root).snapshot()
        self.assertEqual(set(value['cursors']), {'task-a'})
        self.assertEqual(value['revision'], 1)
        self.mark('task-b')
        value = ReadCursors(self.root).snapshot()
        self.assertEqual(set(value['cursors']), {'task-a', 'task-b'})
        self.assertEqual(value['revision'], 2)

    def test_positional_retry_is_noop_and_does_not_acknowledge_later_reports(self):
        self.core.report(self.request())
        displayed = self.state()
        self.mark()
        raw = self.reads.path.read_bytes()
        self.core.report(self.request())
        result = self.reads.mark('task-a', displayed['revision'], displayed['last_report_revision'], 'retry')
        self.assertFalse(result['changed'])
        self.assertEqual(self.reads.path.read_bytes(), raw)
        self.assertLess(result['cursor']['through_report_revision'], self.state()['last_report_revision'])
        self.mark()
        newest = self.reads.path.read_bytes()
        result = self.reads.mark('task-a', displayed['revision'], displayed['last_report_revision'])
        self.assertFalse(result['changed'])
        self.assertEqual(self.reads.path.read_bytes(), newest)

    def test_report_committed_before_advancing_mark_returns_conflict(self):
        self.core.report(self.request())
        displayed = self.state()
        self.core.report(self.request())
        with self.assertRaises(StateError) as caught:
            self.reads.mark('task-a', displayed['revision'], displayed['last_report_revision'])
        self.assertEqual(caught.exception.code, 'revision_conflict')
        self.assertFalse(self.reads.path.exists())

    def test_cursor_commit_holds_same_lock_and_cannot_swallow_following_report(self):
        self.core.report(self.request())
        displayed = self.state()
        report = self.request()
        entered, release = threading.Event(), threading.Event()
        original = self.reads.store._atomic
        def paused_write(path, raw):
            entered.set()
            if not release.wait(2):
                raise AssertionError('cursor test did not release commit')
            return original(path, raw)
        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(self.reads.store, '_atomic', paused_write):
            mark = executor.submit(self.reads.mark, 'task-a', displayed['revision'], displayed['last_report_revision'])
            self.assertTrue(entered.wait(2))
            writer = executor.submit(self.core.report, report)
            try:
                with self.assertRaises(TimeoutError):
                    writer.result(timeout=.05)
            finally:
                release.set()
            self.assertTrue(mark.result(timeout=3)['changed'])
            writer.result(timeout=3)
        cursor = self.reads.snapshot()['cursors']['task-a']
        self.assertEqual(cursor['through_report_revision'], displayed['last_report_revision'])
        self.assertGreater(self.state()['last_report_revision'], cursor['through_report_revision'])
        self.assertEqual(self.state()['revision'], displayed['revision'] + 1)

    def test_future_or_nonlatest_report_position_is_never_accepted(self):
        self.core.report(self.request())
        first = self.state()['last_report_revision']
        self.core.report(self.request())
        for position, code in ((first, 'read_position_conflict'),
                               (self.state()['last_report_revision'] + 1, 'invalid_read_position')):
            with self.subTest(position=position), self.assertRaises(StateError) as caught:
                self.reads.mark('task-a', self.state()['revision'], position)
            self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.reads.path.exists())

    def test_damaged_cursor_preserved_while_ordinary_reporting_continues(self):
        self.core.report(self.request())
        self.mark()
        for raw in (b'{broken', b'{"schema_version":99,"revision":1,"cursors":{}}',
                    json.dumps({'schema_version': 1, 'revision': 1, 'cursors': {
                        'task-a': {'through_report_revision': 3, 'updated_at': 'not a timestamp'}}}).encode()):
            self.reads.path.write_bytes(raw)
            with self.subTest(raw=raw):
                for operation in (self.reads.snapshot, self.mark):
                    with self.assertRaises(StateError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, 'invalid_read_cursors')
                self.assertEqual(self.reads.path.read_bytes(), raw)
        self.core.report(self.request())
        self.assertEqual(self.reads.path.read_bytes(), raw)

    def test_future_stored_cursor_is_visible_error_not_silently_overwritten(self):
        self.core.report(self.request())
        self.mark()
        value = self.reads.snapshot()
        value['cursors']['task-a']['through_report_revision'] = self.state()['revision'] + 5
        raw = json.dumps(value).encode()
        self.reads.path.write_bytes(raw)
        for operation in (self.reads.snapshot, self.mark):
            with self.assertRaises(StateError) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'invalid_read_cursors')
        self.assertEqual(self.reads.path.read_bytes(), raw)

    def test_unhealthy_task_cannot_be_confirmed_from_last_good(self):
        self.core.report(self.request())
        displayed = self.state()
        task_path = self.root / 'tasks/task-a/state.json'
        raw = b'{broken task'
        task_path.write_bytes(raw)
        with self.assertRaises(StateError) as caught:
            self.reads.mark('task-a', displayed['revision'], displayed['last_report_revision'])
        self.assertEqual(caught.exception.code, 'state_unavailable')
        self.assertEqual(task_path.read_bytes(), raw)
        self.assertFalse(self.reads.path.exists())

    def test_schema_published_and_task_position_cannot_exceed_task_revision(self):
        path = Path(__file__).resolve().parents[1] / 'schemas/ui-v2/read-cursors.schema.json'
        self.assertEqual(json.loads(path.read_text()), published())
        state = self.state()
        state['last_report_revision'] = state['revision'] + 1
        with self.assertRaises(ValueError):
            validate(state, 'task')


if __name__ == '__main__':
    unittest.main()
