import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from probe.devin_local import inspect, main


class DevinLocalProbeTests(unittest.TestCase):
    def test_workspace_scoped_counts_chain_duplicates_and_no_body_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'test.db'
            work = Path(tmp) / 'synthetic'
            db = sqlite3.connect(dbpath)
            db.executescript('CREATE TABLE sessions(id TEXT, working_directory TEXT, main_chain_id INTEGER); CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, parent_node_id INTEGER, chat_message TEXT);')
            db.executemany('INSERT INTO sessions VALUES (?,?,?)', [('s1', str(work), 1), ('other', '/other', 0)])
            messages = [(0, None, {'role':'user','message_id':'same','content':'private test body'}), (1, 0, {'role':'assistant','message_id':'same','content':'synthetic result'})]
            db.executemany('INSERT INTO message_nodes VALUES (?,?,?,?)', [('s1', n, p, json.dumps(m)) for n, p, m in messages])
            db.commit(); db.close()
            result = inspect(dbpath, work, b'k' * 32)
            self.assertEqual(result['session_count'], 1)
            session = result['sessions'][0]
            self.assertEqual((session['nodes'], session['distinct_message_ids']), (2, 1))
            self.assertEqual(session['active_chain_nodes'], 2)
            self.assertTrue(session['active_chain_complete'])
            self.assertNotIn('private test body', json.dumps(result))
            self.assertNotIn('s1', json.dumps(result))
            self.assertEqual(result['sessions'][0]['session_ref'], inspect(dbpath, work, b'k' * 32)['sessions'][0]['session_ref'])
            self.assertNotEqual(result['sessions'][0]['session_ref'], inspect(dbpath, work, b'z' * 32)['sessions'][0]['session_ref'])
            self.assertEqual(inspect(dbpath, Path(tmp) / 'other', b'k' * 32)['session_count'], 0)
            db = sqlite3.connect(dbpath)
            db.execute("UPDATE message_nodes SET chat_message = replace(chat_message, 'synthetic result', 'synthetic answer') WHERE node_id=1")
            db.commit(); db.close()
            changed = inspect(dbpath, work, b'k' * 32)['sessions'][0]
            self.assertEqual(session['nodes'], changed['nodes'])
            self.assertNotEqual(session['content_fingerprint'], changed['content_fingerprint'])

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'missing.db'
            with patch.object(sys, 'argv', ['probe', '--database', str(dbpath), '--workspace', tmp]), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as error: main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(dbpath.exists())
            self.assertFalse(json.loads(output.getvalue())['snapshot_complete'])

    def test_busy_database_reports_incomplete_without_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'busy.db'
            db = sqlite3.connect(dbpath)
            db.executescript('CREATE TABLE sessions(id TEXT, working_directory TEXT, main_chain_id INTEGER); CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, parent_node_id INTEGER, chat_message TEXT);')
            db.commit()
            db.execute('BEGIN EXCLUSIVE')
            try:
                with patch.object(sys, 'argv', ['probe', '--database', str(dbpath), '--workspace', tmp]), contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(SystemExit) as error:
                        main()
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(json.loads(output.getvalue())['snapshot_complete'])
            finally:
                db.rollback()
                db.close()

    def test_malformed_node_and_broken_chain_are_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'broken.db'
            db = sqlite3.connect(dbpath)
            db.executescript('CREATE TABLE sessions(id TEXT, working_directory TEXT, main_chain_id INTEGER); CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, parent_node_id INTEGER, chat_message TEXT);')
            db.execute('INSERT INTO sessions VALUES (?,?,?)', ('synthetic', tmp, 1))
            db.execute('INSERT INTO message_nodes VALUES (?,?,?,?)', ('synthetic', 1, 99, '{'))
            db.commit(); db.close()
            result = inspect(dbpath, Path(tmp), b'k' * 32)
            self.assertFalse(result['snapshot_complete'])
            self.assertFalse(result['sessions'][0]['active_chain_complete'])
            self.assertEqual(result['sessions'][0]['malformed_messages'], 1)


if __name__ == '__main__': unittest.main()
