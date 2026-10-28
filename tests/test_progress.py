import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from observer.store import open_store
from observer.registry import create_task, link_session, set_link_active, RevisionConflict
from observer.progress import pending_changes, acknowledge_changes, task_attention


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.store=root/'observer.db';self.source=root/'source'/'sessions.db'
        self.db=open_store(self.store,self.source)
        with self.db:
            self.db.executemany('INSERT INTO sessions VALUES (?,?,?,?,?,?)',[('s1','Synthetic one','/personal',1,'current',1),('s2','Synthetic two','/personal',1,'current',1)])
            for sid in ('s1','s2'):
                for i in range(3):self.db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)',(sid,i,'added',sid+'m'+str(i),'user','synthetic-hash',i))
        self.a=create_task(self.db,'Task A');self.b=create_task(self.db,'Task B')
        self.link=link_session(self.db,self.a,'s1');link_session(self.db,self.a,'s2');link_session(self.db,self.b,'s1')
    def tearDown(self):self.db.close();self.tmp.cleanup()

    def test_task_and_session_positions_are_independent_and_durable(self):
        a=pending_changes(self.db,self.a,'s1');self.assertEqual(len(a['changes']),3);self.assertEqual(a['changes'][0]['kinds'],['unknown'])
        through=a['changes'][1]['id'];self.assertEqual(acknowledge_changes(self.db,self.a,'s1',0,through),1)
        self.assertEqual(len(pending_changes(self.db,self.a,'s1')['changes']),1)
        self.assertEqual(len(pending_changes(self.db,self.a,'s2')['changes']),3)
        self.assertEqual(len(pending_changes(self.db,self.b,'s1')['changes']),3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],6)
        set_link_active(self.db,self.a,self.link,1,False)
        with self.assertRaises(ValueError):pending_changes(self.db,self.a,'s1')
        set_link_active(self.db,self.a,self.link,2,True)
        self.assertEqual(pending_changes(self.db,self.a,'s1')['last_observation_id'],through)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM task_progress_history').fetchone()[0],1)

    def test_attention_counts_only_scoped_unreviewed_changes(self):
        self.assertEqual(task_attention(self.db,self.a,['/personal']),{'pending_changes':6,'stale_sessions':0,'linked_sessions':2})
        through=pending_changes(self.db,self.a,'s1')['changes'][1]['id']
        acknowledge_changes(self.db,self.a,'s1',0,through)
        self.assertEqual(task_attention(self.db,self.a,['/personal'])['pending_changes'],4)
        self.assertEqual(task_attention(self.db,self.b,['/personal'])['pending_changes'],3)
        self.assertEqual(task_attention(self.db,self.a,['/other'])['linked_sessions'],0)
        with self.db:self.db.execute("UPDATE sessions SET status='stale' WHERE id='s2'")
        self.assertEqual(task_attention(self.db,self.a,['/personal'])['stale_sessions'],1)
        set_link_active(self.db,self.a,self.link,1,False)
        self.assertEqual(task_attention(self.db,self.a,['/personal'])['pending_changes'],3)

    def test_stale_revision_and_cross_session_ack_are_rejected(self):
        first=pending_changes(self.db,self.a,'s1')['changes'][0]['id']
        other=pending_changes(self.db,self.a,'s2')['changes'][0]['id']
        with self.assertRaises(ValueError):acknowledge_changes(self.db,self.a,'s1',0,other)
        acknowledge_changes(self.db,self.a,'s1',0,first)
        with self.assertRaises(RevisionConflict):acknowledge_changes(self.db,self.a,'s1',0,first)
        self.assertEqual(pending_changes(self.db,self.a,'s1')['last_observation_id'],first)

    def test_acknowledgment_cannot_skip_beyond_visible_page(self):
        with self.db:
            for i in range(70):self.db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)',('s1',10+i,'added','more'+str(i),'user','hash',i))
        page=pending_changes(self.db,self.a,'s1');self.assertEqual(len(page['changes']),50);self.assertTrue(page['has_more'])
        last=self.db.execute('SELECT MAX(id) FROM observations WHERE session_id=?',('s1',)).fetchone()[0]
        with self.assertRaises(ValueError):acknowledge_changes(self.db,self.a,'s1',0,last)
        acknowledge_changes(self.db,self.a,'s1',0,page['changes'][-1]['id'])
        next_page=pending_changes(self.db,self.a,'s1');self.assertEqual(len(next_page['changes']),23);self.assertFalse(next_page['has_more'])

    def test_observation_arriving_during_review_remains_unacknowledged(self):
        page=pending_changes(self.db,self.a,'s1')
        with self.db:self.db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)',('s1',99,'added','late','assistant','new-hash',99))
        acknowledge_changes(self.db,self.a,'s1',page['revision'],page['changes'][-1]['id'])
        remaining=pending_changes(self.db,self.a,'s1')['changes']
        self.assertEqual(len(remaining),1);self.assertEqual(remaining[0]['node_id'],99)

    def test_only_identical_repeated_added_nodes_are_hidden_from_review(self):
        with self.db:
            for node,transition,digest in [(20,'added','synthetic-hash'),(21,'added','changed-hash'),(20,'missing','synthetic-hash'),(20,'restored','synthetic-hash')]:
                self.db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)',('s1',node,transition,'s1m0','user',digest,20))
        page=pending_changes(self.db,self.a,'s1');shown=[(c['node_id'],c['transition']) for c in page['changes']]
        self.assertNotIn((20,'added'),shown)
        self.assertIn((21,'added'),shown);self.assertIn((20,'missing'),shown);self.assertIn((20,'restored'),shown)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM observations WHERE session_id='s1'").fetchone()[0],7)
        duplicate=self.db.execute("SELECT id FROM observations WHERE session_id='s1' AND node_id=20 AND transition='added'").fetchone()[0]
        with self.assertRaises(ValueError):acknowledge_changes(self.db,self.a,'s1',0,duplicate)

    def test_simultaneous_acknowledgments_do_not_overwrite_each_other(self):
        through=pending_changes(self.db,self.a,'s1')['changes'][0]['id']
        def acknowledge(_):
            db=open_store(self.store,self.source)
            try:
                try:return acknowledge_changes(db,self.a,'s1',0,through)
                except RevisionConflict:return 'conflict'
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as workers:result=list(workers.map(acknowledge,range(2)))
        self.assertEqual(result.count(1),1);self.assertEqual(result.count('conflict'),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM task_progress_history').fetchone()[0],1)


if __name__=='__main__':unittest.main()
