"""Deterministic resource mentions and local Git facts; never external status."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA = '''
CREATE TABLE IF NOT EXISTS discovered_resources (
 id TEXT PRIMARY KEY, workspace TEXT NOT NULL, session_id TEXT, node_id INTEGER,
 source_hash TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, resource_key TEXT NOT NULL,
 verification TEXT NOT NULL, source_ref TEXT NOT NULL, observed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS discovered_resources_session ON discovered_resources(session_id,node_id,source_hash);
CREATE TABLE IF NOT EXISTS resource_choices (
 task_id TEXT NOT NULL REFERENCES tasks(id), resource_key TEXT NOT NULL,
 resource_id TEXT NOT NULL REFERENCES resources(id), PRIMARY KEY(task_id,resource_key)
);
CREATE TABLE IF NOT EXISTS git_observations (
 workspace TEXT PRIMARY KEY, fingerprint TEXT, observed_at INTEGER NOT NULL, error TEXT
);
'''
TICKET = re.compile(r'\b[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,7}(?![A-Za-z0-9]|\.[0-9])\b')
URL = re.compile(r'https?://[^\s<>"`]+')
BRANCH = re.compile(r'(?:\bbranch|브랜치)\s*:\s*([A-Za-z0-9_][A-Za-z0-9_./-]{0,199})(?![A-Za-z0-9_./-])',re.I)
COMMIT = re.compile(r'\bcommit\s*:?\s+([0-9a-f]{7,40})\b',re.I)
WORKTREE = re.compile(r'(?:\bworktree|워크트리)\s*:\s*(/[^\s<>"`]{1,1000})(?![^\s<>"`])',re.I)


def scoped_resource(kind,value):
    return kind=='branch' or (kind in ('commit','ticket') and not value.startswith('https://'))


def resource_key(kind,value,workspace=None):
    if kind in ('pr','repo') and value.startswith('https://github.com/'):
        value=value.rstrip('/').removesuffix('.git').lower()
    return kind+':'+value+(':workspace:'+str(workspace) if workspace is not None and scoped_resource(kind,value) else '')


def extract_resources(text):
    """Only recognized syntax. Mentions (including quoted examples) stay claims."""
    if not isinstance(text,str):return []
    result=set()
    for token in URL.findall(text):
        token=token.rstrip('.,);]}')
        if len(token)>2048:continue
        try:
            p=urlsplit(token)
            if p.username or p.password or p.port not in (None,80,443):continue
        except ValueError:continue
        parts=p.path.strip('/').split('/')
        if p.hostname=='github.com' and len(parts)>=2 and all(re.fullmatch(r'[A-Za-z0-9_.-]+',x) for x in parts[:2]):
            repo='https://github.com/'+parts[0]+'/'+parts[1].removesuffix('.git')
            result.add(('repo',repo.lower()))
            if len(parts)>=4 and parts[2]=='pull' and re.fullmatch(r'[1-9][0-9]{0,11}',parts[3]):
                result.add(('pr',repo.lower()+'/pull/'+parts[3]))
            if len(parts)>=4 and parts[2]=='commit' and re.fullmatch(r'[0-9a-fA-F]{40}',parts[3]):
                result.add(('commit',repo.lower()+'/commit/'+parts[3].lower()))
        # Strip query/fragment instead of persisting possible credentials.
        elif len(parts)==2 and parts[0]=='browse' and TICKET.fullmatch(parts[1]) and p.hostname:
            result.add(('ticket','https://'+p.netloc+'/browse/'+parts[1]))
        elif p.hostname=='linear.app' and len(parts)>=3 and parts[1]=='issue' and TICKET.fullmatch(parts[2]):
            result.add(('ticket','https://linear.app/'+parts[0]+'/issue/'+parts[2]))
    result.update(('ticket',x) for x in TICKET.findall(text))
    # Prefer the explicit URL over a duplicate bare identifier in the same text.
    url_ids={TICKET.search(v).group() for k,v in result if k=='ticket' and v.startswith('https://') and TICKET.search(v)}
    result={x for x in result if not (x[0]=='ticket' and x[1] in url_ids)}
    result.update(('branch',m.group(1).rstrip('.')) for m in BRANCH.finditer(text) if '..' not in m.group(1))
    result.update(('commit',m.group(1).lower()) for m in COMMIT.finditer(text))
    result.update(('worktree',m.group(1).rstrip('.,);]}')) for m in WORKTREE.finditer(text))
    return [{'kind':k,'value':v,'key':resource_key(k,v)} for k,v in sorted(result)]


def save_mentions(db,workspace,sid,node,now):
    for found in node.get('resources',[]):
        key=resource_key(found['kind'],found['value'],workspace)
        identity=json.dumps([sid,node['node_id'],node['content_hash'],key],separators=(',',':'))
        db.execute('INSERT INTO discovered_resources VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_ref=excluded.source_ref',(
            hashlib.sha256(identity.encode()).hexdigest(),workspace,sid,node['node_id'],node['content_hash'],found['kind'],found['value'],key,'mentioned','mentions-v2',now))


def git_executable():
    return os.environ.get('DEVIN_OBSERVER_GIT') or shutil.which('git') or 'git'


def read_git(workspace):
    """Fixed read-only commands, no shell, fetch, hooks, status/index refresh."""
    env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}
    def git(*args,optional=False):
        r=subprocess.run([git_executable(),'-c','core.fsmonitor=false','-C',str(workspace),*args],env=env,capture_output=True,text=True,timeout=3)
        if r.returncode:
            if optional:return None
            raise ValueError('git unavailable or workspace is not a repository')
        if len(r.stdout)>16384:raise ValueError('git output exceeds limit')
        return r.stdout.strip()
    root=git('rev-parse','--show-toplevel')
    # Never inspect a parent repository outside the explicitly allowed root.
    if Path(root).resolve()!=Path(workspace).resolve():raise ValueError('workspace must be a Git worktree root')
    commit=git('rev-parse','--verify','HEAD',optional=True)
    branch=git('symbolic-ref','--quiet','--short','HEAD',optional=True)
    remote=git('config','--get','remote.origin.url',optional=True)
    facts=[('worktree',str(Path(root).resolve()))]
    if branch:facts.append(('branch',branch))
    if commit and re.fullmatch(r'[0-9a-f]{40}',commit):facts.append(('commit',commit))
    if remote:
        if remote.startswith('git@github.com:'):remote='https://github.com/'+remote.split(':',1)[1]
        facts.extend((x['kind'],x['value']) for x in extract_resources(remote) if x['kind']=='repo')
    if commit!=git('rev-parse','--verify','HEAD',optional=True) or branch!=git('symbolic-ref','--quiet','--short','HEAD',optional=True):
        raise ValueError('Git checkout changed while reading; keep previous facts')
    return sorted(set(facts))


def sync_git(db,workspaces):
    for workspace in sorted({str(Path(w).absolute()) for w in workspaces}):
        now=time.time_ns()
        try:
            facts=read_git(workspace)
            fingerprint=hashlib.sha256(json.dumps(facts).encode()).hexdigest()
            with db:
                for kind,value in facts:
                    key=resource_key(kind,value,workspace);identity=hashlib.sha256((workspace+fingerprint+key).encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO discovered_resources VALUES (?,?,?,?,?,?,?,?,?,?,?)',(identity,workspace,None,None,fingerprint,kind,value,key,'local_git','current_checkout',now))
                db.execute('INSERT INTO git_observations VALUES (?,?,?,NULL) ON CONFLICT(workspace) DO UPDATE SET fingerprint=excluded.fingerprint,observed_at=excluded.observed_at,error=NULL',(workspace,fingerprint,now))
        except (OSError,ValueError,subprocess.TimeoutExpired):
            with db:
                db.execute('INSERT INTO git_observations VALUES (?,NULL,?,?) ON CONFLICT(workspace) DO UPDATE SET observed_at=excluded.observed_at,error=excluded.error',(workspace,now,'local Git unavailable; prior facts retained'))


def task_candidates(db,task_id,allowed_workspaces):
    allowed={str(Path(w).absolute()) for w in allowed_workspaces}
    links=db.execute('''SELECT s.id,s.workspace,s.status,s.title FROM contributions c JOIN sessions s ON s.id=c.session_id
      LEFT JOIN contribution_state cs ON cs.contribution_id=c.id WHERE c.task_id=? AND COALESCE(cs.active,1)=1''',(task_id,)).fetchall()
    sessions={s:(w,status,title) for s,w,status,title in links if w in allowed};workspaces={w for w,_,_ in sessions.values()}
    collector=db.execute('SELECT last_error FROM collector_status WHERE singleton=1').fetchone()
    groups={}
    for w in sorted(workspaces):
        rows=db.execute('SELECT session_id,node_id,source_hash,kind,value,resource_key,verification,source_ref,observed_at FROM discovered_resources WHERE workspace=? ORDER BY resource_key,observed_at,id',(w,)).fetchall()
        git=db.execute('SELECT fingerprint,error,observed_at FROM git_observations WHERE workspace=?',(w,)).fetchone()
        for sid,node,digest,kind,value,key,verification,ref,observed_at in rows:
            if sid is not None and ref!='mentions-v2':continue
            if key!=resource_key(kind,value,w):continue
            if sid is not None and sid not in sessions:continue
            item=groups.setdefault(key,{'key':key,'kind':kind,'value':value,'workspace':w if scoped_resource(kind,value) else None,'association':'candidate','external_status':'unknown','evidence':[]})
            if sid is not None:
                present=db.execute('SELECT 1 FROM source_nodes WHERE session_id=? AND node_id=? AND content_hash=? AND present=1',(sid,node,digest)).fetchone() is not None
                count=db.execute('SELECT COUNT(*) FROM contributions c LEFT JOIN contribution_state cs ON cs.contribution_id=c.id WHERE session_id=? AND COALESCE(cs.active,1)=1',(sid,)).fetchone()[0]
                evidence={'workspace':w,'session_id':sid,'session_title':sessions[sid][2],'node_id':node,'content_hash':digest,'verification':'mentioned','current':present and sessions[sid][1]=='current','shared_session':count>1}
            else:
                evidence={'workspace':w,'source':'local_git','verification':'local_git','source_ref':ref,'current':bool(git and not git[1] and git[0]==digest and not (collector and collector[0])),'scope':'workspace_only','observed_at':observed_at}
            item['evidence'].append(evidence)
    # A bare key can alias a single matching ticket URL. Different hosts with
    # the same key remain separate candidates; a human choice is never moved.
    for key,item in list(groups.items()):
        if item['kind']!='ticket' or not TICKET.fullmatch(item['value']):continue
        urls=[x for x in groups.values() if x['kind']=='ticket' and x['value'].startswith('https://') and item['value'] in TICKET.findall(x['value']) and any(e.get('workspace')==item['workspace'] for e in x['evidence'])]
        chosen=db.execute('SELECT 1 FROM resource_choices WHERE task_id=? AND resource_key=?',(task_id,key)).fetchone()
        if len(urls)==1 and not chosen:
            urls[0]['evidence'].extend(item['evidence']);del groups[key]
    for item in groups.values():
        if item['kind']=='pr':item['number']=int(item['value'].rsplit('/',1)[1])
        if item['kind']=='ticket':
            identifiers=TICKET.findall(urlsplit(item['value']).path if item['value'].startswith('https://') else item['value'])
            if identifiers:item['identifier']=identifiers[-1]
        choice=db.execute('SELECT resource_id FROM resource_choices WHERE task_id=? AND resource_key=?',(task_id,item['key'])).fetchone()
        if choice:
            saved=db.execute('SELECT kind,uri FROM resources WHERE id=? AND task_id=?',(choice[0],task_id)).fetchone()
            item['association']='confirmed' if saved and resource_key(*saved,item['workspace'])==item['key'] else 'confirmed_resource_edited'
            state=db.execute('SELECT active FROM resource_state WHERE resource_id=?',(choice[0],)).fetchone()
            if state and not state[0]:item['association']='confirmed_archived'
            item['resource_id']=choice[0]
        item['current']=any(e['current'] for e in item['evidence'])
    return sorted(groups.values(),key=lambda x:(not x['current'],x['kind'],x['value']))


def confirm_candidate(db,task_id,key,allowed_workspaces):
    # Re-evaluate active task/session scope, never trust a client-provided value.
    db.execute('BEGIN IMMEDIATE')
    try:
        candidate=next((x for x in task_candidates(db,task_id,allowed_workspaces) if x['key']==key),None)
        if not candidate or not candidate['current']:raise ValueError('current linked candidate required')
        existing=db.execute('SELECT resource_id FROM resource_choices WHERE task_id=? AND resource_key=?',(task_id,key)).fetchone()
        if existing:
            db.commit();return existing[0]
        saved=next((r for r in db.execute('SELECT id,kind,uri FROM resources WHERE task_id=?',(task_id,)) if not scoped_resource(r[1],r[2]) and resource_key(r[1],r[2])==key),None)
        # add_resource manages a transaction; insert the same small existing model here
        # so the confirmed resource and its explicit association commit together.
        import uuid
        rid=saved[0] if saved else str(uuid.uuid4());now=time.time_ns()
        if not saved:
            db.execute('INSERT INTO resources VALUES (?,?,?,?,?,?,?)',(rid,task_id,candidate['kind'],candidate['value'],'unknown',None,now))
            db.execute('INSERT INTO resource_state VALUES (?,1,1)',(rid,))
            db.execute('INSERT INTO resource_history(resource_id,kind,uri,active,revision,saved_at) VALUES (?,?,?,1,1,?)',(rid,candidate['kind'],candidate['value'],now))
        db.execute('INSERT INTO resource_choices VALUES (?,?,?)',(task_id,key,rid));db.commit();return rid
    except Exception:
        db.rollback();raise


def task_git_status(db,task_id,allowed_workspaces):
    allowed={str(Path(w).absolute()) for w in allowed_workspaces}
    rows=db.execute("""SELECT DISTINCT s.workspace,g.error,g.fingerprint FROM contributions c
        JOIN sessions s ON s.id=c.session_id LEFT JOIN contribution_state cs ON cs.contribution_id=c.id
        LEFT JOIN git_observations g ON g.workspace=s.workspace WHERE c.task_id=? AND COALESCE(cs.active,1)=1 ORDER BY s.workspace""",(task_id,))
    return [{'workspace':w,'available':bool(fingerprint and not error),'error':error} for w,error,fingerprint in rows if w in allowed]
