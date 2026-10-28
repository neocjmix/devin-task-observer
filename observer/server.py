"""Loopback-only dashboard for explicitly selected personal workspaces."""
import argparse
import json
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .devin_local import SourceIncomplete, read_evidence
from .registry import RevisionConflict, add_resource, create_task, link_session, list_tasks, task_resources, task_sessions, update_task
from .store import open_store
from .views import add_item, add_section, attach_evidence, edit_item, ensure_view, get_view, rename_section, reorder_sections


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, source, store, workspaces):
        super().__init__(address, Handler)
        self.source, self.store, self.workspaces = source, store, workspaces
        self.token = secrets.token_hex(32)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.stop_event = threading.Event()
        self.refresh_state = {'ok': False, 'stage': 'not_started'}
        self.refresh_guard = threading.Lock()

    def refresh(self):
        if not self.refresh_guard.acquire(blocking=False):
            return {'ok': False, 'stage': 'busy'}
        try:
            cmd=[sys.executable,'-m','observer','--source',str(self.source),'--store',str(self.store)]
            for w in self.workspaces: cmd += ['--workspace',str(w)]
            try:
                result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
                self.refresh_state=json.loads(result.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.refresh_state={'ok':False,'stage':'refresh_failed'}
            return self.refresh_state
        finally:
            self.refresh_guard.release()

    def poll(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(5)


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    def log_message(self, *args):
        pass  # Avoid logging personal task titles, paths or query strings.

    def headers_safe(self, status, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def respond(self, status, value):
        data=json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.headers_safe(status,'application/json; charset=utf-8');self.wfile.write(data)

    def allowed(self, state_change=False):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            return False
        if self.headers.get('X-Observer-Token') != self.server.token:
            return False
        if state_change and (self.headers.get('Origin') != self.server.origin or self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json'):
            return False
        return True

    def allowed_session(self, db, session_id):
        row=db.execute('SELECT workspace FROM sessions WHERE id=?',(session_id,)).fetchone()
        return bool(row and row[0] in {str(w.absolute()) for w in self.server.workspaces})

    def do_GET(self):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            return self.respond(403,{'error':'forbidden'})
        target=urlsplit(self.path)
        if target.path=='/':
            html=(Path(__file__).parent/'web/index.html').read_text().replace('__OBSERVER_TOKEN__',self.server.token)
            temporary=self.server.store.absolute().resolve().is_relative_to(Path('/tmp').resolve())
            html=html.replace('__DATA_MODE__','임시 로컬 저장소' if temporary else '검증 중인 로컬 저장소')
            self.headers_safe(200,'text/html; charset=utf-8');self.wfile.write(html.encode('utf-8'));return
        if not self.allowed():return self.respond(403,{'error':'forbidden'})
        query=parse_qs(target.query)
        db=None
        try:
            db=open_store(self.server.store,self.server.source)
            if target.path=='/api/state':
                allowed={str(w.absolute()) for w in self.server.workspaces}
                sessions=[{'id':r[0],'title':r[1] or 'Untitled session','status':r[2], 'last_seen':r[4], 'task_count':r[5]} for r in db.execute('SELECT s.id,s.title,s.status,s.workspace,s.last_seen,(SELECT COUNT(*) FROM contributions c WHERE c.session_id=s.id) FROM sessions s ORDER BY s.last_seen DESC,s.id') if r[3] in allowed]
                return self.respond(200,{'tasks':list_tasks(db),'sessions':sessions,'refresh':self.server.refresh_state})
            if target.path=='/api/session':
                sid=query.get('id',[''])[0]
                if not self.allowed_session(db,sid):return self.respond(404,{'error':'not found'})
                offset=int(query.get('offset',['0'])[0])
                if offset<0 or offset>10000000:raise ValueError('invalid offset')
                row=db.execute('SELECT title,status,last_seen FROM sessions WHERE id=?',(sid,)).fetchone()
                rows=db.execute('''SELECT e.kind,e.message_id,e.discriminator,e.first_observed_at,
                    (SELECT x.node_id FROM event_evidence x LEFT JOIN source_nodes n ON n.session_id=x.session_id AND n.node_id=x.node_id AND n.content_hash=x.content_hash AND n.present=1 WHERE x.session_id=e.session_id AND x.message_id=e.message_id AND x.kind=e.kind AND x.discriminator=e.discriminator ORDER BY (n.node_id IS NOT NULL) DESC,x.node_id,x.content_hash LIMIT 1),
                    (SELECT x.content_hash FROM event_evidence x LEFT JOIN source_nodes n ON n.session_id=x.session_id AND n.node_id=x.node_id AND n.content_hash=x.content_hash AND n.present=1 WHERE x.session_id=e.session_id AND x.message_id=e.message_id AND x.kind=e.kind AND x.discriminator=e.discriminator ORDER BY (n.node_id IS NOT NULL) DESC,x.node_id,x.content_hash LIMIT 1)
                    FROM events e WHERE e.session_id=? ORDER BY e.first_observed_at,e.message_id,e.kind,e.discriminator LIMIT 51 OFFSET ?''',(sid,offset)).fetchall()
                items=[{'kind':k,'node_id':n,'content_hash':h} for k,_,_,_,n,h in rows[:50]]
                return self.respond(200,{'id':sid,'title':row[0] or 'Untitled session','status':row[1],'last_seen':row[2],'events':items,'next_offset':offset+50 if len(rows)>50 else None})
            if target.path=='/api/session-evidence':
                sid=query.get('session',[''])[0];node=int(query.get('node',['-1'])[0]);digest=query.get('hash',[''])[0]
                if not self.allowed_session(db,sid) or not db.execute('SELECT 1 FROM event_evidence WHERE session_id=? AND node_id=? AND content_hash=?',(sid,node,digest)).fetchone():
                    return self.respond(404,{'error':'not found'})
                workspace=db.execute('SELECT workspace FROM sessions WHERE id=?',(sid,)).fetchone()[0]
                value=read_evidence(self.server.source,Path(workspace),sid,node,digest)
                content=value.get('content')
                return self.respond(200,{'content':content if isinstance(content,str) else '', 'role':value.get('role','unknown'), 'notice':'Raw source content may contain injected context.'})
            if target.path=='/api/task':
                task_id=query.get('id',[''])[0]
                links=[s for s in task_sessions(db,task_id) if self.allowed_session(db,s['session_id'])]
                view=get_view(db,task_id)
                if view:
                    for section in view['sections']:
                        for item in section['items']:
                            item['evidence']=[e for e in item['evidence'] if self.allowed_session(db,e['session_id'])]
                return self.respond(200,{'view':view,'sessions':links,'resources':task_resources(db,task_id)})
            if target.path=='/api/evidence-list':
                task_id=query.get('task',[''])[0];sid=query.get('session',[''])[0]
                if not self.allowed_session(db,sid) or not db.execute('SELECT 1 FROM contributions WHERE task_id=? AND session_id=?',(task_id,sid)).fetchone():
                    return self.respond(404,{'error':'not found'})
                rows=db.execute('SELECT node_id,role,content_hash,present FROM source_nodes WHERE session_id=? ORDER BY node_id',(sid,)).fetchall()
                return self.respond(200,{'nodes':[{'node_id':n,'role':role,'content_hash':h,'present':bool(p)} for n,role,h,p in rows]})
            if target.path=='/api/evidence':
                task_id=query.get('task',[''])[0];sid=query.get('session',[''])[0];node=int(query.get('node',['-1'])[0]);digest=query.get('hash',[''])[0]
                row=db.execute('SELECT s.workspace FROM sessions s JOIN contributions c ON c.session_id=s.id WHERE c.task_id=? AND s.id=?',(task_id,sid)).fetchone()
                if not row or row[0] not in {str(w.absolute()) for w in self.server.workspaces}:
                    return self.respond(404,{'error':'not found'})
                if not db.execute('SELECT 1 FROM event_evidence WHERE session_id=? AND node_id=? AND content_hash=?',(sid,node,digest)).fetchone():
                    return self.respond(404,{'error':'not found'})
                value=read_evidence(self.server.source,Path(row[0]),sid,node,digest)
                content=value.get('content')
                return self.respond(200,{'content':content if isinstance(content,str) else '', 'role':value.get('role','unknown'), 'notice':'Raw source content may contain injected context.'})
            return self.respond(404,{'error':'not found'})
        except (ValueError,KeyError,sqlite3.Error,SourceIncomplete,OSError):
            return self.respond(400,{'error':'unable to read data'})
        finally:
            if db is not None:db.close()

    def do_POST(self):
        if not self.allowed(True):
            # Drain small rejected bodies so the client receives the response
            # instead of a TCP reset; never parse or act on them.
            try:
                length=int(self.headers.get('Content-Length','0'))
                if 0 < length <= 65536:
                    self.connection.settimeout(2)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            return self.respond(403,{'error':'forbidden'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>65536:return self.respond(400,{'error':'invalid request'})
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):return self.respond(400,{'error':'invalid request'})
        except (ValueError,UnicodeDecodeError):
            return self.respond(400,{'error':'invalid request'})
        target=urlsplit(self.path).path
        if target=='/api/refresh':return self.respond(200,self.server.refresh())
        db=None
        try:
            db=open_store(self.server.store,self.server.source)
            if target=='/api/task/create':
                task=create_task(db,body.get('title',''),body.get('description',''));ensure_view(db,task);result={'id':task}
            elif target=='/api/task/edit':
                result={'revision':update_task(db,body['id'],body['revision'],title=body.get('title'),description=body.get('description'),lifecycle=body.get('lifecycle'))}
            elif target=='/api/link':
                if not self.allowed_session(db,body['session_id']):
                    raise ValueError('session is outside the configured workspaces')
                result={'id':link_session(db,body['task_id'],body['session_id'])}
            elif target=='/api/resource/add':
                result={'id':add_resource(db,body['task_id'],body['kind'],body['uri'])}
            elif target=='/api/view/ensure':
                result={'id':ensure_view(db,body['task_id'])}
            elif target=='/api/section/add':
                sid,rev=add_section(db,body['view_id'],body['revision'],body['title']);result={'id':sid,'revision':rev}
            elif target=='/api/section/rename':
                result={'revision':rename_section(db,body['view_id'],body['section_id'],body['revision'],body['title'])}
            elif target=='/api/section/reorder':
                result={'revision':reorder_sections(db,body['view_id'],body['revision'],body['ordered_ids'])}
            elif target=='/api/item/add':
                iid,rev=add_item(db,body['view_id'],body['section_id'],body['revision'],body['content'],body.get('pinned',False));result={'id':iid,'revision':rev}
            elif target=='/api/item/edit':
                result={'revision':edit_item(db,body['view_id'],body['item_id'],body['revision'],content=body.get('content'),status=body.get('status'),pinned=body.get('pinned'))}
            elif target=='/api/evidence/attach':
                if not self.allowed_session(db,body['session_id']):
                    raise ValueError('session is outside the configured workspaces')
                result={'revision':attach_evidence(db,body['view_id'],body['item_id'],body['revision'],body['session_id'],body['node_id'],body['content_hash'])}
            else:return self.respond(404,{'error':'not found'})
            return self.respond(200,result)
        except RevisionConflict:
            return self.respond(409,{'error':'revision conflict; reload before editing'})
        except (ValueError,KeyError,TypeError,sqlite3.Error,OSError):
            return self.respond(400,{'error':'invalid or unavailable data'})
        finally:
            if db is not None:db.close()


def main():
    home=Path.home();p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',action='append',required=True,type=Path)
    p.add_argument('--source',type=Path,default=home/'.local/share/devin/cli/sessions.db')
    p.add_argument('--store',type=Path,default=home/'Library/Application Support/Devin Task Observer/observer.sqlite3')
    p.add_argument('--port',type=int,default=8765)
    args=p.parse_args()
    db=open_store(args.store,args.source);db.close()
    server=LocalServer(('127.0.0.1',args.port),args.source,args.store,args.workspace)
    thread=threading.Thread(target=server.poll,daemon=True);thread.start()
    print('Dashboard listening at '+server.origin,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.stop_event.set();server.server_close();thread.join(timeout=35)

if __name__=='__main__':main()
