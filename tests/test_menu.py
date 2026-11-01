import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from observer.app import save_config
from observer.cooperative import CooperativeState
from observer.menu import menu_model, snapshot


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = CooperativeState(self.root / 'state')
        self.state.create('task', 'Synthetic implementation', 'synthetic-A')
        self.state.reporting('task', 'synthetic-A', True, 1)
        self.state.report({'schema_version': 1, 'report_id': 'report-1', 'session_id': 'synthetic-A',
                           'expected_revisions': {'task': 2}, 'changes': {'task': [
                               {'op': 'define', 'item_id': 'test', 'title': 'Tests', 'value': '3 passed', 'status': 'passed',
                                'evidence': [{'session_id': 'synthetic-A', 'reference': 'artifact:test-output', 'kind': 'local_artifact'}]}]}})
        self.config = {'version': 1, 'workspaces': [str(self.root)], 'source': str(self.root / 'source/sessions.db'),
                       'store': str(self.root / 'collector.sqlite3'), 'port': 8765}
        self.config_path = self.root / 'config.json'
        save_config(self.config_path, self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def current(self):
        return snapshot(self.root / 'state', self.config)

    def task_row(self, model, task_id='task'):
        return next(row for row in model['rows']
                    if row.get('kind') == 'task' and row['task_id'] == task_id)

    def test_reports_sources_status_and_human_note_are_visible_without_model_calls(self):
        self.state.note('task', 'Do not lose this human note', 3)
        self.state.pin('task', 'test', True, 4)
        value = self.current()
        self.assertEqual(value['model_calls'], 0)
        text = json.dumps(menu_model(value), ensure_ascii=False)
        for expected in ['synthetic-A', '자동 보고 켜짐', '3 passed', 'artifact:test-output', 'Do not lose this human note', '📌', '세션 보고']:
            self.assertIn(expected, text)

    def test_task_overview_shows_values_updaters_and_sessions_without_another_submenu(self):
        model = menu_model(self.current())
        task = self.task_row(model)
        self.assertNotIn('[task]', task['title'])
        children = task['children']
        item = next(row for row in children if row.get('kind') == 'item' and row['item_id'] == 'test')
        summary = next(row for row in children if row.get('kind') == 'item_summary' and row['item_id'] == 'test')
        self.assertIn('Tests', item['title'])
        self.assertNotIn('[test]', item['title'])
        self.assertIn('3 passed', summary['title'])
        self.assertIn('synthetic-A', summary['caption'])
        self.assertIn('세션 보고', summary['caption'])
        self.assertFalse(summary.get('children'))
        self.assertIn('artifact:test-output', json.dumps(item.get('children', []), ensure_ascii=False))
        sessions = [row for row in children if row.get('kind') == 'session']
        self.assertEqual([row['session_id'] for row in sessions], ['synthetic-A'])
        self.assertIn('자동 보고 켜짐', json.dumps(sessions, ensure_ascii=False))
        self.assertFalse(any('참여 세션' in row.get('title', '') and row.get('children')
                             for row in children))

    def test_duplicate_display_titles_keep_distinct_task_and_item_identity(self):
        self.state.create('other', 'Synthetic implementation')
        value = self.current()
        source = next(task for task in value['tasks'] if task['task_id'] == 'task')
        duplicate = copy.deepcopy(source['state']['items'][0])
        duplicate['item_id'] = 'another-test'
        duplicate['value'] = 'Different result with the same title'
        source['state']['items'].append(duplicate)
        model = menu_model(value)
        tasks = [row for row in model['rows'] if row.get('kind') == 'task']
        self.assertEqual({row['task_id'] for row in tasks}, {'task', 'other'})
        for row in tasks:
            self.assertIn('Synthetic implementation', row['title'])
        children = self.task_row(model)['children']
        self.assertEqual([row['item_id'] for row in children if row.get('kind') == 'item'],
                         ['test', 'another-test'])
        summaries = {row['item_id']: row for row in children if row.get('kind') == 'item_summary'}
        self.assertIn('3 passed', summaries['test']['title'])
        self.assertIn('Different result', summaries['another-test']['title'])

    def test_custom_report_status_is_preserved_without_inventing_completion(self):
        value = self.current()
        value['tasks'][0]['state']['items'][0]['status'] = '리뷰 대기 / upstream 조사 중'
        before = copy.deepcopy(value)
        model = menu_model(value)
        item = next(row for row in self.task_row(model)['children'] if row.get('kind') == 'item')
        self.assertIn('리뷰 대기 / upstream 조사 중', item['title'])
        self.assertNotIn('완료', item['title'])
        self.assertEqual(value, before)

    def test_human_pin_changes_item_metadata_without_claiming_human_authored_value(self):
        original = self.state.read('task')['state']['items'][0]['value']
        self.state.pin('task', 'test', True, 3)
        value = self.current()
        item = value['tasks'][0]['state']['items'][0]
        self.assertEqual(item['value'], original)
        self.assertEqual(item['updated_by']['kind'], 'human')
        summary = next(row for row in self.task_row(menu_model(value))['children']
                       if row.get('kind') == 'item_summary')
        self.assertIn('사용자 변경', summary['caption'])
        self.assertNotIn('사용자 입력', summary['caption'])
        self.assertIn(original, summary['title'])

    def test_human_change_of_imported_item_is_not_presented_as_unchanged_import(self):
        # Core preserves legacy_import when a human edits or pins an imported item.
        self.state.pin('task', 'test', True, 3)
        value = self.current()
        value['tasks'][0]['state']['items'][0]['verification'] = 'legacy_import'
        summary = next(row for row in self.task_row(menu_model(value))['children']
                       if row.get('kind') == 'item_summary')
        self.assertIn('사용자 변경', summary['caption'])
        self.assertNotIn('가져온 기록', summary['caption'])

    def test_external_edit_displays_last_good_explicitly_not_modified_current_value(self):
        path = self.root / 'state/tasks/task/state.json'
        changed = json.loads(path.read_text())
        changed['items'][0]['value'] = 'uncommitted raw lie'
        path.write_text(json.dumps(changed))
        value = self.current()
        self.assertEqual(value['tasks'][0]['health'], 'external_edit')
        self.assertEqual(value['tasks'][0]['display_origin'], 'last_good')
        model = menu_model(value)
        text = json.dumps(model, ensure_ascii=False)
        self.assertIn('마지막 정상 기록', text)
        self.assertIn('3 passed', text)
        self.assertNotIn('uncommitted raw lie', text)
        task = self.task_row(model)
        self.assertIn('⚠', task['title'])
        first_item = next(index for index, row in enumerate(task['children']) if row.get('kind') == 'item')
        primary_warning = ' '.join(row.get('title', '') + ' ' + row.get('caption', '')
                                   for row in task['children'][:first_item])
        self.assertIn('마지막 정상 기록', primary_warning)

    def test_malformed_task_does_not_hide_other_healthy_tasks(self):
        self.state.create('other', 'Healthy independent task')
        (self.root / 'state/tasks/task/state.json').write_text('{')
        value = self.current()
        self.assertEqual(len(value['tasks']), 2)
        text = json.dumps(menu_model(value), ensure_ascii=False)
        self.assertIn('Healthy independent task', text)
        self.assertIn('invalid', text)
        self.assertEqual(menu_model(value)['title'], '◉!')

    def test_off_and_freshness_are_not_presented_as_active_reporting(self):
        self.state.reporting('task', 'synthetic-A', False, 3)
        text = json.dumps(menu_model(self.current()), ensure_ascii=False)
        self.assertIn('자동 보고 꺼짐', text)
        self.assertIn('최근 보고', text)
        self.assertIn('관측 일시 중지', text)

    def test_snapshot_real_cli_works_without_optional_native_dependencies(self):
        result = subprocess.run([sys.executable, '-m', 'observer.menu', '--config', str(self.config_path),
                                 '--root', str(self.root / 'state'), '--snapshot'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['snapshot']['tasks'][0]['task_id'], 'task')
        self.assertFalse(Path(self.config['store']).exists())

    def test_menu_rejects_state_inside_devin_source_folder(self):
        result = subprocess.run([sys.executable, '-m', 'observer.menu', '--config', str(self.config_path),
                                 '--root', str(self.root / 'source/observer'), '--snapshot'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'source/observer').exists())

    def test_legacy_view_order_pins_resources_and_origin_visible(self):
        value = self.current()
        value['tasks'][0]['state']['legacy'] = {
            'description': 'Retained description', 'lifecycle': 'active', 'review_cursors': [{'task_id': 'task'}],
            'resources': [{'kind': 'pr', 'url': 'https://example.invalid/pull/3'}],
            'view': {'sections': [{'id': 'sec-1', 'title': 'First section', 'items': [{'content': 'Old pinned note', 'pinned': True}]},
                                  {'id': 'sec-2', 'title': 'Second section', 'items': []}]}}
        text = json.dumps(menu_model(value), ensure_ascii=False)
        self.assertLess(text.index('First section'), text.index('Second section'))
        for expected in ('Old pinned note', 'Retained description', 'example.invalid/pull/3', '가져온 기록', '확인 위치 1개 보존'):
            self.assertIn(expected, text)

    def test_hook_context_warning_and_observed_candidates_are_not_reported_facts(self):
        value = self.current()
        value['tasks'][0]['session_activity'] = {'synthetic-A': {'health': 'ok', 'last_seen': '2026-10-30T00:00:00+00:00',
                                                               'last_event': 'PostCompact', 'needs_context': True}}
        value['observations'] = {'warnings': [], 'sessions': [{
            'id': 'synthetic-A', 'status': 'current', 'observation_count': 7, 'workspace': '/synthetic',
            'resource_mentions': [{'kind': 'pull_request', 'value': 'https://example.invalid/pull/7', 'verification': 'mentioned', 'source_ref': 'node-7'}],
            'plan_snapshots': [{'node_id': 7, 'items': [{'content': 'Run tests', 'status': 'completed'}]}],
            'resources_truncated': False, 'plans_truncated': False}]}
        text = json.dumps(menu_model(value), ensure_ascii=False)
        for expected in ('보고 지침 갱신 필요', 'PostCompact', '후보 · 상태 미확인', '태스크 연결 미확인', '순서·완료 미확인'):
            self.assertIn(expected, text)

    def test_menu_limits_are_explicit_and_do_not_drop_state(self):
        value = self.current()
        original = copy.deepcopy(value['tasks'][0])
        value['tasks'] = [{**copy.deepcopy(original), 'task_id': 'task-' + str(i)} for i in range(51)]
        before = copy.deepcopy(value)
        text = json.dumps(menu_model(value), ensure_ascii=False)
        self.assertIn('태스크 50개 표시', text)
        self.assertEqual(len([row for row in menu_model(value)['rows'] if row.get('kind') == 'task']), 50)
        self.assertEqual(value, before)

    def test_changed_configuration_requires_restart_without_mixing_stores(self):
        changed = {**self.config, 'store': str(self.root / 'different.sqlite3')}
        save_config(self.config_path, changed)
        value = snapshot(self.root / 'state', self.config, config_path=self.config_path)
        self.assertIsNone(value['observations'])
        self.assertEqual(value['errors'][0]['code'], 'configuration_changed')
        self.assertEqual(value['tasks'][0]['state']['items'][0]['value'], '3 passed')
        self.assertFalse(Path(changed['store']).exists())


if __name__ == '__main__':
    unittest.main()
