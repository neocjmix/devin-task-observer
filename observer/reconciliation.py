"""M2 task-scoped incremental reconciliation; models propose, this core commits."""
import datetime
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from .devin_local import read_evidence, observed_plan_preview, SourceIncomplete
from .discovery import extract_resources
from .model_adapters import (CodexExecAdapter, DevinAcpAdapter, ModelError, ModelResult,
                             OP_PROPERTIES, prompt_for)
from .model_adapters import ANALYSIS_LOCK

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tracking_state (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), revision INTEGER NOT NULL DEFAULT 0,
 last_success INTEGER, last_error TEXT
);
CREATE TABLE IF NOT EXISTS tracking_cursors (
 task_id TEXT NOT NULL REFERENCES tasks(id), session_id TEXT NOT NULL REFERENCES sessions(id),
 observation_id INTEGER NOT NULL, PRIMARY KEY(task_id,session_id)
);
CREATE TABLE IF NOT EXISTS tracking_processed (
 task_id TEXT NOT NULL, session_id TEXT NOT NULL, message_id TEXT NOT NULL, text_hash TEXT NOT NULL,
 PRIMARY KEY(task_id,session_id,message_id,text_hash)
);
CREATE TABLE IF NOT EXISTS tracking_items (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), item_key TEXT NOT NULL,
 category TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, epistemic TEXT NOT NULL,
 owner TEXT NOT NULL, pinned INTEGER NOT NULL, revision INTEGER NOT NULL,
 evidence TEXT NOT NULL, conflicts TEXT NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(task_id,item_key)
);
CREATE TABLE IF NOT EXISTS tracking_history (
 id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES tracking_items(id),
 run_id TEXT, snapshot TEXT NOT NULL, saved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracking_runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), input_key TEXT NOT NULL,
 adapter TEXT NOT NULL, prompt_version INTEGER NOT NULL, expected_revision INTEGER NOT NULL,
 status TEXT NOT NULL, budget_day TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, usage_source TEXT, provider_run_id TEXT,
 proposal TEXT, diagnostics TEXT, error TEXT, created_at INTEGER NOT NULL, completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS tracking_run_input ON tracking_runs(task_id,input_key);
CREATE TABLE IF NOT EXISTS tracking_pending (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), run_id TEXT NOT NULL REFERENCES tracking_runs(id),
 operation TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL
);
'''

LIMITS = {'input_bytes': 48000, 'output_bytes': 24000, 'timeout_seconds': 120}
PROMPT_VERSION = 1
CATEGORIES = ('progress', 'blocker', 'decision', 'question', 'next_action', 'finding', 'hypothesis')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def validate_policy(policy):
    if policy is None: return None
    if not isinstance(policy, dict): raise ValueError('semantic_policy_invalid')
    allowed = {'enabled', 'environment', 'adapter', 'executable', 'model', 'daily_calls',
               'task_daily_calls', 'daily_reserved_tokens', 'task_daily_reserved_tokens', 'timeout_seconds', 'acp_approved', 'format_repair'}
    if set(policy) - allowed or type(policy.get('enabled', False)) is not bool:
        raise ValueError('semantic_policy_invalid')
    if not policy.get('enabled'): return policy
    if policy.get('environment') not in ('personal', 'internal') or policy.get('adapter') not in ('codex_exec', 'devin_acp'):
        raise ValueError('semantic_policy_invalid')
    if policy['environment'] == 'internal' and policy['adapter'] != 'devin_acp':
        raise ValueError('internal_requires_devin_acp')
    if policy['adapter'] == 'devin_acp' and policy.get('acp_approved') is not True:
        raise ValueError('acp_compatibility_approval_required')
    if not isinstance(policy.get('executable'), str) or not Path(policy['executable']).is_absolute():
        raise ValueError('semantic_executable_required')
    for key, default, maximum in (('daily_calls', 8, 100), ('task_daily_calls', 4, 30),
                                  ('daily_reserved_tokens', 640000, 10000000), ('task_daily_reserved_tokens', 320768, 10000000), ('timeout_seconds', 120, 180)):
        value = policy.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum: raise ValueError('semantic_policy_invalid')
    if 'model' in policy and (not isinstance(policy['model'], str) or not 1 <= len(policy['model']) <= 100):
        raise ValueError('semantic_policy_invalid')
    if 'format_repair' in policy and type(policy['format_repair']) is not bool:raise ValueError('semantic_policy_invalid')
    return policy


def adapter_for(policy):
    validate_policy(policy)
    if not policy or not policy.get('enabled'): raise ModelError('semantic_disabled')
    if policy['adapter'] == 'codex_exec':
        return CodexExecAdapter(policy['executable'], policy.get('model'), policy.get('timeout_seconds', 120))
    return DevinAcpAdapter(policy['executable'], policy.get('timeout_seconds', 120))


def links(db, task, workspaces):
    allowed = {str(w.absolute()) for w in workspaces}
    result = [dict(zip(('session_id', 'workspace', 'title', 'role', 'scope', 'link_revision', 'metadata_revision', 'unavailable'), row))
        for row in db.execute('''SELECT s.id,s.workspace,s.title,c.role,c.scope,COALESCE(cs.revision,1),
        (SELECT MAX(revision) FROM contribution_metadata_history WHERE contribution_id=c.id),COALESCE(h.source_unavailable,0)
        FROM contributions c JOIN sessions s ON s.id=c.session_id
        LEFT JOIN contribution_state cs ON cs.contribution_id=c.id LEFT JOIN session_health h ON h.session_id=s.id
        WHERE c.task_id=? AND COALESCE(cs.active,1)=1 ORDER BY c.id''', (task,)) if row[1] in allowed]
    for link in result:
        link['shared_session'] = db.execute('''SELECT COUNT(*) FROM contributions c LEFT JOIN contribution_state cs ON cs.contribution_id=c.id
            WHERE c.session_id=? AND COALESCE(cs.active,1)=1''', (link['session_id'],)).fetchone()[0] > 1
    return result


def _items(db, task):
    keys = ('id', 'key', 'category', 'content', 'status', 'epistemic', 'owner', 'pinned', 'revision', 'evidence', 'conflicts', 'updated_at')
    result = []
    for row in db.execute('SELECT id,item_key,category,content,status,epistemic,owner,pinned,revision,evidence,conflicts,updated_at FROM tracking_items WHERE task_id=? ORDER BY updated_at,id', (task,)):
        item = dict(zip(keys, row)); item['evidence'] = json.loads(item['evidence']); item['conflicts'] = json.loads(item['conflicts']); item['pinned'] = bool(item['pinned'])
        result.append(item)
    return result


def _context(db, task, workspaces):
    row = db.execute('SELECT title,description,revision FROM tasks WHERE id=?', (task,)).fetchone()
    view = db.execute('SELECT id,revision FROM task_views WHERE task_id=?', (task,)).fetchone()
    if not row or not view: raise ModelError('task_or_view_missing')
    sections = [{'id': r[0], 'title': r[1]} for r in db.execute('SELECT id,title FROM view_sections WHERE view_id=? ORDER BY position', (view[0],))]
    return {'task': {'title': row[0], 'description': row[1], 'revision': row[2]},
            'human_view_revision': view[1], 'sections': sections, 'contributions': links(db, task, workspaces),
            'policy': 'Separate machine projection. Preserve human/pinned items and arbitrary existing View structure. Claims are not verified.'}


def _rows(db, task, workspaces):
    rows = []
    for link in links(db, task, workspaces):
        cursor = db.execute('SELECT observation_id FROM tracking_cursors WHERE task_id=? AND session_id=?', (task, link['session_id'])).fetchone()
        values = db.execute('''SELECT o.id,o.session_id,o.node_id,o.transition,o.message_id,o.role,o.content_hash,k.kinds
            FROM observations o LEFT JOIN node_kinds k ON k.session_id=o.session_id AND k.node_id=o.node_id AND k.content_hash=o.content_hash
            WHERE o.session_id=? AND o.id>? ORDER BY o.id LIMIT 81''', (link['session_id'], cursor[0] if cursor else 0)).fetchall()
        rows.extend((r, link) for r in values)
    return sorted(rows, key=lambda x: x[0][0])


def _text(value, kinds):
    # Only observed content fields. User records can still include injected context.
    if 'user' in kinds:
        metadata = value.get('metadata')
        extensions = metadata.get('extensions') if isinstance(metadata, dict) else None
        blocks = extensions.get('chisel/acp-content-blocks') if isinstance(extensions, dict) else None
        if blocks is not None:
            if not isinstance(blocks, list) or any(not isinstance(b, dict) or b.get('type') not in ('text', 'resource') or (b['type']=='text' and not isinstance(b.get('text'), str)) for b in blocks):
                raise ModelError('semantic_unsupported_user_blocks')
            # Observed first-input text blocks exclude injected project/session resources.
            return '\n'.join(b['text'] for b in blocks if b['type']=='text').strip()
    content = value.get('content')
    if not isinstance(content, str): return ''
    return content.strip() if any(k in kinds for k in ('assistant', 'user', 'tool_result')) else ''


def _terms(text):
    return set(re.findall(r'[\w-]{2,}', text.lower()))


def prepare(db, source, task, workspaces):
    context = _context(db, task, workspaces)
    revision = db.execute('SELECT revision FROM tracking_state WHERE task_id=?', (task,)).fetchone()
    bundle = {'task_id': task, 'view_revision': revision[0] if revision else 0, 'view_contract': context,
              'affected_items': [], 'human_context': [], 'evidence_delta': [], 'exclusions': [], 'limits': dict(LIMITS)}
    cursors = {}; processed = []; seen = set(); total = 0; consumed = 0; rows = _rows(db, task, workspaces)
    for row, link in rows:
        oid, sid, node, transition, mid, role, content_hash, raw_kinds = row
        kinds = json.loads(raw_kinds) if raw_kinds else ['unknown']
        if len(cursors) > 32 or sum(1 for _ in processed) >= 40: break
        relevant = any(k in kinds for k in ('assistant', 'user', 'tool_result', 'plan'))
        if not relevant:
            reason = 'unsupported_source_kind' if role!='system' and 'unknown' in kinds else 'no_recognized_natural_language'
            cursors[sid] = oid; consumed += 1; bundle['exclusions'].append({'observation_id': oid, 'reason': reason}); continue
        if link['unavailable']: raise ModelError('source_unavailable')
        current = db.execute('SELECT content_hash,present FROM source_nodes WHERE session_id=? AND node_id=?', (sid, node)).fetchone()
        if transition == 'missing' or not current or current != (content_hash, 1):
            raise ModelError('semantic_source_gap')
        try: value = read_evidence(source, Path(link['workspace']), sid, node, content_hash)
        except SourceIncomplete as error: raise ModelError('semantic_source_gap') from error
        text = _text(value, kinds)
        plan = observed_plan_preview(value)
        if plan['plans']:
            text += '\nObserved structured plan (agent report only): ' + encoded(plan['plans'])
        if len(text.encode()) > 16000: raise ModelError('semantic_event_too_large')
        thash = digest([role, text]); identity = (sid, mid, thash)
        already = db.execute('SELECT 1 FROM tracking_processed WHERE task_id=? AND session_id=? AND message_id=? AND text_hash=?', (task, *identity)).fetchone()
        if text and not already and identity not in seen:
            if total + len(text.encode()) > 24000:
                if bundle['evidence_delta']: break
                raise ModelError('semantic_input_limit')
            shared = db.execute('''SELECT COUNT(*) FROM contributions c LEFT JOIN contribution_state cs ON cs.contribution_id=c.id
                                  WHERE c.session_id=? AND COALESCE(cs.active,1)=1''', (sid,)).fetchone()[0] > 1
            bundle['evidence_delta'].append({'id': 'obs:' + str(oid), 'observation_id': oid, 'session_id': sid,
                'session_title': link['title'], 'node_id': node, 'content_hash': content_hash, 'message_id': mid,
                'role': role, 'text': text, 'transition': transition, 'shared_session': shared, 'scope': link['scope'],
                'resource_mentions': extract_resources(text), 'structured_plan': plan,
                'ordering': 'collector sequence only; no global conversation chronology', 'verification': 'reported_source_content'})
            total += len(text.encode()); seen.add(identity)
        else:
            bundle['exclusions'].append({'observation_id': oid, 'reason': 'duplicate_interpretation' if already or identity in seen else 'empty_content'})
        processed.append(identity); cursors[sid] = oid; consumed += 1
    terms = _terms('\n'.join(e['text'] for e in bundle['evidence_delta']))
    sessions = {e['session_id'] for e in bundle['evidence_delta']}
    candidates = []
    scoped_sessions = {r[0] for r in db.execute('SELECT id,workspace FROM sessions') if r[1] in {str(w.absolute()) for w in workspaces}}
    for item in _items(db, task):
        if any(e['session_id'] not in scoped_sessions for e in item['evidence']): continue
        if any(e['session_id'] not in {l['session_id'] for l in context['contributions']} for e in item['evidence']): continue
        score = len(terms & _terms(item['content'])) + int(any(e['session_id'] in sessions for e in item['evidence'])) + int(item['status'] in ('open', 'disputed'))
        if score: candidates.append((score, item))
    for _, item in sorted(candidates, key=lambda x: (-x[0], x[1]['id']))[:24]:
        item = dict(item); item.pop('updated_at', None)
        if len(encoded(bundle['affected_items'] + [item]).encode()) > 10000: break
        bundle['affected_items'].append(item)
    human_rows = db.execute('''SELECT i.id,i.content,i.status,i.pinned FROM view_items i JOIN view_sections s ON s.id=i.section_id
                              JOIN task_views v ON v.id=s.view_id WHERE v.task_id=? ORDER BY i.id''', (task,)).fetchall()
    for iid, content, status, pinned in human_rows:
        if _terms(content) & terms:
            human = {'id': iid, 'content': content, 'status': status, 'pinned': bool(pinned), 'owner': 'human'}
            if len(encoded(bundle['human_context'] + [human]).encode()) > 4000: break
            bundle['human_context'].append(human)
    prompt_for(bundle)  # Validate the final envelope, not just selected source text.
    return bundle, cursors, processed, digest(context), len(rows) > consumed


def _evidence(e):
    return {key: e[key] for key in ('id', 'session_id', 'node_id', 'content_hash')}


def _follows(db, old_refs, new_refs):
    """At least one provable within-session continuation, never cross-session time."""
    for new in new_refs:
        for old in old_refs:
            if new['session_id'] != old['session_id']: continue
            sid = new['session_id']
            old_node = db.execute('SELECT message_id FROM source_nodes WHERE session_id=? AND node_id=? AND content_hash=? AND present=1', (sid, old['node_id'], old['content_hash'])).fetchone()
            if not old_node: continue
            equivalent = {r[0] for r in db.execute('''SELECT n.node_id FROM source_nodes n JOIN node_equivalence e ON e.session_id=n.session_id AND e.node_id=n.node_id AND e.content_hash=n.content_hash
                WHERE n.session_id=? AND n.message_id=? AND n.present=1 AND e.identity_hash IN
                (SELECT identity_hash FROM node_equivalence WHERE session_id=? AND node_id=? AND content_hash=?)''', (sid, old_node[0], sid, old['node_id'], old['content_hash']))}
            equivalent.add(old['node_id']); cursor = new['node_id']; seen = set()
            while cursor is not None and cursor not in seen:
                if cursor in equivalent and cursor != new['node_id']: return True
                seen.add(cursor)
                parent = db.execute('SELECT parent_id FROM source_nodes WHERE session_id=? AND node_id=? AND present=1', (sid, cursor)).fetchone()
                cursor = parent[0] if parent else None
    return False


def _validate(proposal, bundle):
    if not isinstance(proposal, dict) or set(proposal) != {'version', 'task_id', 'view_revision', 'operations'}:
        raise ModelError('invalid_patch')
    if type(proposal['version']) is not int or proposal['version'] != 1 or proposal['task_id'] != bundle['task_id'] or type(proposal['view_revision']) is not int or proposal['view_revision'] != bundle['view_revision']:
        raise ModelError('patch_version_or_revision')
    ops = proposal['operations']
    if not isinstance(ops, list) or len(ops) > 24: raise ModelError('patch_operation_limit')
    if len(encoded(proposal).encode()) > LIMITS['output_bytes']: raise ModelError('patch_output_limit')
    items = {i['id']: i for i in bundle['affected_items']}
    new_keys = {o.get('key') for o in ops if isinstance(o,dict) and o.get('op')=='add' and isinstance(o.get('key'),str)}
    evidence = {e['id']: _evidence(e) for e in bundle['evidence_delta']}
    delta_ids = set(evidence)
    for item in bundle['affected_items']:
        for e in item['evidence']: evidence[e['id']] = e
    ids = set(); keys = set()
    for op in ops:
        if not isinstance(op, dict) or set(op) != set(OP_PROPERTIES): raise ModelError('invalid_operation')
        if any(not isinstance(op[k], str) for k in ('id', 'op', 'key', 'category', 'content', 'status', 'epistemic')):
            raise ModelError('invalid_operation')
        if not 1 <= len(op['id']) <= 100 or op['id'] in ids: raise ModelError('duplicate_operation')
        ids.add(op['id'])
        if not 1 <= len(op['key']) <= 160 or len(op['content']) > 1800: raise ModelError('operation_size')
        if op['op'] not in OP_PROPERTIES['op']['enum'] or op['category'] not in CATEGORIES or op['status'] not in OP_PROPERTIES['status']['enum'] or op['epistemic'] not in ('reported', 'hypothesis'):
            raise ModelError('unsupported_operation')
        if not isinstance(op['evidence_ids'], list) or not 1 <= len(op['evidence_ids']) <= 32 or any(not isinstance(e, str) or e not in evidence for e in op['evidence_ids']) or not delta_ids.intersection(op['evidence_ids']):
            raise ModelError('invalid_patch_evidence')
        if not isinstance(op['conflicts_with'], list) or len(op['conflicts_with']) > 24 or any(not isinstance(x, str) for x in op['conflicts_with']):
            raise ModelError('invalid_conflict')
        if op['op'] == 'add':
            if op['target_id'] is not None or not op['content'].strip() or op['key'] in keys or op['status'] == 'superseded': raise ModelError('invalid_add')
            keys.add(op['key'])
        elif op['op'] != 'propose_section':
            if not isinstance(op['target_id'],str):raise ModelError('invalid_patch_target')
            if op['op']=='flag_conflict' and op['target_id'] in new_keys:
                if op['key']!=op['target_id']:raise ModelError('invalid_patch_target')
            else:
                if not isinstance(op['target_id'], str) or op['target_id'] not in items: raise ModelError('invalid_patch_target')
                target = items[op['target_id']]
                if target['owner'] != 'machine' or target['pinned']: raise ModelError('protected_item')
                if op['key'] != target['key']: raise ModelError('invalid_patch_target')
        elif op['target_id'] is not None or not op['content'].strip(): raise ModelError('invalid_section_proposal')
    references = set(items) | keys | {i['id'] for i in bundle['human_context']}
    for op in ops:
        if any(ref not in references for ref in op['conflicts_with']): raise ModelError('invalid_conflict_target')
        if op['op'] == 'flag_conflict' and not op['conflicts_with']: raise ModelError('invalid_conflict')
    return ops, evidence


def _history(db, item_id, run_id):
    task = db.execute('SELECT task_id FROM tracking_items WHERE id=?', (item_id,)).fetchone()[0]
    item = next(i for i in _items(db, task) if i['id'] == item_id)
    db.execute('INSERT INTO tracking_history(item_id,run_id,snapshot,saved_at) VALUES (?,?,?,?)', (item_id, run_id, encoded(item), time.time_ns()))


def _pending(db, task, run_id, op, evidence):
    pid = digest([task, run_id, op['id']])
    saved = {**op, 'evidence_refs': [evidence[e] for e in op['evidence_ids']]}
    db.execute('INSERT OR IGNORE INTO tracking_pending VALUES (?,?,?,?,?,?)', (pid, task, run_id, encoded(saved), 'pending', time.time_ns()))


def _apply(db, task, run_id, ops, evidence):
    aliases = {i['id']: i['id'] for i in _items(db, task)}
    deferred = set()
    # Add first so conflicts can refer to new semantic keys in the same patch.
    for op in ops:
        if op['op'] != 'add': continue
        refs = [evidence[e] for e in dict.fromkeys(op['evidence_ids'])]
        old = next((i for i in _items(db, task) if i['key'] == op['key'] or (i['category'] == op['category'] and ' '.join(i['content'].split()).casefold() == ' '.join(op['content'].split()).casefold())), None)
        if old:
            aliases[op['key']] = old['id']
            if old['content'] != op['content'] or old['category'] != op['category'] or old['status'] != op['status'] or old['owner'] != 'machine' or old['pinned']:
                _pending(db, task, run_id, op, evidence); deferred.add(op['id']); continue
            combined = {encoded(e): e for e in old['evidence'] + refs}
            if len(combined) != len(old['evidence']):
                db.execute('UPDATE tracking_items SET evidence=?,revision=revision+1,updated_at=? WHERE id=?', (encoded(list(combined.values())), time.time_ns(), old['id']))
                _history(db, old['id'], run_id)
            continue
        iid = str(uuid.uuid4()); aliases[op['key']] = iid; now = time.time_ns()
        db.execute('INSERT INTO tracking_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (iid, task, op['key'], op['category'], op['content'].strip(), op['status'], op['epistemic'], 'machine', 0, 1, encoded(refs), '[]', now))
        _history(db, iid, run_id)
    for op in ops:
        if op['id'] in deferred: continue
        kind = op['op']
        if kind == 'propose_section':
            _pending(db, task, run_id, op, evidence); continue
        iid = aliases.get(op['key']) if kind == 'add' else aliases.get(op['target_id'], op['target_id'])
        item = next((i for i in _items(db, task) if i['id'] == iid), None)
        if not item: raise ModelError('invalid_patch_target')
        if kind != 'add':
            if item['owner']!='machine' or item['pinned']:raise ModelError('protected_item')
            refs = [evidence[e] for e in op['evidence_ids']]
            if kind == 'supersede':
                if (item['conflicts'] or not _follows(db, item['evidence'], refs) or
                    not {e['session_id'] for e in item['evidence']} <= {e['session_id'] for e in refs}):
                    _pending(db, task, run_id, op, evidence); continue
                db.execute('UPDATE tracking_items SET status=\'superseded\' WHERE id=?', (iid,))
            if kind == 'update_status':
                if not _follows(db, item['evidence'], refs) or op['status'] == 'superseded' or item['conflicts']:
                    _pending(db, task, run_id, op, evidence); continue
                db.execute('UPDATE tracking_items SET status=? WHERE id=?', (op['status'], iid))
            combined = {encoded(e): e for e in item['evidence'] + refs}
            db.execute('UPDATE tracking_items SET evidence=?,revision=revision+1,updated_at=? WHERE id=?', (encoded(list(combined.values())), time.time_ns(), iid))
            _history(db, iid, run_id)
        for ref in op['conflicts_with']:
            other_id = aliases.get(ref, ref)
            if other_id == iid: raise ModelError('invalid_conflict_target')
            for target, other in ((iid, other_id), (other_id, iid)):
                row = db.execute('SELECT conflicts,owner,pinned FROM tracking_items WHERE id=? AND task_id=?', (target, task)).fetchone()
                if not row:
                    human = db.execute('''SELECT 1 FROM view_items i JOIN view_sections s ON s.id=i.section_id JOIN task_views v ON v.id=s.view_id
                        WHERE i.id=? AND v.task_id=?''', (target, task)).fetchone()
                    if human: continue  # Attach the machine side only. Human content is immutable here.
                    raise ModelError('invalid_conflict_target')
                if row[1] != 'machine' or row[2]:
                    _pending(db, task, run_id, op, evidence); continue
                conflicts = sorted(set(json.loads(row[0]) + [other]))
                db.execute('UPDATE tracking_items SET conflicts=?,revision=revision+1,updated_at=? WHERE id=?', (encoded(conflicts), time.time_ns(), target))
                _history(db, target, run_id)


def _refresh_once(db, source, store, task, workspaces, policy, *, adapter=None, retry=False, cancel=None, repair_feedback=None):
    validate_policy(policy)
    if not policy or not policy.get('enabled'): raise ModelError('semantic_disabled')
    fd = os.open(str(Path(store).resolve()) + '.tracking.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ModelError('semantic_busy') from None
        with db:
            db.execute('UPDATE tracking_runs SET status=\'interrupted\',error=\'interrupted\' WHERE status=\'running\'')
            db.execute('INSERT OR IGNORE INTO tracking_state(task_id) VALUES (?)', (task,))
        try: bundle, cursors, processed, context_hash, more = prepare(db, source, task, workspaces)
        except (ModelError, ValueError) as error:
            with db: db.execute('UPDATE tracking_state SET last_error=? WHERE task_id=?', (str(error), task))
            raise
        if not cursors: return {'status': 'unchanged', 'model_called': False}
        model = adapter or adapter_for(policy)
        key = digest([bundle, model.identity, PROMPT_VERSION])
        if repair_feedback:
            bundle['repair_feedback']={'rejected_error':repair_feedback,'instruction':'Prior output was REJECTED, nothing applied. Return a fresh complete patch. If there are no writable machine targets, use add with target_id null only. Human context is read-only. To flag conflict with human content, ADD a new evidence-backed machine claim whose conflicts_with includes the human id; never target the human item. Do not omit implementation progress reports from the other session.'}
            prompt_for(bundle)
        prior = db.execute('SELECT status FROM tracking_runs WHERE task_id=? AND input_key=? ORDER BY created_at', (task, key)).fetchall()
        if prior and not retry: raise ModelError('explicit_retry_required')
        if len(prior) >= 3: raise ModelError('retry_limit')
        run_id = str(uuid.uuid4()); day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        model_called = bool(bundle['evidence_delta']); reserve = LIMITS['input_bytes'] + LIMITS['output_bytes'] + 8192 if model_called else 0
        db.execute('BEGIN IMMEDIATE')
        try:
            if model_called:
                calls, tokens = db.execute('SELECT COUNT(*),COALESCE(SUM(MAX(reserved_tokens,COALESCE(input_tokens,0)+COALESCE(output_tokens,0))),0) FROM tracking_runs WHERE budget_day=? AND reserved_tokens>0', (day,)).fetchone()
                task_calls = db.execute('SELECT COUNT(*) FROM tracking_runs WHERE budget_day=? AND task_id=? AND reserved_tokens>0', (day, task)).fetchone()[0]
                task_tokens = db.execute('SELECT COALESCE(SUM(MAX(reserved_tokens,COALESCE(input_tokens,0)+COALESCE(output_tokens,0))),0) FROM tracking_runs WHERE budget_day=? AND task_id=?', (day, task)).fetchone()[0]
                if calls >= policy.get('daily_calls', 8) or task_calls >= policy.get('task_daily_calls', 4) or tokens + reserve > policy.get('daily_reserved_tokens', 640000) or task_tokens + reserve > policy.get('task_daily_reserved_tokens', 320768):
                    raise ModelError('semantic_budget_exhausted')
            db.execute('INSERT INTO tracking_runs(id,task_id,input_key,adapter,prompt_version,expected_revision,status,budget_day,reserved_tokens,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (run_id, task, key, model.identity, PROMPT_VERSION, bundle['view_revision'], 'running', day, reserve, time.time_ns()))
            db.commit()
        except Exception:
            db.rollback()
            with db: db.execute('UPDATE tracking_state SET last_error=? WHERE task_id=?', ('semantic_budget_exhausted', task))
            raise
        try:
            if model_called:
                lock_token = ANALYSIS_LOCK.set(fd)
                try: result = model.analyze(bundle, cancel)
                finally: ANALYSIS_LOCK.reset(lock_token)
            else: result = ModelResult({'version': 1, 'task_id': task, 'view_revision': bundle['view_revision'], 'operations': []}, {'input_tokens': 0, 'output_tokens': 0, 'source': 'no_model_call'}, None, {'reason': 'no_new_natural_language'})
            if not isinstance(result, ModelResult): raise ModelError('invalid_adapter_result')
            if (not isinstance(result.usage, dict) or any(v is not None and (type(v) is not int or not 0 <= v <= 100000000) for v in (result.usage.get('input_tokens'), result.usage.get('output_tokens')))
                or not isinstance(result.usage.get('source'), str) or len(result.usage['source']) > 100
                or (result.provider_run_id is not None and (not isinstance(result.provider_run_id, str) or len(result.provider_run_id) > 512))
                or not isinstance(result.diagnostics, dict) or len(encoded(result.diagnostics)) > 2000):
                raise ModelError('invalid_adapter_usage')
            if len(encoded(result.proposal).encode()) > LIMITS['output_bytes']: raise ModelError('patch_output_limit')
            # Retain bounded rejected proposals and measurable usage for diagnosis/accounting.
            with db:
                diagnostics = {**result.diagnostics, 'coverage_exclusions': bundle['exclusions'], 'format_repair':bool(repair_feedback)}
                db.execute('UPDATE tracking_runs SET proposal=?,input_tokens=?,output_tokens=?,usage_source=?,provider_run_id=?,diagnostics=? WHERE id=?', (encoded(result.proposal), result.usage.get('input_tokens'), result.usage.get('output_tokens'), result.usage['source'], result.provider_run_id, encoded(diagnostics), run_id))
            ops, evidence = _validate(result.proposal, bundle)
            # Re-read exact sources before any effect; output JSON alone never proves truth.
            for e in bundle['evidence_delta']:
                workspace = next(l['workspace'] for l in bundle['view_contract']['contributions'] if l['session_id'] == e['session_id'])
                try: read_evidence(source, Path(workspace), e['session_id'], e['node_id'], e['content_hash'])
                except SourceIncomplete as error: raise ModelError('evidence_changed_during_analysis') from error
            db.execute('BEGIN IMMEDIATE')
            revision = db.execute('SELECT revision FROM tracking_state WHERE task_id=?', (task,)).fetchone()[0]
            if revision != bundle['view_revision'] or digest(_context(db, task, workspaces)) != context_hash:
                raise ModelError('semantic_revision_conflict')
            _apply(db, task, run_id, ops, evidence)
            for sid, oid in cursors.items():
                db.execute('INSERT INTO tracking_cursors VALUES (?,?,?) ON CONFLICT(task_id,session_id) DO UPDATE SET observation_id=excluded.observation_id', (task, sid, oid))
            for identity in processed: db.execute('INSERT OR IGNORE INTO tracking_processed VALUES (?,?,?,?)', (task, *identity))
            now = time.time_ns()
            db.execute('UPDATE tracking_state SET revision=revision+1,last_success=?,last_error=NULL WHERE task_id=?', (now, task))
            db.execute('UPDATE tracking_runs SET status=?,completed_at=? WHERE id=?', ('applied' if ops else 'no_change', now, run_id))
            db.commit()
            return {'status': 'applied' if ops else 'no_change', 'model_called': model_called, 'run_id': run_id, 'operations': len(ops), 'usage': result.usage, 'more_pending': more}
        except Exception as error:
            db.rollback()
            code = str(error) if isinstance(error, ModelError) else 'semantic_execution_failed'
            with db:
                db.execute('UPDATE tracking_runs SET status=\'failed\',error=?,completed_at=? WHERE id=?', (code, time.time_ns(), run_id))
                usage = getattr(error, 'usage', None)
                if isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens')):
                    db.execute('UPDATE tracking_runs SET input_tokens=?,output_tokens=?,usage_source=?,provider_run_id=? WHERE id=?', (usage['input_tokens'],usage['output_tokens'],usage.get('source','provider_failure'),getattr(error,'provider_run_id',None),run_id))
                else:
                    db.execute("UPDATE tracking_runs SET usage_source=COALESCE(usage_source,'unavailable_failure') WHERE id=?", (run_id,))
                db.execute('UPDATE tracking_state SET last_error=? WHERE task_id=?', (code, task))
            failure=ModelError(code);failure.run_id=run_id
            raise failure from error
    finally:
        os.close(fd)


def refresh(db, source, store, task, workspaces, policy, *, adapter=None, retry=False, cancel=None):
    """One manual request; at most one budgeted schema/permission-format repair."""
    try:return _refresh_once(db,source,store,task,workspaces,policy,adapter=adapter,retry=retry,cancel=cancel)
    except ModelError as error:
        repairable={'invalid_patch','patch_version_or_revision','invalid_operation','invalid_json',
                    'duplicate_operation','invalid_add','invalid_patch_target','invalid_patch_evidence','protected_item','invalid_conflict','invalid_conflict_target'}
        if str(error) not in repairable or not policy or not policy.get('format_repair',True):raise
        first_run=getattr(error,'run_id',None)
        if first_run is None:raise
        result=_refresh_once(db,source,store,task,workspaces,policy,adapter=adapter,retry=True,cancel=cancel,repair_feedback=str(error))
        result['format_repair_attempted']=True;result['previous_run_id']=first_run;result['model_called']=True
        row=db.execute('SELECT input_tokens,output_tokens,usage_source FROM tracking_runs WHERE id=?',(first_run,)).fetchone()
        result['attempt_usage']=[{'input_tokens':row[0],'output_tokens':row[1],'source':row[2]},result.get('usage')] if row else [result.get('usage')]
        if row and result.get('usage'):
            if all(type(v) is int for v in (row[0],row[1],result['usage'].get('input_tokens'),result['usage'].get('output_tokens'))):
                result['usage']={'input_tokens':row[0]+result['usage']['input_tokens'],'output_tokens':row[1]+result['usage']['output_tokens'],'source':'summed_format_repair_attempts'}
            else:result['usage']={'input_tokens':None,'output_tokens':None,'source':'some_attempt_usage_unavailable'}
        elif row:result['usage']={'input_tokens':row[0],'output_tokens':row[1],'source':row[2] or 'unavailable'}
        return result


def projection(db, task, workspaces, policy=None):
    linked = links(db, task, workspaces); allowed = {l['session_id'] for l in linked}
    state = db.execute('SELECT revision,last_success,last_error FROM tracking_state WHERE task_id=?', (task,)).fetchone()
    scoped_sessions = {r[0] for r in db.execute('SELECT id,workspace FROM sessions') if r[1] in {str(w.absolute()) for w in workspaces}}
    items = [i for i in _items(db, task) if all(e['session_id'] in scoped_sessions for e in i['evidence'])]
    for item in items:
        item['attribution_uncertain'] = any(l['shared_session'] and any(e['session_id']==l['session_id'] for e in item['evidence']) for l in linked)
        for e in item['evidence']:
            title = db.execute('SELECT title FROM sessions WHERE id=?', (e['session_id'],)).fetchone()
            e['session_title'] = title[0] if title else e['session_id']
            e['current'] = e['session_id'] in allowed and db.execute('SELECT 1 FROM source_nodes WHERE session_id=? AND node_id=? AND content_hash=? AND present=1', (e['session_id'], e['node_id'], e['content_hash'])).fetchone() is not None
        item['source_current'] = bool(item['evidence']) and all(e['current'] for e in item['evidence'])
        item['source_state'] = 'current' if item['source_current'] else 'disputed_source'
    rows = _rows(db, task, workspaces); pending = len(rows)
    runs = [dict(zip(('id', 'status', 'reserved_tokens', 'input_tokens', 'output_tokens', 'usage_source', 'error', 'created_at'), r)) for r in db.execute('SELECT id,status,reserved_tokens,input_tokens,output_tokens,usage_source,error,created_at FROM tracking_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 10', (task,))]
    proposals = [dict(zip(('id', 'operation', 'status'), (r[0], json.loads(r[1]), r[2]))) for r in db.execute('SELECT id,operation,status FROM tracking_pending WHERE task_id=? ORDER BY created_at', (task,))
                 if all(e['session_id'] in scoped_sessions for e in json.loads(r[1]).get('evidence_refs', []))]
    enabled = bool(policy and policy.get('enabled'))
    unsupported = sum(sum(e.get('reason')=='unsupported_source_kind' for e in json.loads(r[0] or '{}').get('coverage_exclusions', []))
                      for r in db.execute("SELECT diagnostics FROM tracking_runs WHERE task_id=? AND status IN ('applied','no_change')", (task,)))
    gap = bool(pending or any(not i['source_current'] for i in items) or any(l['unavailable'] for l in linked) or (state and state[2]))
    return {'enabled': enabled, 'adapter': policy.get('adapter') if policy else None, 'environment': policy.get('environment') if policy else None, 'revision': state[0] if state else 0,
            'last_success': state[1] if state else None, 'last_error': state[2] if state else None,
            'freshness': 'pending' if gap else 'partial' if unsupported else 'current' if state and state[1] else 'never_analyzed',
            'unsupported_observations': unsupported,
            'pending_observations': pending, 'pending_count_capped': pending >= 81,
            'items': items, 'proposals': proposals, 'runs': runs,
            'notice': 'Model interpretations, not independently verified facts. Semantic refresh is manual; viewing uses zero tokens.'}


def correct_item(db, task, iid, expected_revision, *, content=None, pinned=None, status=None):
    if type(expected_revision) is not int or (content is not None and (not isinstance(content, str) or not content.strip() or len(content) > 1800)) or (pinned is not None and type(pinned) is not bool) or (status is not None and status not in ('open', 'resolved', 'disputed', 'superseded')):
        raise ModelError('invalid_correction')
    db.execute('BEGIN IMMEDIATE')
    try:
        item = next((i for i in _items(db, task) if i['id'] == iid), None)
        state = db.execute('SELECT revision FROM tracking_state WHERE task_id=?', (task,)).fetchone()
        if not item or not state or state[0] != expected_revision: raise ModelError('semantic_revision_conflict')
        db.execute('UPDATE tracking_items SET content=?,status=?,pinned=?,owner=\'human\',revision=revision+1,updated_at=? WHERE id=?', (item['content'] if content is None else content.strip(), item['status'] if status is None else status, int(item['pinned'] if pinned is None else pinned), time.time_ns(), iid))
        _history(db, iid, None)
        db.execute('UPDATE tracking_state SET revision=revision+1 WHERE task_id=?', (task,)); db.commit()
    except Exception:
        db.rollback(); raise


def resolve_pending(db, task, pid, expected_revision, approve, source=None, workspaces=()):
    if type(approve) is not bool or type(expected_revision) is not int: raise ModelError('invalid_approval')
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT operation,status FROM tracking_pending WHERE id=? AND task_id=?', (pid, task)).fetchone()
        state = db.execute('SELECT revision FROM tracking_state WHERE task_id=?', (task,)).fetchone()
        if not row or row[1] != 'pending' or not state or state[0] != expected_revision: raise ModelError('semantic_revision_conflict')
        op = json.loads(row[0])
        if approve:
            current_links = {l['session_id']: l for l in links(db, task, workspaces)}
            for e in op['evidence_refs']:
                if source is None or e['session_id'] not in current_links: raise ModelError('approval_evidence_unavailable')
                try: read_evidence(source, Path(current_links[e['session_id']]['workspace']), e['session_id'], e['node_id'], e['content_hash'])
                except SourceIncomplete as error: raise ModelError('approval_evidence_unavailable') from error
            if op['op'] == 'propose_section':
                vid = db.execute('SELECT id FROM task_views WHERE task_id=?', (task,)).fetchone()[0]
                position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM view_sections WHERE view_id=?', (vid,)).fetchone()[0]
                db.execute('INSERT INTO view_sections VALUES (?,?,?,?)', (str(uuid.uuid4()), vid, op['content'], position))
                db.execute('UPDATE task_views SET revision=revision+1 WHERE id=?', (vid,))
            elif op['op'] == 'supersede':
                item = next((i for i in _items(db, task) if i['id'] == op['target_id']), None)
                if not item or item['owner'] != 'machine' or item['pinned']: raise ModelError('protected_item')
                db.execute('UPDATE tracking_items SET status=\'superseded\',owner=\'human\',revision=revision+1,updated_at=? WHERE id=?', (time.time_ns(), item['id']))
                _history(db, item['id'], None)
            else: raise ModelError('explicit_item_correction_required')
        db.execute('UPDATE tracking_pending SET status=? WHERE id=?', ('approved' if approve else 'rejected', pid))
        db.execute('UPDATE tracking_state SET revision=revision+1 WHERE task_id=?', (task,)); db.commit()
    except Exception:
        db.rollback(); raise
