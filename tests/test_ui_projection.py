"""S0 contracts against synthetic v1 data; no native UI or model dependency."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from observer.state_schema import validate
from observer.ui_projection import project


FIXTURE = Path(__file__).parent / 'fixtures/ui_v2/s0.json'


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.inputs = json.loads(FIXTURE.read_text())
        self.value = project(**self.inputs)

    def test_fixture_uses_real_v1_schema_not_future_ui_fields(self):
        for row in self.inputs['task_snapshots']:
            for key in ('state', 'last_good'):
                if row.get(key) is not None:
                    validate(row[key], 'task')

    def test_selected_state_round_trips_without_losing_items_notes_pins_or_legacy(self):
        for row in self.inputs['task_snapshots']:
            selected = row.get('state') if row['health'] == 'ok' else row.get('last_good')
            task_id = row['task_id']
            task = self.value['tasks'][task_id]
            if selected is None:
                self.assertIsNone(task['metadata'])
                continue
            reconstructed = {
                **task['metadata'],
                'items': [self.value['items'][task_id][key] for key in task['item_ids']],
                'contributions': [self.value['contributions'][task_id][key] for key in task['session_ids']],
            }
            self.assertEqual(reconstructed, selected)
            self.assertEqual(task['displayed_updated_at'], selected['updated_at'])
            self.assertEqual(task['displayed_revision'], selected['revision'])

    def test_shared_session_keeps_reporting_and_report_time_per_task(self):
        a = self.value['contributions']['task-auth']['session-a']
        b = self.value['contributions']['task-observer']['session-a']
        self.assertTrue(a['reporting'])
        self.assertFalse(b['reporting'])
        self.assertNotEqual(a['last_report_at'], b['last_report_at'])
        self.assertEqual(set(self.value['sessions']['session-a']['task_ids']), {'task-auth', 'task-observer'})
        self.assertNotIn('reporting', self.value['sessions']['session-a'])
        self.assertEqual(set(self.value['tasks']['task-auth']['session_ids']), {'session-a', 'session-b'})

    def test_same_titles_and_item_ids_do_not_merge(self):
        tasks = self.value['tasks']
        self.assertEqual(tasks['task-auth']['metadata']['title'], tasks['task-empty']['metadata']['title'])
        first = self.value['items']['task-auth']['situation']
        second = self.value['items']['task-observer']['situation']
        self.assertEqual(first['title'], second['title'])
        self.assertNotEqual(first['value'], second['value'])
        self.assertEqual(self.value['items']['task-empty'], {})

    def test_pin_actor_is_last_editor_not_value_authorship_or_verification(self):
        item = self.value['items']['task-auth']['pinned-policy']
        self.assertTrue(item['pinned'])
        self.assertEqual(item['updated_by']['kind'], 'human')
        self.assertEqual(item['created_by']['kind'], 'session')
        self.assertEqual(item['verification'], 'reported')
        self.assertNotIn('author', item)

    def test_does_not_infer_lifecycle_intervention_unread_or_tokens(self):
        self.assertTrue(self.value['items']['task-auth']['archived-plan']['archived'])
        self.assertIn('completed', {i['status'] for i in self.value['items']['task-auth'].values()})
        self.assertTrue(all(flag is False for flag in self.value['capabilities'].values()))
        for task in self.value['tasks'].values():
            for field in ('lifecycle', 'observation_enabled', 'unread_count', 'intervention', 'next_action'):
                self.assertNotIn(field, task)
        for session in self.value['sessions'].values():
            self.assertNotIn('tokens', session)
            self.assertNotIn('locator', session)

    def test_external_edit_is_not_displayed_and_no_last_good_stays_unavailable(self):
        row = next(r for r in self.inputs['task_snapshots'] if r['task_id'] == 'task-external')
        task = self.value['tasks']['task-external']
        self.assertEqual(task['health'], 'external_edit')
        self.assertEqual(task['display_origin'], 'last_good')
        self.assertEqual(task['metadata']['title'], row['last_good']['title'])
        self.assertNotEqual(task['metadata']['title'], row['state']['title'])
        self.assertEqual(task['errors'], row['errors'])
        broken = self.value['tasks']['task-invalid']
        self.assertEqual(broken['display_origin'], 'unavailable')
        self.assertIsNone(broken['displayed_updated_at'])
        self.assertEqual(self.value['tasks']['task-auth']['health'], 'ok')

    def test_malformed_claimed_good_task_is_isolated_and_diagnostic(self):
        row = self.inputs['task_snapshots'][0]
        row['last_good'] = deepcopy(row['state'])
        row['state']['schema_version'] = 999
        value = project(**self.inputs)
        task = value['tasks'][row['task_id']]
        self.assertEqual(task['display_origin'], 'last_good')
        self.assertIn('current:', task['projection_errors'][0])
        row['last_good']['task_id'] = 'wrong-task'
        value = project(**self.inputs)
        task = value['tasks'][row['task_id']]
        self.assertEqual(task['display_origin'], 'unavailable')
        self.assertEqual(len(task['projection_errors']), 2)
        self.assertEqual(len(value['tasks']), len(self.inputs['task_snapshots']))

    def test_resources_deduplicate_without_losing_distinct_evidence_or_confirming(self):
        resources = list(self.value['resource_candidates'].values())
        prs = [r for r in resources if r['kind'] == 'pr']
        self.assertEqual(len(prs), 1)
        pr = prs[0]
        self.assertEqual({s['session_id'] for s in pr['sources']}, {'session-a', 'session-b'})
        expected = {
            json.dumps({**m, 'session_id': s['id'], 'workspace': s['workspace']}, sort_keys=True)
            for s in self.inputs['observations']['sessions'] for m in s['resource_mentions']
            if m['kind'] == 'pr'
        }
        self.assertEqual({json.dumps(s, sort_keys=True) for s in pr['sources']}, expected)
        self.assertEqual(len(pr['sources']), len(expected))
        self.assertEqual(set(pr['related_task_ids']), {'task-auth', 'task-observer'})
        self.assertNotIn(pr['candidate_id'], self.value['tasks']['task-empty']['resource_candidate_ids'])
        for resource in resources:
            self.assertEqual(resource['association'], 'candidate')
            self.assertEqual(resource['external_status'], 'unknown')
            # Bridge has no discovery timestamp; never substitute collector time.
            self.assertNotIn('observed_at', resource)

    def test_workspace_scoped_resources_never_merge_across_repositories(self):
        for kind, value in (('branch', 'main'), ('commit', 'abcdef0'), ('ticket', 'LAB-42')):
            with self.subTest(kind=kind):
                inputs = deepcopy(self.inputs)
                base = inputs['observations']['sessions'][0]
                base['resource_mentions'] = [{'kind': kind, 'value': value, 'verification': 'mentioned',
                                             'source_ref': 'mentions-v2', 'node_id': 1, 'source_hash': 'synthetic'}]
                other = deepcopy(base)
                other.update(id='other-workspace-session', workspace='/synthetic/other-workspace')
                inputs['observations']['sessions'] = [base, other]
                result = project(**inputs)
                self.assertEqual(len(result['resource_candidates']), 2)
                self.assertNotEqual(*[r['key'] for r in result['resource_candidates'].values()])

    def test_resource_identity_reuses_github_normalization_but_retains_original_sources(self):
        record = self.inputs['observations']['sessions'][0]
        record['resource_mentions'] = [
            {'kind': 'repo', 'value': uri, 'verification': 'mentioned', 'source_ref': 'mentions-v2',
             'node_id': number, 'source_hash': str(number)}
            for number, uri in enumerate(('https://github.com/Example/Repo.git', 'https://github.com/example/repo/'))]
        self.inputs['observations']['sessions'] = [record]
        resources = list(project(**self.inputs)['resource_candidates'].values())
        self.assertEqual(len(resources), 1)
        self.assertEqual(len(resources[0]['sources']), 2)

    def test_observation_and_activity_do_not_rewrite_reporting_freshness_or_plan_order(self):
        self.assertEqual(self.value['health']['runtime'], self.inputs['runtime'])
        self.assertEqual(self.value['health']['observations']['collector'], self.inputs['observations']['collector'])
        for observed in self.inputs['observations']['sessions']:
            session = self.value['sessions'][observed['id']]
            self.assertEqual(session['plan_snapshots'], observed['plan_snapshots'])
            self.assertEqual(session['observation']['resources_truncated'], observed['resources_truncated'])
            self.assertEqual(session['observation']['plans_truncated'], observed['plans_truncated'])
            self.assertNotIn('reporting_failed', session)
        self.assertEqual(self.value['sessions']['session-a']['activity'], self.inputs['session_activity']['session-a'])
        self.assertNotEqual(self.value['sessions']['session-a']['activity']['last_seen'],
                            self.value['contributions']['task-auth']['session-a']['last_report_at'])

    def test_zero_io_and_detached_results_including_nested_values(self):
        original = deepcopy(self.inputs)
        with ExitStack() as stack:
            for name in ('builtins.open', 'os.open', 'sqlite3.connect', 'subprocess.Popen', 'socket.socket'):
                stack.enter_context(patch(name, side_effect=AssertionError('projection performed I/O: ' + name)))
            result = project(**self.inputs)
        self.assertEqual(result, self.value)
        self.assertEqual(self.inputs, original)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        result['items']['task-auth']['situation']['evidence'].append({'changed': True})
        result['tasks']['task-auth']['metadata']['notes'].append({'changed': True})
        result['health']['runtime']['changed'] = True
        self.assertEqual(self.inputs, original)
        self.assertEqual(project(**self.inputs), self.value)
        self.inputs['task_snapshots'][0]['state']['title'] = 'new incoming snapshot'
        self.assertEqual(project(**original), self.value)

    def test_acquisition_failure_is_not_reported_as_an_empty_healthy_collection(self):
        value = project([], state_errors=['state_unavailable: journal cannot be recovered'])
        self.assertEqual(value['tasks'], {})
        self.assertTrue(value['health']['state_errors'])
        self.assertIsNone(value['health']['observations'])
        self.assertIsNone(value['health']['runtime'])

    def test_duplicate_input_id_is_rejected_instead_of_silently_replacing_data(self):
        self.inputs['task_snapshots'].append(deepcopy(self.inputs['task_snapshots'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate task snapshot'):
            project(**self.inputs)
        self.inputs['task_snapshots'].pop()
        self.inputs['observations']['sessions'].append(deepcopy(self.inputs['observations']['sessions'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate observed session'):
            project(**self.inputs)


if __name__ == '__main__':
    unittest.main()
