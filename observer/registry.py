"""User-controlled local task registry and many-to-many session links."""
import time
import uuid
from urllib.parse import urlsplit


class RevisionConflict(Exception):
    pass


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
            return existing[0]
        link_id = str(uuid.uuid4())
        db.execute('INSERT INTO contributions VALUES (?,?,?,?,?,?,?,?)', (link_id,task_id,session_id,role,scope,linked_by,confidence,time.time_ns()))
    return link_id


def list_tasks(db):
    return [dict(zip(('id','title','description','lifecycle','created_at','updated_at','revision'), row)) for row in db.execute('SELECT id,title,description,lifecycle,created_at,updated_at,revision FROM tasks ORDER BY created_at,id')]


def task_sessions(db, task_id):
    return [dict(zip(('session_id','title','status','role','scope','linked_by','confidence'),row)) for row in db.execute('SELECT s.id,s.title,s.status,c.role,c.scope,c.linked_by,c.confidence FROM contributions c JOIN sessions s ON s.id=c.session_id WHERE c.task_id=? ORDER BY c.linked_at,c.id',(task_id,))]


def session_tasks(db, session_id):
    return [dict(zip(('task_id','title','lifecycle','role','scope'),row)) for row in db.execute('SELECT t.id,t.title,t.lifecycle,c.role,c.scope FROM contributions c JOIN tasks t ON t.id=c.task_id WHERE c.session_id=? ORDER BY c.linked_at,c.id',(session_id,))]


def add_resource(db, task_id, kind, uri):
    if kind not in ('ticket','pr','repo','document','commit','test','other'):
        raise ValueError('invalid resource kind')
    if not isinstance(uri,str) or not uri.strip() or len(uri)>2048:
        raise ValueError('invalid resource URI')
    uri=uri.strip()
    parsed=urlsplit(uri)
    if not ((parsed.scheme in ('http','https') and parsed.netloc and parsed.username is None and parsed.password is None) or (uri.startswith('/') and not parsed.scheme and not uri.startswith('//'))):
        raise ValueError('resource must be an HTTP URL or absolute local path')
    rid=str(uuid.uuid4())
    with db:
        db.execute('INSERT INTO resources VALUES (?,?,?,?,?,?,?)',(rid,task_id,kind,uri,'unknown',None,time.time_ns()))
    return rid


def task_resources(db,task_id):
    return [dict(zip(('id','kind','uri','external_status','verified_at'),row)) for row in db.execute('SELECT id,kind,uri,external_status,verified_at FROM resources WHERE task_id=? ORDER BY created_at,id',(task_id,))]
