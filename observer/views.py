"""Persistent user-edited task views; operations never silently remove content."""
import time
import uuid
from .registry import RevisionConflict


def ensure_view(db, task_id):
    with db:
        found = db.execute('SELECT id FROM task_views WHERE task_id=?',(task_id,)).fetchone()
        if found:
            return found[0]
        vid = str(uuid.uuid4())
        db.execute('INSERT INTO task_views VALUES(?,?,1)',(vid,task_id))
        db.execute('INSERT INTO view_sections VALUES(?,?,?,?)',(str(uuid.uuid4()),vid,'Inbox',0))
        return vid


def _bump(db, view_id, expected):
    if not isinstance(expected, int) or isinstance(expected,bool):
        raise ValueError('revision required')
    cur = db.execute('UPDATE task_views SET revision=revision+1 WHERE id=? AND revision=?',(view_id,expected))
    if cur.rowcount != 1:
        raise RevisionConflict('view revision changed or view missing')
    return expected + 1


def add_section(db, view_id, expected, title):
    if not isinstance(title,str) or not title.strip():
        raise ValueError('section title is required')
    sid = str(uuid.uuid4())
    with db:
        revision = _bump(db,view_id,expected)
        position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM view_sections WHERE view_id=?',(view_id,)).fetchone()[0]
        db.execute('INSERT INTO view_sections VALUES(?,?,?,?)',(sid,view_id,title.strip(),position))
    return sid,revision


def rename_section(db, view_id, section_id, expected, title):
    if not isinstance(title,str) or not title.strip():
        raise ValueError('section title is required')
    with db:
        revision = _bump(db,view_id,expected)
        cur = db.execute('UPDATE view_sections SET title=? WHERE id=? AND view_id=?',(title.strip(),section_id,view_id))
        if cur.rowcount != 1:
            raise KeyError('section not found')
    return revision


def reorder_sections(db, view_id, expected, ordered_ids):
    if not isinstance(ordered_ids,list) or any(not isinstance(x,str) for x in ordered_ids):
        raise ValueError('section order must be a list of IDs')
    with db:
        revision = _bump(db,view_id,expected)
        existing = [r[0] for r in db.execute('SELECT id FROM view_sections WHERE view_id=? ORDER BY position',(view_id,))]
        if len(existing) != len(ordered_ids) or len(set(ordered_ids)) != len(ordered_ids) or set(existing) != set(ordered_ids):
            raise ValueError('section order must contain every section exactly once')
        for i,sid in enumerate(ordered_ids):
            db.execute('UPDATE view_sections SET position=? WHERE id=? AND view_id=?',(-i-1,sid,view_id))
        for i,sid in enumerate(ordered_ids):
            db.execute('UPDATE view_sections SET position=? WHERE id=? AND view_id=?',(i,sid,view_id))
    return revision


def add_item(db, view_id, section_id, expected, content, pinned=False):
    if not isinstance(content,str) or not content.strip() or not isinstance(pinned,bool):
        raise ValueError('content and valid pin are required')
    iid,now = str(uuid.uuid4()),time.time_ns()
    with db:
        revision = _bump(db,view_id,expected)
        if not db.execute('SELECT 1 FROM view_sections WHERE id=? AND view_id=?',(section_id,view_id)).fetchone():
            raise KeyError('section not found')
        position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM view_items WHERE section_id=?',(section_id,)).fetchone()[0]
        db.execute('INSERT INTO view_items VALUES(?,?,?,?,?,?,?,?,?)',(iid,section_id,content,'open','human',int(pinned),position,now,now))
        db.execute('INSERT INTO view_item_history(item_id,content,status,pinned,saved_at) VALUES(?,?,?,?,?)',(iid,content,'open',int(pinned),now))
    return iid,revision


def edit_item(db, view_id, item_id, expected, *, content=None, status=None, pinned=None):
    if content is not None and (not isinstance(content,str) or not content.strip()):
        raise ValueError('content is required')
    if status is not None and status not in ('open','resolved','superseded','retracted'):
        raise ValueError('unsupported item status')
    if pinned is not None and not isinstance(pinned,bool):
        raise ValueError('pin must be boolean')
    with db:
        row=db.execute('SELECT i.content,i.status,i.pinned FROM view_items i JOIN view_sections s ON s.id=i.section_id WHERE i.id=? AND s.view_id=?',(item_id,view_id)).fetchone()
        if not row:
            raise KeyError('item not found')
        revision = _bump(db,view_id,expected)
        new = (row[0] if content is None else content, row[1] if status is None else status, row[2] if pinned is None else int(pinned))
        if new != row:
            now=time.time_ns()
            db.execute('UPDATE view_items SET content=?,status=?,pinned=?,updated_at=? WHERE id=?',(*new,now,item_id))
            db.execute('INSERT INTO view_item_history(item_id,content,status,pinned,saved_at) VALUES(?,?,?,?,?)',(item_id,*new,now))
    return revision


def move_item(db, view_id, item_id, target_section_id, target_position, expected):
    if not isinstance(target_position,int) or isinstance(target_position,bool) or target_position<0:
        raise ValueError('valid item position required')
    with db:
        row=db.execute('SELECT i.section_id FROM view_items i JOIN view_sections s ON s.id=i.section_id WHERE i.id=? AND s.view_id=?',(item_id,view_id)).fetchone()
        if not row or not db.execute('SELECT 1 FROM view_sections WHERE id=? AND view_id=?',(target_section_id,view_id)).fetchone():
            raise KeyError('item or target section not found')
        source=row[0]
        source_ids=[r[0] for r in db.execute('SELECT id FROM view_items WHERE section_id=? ORDER BY position',(source,))]
        target_ids=source_ids if source==target_section_id else [r[0] for r in db.execute('SELECT id FROM view_items WHERE section_id=? ORDER BY position',(target_section_id,))]
        source_ids.remove(item_id)
        if target_position>len(target_ids):raise ValueError('position outside section')
        target_ids.insert(target_position,item_id)
        revision=_bump(db,view_id,expected)
        # Temporarily move positions below zero to avoid UNIQUE collisions.
        db.execute('UPDATE view_items SET position=-position-1 WHERE section_id=?',(source,))
        if source!=target_section_id:
            db.execute('UPDATE view_items SET position=-position-1 WHERE section_id=?',(target_section_id,))
            db.execute('UPDATE view_items SET section_id=?,position=? WHERE id=?',(target_section_id,-len(target_ids),item_id))
            for i,iid in enumerate(source_ids):db.execute('UPDATE view_items SET position=? WHERE id=?',(i,iid))
        for i,iid in enumerate(target_ids):db.execute('UPDATE view_items SET position=? WHERE id=?',(i,iid))
    return revision


def attach_evidence(db, view_id, item_id, expected, session_id, node_id, content_hash):
    with db:
        row=db.execute('SELECT v.task_id FROM view_items i JOIN view_sections s ON s.id=i.section_id JOIN task_views v ON v.id=s.view_id WHERE i.id=? AND v.id=?',(item_id,view_id)).fetchone()
        if not row:
            raise KeyError('item not found')
        if not db.execute('SELECT 1 FROM contributions c LEFT JOIN contribution_state cs ON cs.contribution_id=c.id WHERE c.task_id=? AND c.session_id=? AND COALESCE(cs.active,1)=1',(row[0],session_id)).fetchone():
            raise ValueError('session is not linked to task')
        if not db.execute('SELECT 1 FROM event_evidence WHERE session_id=? AND node_id=? AND content_hash=?',(session_id,node_id,content_hash)).fetchone():
            raise ValueError('evidence not found')
        revision = _bump(db,view_id,expected)
        db.execute('INSERT OR IGNORE INTO item_evidence VALUES(?,?,?,?)',(item_id,session_id,node_id,content_hash))
    return revision


def get_view(db, task_id):
    row=db.execute('SELECT id,revision FROM task_views WHERE task_id=?',(task_id,)).fetchone()
    if not row:
        return None
    view_id,revision=row
    sections=[]
    for sid,title in db.execute('SELECT id,title FROM view_sections WHERE view_id=? ORDER BY position',(view_id,)).fetchall():
        items=[]
        for iid,content,status,origin,pinned in db.execute('SELECT id,content,status,origin,pinned FROM view_items WHERE section_id=? ORDER BY position',(sid,)).fetchall():
            evidence=[{'session_id':s,'node_id':n,'content_hash':h} for s,n,h in db.execute('SELECT session_id,node_id,content_hash FROM item_evidence WHERE item_id=? ORDER BY session_id,node_id,content_hash',(iid,))]
            items.append({'id':iid,'content':content,'status':status,'origin':origin,'pinned':bool(pinned),'evidence':evidence})
        sections.append({'id':sid,'title':title,'items':items})
    return {'id':view_id,'task_id':task_id,'revision':revision,'sections':sections}
