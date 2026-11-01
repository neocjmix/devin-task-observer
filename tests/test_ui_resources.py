"""Explicit resource registration remains separate from observed candidates."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.state_schema import published, validate
from observer.ui_projection import project


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'
        self.core = CooperativeState(self.root)
        self.core.create('task-a', 'Resources', 'session-a')

    def state(self, task='task-a'):
        return self.core.read(task)['state']

    def resource(self, **fields):
        return {'resource_id': 'resource-pr', 'kind': 'pr', 'label': 'Synthetic PR',
                'target': 'https://github.com/example/synthetic/pull/42', 'open_mode': 'url', **fields}

    def put(self, resource=None, **kwargs):
        return self.core.resource_put('task-a', resource or self.resource(),
                                      self.state()['revision'], **kwargs)

    def stored(self):
        return self.state()['resources'][0]

    def test_old_task_stays_valid_and_registration_stamps_human_origin(self):
        self.assertNotIn('resources', self.state())
        validate(self.state(), 'task')
        self.put()
        resource = self.stored()
        self.assertFalse(resource['archived'])
        self.assertEqual(resource['created_by'], {'kind': 'human', 'session_id': None})
        self.assertEqual(resource['updated_by'], resource['created_by'])
        self.assertEqual(resource['created_at'], resource['updated_at'])
        self.assertEqual(resource['updated_at'], self.state()['updated_at'])
        self.assertEqual(resource['external_status'], 'unknown')
        self.assertEqual(resource['evidence'], [])
        projection = project(self.core.list())
        self.assertTrue(projection['capabilities']['registered_resources'])
        self.assertEqual(projection['tasks']['task-a']['metadata']['resources'], [resource])

    def test_update_preserves_creation_evidence_and_archived_state(self):
        reference = json.dumps({'node_id': 'node-a', 'source_hash': 'abc123', 'source_ref': 'mentions-v2'})
        evidence = [{'session_id': 'session-a', 'kind': 'session_event', 'reference': reference}]
        self.put(self.resource(evidence=evidence))
        created = self.stored()
        self.core.resource_archive('task-a', 'resource-pr', True, self.state()['revision'])
        self.put(self.resource(label='New concise label'))
        current = self.stored()
        self.assertEqual(current['created_at'], created['created_at'])
        self.assertEqual(current['created_by'], created['created_by'])
        self.assertEqual(current['evidence'], evidence)
        self.assertTrue(current['archived'])
        self.assertEqual(current['label'], 'New concise label')
        self.assertEqual(current['external_status'], 'unknown')
        self.core.resource_archive('task-a', 'resource-pr', False, self.state()['revision'])
        self.assertFalse(self.stored()['archived'])
        self.assertIn('Synthetic PR', json.dumps(self.core.store.history('task-a')))

    def test_stale_update_and_reused_request_do_not_overwrite(self):
        original = self.resource()
        revision = self.state()['revision']
        self.core.resource_put('task-a', original, revision, 'resource-request-one')
        stale = self.state()['revision']
        self.put(self.resource(label='Latest human value'))
        current = self.state()
        with self.assertRaises(StateError) as caught:
            self.core.resource_put('task-a', self.resource(label='Stale'), stale)
        self.assertEqual(caught.exception.code, 'revision_conflict')
        self.assertTrue(self.core.resource_put('task-a', original, revision, 'resource-request-one')['replayed'])
        with self.assertRaises(StateError) as caught:
            self.core.resource_put('task-a', self.resource(label='Changed intent'), revision, 'resource-request-one')
        self.assertEqual(caught.exception.code, 'report_id_reused')
        self.assertEqual(self.state(), current)

    def test_task_scoped_registration_does_not_adopt_shared_candidates_elsewhere(self):
        self.core.create('task-b', 'Other task', 'session-a')
        other = self.state('task-b')
        observed = {'sessions': [{'id': 'session-a', 'workspace': '/synthetic/workspace',
                                 'resource_mentions': [{'kind': 'pr', 'value': self.resource()['target'],
                                   'verification': 'mentioned', 'source_ref': 'mentions-v2',
                                   'node_id': 'node-a', 'source_hash': 'a' * 64}]}]}
        before = project(self.core.list(), observations=observed)
        self.assertTrue(before['resource_candidates'])
        self.assertNotIn('resources', self.state())
        self.put()
        after = project(self.core.list(), observations=observed)
        self.assertEqual(self.state('task-b'), other)
        self.assertEqual(after['resource_candidates'], before['resource_candidates'])
        self.assertTrue(all(c['association'] == 'candidate' for c in after['resource_candidates'].values()))

    def test_unlinked_evidence_and_forged_fields_rejected_without_state_changes(self):
        original = self.state()
        bad_values = [self.resource(evidence=[{'session_id': 'unlinked', 'kind': 'session_event', 'reference': 'node'}]),
                      self.resource(updated_by={'kind': 'session', 'session_id': 'session-a'}),
                      self.resource(external_status='merged'), self.resource(archived=True)]
        for resource in bad_values:
            with self.subTest(resource=resource), self.assertRaises(StateError) as caught:
                self.put(resource)
            self.assertEqual(caught.exception.code, 'validation_error')
            self.assertEqual(self.state(), original)

    def test_resource_schema_rejects_duplicate_ids_and_false_external_verification(self):
        self.put()
        for mutate in (lambda state: state['resources'].append(copy.deepcopy(state['resources'][0])),
                       lambda state: state['resources'][0].update(external_status='verified'),
                       lambda state: state['resources'][0].update(created_by={'kind': 'session', 'session_id': 'session-a'})):
            value = self.state()
            mutate(value)
            with self.assertRaises(ValueError):
                validate(value, 'task')

    def test_unsafe_link_protocols_credentials_and_controls_are_rejected(self):
        original = self.state()
        for target in ('javascript:alert(1)', 'data:text/html,x', 'devin://session/a',
                       'file:///tmp/a.md', 'https://user:secret@example.test',
                       'https://example.test:bad/a', 'https://example.test:99999/a',
                       'https://', '//example.test', 'https://example.test/a b',
                       'https://example.test/\nfoo', 'https://example.test/\\evil', '\x00'):
            with self.subTest(target=target), self.assertRaises(StateError):
                self.put(self.resource(target=target))
            self.assertEqual(self.state(), original)
        for mode, target in (('file', '../a.md'), ('file', '~/a.md'), ('file', '//server/a.md'),
                             ('copy', 'branch\nnext'), ('copy', ' ')):
            with self.subTest(mode=mode, target=target), self.assertRaises(StateError):
                self.put(self.resource(open_mode=mode, target=target))

    def test_local_paths_and_literal_copy_need_no_existence_or_network_check(self):
        for resource in (self.resource(resource_id='resource-file', kind='deliverable',
                                       open_mode='file', target='/synthetic/missing/내 문서.md'),
                         self.resource(resource_id='resource-branch', kind='branch',
                                       open_mode='copy', target='feature/UI-v2')):
            validate(resource, 'resource')
            with patch('socket.socket', side_effect=AssertionError('no network probe')), \
                    patch('subprocess.Popen', side_effect=AssertionError('no opener on registration')):
                self.put(resource)
        self.assertEqual(len(self.state()['resources']), 2)

    def test_registered_resources_survive_safe_edit_notes_pins_and_session_reports(self):
        self.core.reporting('task-a', 'session-a', True, self.state()['revision'])
        report = {'schema_version': 1, 'report_id': 'reported-item', 'session_id': 'session-a',
                  'expected_revisions': {'task-a': self.state()['revision']},
                  'changes': {'task-a': [{'op': 'define', 'item_id': 'item-a', 'title': 'Status',
                                        'value': 'Keep', 'status': 'reported', 'evidence': []}]}}
        self.core.report(report)
        self.core.pin('task-a', 'item-a', True, self.state()['revision'])
        self.core.note('task-a', 'Human note', self.state()['revision'])
        items, notes = self.state()['items'], self.state()['notes']
        self.put()
        self.assertEqual(self.state()['items'], items)
        self.assertEqual(self.state()['notes'], notes)
        resource = self.stored()
        edit = self.core.export_edit('task-a')
        edit['view']['markdown'] = 'Edited human view'
        self.core.apply_edit(edit)
        self.assertEqual(self.stored(), resource)
        report['report_id'] = 'valid-report-after-resource'
        report['expected_revisions']['task-a'] = self.state()['revision']
        report['changes']['task-a'][0]['item_id'] = 'item-b'
        self.core.report(report)
        self.assertEqual(self.stored(), resource)
        self.assertEqual(self.state()['notes'], notes)
        self.assertTrue(self.state()['items'][0]['pinned'])
        report['report_id'] = 'attempt-resource-change'
        report['expected_revisions']['task-a'] = self.state()['revision']
        report['resources'] = []
        with self.assertRaises(StateError):
            self.core.report(report)
        self.assertEqual(self.stored(), resource)

    def test_archive_missing_and_nonboolean_flags_do_not_mutate(self):
        original = self.state()
        with self.assertRaises(StateError) as caught:
            self.core.resource_archive('task-a', 'missing', True, original['revision'])
        self.assertEqual(caught.exception.code, 'resource_not_found')
        with self.assertRaises(StateError) as caught:
            self.core.resource_archive('task-a', 'missing', 1, original['revision'])
        self.assertEqual(caught.exception.code, 'validation_error')
        self.assertEqual(self.state(), original)

    def test_cli_registration_validation_archive_and_published_schema(self):
        repository = Path(__file__).resolve().parents[1]
        path = Path(self.temp.name) / 'resource.json'
        path.write_text(json.dumps(self.resource()))
        command = [sys.executable, str(repository / 'observerctl'), 'state', '--root', str(self.root)]
        calls = [['schema', '--kind', 'resource'], ['validate', '--kind', 'resource', '--file', str(path)],
                 ['resource-put', '--task', 'task-a', '--file', str(path), '--expected-revision', '1'],
                 ['resource-archive', '--task', 'task-a', '--resource', 'resource-pr', '--archived', 'on',
                  '--expected-revision', '2']]
        for arguments in calls:
            result = subprocess.run(command + arguments, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertTrue(self.stored()['archived'])
        self.assertEqual(json.loads((repository / 'schemas/cooperative-v1/resource.schema.json').read_text()), published('resource'))


if __name__ == '__main__':
    unittest.main()
