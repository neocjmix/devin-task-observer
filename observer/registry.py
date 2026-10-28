"""User-controlled local task registry and many-to-many session links."""
import time
import uuid
from urllib.parse import urlsplit


class RevisionConflict(Exception):
    pass


_UNCHANGED = object()


def create_task(db, title, description=''):
    if not isinstance(title, str):
        raise ValueError('title must be text')
    title = title.strip()
    if not title:
        raise ValueError('title is required')
    if not isinstance(description, str):
        raise ValueError('description must be text')
    task_id, now = str(uuid.uuid4()), time.time_ns()
    with db:
        db.execute('INSERT INTO tasks(id,title,description,lifecycle,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,1)', (task_id,title,description,'active',now,now))
    return task_id


def update_task(db, task_id, expected_revision, *, title=None, description=None, lifecycle=None):
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ValueError('title is required')
    if description is not None and not isinstance(description, str):
        raise ValueError('description must be text')
    if lifecycle is not None and lifecycle not in ('active', 'completed', 'archived'):
        raise ValueError('unsupported lifecycle')
    with db:
        row = db.execute('SELECT title,description,lifecycle,revision FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError('task not found')
        if row[3] != expected_revision:
            raise RevisionConflict('task revision changed')
        updated = (row[0] if title is None else title.strip(), row[1] if description is None else description, row[2] if lifecycle is None else lifecycle)
        if updated != row[:3]:
            cur = db.execute('UPDATE tasks SET title=?,description=?,lifecycle=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?', (*updated,time.time_ns(),task_id,expected_revision))
            if cur.rowcount != 1:
                raise RevisionConflict('task revision changed')
            return expected_revision + 1
        return expected_revision


def link_session(db, task_id, session_id, *, role='', scope='', linked_by='human', confidence=None):
    if not all(isinstance(x, str) for x in (role, scope, linked_by)) or not linked_by:
        raise ValueError('invalid contribution metadata')
    if confidence is not None and (not isinstance(confidence, (int,float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1):
        raise ValueError('confidence must be between 0 and 1')
    with db:
        existing = db.execute('SELECT id,role,scope,linked_by,confidence FROM contributions WHERE task_id=? AND session_id=?', (task_id,session_id)).fetchone()
        if existing:
            if existing[1:] != (role,scope,linked_by,confidence):
                raise ValueError('link already exists with different metadata')
            state = db.execute('SELECT active FROM contribution_state WHERE contribution_id=?',(existing[0],)).fetchone()
            if state and not state[0]:
                raise ValueError('link is archived; restore it explicitly')
            return existing[0]
        link_id = str(uuid.uuid4())
        now=time.time_ns()
        db.execute('INSERT INTO contributions VALUES (?,?,?,?,?,?,?,?)', (link_id,task_id,session_id,role,scope,linked_by,confidence,now))
        db.execute('INSERT INTO contribution_state VALUES (?,1,1)',(link_id,))
        db.execute('INSERT INTO contribution_history(contribution_id,active,revision,saved_at) VALUES (?,1,1,?)',(link_id,now))
    return link_id


def list_tasks(db):
    return [dict(zip(('id','title','description','lifecycle','created_at','updated_at','revision'), row)) for row in db.execute('SELECT id,title,description,lifecycle,created_at,updated_at,revision FROM tasks ORDER BY created_at,id')]


def task_sessions(db, task_id, include_archived=False):
    rows=db.execute('SELECT s.id,s.title,s.status,c.role,c.scope,c.linked_by,c.confidence,c.id,COALESCE(cs.active,1),COALESCE(cs.revision,1) FROM contributions c JOIN sessions s ON s.id=c.session_id LEFT JOIN contribution_state cs ON cs.contribution_id=c.id WHERE c.task_id=? AND (? OR COALESCE(cs.active,1)=1) ORDER BY c.linked_at,c.id',(task_id,int(include_archived)))
    return [dict(zip(('session_id','title','status','role','scope','linked_by','confidence','link_id','active','revision'),row)) for row in rows]


def session_tasks(db, session_id):
    return [dict(zip(('task_id','title','lifecycle','role','scope'),row)) for row in db.execute('SELECT t.id,t.title,t.lifecycle,c.role,c.scope FROM contributions c JOIN tasks t ON t.id=c.task_id LEFT JOIN contribution_state cs ON cs.contribution_id=c.id WHERE c.session_id=? AND COALESCE(cs.active,1)=1 ORDER BY c.linked_at,c.id',(session_id,))]


def set_link_active(db, task_id, link_id, expected_revision, active):
    if not isinstance(expected_revision,int) or isinstance(expected_revision,bool) or not isinstance(active,bool):
        raise ValueError('revision and active state required')
    with db:
        if not db.execute('SELECT 1 FROM contributions WHERE id=? AND task_id=?',(link_id,task_id)).fetchone():
            raise KeyError('link not found')
        db.execute('INSERT OR IGNORE INTO contribution_state VALUES (?,1,1)',(link_id,))
        old=db.execute('SELECT active,revision FROM contribution_state WHERE contribution_id=?',(link_id,)).fetchone()
        if old[1]!=expected_revision: raise RevisionConflict('link revision changed')
        if old[0]==int(active): return old[1]
        cur=db.execute('UPDATE contribution_state SET active=?,revision=revision+1 WHERE contribution_id=? AND revision=?',(int(active),link_id,expected_revision))
        if cur.rowcount!=1: raise RevisionConflict('link revision changed')
        if not db.execute('SELECT 1 FROM contribution_history WHERE contribution_id=?',(link_id,)).fetchone():
            db.execute('INSERT INTO contribution_history(contribution_id,active,revision,saved_at) VALUES (?,?,?,?)',(link_id,*old,time.time_ns()))
        db.execute('INSERT INTO contribution_history(contribution_id,active,revision,saved_at) VALUES (?,?,?,?)',(link_id,int(active),expected_revision+1,time.time_ns()))
        return expected_revision+1


def update_contribution(db, task_id, link_id, expected_revision, *, role=None, scope=None, confidence=_UNCHANGED):
    if (not isinstance(expected_revision,int) or isinstance(expected_revision,bool) or
        (role is not None and not isinstance(role,str)) or (scope is not None and not isinstance(scope,str)) or
        (confidence is not _UNCHANGED and confidence is not None and
         (not isinstance(confidence,(int,float)) or isinstance(confidence,bool) or not 0<=confidence<=1))):
        raise ValueError('invalid contribution metadata')
    with db:
        row=db.execute('SELECT role,scope,confidence FROM contributions WHERE id=? AND task_id=?',(link_id,task_id)).fetchone()
        if not row:raise KeyError('link not found')
        db.execute('INSERT OR IGNORE INTO contribution_state VALUES (?,1,1)',(link_id,))
        state=db.execute('SELECT active,revision FROM contribution_state WHERE contribution_id=?',(link_id,)).fetchone()
        if state[1]!=expected_revision:raise RevisionConflict('link revision changed')
        if not state[0]:raise ValueError('restore link before editing')
        new=(row[0] if role is None else role,row[1] if scope is None else scope,row[2] if confidence is _UNCHANGED else confidence)
        if new==row:return state[1]
        cur=db.execute('UPDATE contribution_state SET revision=revision+1 WHERE contribution_id=? AND revision=?',(link_id,expected_revision))
        if cur.rowcount!=1:raise RevisionConflict('link revision changed')
        if not db.execute('SELECT 1 FROM contribution_metadata_history WHERE contribution_id=?',(link_id,)).fetchone():
            db.execute('INSERT INTO contribution_metadata_history(contribution_id,role,scope,confidence,revision,saved_at) VALUES (?,?,?,?,?,?)',(link_id,*row,expected_revision,time.time_ns()))
        db.execute('UPDATE contributions SET role=?,scope=?,confidence=? WHERE id=?',(*new,link_id))
        db.execute('INSERT INTO contribution_metadata_history(contribution_id,role,scope,confidence,revision,saved_at) VALUES (?,?,?,?,?,?)',(link_id,*new,expected_revision+1,time.time_ns()))
        return expected_revision+1


def _validate_resource(kind, uri):
    if kind not in ('ticket','pr','repo','document','commit','test','other'):
        raise ValueError('invalid resource kind')
    if not isinstance(uri,str) or not uri.strip() or len(uri)>2048:
        raise ValueError('invalid resource URI')
    uri=uri.strip()
    parsed=urlsplit(uri)
    if not ((parsed.scheme in ('http','https') and parsed.netloc and parsed.username is None and parsed.password is None) or (uri.startswith('/') and not parsed.scheme and not uri.startswith('//'))):
        raise ValueError('resource must be an HTTP URL or absolute local path')
    return uri


def add_resource(db, task_id, kind, uri):
    uri=_validate_resource(kind,uri)
    rid=str(uuid.uuid4())
    with db:
        now=time.time_ns()
        db.execute('INSERT INTO resources VALUES (?,?,?,?,?,?,?)',(rid,task_id,kind,uri,'unknown',None,now))
        db.execute('INSERT INTO resource_state VALUES (?,1,1)',(rid,))
        db.execute('INSERT INTO resource_history(resource_id,kind,uri,active,revision,saved_at) VALUES (?,?,?,1,1,?)',(rid,kind,uri,now))
    return rid


def task_resources(db,task_id,include_archived=False):
    rows=db.execute('SELECT r.id,r.kind,r.uri,r.external_status,r.verified_at,COALESCE(rs.active,1),COALESCE(rs.revision,1) FROM resources r LEFT JOIN resource_state rs ON rs.resource_id=r.id WHERE r.task_id=? AND (? OR COALESCE(rs.active,1)=1) ORDER BY r.created_at,r.id',(task_id,int(include_archived)))
    return [dict(zip(('id','kind','uri','external_status','verified_at','active','revision'),row)) for row in rows]


def update_resource(db, task_id, resource_id, expected_revision, *, kind=None, uri=None, active=None):
    if not isinstance(expected_revision,int) or isinstance(expected_revision,bool) or (active is not None and not isinstance(active,bool)):
        raise ValueError('revision and valid active state required')
    with db:
        row=db.execute('SELECT kind,uri FROM resources WHERE id=? AND task_id=?',(resource_id,task_id)).fetchone()
        if not row: raise KeyError('resource not found')
        db.execute('INSERT OR IGNORE INTO resource_state VALUES (?,1,1)',(resource_id,))
        old=db.execute('SELECT active,revision FROM resource_state WHERE resource_id=?',(resource_id,)).fetchone()
        if old[1]!=expected_revision: raise RevisionConflict('resource revision changed')
        new_kind=row[0] if kind is None else kind
        new_uri=_validate_resource(new_kind,row[1] if uri is None else uri)
        new_active=old[0] if active is None else int(active)
        if (new_kind,new_uri,new_active)==(*row,old[0]): return old[1]
        cur=db.execute('UPDATE resource_state SET active=?,revision=revision+1 WHERE resource_id=? AND revision=?',(new_active,resource_id,expected_revision))
        if cur.rowcount!=1: raise RevisionConflict('resource revision changed')
        if not db.execute('SELECT 1 FROM resource_history WHERE resource_id=?',(resource_id,)).fetchone():
            db.execute('INSERT INTO resource_history(resource_id,kind,uri,active,revision,saved_at) VALUES (?,?,?,?,?,?)',(resource_id,*row,*old,time.time_ns()))
        db.execute("UPDATE resources SET kind=?,uri=?,external_status='unknown',verified_at=NULL WHERE id=?",(new_kind,new_uri,resource_id))
        db.execute('INSERT INTO resource_history(resource_id,kind,uri,active,revision,saved_at) VALUES (?,?,?,?,?,?)',(resource_id,new_kind,new_uri,new_active,expected_revision+1,time.time_ns()))
        return expected_revision+1
