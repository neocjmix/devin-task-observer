import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
import test_observer as fixture
from observer.devin_local import read_sessions, SourceIncomplete
from observer.discovery import extract_resources,task_candidates,confirm_candidate,sync_git,git_executable,read_git
from observer.plans import task_plans
from observer.registry import create_task,link_session,add_resource,update_resource,set_link_active
from observer.store import sync


class UsefulObserverTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.CollectorTests('test_replay_same_size_edit_and_append');self.fixture.setUp()
        f=self.fixture;self.db=f.db;self.writer=f.writer;self.work=f.work;f.collect()
        self.task=create_task(self.db,'Task A');self.link=link_session(self.db,self.task,'one')
    def tearDown(self):self.fixture.tearDown()
    def append(self,node,content,parent=None,mid=None):
        content={'message_id':mid or 'm'+str(node),'role':'assistant',**content}
        self.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(node+10,'one',node,node-1 if parent is None else parent,json.dumps(content)))
        self.writer.execute('UPDATE sessions SET main_chain_id=?',(node,));self.writer.commit();self.fixture.collect()
    def plan(self,node,items,parent=None,mid=None):
        self.append(node,{'content':'','metadata':{},'tool_calls':[{'id':'call'+str(node),'name':'todo_write','arguments':{'todos':[{'content':c,'status':s} for c,s in items]}}]},parent,mid)
    def candidates(self,task=None):return task_candidates(self.db,task or self.task,[self.work])
    def plans(self):return task_plans(self.db,self.task,[self.work])[0]

    def test_mentions_are_deduplicated_evidence_scoped_and_never_confirmed(self):
        text='OBS-123 https://example.atlassian.net/browse/OBS-123 https://github.com/example/demo/pull/42 branch: feat/observer commit: abcdef1234567'
        self.append(2,{'content':text});self.append(3,{'content':text})
        candidates=self.candidates();pr=next(x for x in candidates if x['kind']=='pr')
        self.assertEqual(len(pr['evidence']),2);self.assertEqual(pr['association'],'candidate');self.assertEqual(pr['external_status'],'unknown')
        self.assertEqual(len([x for x in candidates if x['kind']=='ticket']),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM resources').fetchone()[0],0)
        other=create_task(self.db,'Task B');link_session(self.db,other,'one')
        self.assertTrue(next(x for x in self.candidates() if x['kind']=='pr')['evidence'][0]['shared_session'])
        self.assertTrue(all(x['association']=='candidate' for x in self.candidates(other)))
        unrelated=create_task(self.db,'Task C');self.assertEqual(self.candidates(unrelated),[])
        self.assertEqual(task_candidates(self.db,self.task,[self.work.parent/'unapproved']),[])

    def test_same_branch_name_in_different_workspaces_is_not_one_resource(self):
        self.append(2,{'content':'branch: main'})
        other=self.work.parent/'other-personal';other.mkdir()
        self.writer.execute('INSERT INTO sessions VALUES (?,?,?,?)',('two','Other',str(other),0))
        self.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(100,'two',0,None,fixture.message('other','branch: main','assistant')));self.writer.commit()
        sync(self.db,read_sessions(self.fixture.src,[self.work,other]),[self.work,other]);link_session(self.db,self.task,'two')
        found=task_candidates(self.db,self.task,[self.work,other]);branches=[x for x in found if x['kind']=='branch']
        self.assertEqual(len(branches),2);self.assertEqual(len({x['key'] for x in branches}),2)
        self.assertEqual({x['workspace'] for x in branches},{str(self.work),str(other)})

    def test_bare_ticket_alias_merges_only_when_url_is_unambiguous(self):
        self.append(2,{'content':'OBS-123'})
        self.append(3,{'content':'https://one.example/browse/OBS-123'})
        tickets=[x for x in self.candidates() if x['kind']=='ticket'];self.assertEqual(len(tickets),1);self.assertEqual(len(tickets[0]['evidence']),2)
        self.append(4,{'content':'https://two.example/browse/OBS-123'})
        self.assertEqual(len([x for x in self.candidates() if x['kind']=='ticket']),3)

    def test_changed_plan_payload_at_same_node_keeps_versions_and_marks_order_uncertain(self):
        self.plan(2,[('first','pending')])
        raw=json.loads(self.writer.execute('SELECT chat_message FROM message_nodes WHERE node_id=2').fetchone()[0])
        raw['tool_calls'][0]['arguments']['todos'][0]['status']='completed'
        self.writer.execute('UPDATE message_nodes SET chat_message=? WHERE node_id=2',(json.dumps(raw),));self.writer.commit();self.fixture.collect()
        plans=self.plans();self.assertEqual(plans['ordering'],'uncertain');self.assertEqual(len(plans['versions']),2)

    def test_confirmation_reuses_manual_resource_without_overwriting_it(self):
        url='https://github.com/example/demo/pull/42';rid=add_resource(self.db,self.task,'pr',url)
        self.append(2,{'content':url});key=next(x['key'] for x in self.candidates() if x['kind']=='pr')
        self.assertEqual(confirm_candidate(self.db,self.task,key,[self.work]),rid)
        self.assertEqual(confirm_candidate(self.db,self.task,key,[self.work]),rid)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM resources').fetchone()[0],1)
        update_resource(self.db,self.task,rid,1,uri='https://github.com/example/demo/pull/99')
        self.fixture.collect();self.assertEqual(self.db.execute('SELECT uri FROM resources WHERE id=?',(rid,)).fetchone()[0],'https://github.com/example/demo/pull/99')
        self.assertEqual(next(x for x in self.candidates() if x['key']==key)['association'],'confirmed_resource_edited')
        update_resource(self.db,self.task,rid,2,active=False)
        self.fixture.collect();self.assertEqual(self.db.execute('SELECT active FROM resource_state WHERE resource_id=?',(rid,)).fetchone()[0],0)
        self.assertEqual(next(x for x in self.candidates() if x['key']==key)['association'],'confirmed_archived')
        other=create_task(self.db,'Other')
        with self.assertRaises(ValueError):confirm_candidate(self.db,other,key,[self.work])
        set_link_active(self.db,self.task,self.link,1,False)
        self.assertEqual(self.candidates(),[])

    def test_old_source_evidence_is_retained_and_cannot_be_confirmed(self):
        self.append(2,{'content':'https://github.com/example/demo/pull/42'})
        key=next(x['key'] for x in self.candidates() if x['kind']=='pr')
        self.writer.execute('UPDATE message_nodes SET chat_message=? WHERE node_id=2',(fixture.message('m2','no resources'),));self.writer.commit();self.fixture.collect()
        self.assertFalse(next(x for x in self.candidates() if x['key']==key)['current'])
        with self.assertRaises(ValueError):confirm_candidate(self.db,self.task,key,[self.work])

    def test_resource_url_credentials_and_unsupported_forms_are_not_saved(self):
        found=extract_resources('https://user:secret@github.com/example/demo/pull/1 https://github.com/example/demo/pull/2?token=secret#secret')
        self.assertTrue(any(x['value'].endswith('/pull/2') for x in found));self.assertNotIn('secret',json.dumps(found));self.assertFalse(any(x['value'].endswith('/pull/1') for x in found))
        self.assertEqual(extract_resources('unknown prose with no explicit branch or commit'),[])
        self.assertEqual(extract_resources('SWE-1.6 Slow'),[])
        self.assertEqual(extract_resources('https://github.com:bad/example/demo'),[])
        self.assertIn('https://linear.app/team/issue/OBS-123',[x['value'] for x in extract_resources('https://linear.app/team/issue/OBS-123')])
        self.assertFalse(any(x['kind']=='pr' for x in extract_resources('https://github.com/example/demo/pull/'+('9'*5000))))
        self.assertEqual(extract_resources('branch: '+('a'*300)),[])
        self.assertEqual(extract_resources('worktree: /'+('a'*1200)),[])

    def test_plan_lineage_tracks_reported_complete_reopen_add_and_remove(self):
        self.plan(2,[('inspect','pending'),('report','pending')])
        self.plan(3,[('inspect','completed'),('report','pending')])
        self.plan(4,[('inspect','in_progress'),('test','pending')])
        plans=self.plans();self.assertEqual(plans['ordering'],'source_lineage');self.assertEqual(len(plans['heads']),1)
        head=next(v for v in plans['versions'] if v['id']==plans['heads'][0])
        self.assertEqual({x['type'] for x in head['changes']},{'agent_reported_reopened','added','removed_or_replaced'})
        self.assertFalse(head['completion_verified'])
        self.assertIn('agent_reported_completed',{c['type'] for v in plans['versions'] for c in v['changes']})

    def test_recognized_plan_item_mentions_are_candidates_with_the_plan_source(self):
        self.plan(2,[('Investigate OBS-321 branch: feat/m1','pending')])
        found=self.candidates();self.assertEqual({x['kind'] for x in found},{'ticket','branch'})
        self.assertTrue(all(x['association']=='candidate' and x['evidence'][0]['node_id']==2 for x in found))

    def test_git_checkout_change_during_read_is_not_published_as_consistent_facts(self):
        from types import SimpleNamespace
        head_reads=[]
        def run(argv,**kwargs):
            if argv[-1]=='--show-toplevel':out=str(self.work)
            elif argv[-2:]==['--verify','HEAD']:
                head_reads.append(1);out=('a' if len(head_reads)==1 else 'b')*40
            elif argv[-1]=='remote.origin.url':out='https://github.com/example/demo.git'
            else:out='main'
            return SimpleNamespace(returncode=0,stdout=out+'\n')
        with patch('observer.discovery.subprocess.run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'changed while reading'):read_git(self.work)

    def test_duplicate_plan_nodes_preserve_evidence_and_do_not_duplicate_projection(self):
        self.plan(2,[('inspect','pending')])
        raw=self.writer.execute('SELECT chat_message FROM message_nodes WHERE node_id=2').fetchone()[0]
        self.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(99,'one',3,2,raw));self.writer.execute('UPDATE sessions SET main_chain_id=3');self.writer.commit();self.fixture.collect()
        plans=self.plans();self.assertEqual(len(plans['versions']),1);self.assertEqual(len(plans['versions'][0]['evidence']),2)

    def test_disconnected_plans_never_choose_latest_by_timestamp_or_node_number(self):
        self.plan(2,[('first','pending')]);self.plan(3,[('other','completed')],parent=0)
        plans=self.plans();self.assertEqual(plans['ordering'],'uncertain');self.assertEqual(len(plans['heads']),2)
        self.assertTrue(all(v['changes']==[] for v in plans['versions']))

    def test_changed_and_unsupported_plan_retains_prior_version_without_silent_fallback(self):
        self.plan(2,[('first','pending')]);self.append(3,{'content':'','tool_calls':[{'id':'unknown','name':'todo_write','arguments':{'not_todos':[]}}]})
        plans=self.plans();self.assertEqual(plans['ordering'],'uncertain');head=next(v for v in plans['versions'] if v['id'] in plans['heads']);self.assertIsNone(head['items'])
        self.assertEqual(len(plans['versions']),2)

    def test_session_plans_are_never_merged(self):
        self.plan(2,[('inspect','pending')]);self.writer.execute('INSERT INTO sessions VALUES (?,?,?,?)',('two','Second',str(self.work),0))
        data={'message_id':'m0','role':'assistant','content':'','tool_calls':[{'id':'c0','name':'todo_write','arguments':{'todos':[{'content':'inspect','status':'completed'}]}}]}
        self.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(100,'two',0,None,json.dumps(data)));self.writer.commit();self.fixture.collect();link_session(self.db,self.task,'two')
        plans=task_plans(self.db,self.task,[self.work]);self.assertEqual(len(plans),2)
        self.assertEqual({x['versions'][0]['items'][0]['source_status'] for x in plans},{'pending','completed'})

    def test_derived_state_rolls_back_with_checkpoint(self):
        self.db.execute("CREATE TRIGGER fail_plan BEFORE INSERT ON plan_records BEGIN SELECT RAISE(ABORT, 'injected'); END");self.db.commit()
        before=self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0]
        with self.assertRaises(Exception):self.plan(2,[('inspect','pending')])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plan_records').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0],before)

    def test_local_git_is_read_only_workspace_scoped_and_failure_preserves_prior_facts(self):
        git=git_executable();subprocess.run([git,'init','-q',str(self.work)],check=True)
        env={**os.environ,'GIT_AUTHOR_NAME':'Synthetic','GIT_AUTHOR_EMAIL':'synthetic@example.invalid','GIT_COMMITTER_NAME':'Synthetic','GIT_COMMITTER_EMAIL':'synthetic@example.invalid'}
        subprocess.run([git,'-C',str(self.work),'commit','--allow-empty','-qm','Synthetic initial'],env=env,check=True)
        subprocess.run([git,'-C',str(self.work),'config','remote.origin.url','https://github.com/example/demo.git'],check=True)
        head=(self.work/'.git/HEAD').read_bytes();sync_git(self.db,[self.work]);sync_git(self.db,[self.work])
        found=self.candidates();self.assertEqual({x['kind'] for x in found},{'repo','branch','commit','worktree'})
        self.assertTrue(all(e['verification']=='local_git' and e['scope']=='workspace_only' for x in found for e in x['evidence']))
        self.assertEqual((self.work/'.git/HEAD').read_bytes(),head)
        with patch('observer.discovery.read_git',side_effect=ValueError('unavailable')):sync_git(self.db,[self.work])
        self.assertTrue(all(not x['current'] for x in self.candidates()))
