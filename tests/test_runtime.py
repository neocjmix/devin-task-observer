from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from observer.app import save_config, status
from observer.cooperative import CooperativeState
from observer.runtime import CollectorRuntime, collector_status, service_running


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.root / 'source').mkdir()
        self.source = self.root / 'source/sessions.db'
        self.config_path = self.root / 'config.json'
        self.config = {'version': 1, 'workspaces': [str(self.workspace)], 'source': str(self.source),
                       'store': str(self.root / 'collector/observer.sqlite3'), 'port': 8765}
        self.writer = sqlite3.connect(self.source)
        self.writer.executescript('CREATE TABLE refinery_schema_history(version INTEGER); INSERT INTO refinery_schema_history VALUES(17);'
                                 'CREATE TABLE sessions(id TEXT,title TEXT,working_directory TEXT,main_chain_id INTEGER);'
                                 'CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,parent_node_id INTEGER,chat_message TEXT);')
        self.writer.execute('INSERT INTO sessions VALUES (?,?,?,?)', ('synthetic-A', 'Synthetic source only', str(self.workspace), 0))
        self.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?)', ('synthetic-A', 0, None, json.dumps({'message_id': 'message-0', 'role': 'user', 'content': 'synthetic fixture'})))
        self.writer.commit()
        save_config(self.config_path, self.config)
        self.runtime = CollectorRuntime(self.config, interval=.1, max_delay=.4)

    def tearDown(self):
        self.runtime.stop()
        self.writer.close()
        self.temporary.cleanup()

    def count(self):
        with closing(sqlite3.connect(self.config['store'])) as db:
            return db.execute('SELECT count(*) FROM observations').fetchone()[0]

    def await_condition(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.025)
        self.fail('condition did not become true before timeout')

    def test_once_is_read_only_to_source_and_preserves_reported_state(self):
        state = CooperativeState(self.root / 'state')
        state.create('task', 'Current report', 'synthetic-A')
        before = state.read('task')
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.runtime.acquire()
        self.assertTrue(self.runtime.refresh_once()['ok'])
        self.assertEqual(self.count(), 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), digest)
        self.assertEqual(state.read('task'), before)
        self.assertTrue(self.runtime.snapshot()['running'])
        self.assertIsNotNone(collector_status(self.config)['last_success'])

    def test_duplicate_service_refused_before_collecting(self):
        self.runtime.acquire()
        another = CollectorRuntime(self.config)
        with self.assertRaisesRegex(ValueError, '이미 실행 중'):
            another.acquire()
        another.stop()
        self.assertTrue(status(self.config)['running'])
        self.assertTrue(service_running(self.config))

    def test_background_manual_refresh_and_restart_preserve_checkpoint(self):
        self.runtime.interval = 30
        self.runtime.start()
        self.await_condition(lambda: collector_status(self.config)['last_success'] is not None)
        self.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?)', ('synthetic-A', 1, 0, json.dumps({'message_id': 'message-1', 'role': 'assistant', 'content': 'new synthetic update'})))
        self.writer.execute('UPDATE sessions SET main_chain_id=1')
        self.writer.commit()
        self.runtime.request_refresh()
        self.await_condition(lambda: self.count() == 2)
        self.runtime.stop()
        self.runtime.start()
        self.await_condition(lambda: self.runtime.snapshot()['refresh'].get('ok') is True)
        self.assertEqual(self.count(), 2)
        self.runtime.stop()
        with closing(sqlite3.connect(self.config['store'])) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_missing_source_retries_and_recovers_without_dropping_checkpoint(self):
        self.runtime.acquire()
        self.assertTrue(self.runtime.refresh_once()['ok'])
        old_success = collector_status(self.config)['last_success']
        hidden = self.source.with_suffix('.hidden')
        self.source.rename(hidden)
        self.runtime.start()
        self.await_condition(lambda: collector_status(self.config)['last_error'] == 'source_missing')
        self.assertEqual(collector_status(self.config)['last_success'], old_success)
        self.assertEqual(self.count(), 1)
        self.assertLessEqual(self.runtime.snapshot()['next_retry_seconds'], .4)
        hidden.rename(self.source)
        self.runtime.request_refresh()
        self.await_condition(lambda: collector_status(self.config)['last_error'] is None)
        self.assertEqual(self.count(), 1)

    def test_invalid_child_response_or_exit_cannot_claim_success(self):
        self.runtime.acquire()
        for stdout, code in ((b'{"ok":true}', 4), (b'{"ok":"yes"}', 0), (b'x' * 65537, 0)):
            with self.subTest(code=code, length=len(stdout)):
                with patch('observer.runtime.subprocess.Popen') as popen:
                    popen.return_value.communicate.return_value = stdout, b''
                    popen.return_value.returncode = code
                    self.assertEqual(self.runtime.refresh_once()['reason'], 'refresh_process_failed')
        self.assertEqual(collector_status(self.config)['last_error'], 'refresh_process_failed')

    def test_child_timeout_is_killed_and_recorded(self):
        self.runtime.acquire()
        with patch('observer.runtime.subprocess.Popen') as popen:
            popen.return_value.communicate.side_effect = [subprocess.TimeoutExpired('fixture', .01), (b'', b'')]
            self.assertEqual(self.runtime.refresh_once()['reason'], 'refresh_timeout')
            popen.return_value.kill.assert_called_once()

    def test_real_process_shutdown_and_restart_have_no_http_listener(self):
        for _ in range(2):
            process = subprocess.Popen([sys.executable, '-m', 'observer.runtime', '--config', str(self.config_path)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertIn('no browser, no model calls', process.stdout.readline())
                self.await_condition(lambda: collector_status(self.config)['last_success'] is not None)
                self.assertTrue(status(self.config)['running'])
            finally:
                process.send_signal(signal.SIGTERM)
                _, error = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, error)
                self.assertFalse(status(self.config)['running'])
        self.assertEqual(self.count(), 1)

    def test_read_status_never_creates_missing_database(self):
        self.assertEqual(collector_status(self.config)['last_error'], 'collector_unavailable_or_incompatible')
        self.assertFalse(Path(self.config['store']).exists())
        self.assertFalse(service_running(self.config))


if __name__ == '__main__':
    unittest.main()
