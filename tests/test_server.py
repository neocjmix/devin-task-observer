import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from observer.server import LocalServer
from observer.store import open_store


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
        db=sqlite3.connect(self.source);db.execute("UPDATE message_nodes SET chat_message='{}'");db.commit();db.close()
        status,_=self.request('GET',f'/api/evidence?task={task}&session=s1&node=0&hash={digest}');self.assertEqual(status,400)


if __name__=='__main__':unittest.main()
