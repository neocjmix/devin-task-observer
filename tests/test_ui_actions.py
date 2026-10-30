import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from observer.cooperative import CooperativeState
from observer.ui_actions import response


class UIActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = CooperativeState(self.root)
        self.core.create('task', 'Task', 'session-a')

    def request(self, action, **fields):
        return {'action': action, 'request_id': 'ui-request', 'task_id': 'task', **fields}

    def test_note_replay_and_stale_conflict_preserve_state(self):
        request = self.request('note', expected_revision=1, text='Human note')
        self.assertTrue(response(self.root, request)['ok'])
        self.assertTrue(response(self.root, request)['result']['replayed'])
        stale = dict(request, request_id='stale', text='Unsaved text')
        result = response(self.root, stale)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'revision_conflict')
        state = self.core.read('task')['state']
        self.assertEqual(state['revision'], 2)
        self.assertEqual([v['text'] for v in state['notes']], ['Human note'])

    def test_history_pagination_is_explicit_and_read_only(self):
        for revision in range(1, 5):
            self.core.note('task', str(revision), revision)
        request = self.request('history', limit=2)
        first = response(self.root, request)['result']
        self.assertEqual([v['revision'] for v in first['entries']], [5, 4])
        second = response(self.root, dict(request, before_revision=first['before_revision']))['result']
        self.assertEqual([v['revision'] for v in second['entries']], [3, 2])
        self.assertEqual(self.core.read('task')['state']['revision'], 5)

    def test_rejects_unsafe_or_oversized_requests_before_writes(self):
        for request in [self.request('execute', command='touch unwanted'),
                        self.request('note', expected_revision=True, text='bad'),
                        self.request('note', expected_revision=1, text='x' * 65537),
                        self.request('note', expected_revision=1, text='hi', unknown='field'),
                        self.request('history', limit=21),
                        self.request('history', before_revision=False)]:
            self.assertFalse(response(self.root, request)['ok'], request['action'])
        self.assertEqual(self.core.read('task')['state']['revision'], 1)

    def test_fixture_cannot_mutate(self):
        result = response(self.root, self.request('note', expected_revision=1, text='No write'), read_only=True)
        self.assertEqual(result['error']['code'], 'read_only')
        self.assertEqual(self.core.read('task')['state']['notes'], [])

    def test_data_navigation_uses_configured_paths_without_touching_broken_state(self):
        path = self.root / 'tasks/task/state.json'
        path.write_text('{broken')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        folder = response(self.root, {'action': 'state_folder', 'request_id': 'folder'})
        self.assertEqual(folder['result']['native_action'], {'kind': 'directory', 'target': str(self.root)})
        revealed = response(self.root, self.request('task_reveal'))
        self.assertEqual(revealed['result']['native_action'], {'kind': 'reveal', 'target': str(path)})
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        path.unlink()
        self.assertEqual(response(self.root, self.request('task_reveal'))['error']['code'], 'state_file_missing')
        self.assertFalse(path.exists())

    def test_data_navigation_rejects_forged_paths_and_preview_without_creating_folders(self):
        for command in (
            self.request('task_reveal', path='/tmp/other.json'),
            dict(self.request('task_reveal'), task_id='../other'),
            dict(self.request('task_reveal'), task_id='unknown'),
            self.request('state_folder'),  # global action does not take a task
            {'action': 'state_folder', 'request_id': 'folder', 'target': '/tmp'},
        ):
            self.assertFalse(response(self.root, command)['ok'])
        for command in (self.request('task_reveal'), {'action': 'state_folder', 'request_id': 'folder'}):
            self.assertEqual(response(self.root, command, read_only=True)['error']['code'], 'read_only')
        missing = self.root / 'absent'
        self.assertFalse(response(missing, {'action': 'state_folder', 'request_id': 'folder'})['ok'])
        self.assertFalse(missing.exists())

    def test_data_navigation_rejects_state_symlinks(self):
        path = self.root / 'tasks/task/state.json'
        other = self.root / 'other.json'
        other.write_text('untouched')
        path.unlink()
        path.symlink_to(other)
        self.assertEqual(response(self.root, self.request('task_reveal'))['error']['code'], 'unsafe_path')
        self.assertEqual(other.read_text(), 'untouched')

    def test_unhealthy_task_rejects_note_keeps_original_and_last_good(self):
        path = self.root / 'tasks/task/state.json'
        path.write_text('{broken')
        result = response(self.root, self.request('note', expected_revision=1, text='No write'))
        self.assertFalse(result['ok'])
        self.assertEqual(path.read_text(), '{broken')
        self.assertEqual(self.core.read('task')['last_good']['revision'], 1)

    def test_history_reads_only_requested_page_and_enforces_first_record_limit(self):
        for revision in range(1, 8):
            self.core.note('task', str(revision), revision)
        with patch.object(self.core.store, '_history_state', wraps=self.core.store._history_state) as read:
            page = self.core.store.history_page('task', 2)
            self.assertEqual(read.call_count, 2)
            self.assertEqual([row['revision'] for row in page['entries']], [8, 7])
        from observer.file_store import StateError
        with self.assertRaises(StateError) as caught:
            self.core.store.history_page('task', 2, max_bytes=1)
        self.assertEqual(caught.exception.code, 'history_entry_too_large')

    def test_korean_note_limit_is_utf8_not_ascii_escape_size(self):
        result = response(self.root, self.request('note', expected_revision=1, text='가' * 12000))
        self.assertTrue(result['ok'], result)

    def test_resource_actions_resolve_only_saved_task_scoped_targets(self):
        resource = {'resource_id': 'pr', 'kind': 'pr', 'label': 'Pull Request',
                    'target': 'https://github.com/example/demo/pull/42', 'open_mode': 'url'}
        put = self.request('resource_put', expected_revision=1, resource=resource)
        self.assertTrue(response(self.root, put)['ok'])
        self.assertTrue(response(self.root, put)['result']['replayed'])
        opened = response(self.root, self.request('resource_open', resource_id='pr'))
        self.assertEqual(opened['result']['native_action'], {'kind': 'url', 'target': resource['target']})
        copied = response(self.root, self.request('resource_copy', resource_id='pr'))
        self.assertEqual(copied['result']['native_action']['kind'], 'copy')
        malicious = self.request('resource_open', resource_id='pr', target='devin://acp?prompt=bad')
        self.assertFalse(response(self.root, malicious)['ok'])
        self.assertFalse(response(self.root, self.request('resource_open', resource_id='other'))['ok'])
        archived = self.request('resource_archive', expected_revision=2, resource_id='pr', archived=True)
        archived['request_id'] = 'archive'
        self.assertTrue(response(self.root, archived)['ok'])
        self.assertTrue(self.core.read('task')['state']['resources'][0]['archived'])
        self.assertEqual(self.core.read('task')['state']['resources'][0]['external_status'], 'unknown')

    def test_task_lifecycle_restores_previous_observation_and_keeps_notes(self):
        self.core.note('task', 'Keep', 1)
        off = self.request('observation', enabled=False, expected_revision=2)
        self.assertTrue(response(self.root, off)['ok'])
        done = dict(self.request('lifecycle', completed=True, expected_revision=3), request_id='done')
        self.assertTrue(response(self.root, done)['ok'])
        self.assertTrue(response(self.root, done)['result']['replayed'])
        restored = dict(self.request('lifecycle', completed=False, expected_revision=4), request_id='restore')
        self.assertTrue(response(self.root, restored)['ok'])
        state = self.core.read('task')['state']
        self.assertEqual(state['management']['lifecycle'], 'active')
        self.assertFalse(state['management']['observation_enabled'])
        self.assertEqual(state['notes'][0]['text'], 'Keep')

    def test_reporting_toggle_is_per_task_session_and_revision_guarded(self):
        self.core.create('second', 'Second', 'session-a')
        request = self.request('reporting', session_id='session-a', enabled=True, expected_revision=1)
        self.assertTrue(response(self.root, request)['ok'])
        self.assertTrue(self.core.read('task')['state']['contributions'][0]['reporting'])
        self.assertFalse(self.core.read('second')['state']['contributions'][0]['reporting'])
        self.assertFalse(response(self.root, dict(request, request_id='stale', enabled=False))['ok'])

    def test_session_fallback_never_returns_devin_url_or_opens_unlinked_session(self):
        copied = response(self.root, self.request('session_copy', session_id='session-a'))
        self.assertEqual(copied['result']['native_action'], {'kind': 'copy', 'target': 'session-a'})
        activated = response(self.root, self.request('devin_activate', session_id='session-a'))
        self.assertEqual(activated['result']['native_action']['kind'], 'devin_existing')
        self.assertFalse(response(self.root, self.request('session_copy', session_id='unlinked'))['ok'])
        self.assertFalse(response(self.root, self.request('session_copy', session_id='devin://acp'))['ok'])

    def test_read_mark_does_not_advance_task_revision_or_other_task_cursor(self):
        from observer.ui_reads import ReadCursors
        from observer.ui_projection import project
        self.core.create('second', 'Second', 'session-a')
        for task in ('task', 'second'):
            self.core.reporting(task, 'session-a', True, 1)
        self.core.report({'schema_version': 1, 'report_id': 'shared-report', 'session_id': 'session-a',
            'expected_revisions': {'task': 2, 'second': 2}, 'changes': {task: [
                {'op': 'define', 'item_id': 'p', 'title': 'Progress', 'value': 'reported', 'status': 'reported', 'evidence': []}
            ] for task in ('task', 'second')}})
        before = self.core.read('task')['state']
        command = self.request('mark_read', expected_revision=3, through_report_revision=3)
        self.assertTrue(response(self.root, command)['ok'])
        self.assertEqual(self.core.read('task')['state'], before)
        value = project(self.core.list(), read_cursors=ReadCursors(self.root).snapshot())
        self.assertEqual(value['tasks']['task']['read_state']['status'], 'read')
        self.assertEqual(value['tasks']['second']['read_state']['status'], 'unread')
        broken = project(self.core.list(), read_cursor_error={'code': 'invalid_read_cursors'})
        self.assertEqual(broken['tasks']['task']['read_state']['status'], 'unavailable')
