"""Persistent projections of the observed todo_write shape, scoped by session."""
import hashlib
import json

SCHEMA = '''
CREATE TABLE IF NOT EXISTS plan_records (
 session_id TEXT NOT NULL, node_id INTEGER NOT NULL, source_hash TEXT NOT NULL,
 message_id TEXT NOT NULL, call_id TEXT NOT NULL, plan_hash TEXT NOT NULL, items TEXT,
 PRIMARY KEY(session_id,node_id,source_hash,call_id)
);
'''


def save_plans(db,sid,node):
    preview=node.get('plan_preview',{'plans':[],'unsupported':False})
    plans=preview['plans']
    if preview['unsupported']:plans=[{'tool_call_id':'unsupported','items':None}]
    for plan in plans:
        items=json.dumps(plan['items'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(items.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO plan_records VALUES (?,?,?,?,?,?,?)',(sid,node['node_id'],node['content_hash'],node['message_id'],plan['tool_call_id'],digest,items if plan['items'] is not None else None))


def plan_changes(previous,current):
    if previous is None or current is None:return [{'type':'unsupported_shape'}]
    a=[x['content'] for x in previous];b=[x['content'] for x in current]
    if len(set(a))!=len(a) or len(set(b))!=len(b):return [{'type':'ambiguous_item_identity'}]
    old={x['content']:x['source_status'] for x in previous};new={x['content']:x['source_status'] for x in current}
    result=[{'type':'removed_or_replaced','content':x,'previous_status':old[x]} for x in a if x not in new]
    for content in b:
        status=new[content]
        if content not in old:result.append({'type':'added','content':content,'source_status':status})
        elif status!=old[content]:
            kind='agent_reported_completed' if status=='completed' else 'agent_reported_reopened' if old[content]=='completed' and status in ('pending','in_progress') else 'reported_status_changed'
            result.append({'type':kind,'content':content,'previous_status':old[content],'source_status':status})
    return result


def task_plans(db,task_id,allowed_workspaces):
    allowed={str(w.absolute()) for w in allowed_workspaces}
    links=db.execute('''SELECT s.id,s.title,s.workspace,s.status FROM contributions c JOIN sessions s ON s.id=c.session_id
        LEFT JOIN contribution_state cs ON cs.contribution_id=c.id WHERE c.task_id=? AND COALESCE(cs.active,1)=1 ORDER BY c.linked_at''',(task_id,)).fetchall()
    result=[]
    for sid,title,workspace,status in links:
        if workspace not in allowed:continue
        nodes={n:(parent,digest,bool(present)) for n,parent,digest,present in db.execute('SELECT node_id,parent_id,content_hash,present FROM source_nodes WHERE session_id=?',(sid,))}
        records=db.execute('SELECT node_id,source_hash,message_id,call_id,plan_hash,items FROM plan_records WHERE session_id=? ORDER BY node_id,source_hash,call_id',(sid,)).fetchall()
        groups={}
        for node,digest,mid,call,phash,items in records:
            key=hashlib.sha256(json.dumps([mid,call,phash]).encode()).hexdigest()
            group=groups.setdefault(key,{'id':key,'plan_hash':phash,'items':json.loads(items) if items is not None else None,'evidence':[],'changes':[],'predecessors':[]})
            current=node in nodes and nodes[node][1]==digest and nodes[node][2]
            group['evidence'].append({'session_id':sid,'session_title':title,'node_id':node,'content_hash':digest,'current':current})
        # Ancestor relationships, not timestamps or node-number sorting, establish
        # order within the source graph. Revisions of the same node stay unordered.
        ancestry={}
        def ancestors(node):
            if node not in ancestry:
                seen=set();cursor=nodes.get(node,(None,))[0]
                while cursor is not None and cursor in nodes and cursor not in seen:
                    seen.add(cursor);cursor=nodes[cursor][0]
                ancestry[node]=seen
            return ancestry[node]
        def before(a,b):
            an={e['node_id'] for e in a['evidence'] if e['current']};bn={e['node_id'] for e in b['evidence'] if e['current']}
            forward=any(x in ancestors(y) for x in an for y in bn)
            reverse=any(y in ancestors(x) for x in an for y in bn)
            return forward and not reverse
        values=list(groups.values());active=[g for g in values if any(e['current'] for e in g['evidence'])]
        node_versions={}
        for node,_,_,_,phash,_ in records:node_versions.setdefault(node,set()).add(phash)
        revised=any(len(hashes)>1 for hashes in node_versions.values())
        for g in values:
            g['current']=g in active;g['completion_verified']=False
            prior=[p for p in active if p is not g and before(p,g)] if g in active else []
            nearest=[p for p in prior if not any(q is not p and before(p,q) for q in prior)]
            g['predecessors']=[p['id'] for p in nearest]
            if len(nearest)==1:g['changes']=plan_changes(nearest[0]['items'],g['items'])
            elif len(nearest)>1:g['changes']=[{'type':'ambiguous_predecessors'}]
        heads=[g['id'] for g in active if not any(other is not g and before(g,other) for other in active)]
        ambiguous=revised or len(heads)>1 or status!='current' or any(g['items'] is None for g in active)
        result.append({'session_id':sid,'title':title,'source_status':status,'versions':values,'heads':heads,
            'ordering':'uncertain' if ambiguous else 'source_lineage' if heads else 'unavailable',
            'notice':'Agent-reported plan only; completion and task attribution are unverified. Sessions are never merged.'})
    return result
