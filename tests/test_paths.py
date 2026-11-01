"""Default entry points share a hidden data home; overrides never migrate data."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]


class DataPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='observer home ')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.data = self.home / '.devin-task-observer'
        self.workspace = self.home / 'personal workspace'
        self.workspace.mkdir()
        self.env = {**os.environ, 'HOME': str(self.home), 'PYTHONPATH': str(REPOSITORY)}
        self.source = self.home / 'source/sessions.db'
        self.source.parent.mkdir()
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript('CREATE TABLE refinery_schema_history(version INTEGER);'
                             'INSERT INTO refinery_schema_history VALUES(17);'
                             'CREATE TABLE sessions(id TEXT,title TEXT,working_directory TEXT,main_chain_id INTEGER);'
                             'CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,parent_node_id INTEGER,chat_message TEXT);')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', ('synthetic-session', 'Synthetic', str(self.workspace), 0))
            db.execute('INSERT INTO message_nodes VALUES (?,?,?,?)', ('synthetic-session', 0, None,
                       json.dumps({'message_id': 'm0', 'role': 'user', 'content': 'Synthetic fixture only'})))
            db.commit()

    def process(self, *arguments):
        result = subprocess.run([sys.executable, *map(str, arguments)], cwd=self.workspace,
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def cli(self, *arguments):
        return self.process(REPOSITORY / 'observerctl', *arguments)

    def test_default_config_state_collection_and_both_ui_projections_share_data_home(self):
        # A previous installation is not silently adopted, changed, or deleted.
        legacy = self.home / 'Library/Application Support/Devin Task Observer'
        legacy.mkdir(parents=True)
        (legacy / 'config.json').write_text('OLD_CONFIG_SENTINEL')
        (legacy / 'state').mkdir()
        (legacy / 'state/untouched.txt').write_text('OLD_STATE_SENTINEL')
        original = {str(p): p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.cli('configure', '--workspace', self.workspace, '--source', self.source)
        config = json.loads((self.data / 'config.json').read_text())
        self.assertEqual(config['store'], str(self.data / 'observer.sqlite3'))
        self.cli('state', 'create', '--task', 'task-t', '--title', 'Persistent synthetic task', '--session', 'synthetic-session')
        state_path = self.data / 'state/tasks/task-t/state.json'
        state = state_path.read_bytes()
        self.assertTrue(json.loads(self.cli('collect', '--once'))['ok'])
        self.assertTrue((self.data / 'observer.sqlite3').is_file())
        menu = json.loads(self.cli('menu', '--snapshot'))
        self.assertEqual(menu['snapshot']['root'], str(self.data / 'state'))
        self.assertEqual(menu['snapshot']['tasks'][0]['task_id'], 'task-t')
        popover = json.loads(self.process('-m', 'observer.desktop', '--snapshot'))
        self.assertIn('task-t', popover['tasks'])
        self.assertEqual(state_path.read_bytes(), state)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual({str(p): p.read_bytes() for p in legacy.rglob('*') if p.is_file()}, original)

    def test_explicit_config_store_and_root_override_all_defaults(self):
        custom = self.home / 'custom data'
        config, store, state = custom / 'config.json', custom / 'collector.sqlite3', custom / 'tasks-state'
        self.cli('--config', config, 'configure', '--workspace', self.workspace, '--source', self.source, '--store', store)
        self.cli('state', '--root', state, 'create', '--task', 'custom', '--title', 'Explicit custom location')
        self.assertTrue(json.loads(self.cli('collect', '--config', config, '--once'))['ok'])
        menu = json.loads(self.cli('menu', '--config', config, '--root', state, '--snapshot'))
        self.assertEqual(menu['snapshot']['root'], str(state))
        self.assertEqual(menu['snapshot']['tasks'][0]['task_id'], 'custom')
        popover = json.loads(self.process('-m', 'observer.desktop', '--config', config, '--root', state, '--snapshot'))
        self.assertIn('custom', popover['tasks'])
        self.assertTrue(store.is_file())
        self.assertFalse(self.data.exists())

    def test_setup_script_defaults_match_cli_without_moving_native_runtime(self):
        runtime = self.home / 'Library/Application Support/Devin Task Observer/menu-runtime'
        (runtime / 'bin').mkdir(parents=True)
        (runtime / 'bin/python').touch()
        (runtime / 'pyvenv.cfg').write_text('synthetic environment')
        program = '''
import json, runpy, sys
from unittest.mock import patch
setup = runpy.run_path(sys.argv[1])
def capture(app, python, config, root, **kwargs):
    return {'config': str(config), 'root': str(root), 'upgraded': False, 'previous': kwargs}
with patch.dict(setup['main'].__globals__, {'upgrade_launcher': capture}), patch('sys.platform', 'darwin'):
    setup['main'](['--upgrade-app', sys.argv[2]])
'''
        result = json.loads(self.process('-c', program, REPOSITORY / 'scripts/setup_menu.py', self.home / 'Observer.app'))
        self.assertEqual(result['config'], str(self.data / 'config.json'))
        self.assertEqual(result['root'], str(self.data / 'state'))
        self.assertEqual(result['python'], str(runtime / 'bin/python'))
        self.assertFalse(result['auto_start_enabled'])
        self.assertFalse(self.data.exists())
        self.assertFalse((self.home / 'Observer.app').exists())

    def test_all_collectors_and_state_entrypoints_import_one_default_without_side_effects(self):
        program = '''
import json
from observer import paths, app, state_cli, menu, popover, integration, server, __main__
assert app.DEFAULT_CONFIG == paths.DEFAULT_CONFIG
assert app.DEFAULT_STORE == server.DEFAULT_STORE == __main__.DEFAULT_STORE == paths.DEFAULT_STORE
assert app.DEFAULT_ROOT == state_cli.DEFAULT_ROOT == menu.DEFAULT_ROOT == popover.DEFAULT_ROOT == integration.DEFAULT_ROOT == paths.DEFAULT_ROOT
assert state_cli.parser().parse_args(['list']).root == paths.DEFAULT_ROOT
assert not paths.DATA_HOME.exists()
print(json.dumps({'home': str(paths.DATA_HOME)}))
'''
        result = json.loads(self.process('-c', program))
        self.assertEqual(result['home'], str(self.data))
        self.assertFalse(self.data.exists())


if __name__ == '__main__':
    unittest.main()
