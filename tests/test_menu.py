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

    def test_reports_sources_status_and_human_note_are_visible_without_model_calls(self):
        self.state.note('task', 'Do not lose this human note', 3)
        self.state.pin('task', 'test', True, 4)
        value = self.current()
        self.assertEqual(value['model_calls'], 0)
        text = json.dumps(menu_model(value), ensure_ascii=False)
        for expected in ['synthetic-A', '능동보고 ON', '3 passed', 'passed', 'artifact:test-output', 'Do not lose this human note', '📌', '독립 검증 아님']:
            self.assertIn(expected, text)

    def test_external_edit_displays_last_good_explicitly_not_modified_current_value(self):
        path = self.root / 'state/tasks/task/state.json'
        changed = json.loads(path.read_text())
        changed['items'][0]['value'] = 'uncommitted raw lie'
        path.write_text(json.dumps(changed))
        value = self.current()
        self.assertEqual(value['tasks'][0]['health'], 'external_edit')
        self.assertEqual(value['tasks'][0]['display_origin'], 'last_good')
        text = json.dumps(menu_model(value), ensure_ascii=False)
        self.assertIn('마지막 정상 커밋', text)
        self.assertIn('3 passed', text)
        self.assertNotIn('uncommitted raw lie', text)

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
        self.assertIn('능동보고 OFF', text)
        self.assertIn('마지막 보고', text)
        self.assertIn('수집기 정지', text)

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
        for expected in ('Old pinned note', 'Retained description', 'example.invalid/pull/3', '이관 당시 스냅샷', '독립 확인 위치 1개 보존'):
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
        for expected in ('보고 지침 재전달 필요', 'PostCompact', '후보 1', '외부 상태 unknown', '태스크 소속을 자동 확정하지 않음', '순서/완료 독립 검증 없음'):
            self.assertIn(expected, text)

    def test_menu_limits_are_explicit_and_do_not_drop_state(self):
        value = self.current()
        original = copy.deepcopy(value['tasks'][0])
        value['tasks'] = [{**copy.deepcopy(original), 'task_id': 'task-' + str(i)} for i in range(51)]
        before = copy.deepcopy(value)
        text = json.dumps(menu_model(value), ensure_ascii=False)
        self.assertIn('태스크 50개까지 표시', text)
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
