import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from observer.devin_local import read_sessions, SourceIncomplete
from observer.store import open_store, sync


def message(mid, text, role='user'):
    return json.dumps({'message_id': mid, 'role': role, 'content': text})


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / 'source' / 'sessions.db'
        self.src.parent.mkdir()
        self.work = self.root / 'personal'
        self.work.mkdir()
        self.writer = sqlite3.connect(self.src)
        self.writer.executescript('''
        CREATE TABLE refinery_schema_history(version INTEGER);
        INSERT INTO refinery_schema_history VALUES(17);
        CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, working_directory TEXT, main_chain_id INTEGER);
        CREATE TABLE message_nodes(row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INTEGER, parent_node_id INTEGER, chat_message TEXT);
        ''')
        self.writer.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('one','synthetic',str(self.work),1))
        self.writer.executemany('INSERT INTO message_nodes VALUES(?,?,?,?,?)', [(1,'one',0,None,message('a','one')), (2,'one',1,0,message('b','two','assistant'))])
        self.writer.commit()
        self.db = open_store(self.root / 'observer' / 'observer.db', self.src)

    def tearDown(self):
        self.db.close()
        self.writer.close()
        self.tmp.cleanup()

    def collect(self):
        return sync(self.db, read_sessions(self.src, [self.work]), [self.work])

    def test_replay_same_size_edit_and_append(self):
        self.assertEqual(self.collect(), 2)
        self.assertEqual(self.collect(), 0)
        self.writer.execute('UPDATE message_nodes SET chat_message=? WHERE node_id=1', (message('b','six','assistant'),))
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(3,'one',2,1,message('c','new')))
        self.writer.execute('UPDATE sessions SET main_chain_id=2')
        self.writer.commit()
        self.assertEqual(self.collect(), 2)
        self.assertEqual(self.collect(), 0)
        self.assertEqual([r[0] for r in self.db.execute('SELECT transition FROM observations ORDER BY id')], ['added','added','modified','added'])
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'current')

    def test_deleted_node_and_reintroduced_node_are_recorded_and_stale(self):
        self.collect()
        self.writer.execute('DELETE FROM message_nodes WHERE node_id=1')
        self.writer.execute('UPDATE sessions SET main_chain_id=0')
        self.writer.commit()
        self.assertEqual(self.collect(), 1)
        self.assertEqual(self.collect(), 0)
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'stale')
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(9,'one',1,0,message('b','two','assistant')))
        self.writer.execute('UPDATE sessions SET main_chain_id=1')
        self.writer.commit()
        self.assertEqual(self.collect(), 1)
        self.assertEqual(self.db.execute('SELECT transition FROM observations ORDER BY id DESC LIMIT 1').fetchone()[0], 'restored')
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'stale')

    def test_checkpoint_and_observations_roll_back_together(self):
        self.collect()
        before = self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0]
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(3,'one',2,1,message('c','new')))
        self.writer.execute('UPDATE sessions SET main_chain_id=2')
        self.writer.commit()
        self.db.execute("CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON checkpoints BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError): self.collect()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_nodes').fetchone()[0], 2)
        self.assertEqual(self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0], before)
        self.db.execute('DROP TRIGGER fail_checkpoint'); self.db.commit()
        self.assertEqual(self.collect(),1)

    def test_ambiguous_duplicate_and_malformed_source(self):
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(3,'one',2,1,message('a','different')))
        self.writer.execute('UPDATE sessions SET main_chain_id=2'); self.writer.commit()
        self.collect()
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'stale')
        before = self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0]
        self.writer.execute("UPDATE message_nodes SET chat_message='{' WHERE node_id=2"); self.writer.commit()
        with self.assertRaises(SourceIncomplete): self.collect()
        self.assertEqual(self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0], before)

    def test_source_file_is_never_a_store(self):
        with self.assertRaises(ValueError): open_store(self.src, self.src)
        with self.assertRaises(ValueError): open_store(self.src.parent / 'other.db', self.src)

    def test_unknown_source_version_does_not_advance_checkpoint(self):
        self.collect()
        before = self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0]
        self.writer.execute('UPDATE refinery_schema_history SET version=999'); self.writer.commit()
        with self.assertRaises(SourceIncomplete): self.collect()
        self.assertEqual(self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0], before)

    def test_locked_source_does_not_advance_checkpoint(self):
        self.collect()
        before = self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0]
        self.writer.execute('BEGIN EXCLUSIVE')
        try:
            with self.assertRaises(SourceIncomplete):
                self.collect()
            self.assertEqual(self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0], before)
        finally:
            self.writer.rollback()

    def test_duplicate_rows_and_sqlite_rowid_rewrite_do_not_replay(self):
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)', (3,'one',2,1,message('a','one')))
        self.writer.execute('UPDATE sessions SET main_chain_id=2'); self.writer.commit()
        self.assertEqual(self.collect(), 3)
        self.writer.execute('UPDATE message_nodes SET row_id=33 WHERE node_id=2'); self.writer.commit()
        self.assertEqual(self.collect(), 0)
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'current')

    def test_observed_additive_tool_metadata_preserves_both_nodes(self):
        base = {'message_id':'tool-msg','role':'assistant','content':'', 'tool_calls':[{'id':'call','name':'read','arguments':'{}','index':0,'kind':'tool'}], 'metadata':{}}
        updated = json.loads(json.dumps(base))
        updated['metadata']['extensions'] = {'chisel/tool_call_content': {'call': []}}
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(3,'one',2,1,json.dumps(base)))
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(4,'one',3,2,json.dumps(updated)))
        self.writer.execute('UPDATE sessions SET main_chain_id=3'); self.writer.commit()
        self.assertEqual(self.collect(), 4)
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'current')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_nodes').fetchone()[0], 4)
        changed = json.loads(json.dumps(updated)); changed['content'] = 'different'
        self.writer.execute('UPDATE message_nodes SET chat_message=? WHERE node_id=3',(json.dumps(changed),)); self.writer.commit()
        self.collect()
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'stale')

    def test_reorder_and_disappeared_session_preserve_history(self):
        self.collect()
        self.writer.execute('UPDATE message_nodes SET parent_node_id=NULL WHERE node_id=1')
        self.writer.commit()
        self.assertEqual(self.collect(), 1)
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'stale')
        self.writer.execute('DELETE FROM sessions'); self.writer.commit()
        self.assertEqual(self.collect(), 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT status FROM sessions').fetchone()[0], 'stale')

    def test_killed_process_during_checkpoint_rolls_back(self):
        self.collect()
        self.writer.execute('INSERT INTO message_nodes VALUES(?,?,?,?,?)',(3,'one',2,1,message('c','new')))
        self.writer.execute('UPDATE sessions SET main_chain_id=2'); self.writer.commit()
        store_path = self.root / 'observer' / 'observer.db'
        code = '''
import sqlite3,sys,time
from pathlib import Path
from observer.devin_local import read_sessions
from observer.store import sync
db=sqlite3.connect(sys.argv[1])
def block():
 print('ready', flush=True)
 time.sleep(30)
 return 0
db.create_function('block_checkpoint',0,block)
db.execute("CREATE TEMP TRIGGER pause_checkpoint BEFORE UPDATE ON checkpoints BEGIN SELECT block_checkpoint(); END")
sync(db,read_sessions(Path(sys.argv[2]),[Path(sys.argv[3])]),[Path(sys.argv[3])])
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(store_path), str(self.src), str(self.work)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            child.kill(); child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
            child.stdout.close()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_nodes').fetchone()[0], 2)
        self.assertEqual(self.collect(), 1)


if __name__ == '__main__': unittest.main()
