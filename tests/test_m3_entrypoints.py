"""Installed-entrypoint subprocess journeys using disposable synthetic state."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from observer.app import save_config
from observer.registry import create_task, link_session
from observer.store import open_store
from observer.views import add_item, ensure_view, get_view

REPOSITORY = Path(__file__).resolve().parents[1]


class M3EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='m3 entrypoint ')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / 'synthetic workspace'
        self.workspace.mkdir()
        self.source = self.base / 'source/sessions.db'
        self.source.parent.mkdir()
        self.state = self.base / 'state'
        self.config_path = self.base / 'config.json'
        self.config = {'version': 1, 'workspaces': [str(self.workspace)], 'source': str(self.source),
                       'store': str(self.base / 'collector/observer.sqlite3'), 'port': 8765}
        save_config(self.config_path, self.config)

    def cli(self, *arguments, stdin=None, ok=True, json_output=True):
        result = subprocess.run([sys.executable, str(REPOSITORY / 'observerctl'), *map(str, arguments)],
                                cwd=self.workspace, input=stdin, capture_output=True, text=True, timeout=15)
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0 if ok else 2, result.stdout + result.stderr)
        if not json_output:
            return result
        value = json.loads(result.stdout)
        if 'ok' in value:
            self.assertEqual(value['ok'], ok, value)
        return value

    def source_fixture(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript('CREATE TABLE refinery_schema_history(version INTEGER); INSERT INTO refinery_schema_history VALUES(17);'
                             'CREATE TABLE sessions(id TEXT,title TEXT,working_directory TEXT,main_chain_id INTEGER);'
                             'CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,parent_node_id INTEGER,chat_message TEXT);')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', ('synthetic-session', 'Synthetic only', str(self.workspace), 0))
            db.execute('INSERT INTO message_nodes VALUES (?,?,?,?)', ('synthetic-session', 0, None,
                        json.dumps({'message_id': 'synthetic-message', 'role': 'user', 'content': 'Synthetic fixture, no private data'})))
            db.commit()
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def state_cli(self, *args, **kwargs):
        return self.cli('state', '--root', self.state, *args, **kwargs)

    def test_integration_entrypoint_hook_context_read_and_reversible_remove(self):
        options = ['--workspace', self.workspace, '--root', self.state]
        self.assertFalse(self.cli('integration', 'status', *options)['installed'])
        self.assertTrue(self.cli('integration', 'install', *options)['installed'])
        value = self.cli('--config', self.config_path, 'integration', 'status', *options)
        self.assertEqual(value['health'], 'ok')
        payload = {'session_id': 'synthetic-hook-session', 'hook_event_name': 'SessionStart',
                   'prompt': 'PRIVATE_SENTINEL_NEVER_PERSIST'}
        hook = self.cli('integration', 'hook', *options, stdin=json.dumps(payload))
        self.assertIn('additionalContext', hook['hookSpecificOutput'])
        context = next(self.state.glob('sessions/*/context.json'))
        who = self.state_cli('whoami', '--context', context)
        self.assertEqual(who['context']['session_id'], payload['session_id'])
        self.assertFalse(who['identity_verified'])
        for path in self.state.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'PRIVATE_SENTINEL', path.read_bytes())
        self.assertEqual(self.cli('integration', 'hook', *options, stdin='{broken'), {})
        self.assertFalse(self.cli('integration', 'remove', *options)['installed'])
        self.assertFalse((self.workspace / '.devin/hooks.v1.json').exists())
        self.assertTrue(context.exists())

    def test_collect_and_menu_entrypoints_preserve_source_and_reported_state(self):
        source_hash = self.source_fixture()
        self.state_cli('create', '--task', 'task-t', '--title', 'Synthetic tracked task', '--session', 'synthetic-session')
        self.state_cli('reporting', '--task', 'task-t', '--session', 'synthetic-session', '--enabled', 'on', '--expected-revision', 1)
        report = {'schema_version': 1, 'session_id': 'synthetic-session', 'report_id': 'entrypoint-report',
                  'expected_revisions': {'task-t': 2}, 'changes': {'task-t': [
                      {'op': 'define', 'item_id': 'tests', 'title': 'Test state', 'value': 'Synthetic tests reported passing',
                       'status': 'passed', 'evidence': []}]}}
        self.state_cli('report', '--file', '-', stdin=json.dumps(report))
        before = (self.state / 'tasks/task-t/state.json').read_bytes()
        self.assertTrue(self.cli('collect', '--config', self.config_path, '--once')['ok'])
        self.assertTrue(self.cli('--config', self.config_path, 'collect', '--once')['ok'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual((self.state / 'tasks/task-t/state.json').read_bytes(), before)
        with closing(sqlite3.connect(self.config['store'])) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 1)
        for arguments in (['menu', '--config', self.config_path], ['--config', self.config_path, 'menu']):
            menu = self.cli(*arguments, '--root', self.state, '--snapshot')
            self.assertEqual(menu['snapshot']['model_calls'], 0)
            self.assertEqual(menu['snapshot']['tasks'][0]['task_id'], 'task-t')
            self.assertIn('Synthetic tests reported passing', json.dumps(menu))
        self.assertEqual((self.state / 'tasks/task-t/state.json').read_bytes(), before)

    def test_collect_failed_source_is_visible_and_next_process_recovers(self):
        failure = self.cli('collect', '--config', self.config_path, '--once', ok=False)
        self.assertEqual(failure['reason'], 'source_missing')
        status = self.cli('--config', self.config_path, 'status')
        self.assertFalse(status['running'])
        self.assertIsNone(status['last_success'])
        self.assertEqual(status['last_error'], 'source_missing')
        self.source_fixture()
        self.assertTrue(self.cli('collect', '--config', self.config_path, '--once')['ok'])
        status = self.cli('--config', self.config_path, 'status')
        self.assertIsNotNone(status['last_success'])
        self.assertIsNone(status['last_error'])

    def legacy_fixture(self):
        self.source_fixture()
        self.cli('collect', '--config', self.config_path, '--once')
        with closing(open_store(Path(self.config['store']), self.source)) as db:
            task = create_task(db, 'Legacy synthetic task', 'Keep original human description')
            link_session(db, task, 'synthetic-session')
            view = ensure_view(db, task)
            section = db.execute('SELECT id FROM view_sections WHERE view_id=?', (view,)).fetchone()[0]
            add_item(db, view, section, 1, 'Pinned human decision remains', True)
            old_view = get_view(db, task)
        return task, old_view, hashlib.sha256(Path(self.config['store']).read_bytes()).hexdigest()

    def test_migration_plan_apply_check_and_rollback_entrypoints(self):
        task, old_view, source_hash = self.legacy_fixture()
        source = Path(self.config['store'])
        target = self.base / 'migration target'
        plan = self.cli('migrate', 'plan', '--source', source, '--all')
        self.assertFalse(target.exists())
        applied = self.cli('migrate', 'apply', '--source', source, '--all', '--target', target,
                           '--expected-fingerprint', plan['source_fingerprint'])
        self.assertFalse(applied['replayed'])
        imported = self.cli('state', '--root', target, 'read', '--task', task)['state']
        self.assertEqual(imported['legacy']['view'], old_view)
        self.assertTrue(imported['items'][0]['pinned'])
        self.assertFalse(imported['contributions'][0]['reporting'])
        checked = self.cli('--config', self.config_path, 'migrate', 'check', '--target', target)
        self.assertTrue(checked['rollback_eligible'])
        self.assertEqual(checked['errors'], [])
        restored = self.base / 'restored.sqlite'
        rollback = self.cli('migrate', 'rollback', '--target', target, '--restore-to', restored)
        self.assertTrue(rollback['retired'])
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(get_view(db, task), old_view)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
        retired = self.cli('state', '--root', target, 'read', '--task', task, ok=False)
        self.assertEqual(retired['error']['code'], 'retired_store')
        self.assertTrue(self.cli('migrate', 'check', '--target', target)['retired'])

    def test_migration_entrypoint_refuses_stale_plan_and_rollback_after_new_note(self):
        task, _, _ = self.legacy_fixture()
        source = Path(self.config['store'])
        target = self.base / 'migration target'
        plan = self.cli('migrate', 'plan', '--source', source, '--task', task)
        with closing(sqlite3.connect(source)) as db:
            db.execute('UPDATE tasks SET description=? WHERE id=?', ('Changed after plan', task))
            db.commit()
        failed = self.cli('migrate', 'apply', '--source', source, '--task', task, '--target', target,
                          '--expected-fingerprint', plan['source_fingerprint'], ok=False)
        self.assertEqual(failed['error']['code'], 'source_changed')
        self.assertFalse(target.exists())
        self.cli('migrate', 'apply', '--source', source, '--task', task, '--target', target)
        self.cli('state', '--root', target, 'note', '--task', task, '--text', 'New human note', '--expected-revision', 1)
        restore = self.base / 'must-not-exist.sqlite'
        failed = self.cli('migrate', 'rollback', '--target', target, '--restore-to', restore, ok=False)
        self.assertEqual(failed['error']['code'], 'rollback_conflict')
        self.assertFalse(restore.exists())
        current = self.cli('state', '--root', target, 'read', '--task', task)
        self.assertIn('New human note', json.dumps(current))


if __name__ == '__main__':
    unittest.main()
