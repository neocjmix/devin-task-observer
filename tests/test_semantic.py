import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from observer.devin_local import read_sessions
from observer.store import open_store, sync
from observer.registry import create_task, link_session, set_link_active, RevisionConflict
from observer.semantic import create_proposal, apply_proposal, reject_proposal, list_proposals
from observer.views import ensure_view, get_view, add_item


def fake(content):
    return {'done':True,'done_reason':'stop','prompt_eval_count':110,'eval_count':20,'response':json.dumps({'suggestion':'Tentative synthetic observation'})}


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.workspace=root/'personal';self.workspace.mkdir();(root/'source').mkdir();self.source=root/'source'/'source.db';self.store=root/'observer.db'
        source=sqlite3.connect(self.source);source.executescript('CREATE TABLE refinery_schema_history(version INTEGER); INSERT INTO refinery_schema_history VALUES(17); CREATE TABLE sessions(id TEXT, title TEXT, working_directory TEXT, main_chain_id INTEGER); CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, parent_node_id INTEGER, chat_message TEXT);')
        source.execute('INSERT INTO sessions VALUES (?,?,?,?)',('s1','Synthetic',str(self.workspace),2))
        for i in range(3):
            raw=json.dumps({'message_id':'m'+str(i),'role':'user','content':'Synthetic note '+str(i),'metadata':{'is_user_input':True}})
            source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',i,None if i==0 else i-1,raw))
        source.commit();source.close()
        self.db=open_store(self.store,self.source);sync(self.db,read_sessions(self.source,[self.workspace]),[self.workspace])
        self.task=create_task(self.db,'Synthetic task');self.link=link_session(self.db,self.task,'s1');self.view=ensure_view(self.db,self.task);self.section=get_view(self.db,self.task)['sections'][0]['id']

    def tearDown(self):self.db.close();self.tmp.cleanup()
    def digest(self,node):return self.db.execute('SELECT content_hash FROM source_nodes WHERE session_id=? AND node_id=?',('s1',node)).fetchone()[0]
    def propose(self,node=0,generate=fake):return create_proposal(self.db,self.source,self.task,'s1',node,self.digest(node),generate)

    def test_cache_and_human_acceptance_preserve_existing_notes(self):
        item,revision=add_item(self.db,self.view,self.section,1,'Human pinned',True)
        proposal=self.propose();self.assertEqual(proposal['status'],'proposed')
        def fail(_):raise AssertionError('unchanged input was analyzed again')
        self.assertTrue(self.propose(generate=fail)['cached'])
        with self.assertRaises(RevisionConflict):apply_proposal(self.db,self.task,proposal['proposal_id'],self.view,self.section,1)
        self.assertEqual(apply_proposal(self.db,self.task,proposal['proposal_id'],self.view,self.section,revision),revision+1)
        items=get_view(self.db,self.task)['sections'][0]['items']
        self.assertEqual((items[0]['id'],items[0]['content'],items[0]['pinned']),(item,'Human pinned',True))
        self.assertEqual((items[1]['origin'],items[1]['evidence'][0]['node_id']),('local_model',0))
        with self.assertRaises(ValueError):apply_proposal(self.db,self.task,proposal['proposal_id'],self.view,self.section,revision+1)

    def test_daily_reservation_survives_failures_and_caps_calls(self):
        def broken(_):raise OSError('unavailable')
        with self.assertRaises(ValueError):self.propose(0,broken)
        self.propose(1)
        with self.assertRaisesRegex(ValueError,'budget exhausted'):self.propose(2)
        self.assertEqual(self.db.execute('SELECT status FROM semantic_runs ORDER BY created_at').fetchall(),[('failed',),('proposed',)])
        self.assertEqual(self.db.execute('SELECT SUM(reserved_tokens) FROM semantic_runs').fetchone()[0],4608)

    def test_validation_never_applies_or_stores_bad_output(self):
        def invalid(_):return {'done':True,'done_reason':'stop','prompt_eval_count':20,'eval_count':1,'response':'{"suggestion":"x","delete":"all"}'}
        with self.assertRaises(ValueError):self.propose(generate=invalid)
        self.assertEqual(list_proposals(self.db,self.task),[])
        self.assertEqual(get_view(self.db,self.task)['sections'][0]['items'],[])

    def test_unlinked_and_changed_source_are_rejected(self):
        digest=self.digest(0);set_link_active(self.db,self.task,self.link,1,False)
        with self.assertRaises(ValueError):self.propose()
        set_link_active(self.db,self.task,self.link,2,True)
        source=sqlite3.connect(self.source);source.execute("UPDATE message_nodes SET chat_message='{}' WHERE node_id=0");source.commit();source.close()
        with self.assertRaises(Exception):create_proposal(self.db,self.source,self.task,'s1',0,digest,fake)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM semantic_runs').fetchone()[0],0)

    def test_reject_and_unlink_prevent_apply(self):
        proposal=self.propose();set_link_active(self.db,self.task,self.link,1,False)
        with self.assertRaises(ValueError):apply_proposal(self.db,self.task,proposal['proposal_id'],self.view,self.section,1)
        reject_proposal(self.db,self.task,proposal['proposal_id'])
        self.assertEqual(list_proposals(self.db,self.task)[0]['status'],'rejected')
        with self.assertRaises(ValueError):reject_proposal(self.db,self.task,proposal['proposal_id'])

    def test_concurrent_requests_cannot_overreserve_daily_budget(self):
        hashes=[self.digest(i) for i in range(3)]
        def request(node):
            db=open_store(self.store,self.source)
            try:
                try:return create_proposal(db,self.source,self.task,'s1',node,hashes[node],fake)['status']
                except ValueError as error:return str(error)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=3) as workers:
            results=list(workers.map(request,range(3)))
        self.assertEqual(results.count('proposed'),2)
        self.assertEqual(results.count('daily local analysis budget exhausted'),1)
        self.assertEqual(self.db.execute('SELECT SUM(reserved_tokens) FROM semantic_runs').fetchone()[0],4608)


if __name__=='__main__':unittest.main()
