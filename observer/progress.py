"""Deterministic task-specific review positions over immutable observations."""
import json
import time
from .registry import RevisionConflict

PAGE_SIZE=50

# Only suppress an added node when the exact same message payload was already
# observed in this session. All raw node observations and references remain.
_DISTINCT_OBSERVATIONS = '''NOT (o.transition='added' AND EXISTS (
    SELECT 1 FROM observations p WHERE p.session_id=o.session_id AND p.message_id=o.message_id
    AND p.content_hash=o.content_hash AND p.id<o.id AND p.transition IN ('added','modified','restored')
))'''


def _linked(db, task_id, session_id):
    return db.execute('''SELECT 1 FROM contributions c LEFT JOIN contribution_state cs ON cs.contribution_id=c.id
        WHERE c.task_id=? AND c.session_id=? AND COALESCE(cs.active,1)=1''',(task_id,session_id)).fetchone() is not None


def pending_changes(db,task_id,session_id):
    if not _linked(db,task_id,session_id):raise ValueError('session is not linked')
    row=db.execute('SELECT last_observation_id,revision FROM task_session_progress WHERE task_id=? AND session_id=?',(task_id,session_id)).fetchone()
    last,revision=row if row else (0,0)
    rows=db.execute('''SELECT o.id,o.node_id,o.transition,o.role,o.content_hash,o.observed_at,k.kinds FROM observations o
        LEFT JOIN node_kinds k ON k.session_id=o.session_id AND k.node_id=o.node_id AND k.content_hash=o.content_hash
        WHERE o.session_id=? AND o.id>? AND '''+_DISTINCT_OBSERVATIONS+''' ORDER BY o.id LIMIT ?''',(session_id,last,PAGE_SIZE+1)).fetchall()
    changes=[]
    for r in rows[:PAGE_SIZE]:
        kinds=json.loads(r[6]) if r[6] else ['unknown']
        if not isinstance(kinds,list) or not all(isinstance(k,str) for k in kinds):raise ValueError('invalid labels')
        item=dict(zip(('id','node_id','transition','role','content_hash','observed_at'),r[:6]));item['kinds']=kinds;changes.append(item)
    return {'last_observation_id':last,'revision':revision,'changes':changes,'has_more':len(rows)>PAGE_SIZE}


def task_attention(db,task_id,allowed_workspaces):
    """Count unreviewed observations and stale linked sessions without a model."""
    allowed={str(p) for p in allowed_workspaces};pending=0;stale=0;linked=0
    rows=db.execute('''SELECT s.id,s.workspace,s.status,COALESCE(p.last_observation_id,0)
        FROM contributions c JOIN sessions s ON s.id=c.session_id
        LEFT JOIN contribution_state cs ON cs.contribution_id=c.id
        LEFT JOIN task_session_progress p ON p.task_id=c.task_id AND p.session_id=c.session_id
        WHERE c.task_id=? AND COALESCE(cs.active,1)=1''',(task_id,)).fetchall()
    for sid,workspace,status,last in rows:
        if workspace not in allowed:continue
        linked+=1;stale+=int(status=='stale')
        pending+=db.execute('SELECT COUNT(*) FROM observations o WHERE o.session_id=? AND o.id>? AND '+_DISTINCT_OBSERVATIONS,(sid,last)).fetchone()[0]
    return {'pending_changes':pending,'stale_sessions':stale,'linked_sessions':linked}


def acknowledge_changes(db,task_id,session_id,expected_revision,through_id):
    if (not isinstance(expected_revision,int) or isinstance(expected_revision,bool) or
        not isinstance(through_id,int) or isinstance(through_id,bool) or through_id<=0):
        raise ValueError('valid revision and observation required')
    db.execute('BEGIN IMMEDIATE')
    try:
        if not _linked(db,task_id,session_id):raise ValueError('session is not linked')
        row=db.execute('SELECT last_observation_id,revision FROM task_session_progress WHERE task_id=? AND session_id=?',(task_id,session_id)).fetchone()
        last,revision=row if row else (0,0)
        if revision!=expected_revision:raise RevisionConflict('review position changed')
        ids=[r[0] for r in db.execute('SELECT o.id FROM observations o WHERE o.session_id=? AND o.id>? AND '+_DISTINCT_OBSERVATIONS+' ORDER BY o.id LIMIT ?',(session_id,last,PAGE_SIZE))]
        if through_id not in ids:raise ValueError('observation is outside the current review page')
        if row:
            cur=db.execute('UPDATE task_session_progress SET last_observation_id=?,revision=revision+1 WHERE task_id=? AND session_id=? AND revision=?',(through_id,task_id,session_id,expected_revision))
            if cur.rowcount!=1:raise RevisionConflict('review position changed')
        else:
            db.execute('INSERT INTO task_session_progress VALUES (?,?,?,1)',(task_id,session_id,through_id))
        new_revision=revision+1
        db.execute('INSERT INTO task_progress_history(task_id,session_id,last_observation_id,revision,acknowledged_at) VALUES (?,?,?,?,?)',(task_id,session_id,through_id,new_revision,time.time_ns()))
        db.commit()
        return new_revision
    except Exception:
        db.rollback();raise
