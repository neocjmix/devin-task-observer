"""Task lifecycle pauses only task updates, preserving shared-session state."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.state_schema import task_management, validate
from observer.ui_projection import project


class TaskManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'
        self.core = CooperativeState(self.root)
        self.serial = 0
        for task in ('task-a', 'task-b'):
            self.core.create(task, task, 'shared-session')
            self.core.reporting(task, 'shared-session', True, self.revision(task))

    def state(self, task='task-a'):
        return self.core.read(task)['state']

    def revision(self, task='task-a'):
        return self.state(task)['revision']

    def request(self, task='task-a', **fields):
        self.serial += 1
        return {'schema_version': 1, 'report_id': f'management-report-{self.serial}',
                'session_id': 'shared-session', 'expected_revisions': {task: self.revision(task)},
                'changes': {task: [{'op': 'define', 'item_id': f'item-{self.serial}', 'title': 'Status',
                                    'value': 'Synthetic result', 'status': 'reported', 'evidence': [], **fields}]}}

    def observed(self):
        return {'sessions': [{'id': 'shared-session', 'workspace': '/synthetic/workspace',
                              'resource_mentions': [{'kind': 'pr', 'value': 'https://github.com/example/synthetic/pull/4',
                                'verification': 'mentioned', 'source_ref': 'mentions-v2',
                                'node_id': 'node-a', 'source_hash': 'a' * 64}],
                              'plan_snapshots': [{'node_id': 'node-plan', 'items': [{'id': 'plan-a'}],
                                                  'verification': 'agent_reported'}]}]}

    def test_legacy_default_active_on_is_projected_without_rewriting_state(self):
        before = self.state()
        self.assertNotIn('management', before)
        value = project(self.core.list())['tasks']['task-a']
        self.assertEqual(value['management'], {'lifecycle': 'active', 'observation_enabled': True,
                                               'previous_observation_enabled': True})
        self.assertTrue(value['effective_observation_enabled'])
        self.assertNotIn('management', value['metadata'])
        self.assertEqual(self.state(), before)

    def test_agent_completed_status_does_not_complete_task(self):
        self.core.report(self.request(status='completed'))
        self.assertEqual(task_management(self.state())['lifecycle'], 'active')
        self.assertTrue(task_management(self.state())['observation_enabled'])

    def test_complete_and_restore_preserve_both_previous_observation_preferences(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.core.observation('task-a', enabled, self.revision())
                edges = self.state()['contributions']
                self.core.lifecycle('task-a', True, self.revision())
                self.assertEqual(self.state()['management'], {'lifecycle': 'completed',
                    'observation_enabled': False, 'previous_observation_enabled': enabled})
                self.assertEqual(self.state()['contributions'], edges)
                self.core.lifecycle('task-a', False, self.revision())
                self.assertEqual(self.state()['management']['lifecycle'], 'active')
                self.assertEqual(self.state()['management']['observation_enabled'], enabled)
                self.assertEqual(self.state()['contributions'], edges)

    def test_completion_preserves_values_pins_notes_resources_report_time_and_history(self):
        self.core.report(self.request())
        item_id = self.state()['items'][0]['item_id']
        self.core.pin('task-a', item_id, True, self.revision())
        self.core.note('task-a', 'Human decision retained', self.revision())
        self.core.resource_put('task-a', {'resource_id': 'branch-a', 'kind': 'branch', 'label': 'Branch',
                                        'target': 'feature/synthetic', 'open_mode': 'copy'}, self.revision())
        before = self.state()
        history = self.core.store.history('task-a')
        self.core.lifecycle('task-a', True, self.revision())
        for key in ('items', 'notes', 'resources', 'contributions', 'view'):
            self.assertEqual(self.state()[key], before[key])
        self.assertEqual(self.core.store.history('task-a')[:-1], history)
        self.assertEqual(self.state()['updated_by']['kind'], 'human')

    def test_off_task_rejects_report_while_shared_session_updates_other_task(self):
        self.core.observation('task-a', False, self.revision())
        before = self.state()
        with self.assertRaises(StateError) as caught:
            self.core.report(self.request())
        self.assertEqual(caught.exception.code, 'observation_disabled')
        self.core.report(self.request('task-b'))
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.state('task-b')['items']), 1)
        self.assertTrue(self.state()['contributions'][0]['reporting'])

    def test_postcompletion_report_rejected_but_successful_receipt_replays(self):
        original = self.request()
        self.core.report(original)
        self.core.lifecycle('task-a', True, self.revision())
        before = self.state()
        history = self.core.store.history('task-a')
        with self.assertRaises(StateError) as caught:
            self.core.report(self.request())
        self.assertEqual(caught.exception.code, 'task_completed')
        self.assertTrue(self.core.report(original)['replayed'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.core.store.history('task-a'), history)

    def test_multi_task_request_is_atomic_when_one_task_is_paused(self):
        self.core.observation('task-a', False, self.revision())
        request = self.request('task-b')
        request['expected_revisions']['task-a'] = self.revision()
        request['changes']['task-a'] = copy.deepcopy(request['changes']['task-b'])
        before = {t: self.state(t) for t in ('task-a', 'task-b')}
        with self.assertRaises(StateError) as caught:
            self.core.report(request)
        self.assertEqual(caught.exception.code, 'observation_disabled')
        self.assertEqual({t: self.state(t) for t in before}, before)

    def test_stale_human_control_conflicts_without_losing_newer_report(self):
        stale = self.revision()
        self.core.report(self.request())
        before = self.state()
        with self.assertRaises(StateError) as caught:
            self.core.lifecycle('task-a', True, stale)
        self.assertEqual(caught.exception.code, 'revision_conflict')
        self.assertEqual(self.state(), before)

    def test_duplicate_completion_does_not_overwrite_restore_preference(self):
        revision = self.revision()
        self.core.lifecycle('task-a', True, revision, 'complete-once')
        before = self.state()
        self.assertTrue(self.core.lifecycle('task-a', True, revision, 'complete-once')['replayed'])
        with self.assertRaises(StateError) as caught:
            self.core.lifecycle('task-a', True, self.revision())
        self.assertEqual(caught.exception.code, 'already_completed')
        with self.assertRaises(StateError) as caught:
            self.core.observation('task-a', True, self.revision())
        self.assertEqual(caught.exception.code, 'task_completed')
        self.assertEqual(self.state(), before)
        self.core.lifecycle('task-a', False, self.revision())
        self.assertTrue(self.state()['management']['observation_enabled'])

    def test_projection_removes_paused_automatic_associations_not_global_sessions(self):
        observed = self.observed()
        active = project(self.core.list(), observations=observed)
        self.assertEqual(active['tasks']['task-a']['plan_session_ids'], ['shared-session'])
        self.core.observation('task-a', False, self.revision())
        paused = project(self.core.list(), observations=observed)
        self.assertEqual(paused['tasks']['task-a']['resource_candidate_ids'], [])
        self.assertEqual(paused['tasks']['task-a']['plan_session_ids'], [])
        self.assertTrue(paused['tasks']['task-b']['resource_candidate_ids'])
        self.assertEqual(paused['tasks']['task-b']['plan_session_ids'], ['shared-session'])
        self.assertEqual(paused['sessions']['shared-session'], active['sessions']['shared-session'])
        candidate = next(iter(paused['resource_candidates'].values()))
        self.assertEqual(candidate['related_task_ids'], ['task-b'])
        self.assertEqual(paused['contributions'], active['contributions'])
        self.assertEqual(paused['tasks']['task-a']['session_ids'], ['shared-session'])
        self.core.lifecycle('task-b', True, self.revision('task-b'))
        completed = project(self.core.list(), observations=observed)
        self.assertEqual(next(iter(completed['resource_candidates'].values()))['related_task_ids'], [])
        self.assertTrue(completed['sessions']['shared-session']['plan_snapshots'])

    def test_invalid_management_and_agent_mutation_are_rejected(self):
        for management in ({'lifecycle': 'completed', 'observation_enabled': True, 'previous_observation_enabled': True},
                           {'lifecycle': 'active'},
                           {'lifecycle': 'active', 'observation_enabled': 1, 'previous_observation_enabled': True}):
            state = self.state()
            state['management'] = management
            with self.subTest(management=management), self.assertRaises(ValueError):
                validate(state, 'task')
        for method in (self.core.lifecycle, self.core.observation):
            with self.assertRaises(StateError) as caught:
                method('task-a', 1, self.revision())
            self.assertEqual(caught.exception.code, 'validation_error')
        report = self.request()
        report['management'] = {'lifecycle': 'active'}
        with self.assertRaises(StateError):
            self.core.report(report)

    def test_corrupt_management_uses_last_good_without_reactivating_associations(self):
        self.core.lifecycle('task-a', True, self.revision())
        committed = self.state()
        invalid = copy.deepcopy(committed)
        invalid['management']['observation_enabled'] = True
        path = self.root / 'tasks/task-a/state.json'
        raw = json.dumps(invalid).encode()
        path.write_bytes(raw)
        projection = project(self.core.list(), observations=self.observed())
        task = projection['tasks']['task-a']
        self.assertEqual(task['display_origin'], 'last_good')
        self.assertEqual(task['health'], 'invalid')
        self.assertEqual(task['management'], committed['management'])
        self.assertFalse(task['effective_observation_enabled'])
        self.assertEqual(task['resource_candidate_ids'], [])
        self.assertEqual(projection['tasks']['task-b']['health'], 'ok')
        self.assertEqual(path.read_bytes(), raw)

    def test_cli_pause_complete_restore_and_restart_preserve_off_preference(self):
        repository = Path(__file__).resolve().parents[1]
        command = [sys.executable, str(repository / 'observerctl'), 'state', '--root', str(self.root)]
        for arguments in (['observation', '--enabled', 'off'], ['lifecycle', '--completed', 'on'],
                          ['lifecycle', '--completed', 'off']):
            result = subprocess.run(command + arguments + ['--task', 'task-a', '--expected-revision', str(self.revision())],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(json.loads(result.stdout)['ok'])
        restarted = CooperativeState(self.root)
        state = restarted.read('task-a')['state']
        self.assertEqual(state['management']['lifecycle'], 'active')
        self.assertFalse(state['management']['observation_enabled'])
        result = subprocess.run(command + ['list'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        task = next(t for t in json.loads(result.stdout)['tasks'] if t['task_id'] == 'task-a')
        self.assertFalse(task['management']['observation_enabled'])


if __name__ == '__main__':
    unittest.main()
