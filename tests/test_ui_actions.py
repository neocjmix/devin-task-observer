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
