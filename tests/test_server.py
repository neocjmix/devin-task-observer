import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from observer.server import LocalServer
from observer.store import open_store
from observer.registry import link_session


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.workspace=root/'personal';self.workspace.mkdir();(root/'source').mkdir();self.source=root/'source'/'source.db';self.store=root/'observer.db'
        self.raw=json.dumps({'message_id':'m1','role':'user','content':'synthetic evidence','metadata':{'is_user_input':True}})
        db=sqlite3.connect(self.source);db.executescript('CREATE TABLE refinery_schema_history(version INTEGER); INSERT INTO refinery_schema_history VALUES(17); CREATE TABLE sessions(id TEXT, title TEXT, working_directory TEXT, main_chain_id INTEGER); CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, parent_node_id INTEGER, chat_message TEXT);')
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)',('s1','Synthetic',str(self.workspace),0));db.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',0,None,self.raw));db.commit();db.close()
        db=open_store(self.store,self.source);db.close()
        self.server=LocalServer(('127.0.0.1',0),self.source,self.store,[self.workspace]);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.assertTrue(self.server.refresh()['ok'])

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,method,path,data=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        opts={'X-Observer-Token':self.server.token}
        if data is not None:
            opts.update({'Origin':self.server.origin,'Content-Type':'application/json'});data=json.dumps(data)
        if headers:opts.update(headers)
        connection.request(method,path,body=data,headers=opts);resp=connection.getresponse();status=resp.status;raw=resp.read();connection.close()
        return status,json.loads(raw) if raw else None

    def test_host_origin_and_token_protection(self):
        status,_=self.request('GET','/api/state',headers={'X-Observer-Token':'wrong'});self.assertEqual(status,403)
        status,_=self.request('GET','/api/state',headers={'Host':'evil.example'});self.assertEqual(status,403)
        status,_=self.request('POST','/api/task/create',{'title':'x'},headers={'Origin':'https://evil.example'});self.assertEqual(status,403)
        status,_=self.request('POST','/api/task/create',{'title':'x'},headers={'Content-Type':'text/plain'});self.assertEqual(status,403)

    def test_restart_bootstrap_requires_custom_same_origin_request(self):
        self.assertEqual(self.request('GET','/api/bootstrap')[0],403)
        self.assertEqual(self.request('GET','/api/bootstrap',headers={'X-Observer-Bootstrap':'1','Origin':'https://evil.example'})[0],403)
        status,result=self.request('GET','/api/bootstrap',headers={'X-Observer-Bootstrap':'1','X-Observer-Token':'old-process-token'})
        self.assertEqual(status,200);self.assertEqual(result['token'],self.server.token)

    def test_sessions_outside_workspace_allowlist_are_not_exposed_or_linked(self):
        db=open_store(self.store,self.source)
        with db:db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',('s2','outside','/unapproved',0,'current',1))
        db.close()
        _,state=self.request('GET','/api/state')
        self.assertEqual([s['id'] for s in state['sessions']],['s1'])
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'})
        status,_=self.request('POST','/api/link',{'task_id':created['id'],'session_id':'s2'})
        self.assertEqual(status,400)
        status,_=self.request('GET','/api/session?id=s2')
        self.assertEqual(status,404)
        db=open_store(self.store,self.source);link_session(db,created['id'],'s2')
        with db:db.execute("INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES ('s2',1,'added','m','user','h',1)")
        db.close()
        _,state=self.request('GET','/api/state')
        self.assertEqual(state['tasks'][0]['attention'],{'pending_changes':0,'stale_sessions':0,'linked_sessions':0})

    def test_session_event_pagination_is_explicit_and_complete(self):
        db=open_store(self.store,self.source)
        with db:
            for i in range(60):
                mid='extra'+str(i)
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',('s1',mid,'unknown','',i+100))
                db.execute('INSERT INTO event_evidence VALUES (?,?,?,?,?,?)',('s1',mid,'unknown','',i+1,'hash'+str(i)))
        db.close()
        status,first=self.request('GET','/api/session?id=s1');self.assertEqual(status,200)
        self.assertEqual(len(first['events']),50);self.assertEqual(first['next_offset'],50)
        status,second=self.request('GET','/api/session?id=s1&offset=50');self.assertEqual(status,200)
        self.assertEqual(len(second['events']),11);self.assertIsNone(second['next_offset'])

    def test_m1_projection_is_automatic_scoped_and_uses_no_model(self):
        source=sqlite3.connect(self.source)
        raw=json.dumps({'message_id':'m-plan','role':'assistant','content':'OBS-123 https://github.com/example/demo/pull/42 branch: feat/m1',
            'tool_calls':[{'id':'todo','name':'todo_write','arguments':{'todos':[{'content':'inspect example','status':'pending'}]}}]})
        source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',1,0,raw));source.execute('UPDATE sessions SET main_chain_id=1');source.commit();source.close()
        self.assertTrue(self.server.refresh()['ok'])
        _,task=self.request('POST','/api/task/create',{'title':'M1 synthetic'})
        self.request('POST','/api/link',{'task_id':task['id'],'session_id':'s1'})
        with patch('observer.semantic.local_generate',side_effect=AssertionError('no model calls')):
            status,projection=self.request('GET','/api/task-observer?task='+task['id'])
        self.assertEqual(status,200);pr=next(x for x in projection['resources'] if x['kind']=='pr')
        self.assertEqual(pr['association'],'candidate');self.assertEqual(pr['external_status'],'unknown');self.assertEqual(pr['number'],42)
        self.assertEqual(projection['plans'][0]['versions'][0]['items'][0]['content'],'inspect example')
        self.assertEqual(projection['health'][0]['state'],'readable')
        status,resource=self.request('POST','/api/resource/confirm',{'task_id':task['id'],'key':pr['key']});self.assertEqual(status,200)
        _,projection=self.request('GET','/api/task-observer?task='+task['id']);self.assertEqual(next(x for x in projection['resources'] if x['kind']=='pr')['association'],'confirmed')
        _,other=self.request('POST','/api/task/create',{'title':'Other task'})
        self.assertEqual(self.request('POST','/api/resource/confirm',{'task_id':other['id'],'key':pr['key']})[0],400)
        _,state=self.request('GET','/api/state');self.assertIsNotNone(state['collector']['last_success'])

    def test_live_script_is_served_with_local_script_policy(self):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request('GET','/live.js');r=c.getresponse()
        self.assertEqual(r.status,200);self.assertIn("script-src 'self'",r.getheader('Content-Security-Policy'))
        self.assertIn(b'async function refreshOpen',r.read());c.close()

    def test_m1_health_distinguishes_readable_ambiguity_from_failed_source(self):
        _,task=self.request('POST','/api/task/create',{'title':'Health synthetic'})
        self.request('POST','/api/link',{'task_id':task['id'],'session_id':'s1'})
        source=sqlite3.connect(self.source)
        source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',1,0,json.dumps({'message_id':'m1','role':'user','content':'conflicting synthetic'})))
        source.execute('UPDATE sessions SET main_chain_id=1');source.commit()
        self.assertTrue(self.server.refresh()['ok'])
        _,projection=self.request('GET','/api/task-observer?task='+task['id']);self.assertEqual(projection['health'][0]['state'],'readable_ambiguous')
        db=open_store(self.store,self.source);fingerprint=db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0];db.close()
        _,before=self.request('GET','/api/state')
        source.execute("UPDATE message_nodes SET chat_message='broken' WHERE node_id=1");source.commit()
        self.assertFalse(self.server.refresh()['ok'])
        _,projection=self.request('GET','/api/task-observer?task='+task['id']);self.assertEqual(projection['health'][0]['state'],'unavailable')
        _,after=self.request('GET','/api/state');self.assertEqual(after['collector']['last_success'],before['collector']['last_success'])
        db=open_store(self.store,self.source);self.assertEqual(db.execute('SELECT source_fingerprint FROM checkpoints').fetchone()[0],fingerprint);db.close()
        source.execute('DELETE FROM message_nodes WHERE node_id=1');source.execute('UPDATE sessions SET main_chain_id=0');source.commit();source.close()
        self.assertTrue(self.server.refresh()['ok'])
        _,projection=self.request('GET','/api/task-observer?task='+task['id']);self.assertEqual(projection['health'][0]['state'],'readable_changed')

    def test_session_metadata_and_history_are_workspace_scoped(self):
        source=sqlite3.connect(self.source)
        source.execute('ALTER TABLE sessions ADD COLUMN model TEXT')
        source.execute('ALTER TABLE sessions ADD COLUMN backend_type TEXT')
        source.execute('ALTER TABLE sessions ADD COLUMN last_activity_at INTEGER')
        source.execute("UPDATE sessions SET model='synthetic-model',backend_type='synthetic-backend',last_activity_at=1234")
        source.commit();source.close();self.assertTrue(self.server.refresh()['ok'])
        _,state=self.request('GET','/api/state');self.assertEqual(state['sessions'][0]['model'],'synthetic-model');self.assertEqual(state['sessions'][0]['source_activity_raw'],1234)
        status,meta=self.request('GET','/api/session-metadata?id=s1');self.assertEqual(status,200)
        self.assertEqual(meta['history'][0]['backend'],'synthetic-backend');self.assertIsNone(meta['next_offset'])
        self.assertEqual(self.request('GET','/api/session-metadata?id=outside')[0],404)

    def test_same_snapshot_events_use_observation_order_not_identifier_order(self):
        db=open_store(self.store,self.source)
        with db:
            for mid,node in [('z-first',100),('a-second',101)]:
                digest='hash-'+mid
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',('s1',mid,'unknown','',1))
                db.execute('INSERT INTO event_evidence VALUES (?,?,?,?,?,?)',('s1',mid,'unknown','',node,digest))
                db.execute('INSERT INTO observations(session_id,node_id,transition,message_id,role,content_hash,observed_at) VALUES (?,?,?,?,?,?,?)',('s1',node,'added',mid,'unknown',digest,1))
        db.close()
        status,result=self.request('GET','/api/session?id=s1');self.assertEqual(status,200)
        self.assertEqual([e['node_id'] for e in result['events'][:2]],[100,101])

    def test_tool_call_arguments_are_read_on_demand_without_body_storage(self):
        source=sqlite3.connect(self.source)
        raw=json.dumps({'message_id':'tool-message','role':'assistant','content':'','tool_calls':[{'id':'call1','name':'read','arguments':'synthetic-only'}], 'metadata':{'extensions':{'chisel/tool_call_content':{'call1':{'title':'synthetic metadata','status':'pending'}}}}})
        source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',1,0,raw))
        source.execute('UPDATE sessions SET main_chain_id=1');source.commit();source.close();self.assertTrue(self.server.refresh()['ok'])
        digest=hashlib.sha256(raw.encode()).hexdigest()
        status,result=self.request('GET',f'/api/session-evidence?session=s1&node=1&hash={digest}')
        self.assertEqual(status,200);self.assertEqual(result['tool_calls'][0]['arguments'],'synthetic-only')
        self.assertEqual(result['tool_call_metadata']['call1']['title'],'synthetic metadata')
        db=open_store(self.store,self.source)
        self.assertNotIn('synthetic-only',str(db.execute('SELECT * FROM source_nodes').fetchall()))
        db.close()

    def test_plan_preview_is_evidence_linked_and_preserves_source_status(self):
        source=sqlite3.connect(self.source)
        raw=json.dumps({'message_id':'plan-message','role':'assistant','content':'','tool_calls':[{'id':'call-plan','name':'todo_write','arguments':{'todos':[{'content':'synthetic step','status':'pending'}]}}]})
        source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',1,0,raw))
        source.execute('UPDATE sessions SET main_chain_id=1');source.commit();source.close();self.assertTrue(self.server.refresh()['ok'])
        digest=hashlib.sha256(raw.encode()).hexdigest()
        status,result=self.request('GET',f'/api/session-evidence?session=s1&node=1&hash={digest}')
        self.assertEqual(status,200);self.assertEqual(result['plan_preview']['plans'][0]['items'],[{'content':'synthetic step','source_status':'pending'}])
        self.assertEqual(self.request('GET','/api/session-evidence?session=s1&node=1&hash=wrong')[0],404)

    def test_active_chain_uses_parent_order_and_excludes_branches(self):
        db=open_store(self.store,self.source)
        with db:
            db.execute('UPDATE sessions SET head=2 WHERE id=?',('s1',))
            db.execute('INSERT INTO source_nodes VALUES (?,?,?,?,?,?,?)',('s1',2,0,'m2','assistant','hash2',1))
            db.execute('INSERT INTO source_nodes VALUES (?,?,?,?,?,?,?)',('s1',1,0,'branch','assistant','branchhash',1))
            db.execute('INSERT INTO node_kinds VALUES (?,?,?,?)',('s1',2,'hash2','["assistant"]'))
            db.execute('INSERT INTO node_kinds VALUES (?,?,?,?)',('s1',1,'branchhash','["assistant"]'))
        db.close()
        status,chain=self.request('GET','/api/session-chain?id=s1')
        self.assertEqual(status,200);self.assertEqual([n['node_id'] for n in chain['nodes']],[0,2]);self.assertEqual(chain['excluded_nodes'],1)
        db=open_store(self.store,self.source)
        with db:db.execute('UPDATE source_nodes SET parent_id=2 WHERE session_id=? AND node_id=0',('s1',))
        db.close()
        status,_=self.request('GET','/api/session-chain?id=s1');self.assertEqual(status,400)

    def test_active_chain_pagination(self):
        db=open_store(self.store,self.source)
        with db:
            for i in range(1,61):
                db.execute('INSERT INTO source_nodes VALUES (?,?,?,?,?,?,?)',('s1',i,i-1,'m'+str(i),'assistant','h'+str(i),1))
            db.execute('UPDATE sessions SET head=60 WHERE id=?',('s1',))
        db.close()
        status,first=self.request('GET','/api/session-chain?id=s1');self.assertEqual(status,200)
        self.assertEqual([n['node_id'] for n in first['nodes']],list(range(50)));self.assertEqual(first['next_offset'],50)
        _,last=self.request('GET','/api/session-chain?id=s1&offset=50');self.assertEqual([n['node_id'] for n in last['nodes']],list(range(50,61)));self.assertIsNone(last['next_offset'])

    def test_archive_restore_link_and_resource_with_conflicts(self):
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'});task=created['id']
        _,link=self.request('POST','/api/link',{'task_id':task,'session_id':'s1'})
        status,resource=self.request('POST','/api/resource/add',{'task_id':task,'kind':'pr','uri':'https://example.com/old'});self.assertEqual(status,200)
        rid=resource['id']
        status,_=self.request('POST','/api/resource/edit',{'task_id':task,'resource_id':rid,'revision':1,'uri':'https://example.com/new'});self.assertEqual(status,200)
        status,_=self.request('POST','/api/resource/edit',{'task_id':task,'resource_id':rid,'revision':1,'active':False});self.assertEqual(status,409)
        status,_=self.request('POST','/api/link/state',{'task_id':task,'link_id':link['id'],'revision':1,'active':False});self.assertEqual(status,200)
        _,detail=self.request('GET','/api/task?id='+task);self.assertEqual(detail['sessions'][0]['active'],0);self.assertEqual(detail['resources'][0]['uri'],'https://example.com/new')
        _,state=self.request('GET','/api/state');self.assertEqual(state['sessions'][0]['task_count'],0)
        status,_=self.request('GET','/api/evidence-list?task='+task+'&session=s1');self.assertEqual(status,404)
        status,_=self.request('POST','/api/link/state',{'task_id':task,'link_id':link['id'],'revision':2,'active':True});self.assertEqual(status,200)
        status,_=self.request('POST','/api/resource/edit',{'task_id':task,'resource_id':rid,'revision':2,'active':False});self.assertEqual(status,200)
        status,_=self.request('POST','/api/resource/edit',{'task_id':task,'resource_id':rid,'revision':3,'active':True});self.assertEqual(status,200)

    def test_local_proposals_are_disabled_by_default_and_require_review(self):
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'});task=created['id']
        self.request('POST','/api/link',{'task_id':task,'session_id':'s1'})
        digest=hashlib.sha256(self.raw.encode()).hexdigest()
        request={'task_id':task,'session_id':'s1','node_id':0,'content_hash':digest}
        status,_=self.request('POST','/api/proposal/create',request);self.assertEqual(status,400)
        db=open_store(self.store,self.source);self.assertEqual(db.execute('SELECT COUNT(*) FROM semantic_runs').fetchone()[0],0);db.close()
        self.server.enable_local_analysis=True
        with patch('observer.semantic.local_generate',return_value={'done':True,'done_reason':'stop','prompt_eval_count':100,'eval_count':20,'response':'{"suggestion":"Synthetic candidate"}'}) as model:
            status,proposal=self.request('POST','/api/proposal/create',request);self.assertEqual(status,200)
            status,cached=self.request('POST','/api/proposal/create',request);self.assertTrue(cached['cached']);self.assertEqual(model.call_count,1)
        _,detail=self.request('GET','/api/task?id='+task);view=detail['view'];section=view['sections'][0]['id']
        self.assertEqual(view['sections'][0]['items'],[])
        status,_=self.request('POST','/api/proposal/apply',{'task_id':task,'proposal_id':proposal['proposal_id'],'view_id':view['id'],'section_id':section,'revision':view['revision']});self.assertEqual(status,200)
        _,detail=self.request('GET','/api/task?id='+task);self.assertEqual(detail['view']['sections'][0]['items'][0]['origin'],'local_model');self.assertEqual(detail['proposals'][0]['status'],'accepted')

    def test_changed_source_cannot_be_used_to_accept_an_old_proposal(self):
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'});task=created['id']
        self.request('POST','/api/link',{'task_id':task,'session_id':'s1'});self.server.enable_local_analysis=True
        digest=hashlib.sha256(self.raw.encode()).hexdigest()
        with patch('observer.semantic.local_generate',return_value={'done':True,'done_reason':'stop','prompt_eval_count':100,'eval_count':20,'response':'{"suggestion":"Synthetic candidate"}'}):
            _,proposal=self.request('POST','/api/proposal/create',{'task_id':task,'session_id':'s1','node_id':0,'content_hash':digest})
        _,detail=self.request('GET','/api/task?id='+task);view=detail['view']
        source=sqlite3.connect(self.source);source.execute("UPDATE message_nodes SET chat_message='{}' WHERE node_id=0");source.commit();source.close()
        status,_=self.request('POST','/api/proposal/apply',{'task_id':task,'proposal_id':proposal['proposal_id'],'view_id':view['id'],'section_id':view['sections'][0]['id'],'revision':view['revision']})
        self.assertEqual(status,400)
        _,detail=self.request('GET','/api/task?id='+task);self.assertEqual(detail['view']['sections'][0]['items'],[]);self.assertEqual(detail['proposals'][0]['status'],'pending')

    def test_failed_local_analysis_can_be_explicitly_retried_with_budget(self):
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'});task=created['id']
        self.request('POST','/api/link',{'task_id':task,'session_id':'s1'});self.server.enable_local_analysis=True
        request={'task_id':task,'session_id':'s1','node_id':0,'content_hash':hashlib.sha256(self.raw.encode()).hexdigest()}
        response={'done':True,'done_reason':'stop','prompt_eval_count':100,'eval_count':20,'response':'{"suggestion":"Synthetic candidate"}'}
        with patch('observer.semantic.local_generate',side_effect=[OSError('fixture failure'),response]) as model:
            status,_=self.request('POST','/api/proposal/create',request);self.assertEqual(status,400)
            _,state=self.request('GET','/api/state');self.assertEqual(state['analysis_budget']['reserved'],2304)
            request['retry_failed']=True
            status,result=self.request('POST','/api/proposal/create',request);self.assertEqual(status,200);self.assertEqual(result['status'],'proposed')
            _,state=self.request('GET','/api/state');self.assertEqual(state['analysis_budget']['reserved'],4608)
            self.assertEqual(model.call_count,2)

    def test_recovery_requires_fresh_snapshot_and_retains_history(self):
        source=sqlite3.connect(self.source)
        source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',1,0,json.dumps({'message_id':'m2','role':'assistant','content':'synthetic reply'})))
        source.execute('UPDATE sessions SET main_chain_id=1');source.commit();self.server.refresh()
        source.execute('DELETE FROM message_nodes WHERE node_id=1');source.execute('UPDATE sessions SET main_chain_id=0');source.commit();self.server.refresh()
        _,recovery=self.request('GET','/api/session-recovery?id=s1');self.assertTrue(recovery['eligible']);self.assertEqual(recovery['missing_nodes'],1)
        old=recovery['fingerprint']
        source.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('s1',2,0,json.dumps({'message_id':'m3','role':'assistant','content':'new synthetic reply'})))
        source.execute('UPDATE sessions SET main_chain_id=2');source.commit()
        status,_=self.request('POST','/api/session/acknowledge',{'session_id':'s1','fingerprint':old});self.assertEqual(status,400)
        _,recovery=self.request('GET','/api/session-recovery?id=s1');self.assertTrue(recovery['eligible'])
        status,_=self.request('POST','/api/session/acknowledge',{'session_id':'s1','fingerprint':recovery['fingerprint']});self.assertEqual(status,200)
        self.server.refresh();_,state=self.request('GET','/api/state');self.assertEqual(state['sessions'][0]['status'],'current')
        db=open_store(self.store,self.source);self.assertEqual(db.execute('SELECT present FROM source_nodes WHERE node_id=1').fetchone()[0],0);self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],4);db.close();source.close()

    def test_move_item_over_http_preserves_content_and_pin(self):
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'});task=created['id']
        _,detail=self.request('GET','/api/task?id='+task);view=detail['view'];source=view['sections'][0]['id']
        _,section=self.request('POST','/api/section/add',{'view_id':view['id'],'revision':view['revision'],'title':'Notes'})
        _,item=self.request('POST','/api/item/add',{'view_id':view['id'],'section_id':source,'revision':section['revision'],'content':'human note','pinned':True})
        body={'view_id':view['id'],'item_id':item['id'],'section_id':section['id'],'position':0,'revision':item['revision']}
        status,_=self.request('POST','/api/item/move',body);self.assertEqual(status,200)
        status,_=self.request('POST','/api/item/move',body);self.assertEqual(status,409)
        _,detail=self.request('GET','/api/task?id='+task);saved=detail['view']['sections'][1]['items'][0]
        self.assertEqual((saved['id'],saved['content'],saved['pinned']),(item['id'],'human note',True))

    def test_contribution_metadata_is_scoped_and_revisioned(self):
        _,created=self.request('POST','/api/task/create',{'title':'synthetic task'});task=created['id']
        _,link=self.request('POST','/api/link',{'task_id':task,'session_id':'s1'})
        body={'task_id':task,'link_id':link['id'],'revision':1,'role':'investigation','scope':'synthetic only','confidence':.7}
        status,_=self.request('POST','/api/link/edit',body);self.assertEqual(status,200)
        status,_=self.request('POST','/api/link/edit',body);self.assertEqual(status,409)
        _,detail=self.request('GET','/api/task?id='+task)
        self.assertEqual((detail['sessions'][0]['role'],detail['sessions'][0]['scope'],detail['sessions'][0]['confidence']),('investigation','synthetic only',.7))
        body['task_id']='other';body['revision']=2
        status,_=self.request('POST','/api/link/edit',body);self.assertEqual(status,400)

    def test_task_observation_review_is_independent_over_http(self):
        _,a=self.request('POST','/api/task/create',{'title':'Task A'})
        _,b=self.request('POST','/api/task/create',{'title':'Task B'})
        self.request('POST','/api/link',{'task_id':a['id'],'session_id':'s1'})
        self.request('POST','/api/link',{'task_id':b['id'],'session_id':'s1'})
        status,changes=self.request('GET','/api/task/changes?task='+a['id']+'&session=s1');self.assertEqual(status,200)
        change=changes['changes'][0]
        self.assertEqual(change['kinds'],['user'])
        body={'task_id':a['id'],'session_id':'s1','revision':changes['revision'],'through_id':change['id']}
        status,_=self.request('POST','/api/task/changes/acknowledge',body);self.assertEqual(status,200)
        status,_=self.request('POST','/api/task/changes/acknowledge',body);self.assertEqual(status,409)
        _,a_changes=self.request('GET','/api/task/changes?task='+a['id']+'&session=s1')
        _,b_changes=self.request('GET','/api/task/changes?task='+b['id']+'&session=s1')
        self.assertEqual(a_changes['changes'],[]);self.assertEqual(len(b_changes['changes']),1)
        status,_=self.request('GET','/api/task/changes?task='+a['id']+'&session=unknown');self.assertEqual(status,404)

    def test_task_session_view_and_hash_checked_evidence_flow(self):
        status,session=self.request('GET','/api/session?id=s1');self.assertEqual(status,200);self.assertEqual(len(session['events']),1);self.assertEqual(session['events'][0]['kind'],'user')
        status,original=self.request('GET',f"/api/session-evidence?session=s1&node=0&hash={session['events'][0]['content_hash']}");self.assertEqual(status,200);self.assertEqual(original['content'],'synthetic evidence')
        status,created=self.request('POST','/api/task/create',{'title':'synthetic task'});self.assertEqual(status,200);task=created['id']
        status,_=self.request('POST','/api/link',{'task_id':task,'session_id':'s1'});self.assertEqual(status,200)
        status,detail=self.request('GET','/api/task?id='+task);self.assertEqual(status,200);view=detail['view'];section=view['sections'][0]['id']
        status,item=self.request('POST','/api/item/add',{'view_id':view['id'],'section_id':section,'revision':view['revision'],'content':'human note','pinned':True});self.assertEqual(status,200)
        digest=hashlib.sha256(self.raw.encode()).hexdigest()
        status,attached=self.request('POST','/api/evidence/attach',{'view_id':view['id'],'item_id':item['id'],'revision':item['revision'],'session_id':'s1','node_id':0,'content_hash':digest});self.assertEqual(status,200)
        status,evidence=self.request('GET',f'/api/evidence?task={task}&session=s1&node=0&hash={digest}');self.assertEqual(status,200);self.assertEqual(evidence['content'],'synthetic evidence')
        status,_=self.request('POST','/api/item/edit',{'view_id':view['id'],'item_id':item['id'],'revision':item['revision'],'content':'stale overwrite'});self.assertEqual(status,409)
        status,detail=self.request('GET','/api/task?id='+task);self.assertEqual(status,200);saved=detail['view']['sections'][0]['items'][0];self.assertEqual(saved['content'],'human note');self.assertTrue(saved['pinned']);self.assertEqual(len(saved['evidence']),1)
        link=detail['sessions'][0]
        status,_=self.request('POST','/api/link/state',{'task_id':task,'link_id':link['link_id'],'revision':link['revision'],'active':False});self.assertEqual(status,200)
        status,retained=self.request('GET',f'/api/evidence?task={task}&session=s1&node=0&hash={digest}');self.assertEqual(status,200);self.assertEqual(retained['content'],'synthetic evidence')
        status,_=self.request('POST','/api/evidence/attach',{'view_id':view['id'],'item_id':item['id'],'revision':attached['revision'],'session_id':'s1','node_id':0,'content_hash':digest});self.assertEqual(status,400)
        db=sqlite3.connect(self.source);db.execute("UPDATE message_nodes SET chat_message='{}'");db.commit();db.close()
        status,_=self.request('GET',f'/api/evidence?task={task}&session=s1&node=0&hash={digest}');self.assertEqual(status,400)


if __name__=='__main__':unittest.main()
