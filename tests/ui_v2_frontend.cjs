'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ui = require('../observer/ui_v2/app.js');

assert.equal(ui.milliseconds('1793447700000000001'), 1793447700000);
assert.equal(ui.milliseconds(null), null);
assert.equal(ui.milliseconds('bad date'), null);
assert.equal(ui.time('2026-10-31T00:00:00Z', Date.parse('2026-10-31T00:02:00Z')).relative, '2분 전');
assert.equal(ui.valueText({x: '<script>'}), '{\n  "x": "<script>"\n}');
const projection = {projection_version: 1, capabilities: {}, tasks: {
  a: {task_id: 'a', metadata: {title: '같은 제목'}, health: 'ok', display_origin: 'current', item_ids: ['old', 'latest', 'archived']},
  b: {task_id: 'b', metadata: {title: '같은 제목'}, health: 'invalid', display_origin: 'last_good', item_ids: [], errors: ['bad JSON']},
}, items: {a: {
  old: {item_id: 'old', title: '블로커', value: 'older', updated_at: '2026-10-30T00:00:00Z', updated_by: {kind: 'session', session_id: 's'}},
  latest: {item_id: 'latest', title: '자유 항목', value: 'latest', updated_at: '2026-10-31T00:00:00Z', updated_by: {kind: 'human'}},
  archived: {item_id: 'archived', title: 'archived', value: 'ignored', archived: true, updated_at: '2026-11-01T00:00:00Z'},
}}, sessions: {s: {observation: {title: 'Session A'}}}, health: {runtime: {running: true, collector: {last_success: '1793447700000000001'}}, state_errors: []}};
const before = JSON.stringify(projection);
const rows = ui.taskRows(projection);
assert.equal(rows.length, 2);
assert.equal(rows[0].summary, '자유 항목 · latest');
assert.equal(rows[0].source, '사용자');
assert.equal(ui.actorName({kind: 'session', session_id: 's'}, projection), 'Session A');
assert.equal(ui.filterRows(rows, '같은 제목', 'all').length, 2);
assert.equal(ui.filterRows(rows, '', 'errors').length, 1);
assert.equal(ui.collectionHealth(projection).label, '기록 확인 필요');
assert.equal(ui.collectionHealth(projection).lastSuccess, '1793447700000000001');
projection.health.runtime.collector.last_error = 'source offline';
assert.equal(ui.collectionHealth(projection).label, '수집 실패');
delete projection.health.runtime.collector.last_error;
assert.equal(JSON.stringify(projection), before);
assert.equal(ui.collectionHealth({health: {runtime: {running: false}}, tasks: {}}).label, '관측 중지');
assert.equal(ui.collectionHealth({health: {}, tasks: {}}).label, '수집 기록 없음');
const code = fs.readFileSync(path.join(__dirname, '../observer/ui_v2/app.js'), 'utf8');
assert.ok(!/\.innerHTML\s*=|eval\(|fetch\(/.test(code));
assert.ok(!code.includes('devin://'));
assert.deepEqual(ui.roleGroups(projection, 'a'), []);
assert.equal(ui.taskRows(projection)[0].attention, false);
projection.items.a.old.role = 'intervention';
assert.equal(ui.taskRows(projection)[0].attention, false);
projection.items.a.old.attention_required = true;
assert.equal(ui.taskRows(projection)[0].summary, '블로커 · older');
assert.equal(ui.filterRows(ui.taskRows(projection), '', 'attention').length, 1);
assert.equal(ui.roleGroups(projection, 'a')[0].role, 'intervention');
const historyOld = {revision: 1, title: 'T', items: [{item_id: 'x', title: 'Value', value: 'old', pinned: true}], notes: []};
const historyNew = {revision: 2, title: 'T', items: [{item_id: 'x', title: 'Value', value: 'new', pinned: true}], notes: [{note_id: 'n', text: 'human note'}]};
assert.deepEqual(ui.historyChanges(historyNew, historyOld).map(change => [change.field, change.before, change.after]), [['value', 'old', 'new'], ['추가', null, 'human note']]);
assert.equal(ui.historyChanges(historyOld, null), null);
assert.match(ui.readDescription({display_origin: 'last_good', read_state: {status: 'unavailable'}}, {}), /마지막 정상 기록/);
assert.ok(!ui.readDescription({display_origin: 'last_good', read_state: {status: 'unavailable'}}, {}).includes('확인 위치를 읽지 못했습니다'));
assert.match(ui.readDescription({display_origin: 'unavailable', read_state: {status: 'unavailable'}}, {}), /태스크 기록을 읽을 수 없어/);
assert.match(ui.readDescription({display_origin: 'current', read_state: {status: 'unavailable'}}, {read_cursor_error: {code: 'invalid_read_cursors'}}), /확인 위치를 읽지 못했습니다/);
const candidate = {candidate_id: 'candidate-one', kind: 'pr', value: 'https://github.com/example/demo/pull/42', sources: [
  {session_id: 'session-a', source_ref: 'mentions-v2', node_id: 1, source_hash: 'a'.repeat(64), workspace: '/synthetic', verification: 'mentioned'},
  {session_id: 'session-a', source_ref: 'mentions-v2', node_id: 2, source_hash: 'b'.repeat(64), workspace: '/synthetic', verification: 'mentioned'}
]};
const candidateResource = ui.candidateDraft(candidate, 'resource-core');
assert.equal(candidateResource.kind, 'pr');
assert.equal(candidateResource.evidence.length, 2);
assert.equal(JSON.parse(candidateResource.evidence[1].reference).node_id, 2);
assert.equal(ui.candidateDraft({...candidate, kind: 'repo'}, 'r').kind, 'repository');
assert.equal(ui.candidateDraft({...candidate, kind: 'branch', value: 'main'}, 'r').open_mode, 'copy');
assert.equal(ui.candidateDraft({...candidate, kind: 'worktree', value: '/synthetic'}, 'r').open_mode, 'copy');

// Cross the actual Python schema/core/projection boundary, not a hand-shaped UI contract.
const {execFileSync} = require('node:child_process');
const coreBoundary = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', `
import json, tempfile, sys, sqlite3
from pathlib import Path
from observer.cooperative import CooperativeState
from observer.ui_projection import project
from observer.session_usage import load_session_usage
from observer.ui_reads import ReadCursors
from observer.file_store import StateError
with tempfile.TemporaryDirectory() as root:
    core = CooperativeState(root)
    core.create('ui-test', 'Synthetic explicit intervention', 'session-a')
    core.reporting('ui-test', 'session-a', True, 1)
    core.report({'schema_version': 1, 'report_id': 'ui-test-report', 'session_id': 'session-a',
      'expected_revisions': {'ui-test': 2}, 'changes': {'ui-test': [
        {'op': 'define', 'item_id': 'ask', 'title': 'Policy decision', 'value': 'Choose A or B',
         'status': 'waiting', 'evidence': [], 'role': 'intervention', 'attention_required': True},
        {'op': 'define', 'item_id': 'blocker', 'title': 'Blocker', 'value': 'No user request',
         'status': 'blocked', 'evidence': [], 'role': 'blocker'},
        {'op': 'define', 'item_id': 'closed', 'title': 'Already answered', 'value': 'Keep as history',
         'status': 'completed', 'evidence': [], 'role': 'intervention', 'attention_required': False}
      ]}})
    core.resource_put('ui-test', json.load(sys.stdin), 3)
    active = project(core.list())
    core.observation('ui-test', False, 4)
    off = project(core.list())
    core.lifecycle('ui-test', True, 5)
    completed = project(core.list())
    core.lifecycle('ui-test', False, 6)
    restored = project(core.list())
    core.observation('ui-test', True, 7)
    core.create('other-task', 'Synthetic other task', 'session-b')
    core.link('other-task', 'session-a', 1)
    source = Path(root) / 'usage.sqlite'
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE refinery_schema_history(version INTEGER); INSERT INTO refinery_schema_history VALUES(17); CREATE TABLE sessions(id TEXT, working_directory TEXT, last_activity_at INTEGER); CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,chat_message TEXT);')
        db.execute('INSERT INTO sessions VALUES(?,?,?)', ('session-a', '/synthetic', 1793447700))
        message = json.dumps({'role': 'assistant', 'message_id': 'duplicate', 'metadata': {'metrics': {'input_tokens': 100, 'cache_read_tokens': 900, 'output_tokens': 50}}})
        db.executemany('INSERT INTO message_nodes VALUES(?,?,?)', [('session-a', n, message) for n in (1, 2)])
    usage = load_session_usage({'source': str(source), 'workspaces': ['/synthetic']}, measured_at='2026-10-31T12:00:00+00:00')
    def record(sid, n):
        return {'id': sid, 'title': sid, 'workspace': '/synthetic', 'resource_mentions': [{'kind': 'pr', 'value': 'https://github.com/example/demo/pull/42', 'verification': 'mentioned', 'source_ref': 'mentions-v2', 'node_id': n, 'source_hash': 'a'*64}]}
    shared = project(core.list(), observations={'sessions': [record('session-a', 1), record('session-b', 2)]}, session_usage={'sessions': usage, 'stale': False, 'error': None, 'last_attempt': '2026-10-31T12:00:00+00:00'})
    core.reporting('other-task', 'session-a', True, 2)
    core.report({'schema_version': 1, 'report_id': 'both-task-report', 'session_id': 'session-a', 'expected_revisions': {'ui-test': 8, 'other-task': 3}, 'changes': {task: [{'op': 'define', 'item_id': 'report-read', 'title': 'Report', 'value': 'New shared-session report', 'status': 'open', 'evidence': []}] for task in ['ui-test', 'other-task']}})
    reads = ReadCursors(root)
    read_before = project(core.list(), read_cursors=reads.snapshot())
    state_before_mark = core.read('ui-test')['state']
    reads.mark('ui-test', 9, 9, 'mark-a')
    assert state_before_mark == core.read('ui-test')['state']
    read_after = project(core.list(), read_cursors=reads.snapshot())
    core.report({'schema_version': 1, 'report_id': 'racing-report', 'session_id': 'session-a', 'expected_revisions': {'ui-test': 9, 'other-task': 4}, 'changes': {task: [{'op': 'update', 'item_id': 'report-read', 'value': 'Newer report after display'}] for task in ['ui-test', 'other-task']}})
    assert reads.mark('ui-test', 9, 9, 'old-mark')['changed'] is False
    mark_error = None
    try:
        reads.mark('other-task', 4, 4, 'stale-mark')
    except StateError as error:
        mark_error = error.code
    read_race = project(core.list(), read_cursors=reads.snapshot())
    print(json.dumps({'active': active, 'off': off, 'completed': completed, 'restored': restored, 'shared': shared, 'read_before': read_before, 'read_after': read_after, 'read_race': read_race, 'mark_error': mark_error}))
`], {cwd: path.join(__dirname, '..'), encoding: 'utf8', input: JSON.stringify(candidateResource)}));
const actual = coreBoundary.active;
assert.equal(ui.taskRows(actual)[0].attention, true);
assert.equal(ui.filterRows(ui.taskRows(actual), '', 'attention').length, 1);
assert.deepEqual(ui.roleGroups(actual, 'ui-test').map(group => [group.role, group.items.map(item => item.item_id)]), [['intervention', ['ask']]]);
assert.deepEqual(actual.tasks['ui-test'].metadata.resources[0].evidence, candidateResource.evidence);
assert.equal(actual.tasks['ui-test'].metadata.resources[0].external_status, 'unknown');
assert.equal(ui.taskRows(actual)[0].lifecycle, 'active');
assert.equal(ui.taskRows(actual)[0].observing, true);
assert.equal(ui.taskRows(coreBoundary.off)[0].observing, false);
assert.equal(ui.filterRows(ui.taskRows(coreBoundary.completed), '', 'all').length, 0);
assert.equal(ui.filterRows(ui.taskRows(coreBoundary.completed), '', 'archive').length, 1);
assert.equal(ui.taskRows(coreBoundary.restored)[0].lifecycle, 'active');
assert.equal(ui.taskRows(coreBoundary.restored)[0].observing, false);
assert.equal(coreBoundary.restored.contributions['ui-test']['session-a'].reporting, true);
assert.deepEqual(coreBoundary.restored.items, actual.items);
const shared = coreBoundary.shared;
const sharedCandidate = Object.values(shared.resource_candidates)[0];
assert.equal(sharedCandidate.sources.length, 2);
assert.equal(shared.sessions['session-a'].task_ids.length, 2);
const scopedResource = ui.candidateDraft(sharedCandidate, 'scoped-pr', shared.tasks['ui-test'].session_ids);
assert.deepEqual(scopedResource.evidence.map(evidence => evidence.session_id), ['session-a']);
const scopedPersisted = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', `
import json, tempfile, sys
from observer.cooperative import CooperativeState
with tempfile.TemporaryDirectory() as root:
    core = CooperativeState(root)
    core.create('ui-test', 'Synthetic task A', 'session-a')
    core.create('other-task', 'Synthetic task B', 'session-b')
    core.resource_put('ui-test', json.load(sys.stdin), 1)
    print(json.dumps(core.read('ui-test')['state']))
`], {cwd: path.join(__dirname, '..'), encoding: 'utf8', input: JSON.stringify(scopedResource)}));
assert.deepEqual(scopedPersisted.contributions.map(edge => edge.session_id), ['session-a']);
assert.deepEqual(scopedPersisted.resources[0].evidence, scopedResource.evidence);
const measured = shared.sessions['session-a'];
assert.deepEqual(ui.usageRows(measured).map(row => row.value), [1000, 100, 900, 50]);
assert.equal(measured.usage.duplicate_node_count, 1);
assert.equal(ui.sessionActivity(measured).source, 'Devin Local');
assert.equal(ui.sessionActivity({...measured, activity: {last_seen: '2027-01-01T00:00:00Z'}}).source, '보고 훅');
assert.deepEqual(ui.usageRows({usage: null}).map(row => row.value), [null, null, null, null]);
assert.equal(shared.tasks['ui-test'].usage, undefined);
assert.equal(ui.filterRows(ui.taskRows(coreBoundary.read_before), '', 'new').length, 2);
assert.equal(coreBoundary.read_after.tasks['ui-test'].read_state.status, 'read');
assert.equal(coreBoundary.read_after.tasks['other-task'].read_state.status, 'unread');
assert.equal(coreBoundary.read_race.tasks['ui-test'].read_state.status, 'unread');
assert.equal(coreBoundary.read_race.tasks['ui-test'].read_state.through_report_revision, 9);
assert.equal(coreBoundary.mark_error, 'revision_conflict');
assert.deepEqual(ui.filterRows([
  {id: 'old', lifecycle: 'active', attention: false, unread: false, lastReportTime: 1},
  {id: 'recent', lifecycle: 'active', attention: false, unread: false, lastReportTime: 999},
  {id: 'new', lifecycle: 'active', attention: false, unread: true, lastReportTime: 1},
  {id: 'attention', lifecycle: 'active', attention: true, unread: false, lastReportTime: 1},
], '', 'all').map(row => row.id), ['attention', 'new', 'recent', 'old']);

// A small DOM harness exercises refresh and asynchronous action ownership.
const vm = require('node:vm');
const ids = new Map();
let document;
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.events = {}; this.attributes = {}; this.hidden = false; this.scrollTop = 0; this.value = ''; this._text = ''; this.parentNode = null; this.className = ''; this.classList = {toggle: (name, yes) => { const names = new Set(this.className.split(' ').filter(Boolean)); yes ? names.add(name) : names.delete(name); this.className = [...names].join(' '); }}; }
  get firstChild() { return this.children[0] || null; }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = value; }
  append(...nodes) { for (const item of nodes) { item.remove(); item.parentNode = this; this.children.push(item); } }
  insertBefore(item, before) { item.remove(); item.parentNode = this; const index = before ? this.children.indexOf(before) : this.children.length; this.children.splice(index, 0, item); }
  replaceChildren(...nodes) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = ''; this.append(...nodes); }
  remove() { if (this.parentNode) { const siblings = this.parentNode.children; siblings.splice(siblings.indexOf(this), 1); this.parentNode = null; } }
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
  fire(name, extra = {}) { for (const fn of this.events[name] || []) fn({target: this, preventDefault() {}, ...extra}); }
  querySelector(selector) { return walk(this).find(child => selector.startsWith('.') ? child.className.split(' ').includes(selector.slice(1)) : child.tagName === selector.toUpperCase()) || null; }
  focus() { document.activeElement = this; }
}
function walk(element) { return element.children.flatMap(child => [child, ...walk(child)]); }
const html = fs.readFileSync(path.join(__dirname, '../observer/ui_v2/index.html'), 'utf8');
assert.match(html, /class="detail-header"[^]*?<\/div>\s*<div id="task-toolbar" hidden><\/div>\s*<div id="detail-body" class="scroll">/); // controls are outside the scrolling content
for (const match of html.matchAll(/id="([^"]+)"/g)) ids.set(match[1], new Element());
const documentEvents = {};
document = {readyState: 'complete', activeElement: null, documentElement: {dataset: {}}, getElementById: id => ids.get(id), createElement: tag => new Element(tag), addEventListener(name, fn) { (documentEvents[name] ||= []).push(fn); }};
const sent = [];
const window = {document, webkit: {messageHandlers: {observer: {postMessage: message => { assert.equal(typeof message, 'string'); sent.push(JSON.parse(message)); }}}}};
vm.runInNewContext(code, {window, Date, Map, Set, BigInt, JSON, Object, String, Boolean, Number, Array});
const runtime = JSON.parse(JSON.stringify(projection));
runtime.capabilities = {typed_item_roles: true, task_lifecycle: true}; runtime.contributions = {a: {s: {reporting: true, last_report_at: '2026-10-31T00:00:00Z'}}, b: {}}; runtime.resource_candidates = {};
runtime.sessions.s.session_id = 's'; runtime.sessions.s.task_ids = ['a']; runtime.sessions.s.activity = {last_seen: '2026-10-31T01:00:00Z'};
for (const task of Object.values(runtime.tasks)) { task.session_ids = task.task_id === 'a' ? ['s'] : []; task.displayed_revision = 2; task.resource_candidate_ids = []; task.metadata.notes = []; task.metadata.view = {markdown: ''}; }
assert.equal(window.Observer.receive(runtime), true);
assert.equal(sent[0].action, 'ready');
assert.equal(ids.get('task-toolbar').hidden, true);
ids.get('task-list').children[0].fire('click');
let detail = ids.get('detail-body');
const toolbar = ids.get('task-toolbar');
const controlsA = toolbar.firstChild;
assert.equal(toolbar.hidden, false);
assert.equal(controlsA.parentNode, toolbar);
assert.equal(walk(detail).includes(controlsA), false);
assert.equal(detail.textContent.includes('태스크 관리'), false);
const draft = walk(detail).find(element => element.tagName === 'TEXTAREA');
draft.value = 'unsaved note'; draft.fire('input'); draft.focus();
const disclosure = walk(detail).find(element => element.className === 'item-details'); disclosure.open = true;
detail.scrollTop = 70;
runtime.tasks.a.displayed_revision = 3;
runtime.items.a.latest.value = 'updated while typing';
window.Observer.receive(runtime);
assert.equal(walk(detail).find(element => element.tagName === 'TEXTAREA'), draft);
assert.equal(draft.value, 'unsaved note');
assert.equal(document.activeElement, draft);
assert.equal(disclosure.open, true);
assert.equal(detail.scrollTop, 70);
const form = walk(detail).find(element => element.tagName === 'FORM' && element.className.includes('note-form')); form.fire('submit');
const noteRequest = sent.at(-1);
assert.equal(noteRequest.action, 'note');
assert.equal(noteRequest.expected_revision, 2);
draft.value = 'edited after request'; draft.fire('input');
ids.get('back').fire('click');
ids.get('task-list').children.find(card => card.dataset.taskId === 'b').fire('click');
assert.equal(window.Observer.actionResult({request_id: noteRequest.request_id, ok: true, result: {}}), true);
assert.equal(ids.get('detail-title').textContent, '같은 제목');
assert.equal(draft.value, 'edited after request');
assert.equal(sent.at(-1).action, 'state_refresh');
assert.equal(window.Observer.actionResult({request_id: noteRequest.request_id, ok: true}), false);
ids.get('back').fire('click');
ids.get('task-list').children.find(card => card.dataset.taskId === 'a').fire('click');
assert.equal(walk(detail).find(element => element.tagName === 'TEXTAREA'), draft);
assert.equal(detail.scrollTop, 70);
// Finder navigation is read-only, task scoped and independent of task health.
const revealA = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '상태 파일 보기');
revealA.fire('click'); const revealARequest = sent.at(-1);
assert.deepEqual(Object.keys(revealARequest).sort(), ['action', 'request_id', 'task_id']);
assert.equal(revealARequest.action, 'task_reveal'); assert.equal(revealARequest.task_id, 'a');
const beforeRevealResult = sent.length;
window.Observer.actionResult({request_id: revealARequest.request_id, ok: true, result: {revealed: true}});
assert.equal(sent.length, beforeRevealResult); // opening a location never refreshes the collector
assert.equal(draft.value, 'edited after request');
ids.get('back').fire('click'); ids.get('task-list').children.find(card => card.dataset.taskId === 'b').fire('click');
runtime.capabilities.task_lifecycle = false; window.Observer.receive(runtime);
assert.equal(toolbar.hidden, true);
const revealB = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '상태 파일 보기');
assert.equal(revealB.disabled, false);
assert.equal(revealB.parentNode.hidden, false); // not inside a hidden lifecycle section
revealB.fire('click'); const revealBRequest = sent.at(-1); assert.equal(revealBRequest.task_id, 'b');
ids.get('back').fire('click'); ids.get('task-list').children.find(card => card.dataset.taskId === 'a').fire('click');
const currentTaskRoot = detail.firstChild; const beforeLateReveal = sent.length;
window.Observer.actionResult({request_id: revealBRequest.request_id, ok: false, error: {code: 'open_failed', message: '파일을 찾을 수 없습니다.'}});
assert.equal(detail.firstChild, currentTaskRoot); assert.equal(sent.length, beforeLateReveal);
assert.equal(draft.value, 'edited after request');
ids.get('back').fire('click'); ids.get('task-list').children.find(card => card.dataset.taskId === 'b').fire('click');
assert.ok(detail.textContent.includes('파일을 찾을 수 없습니다.'));
assert.ok(walk(detail).some(element => element.attributes.role === 'status' && element.textContent === '파일을 찾을 수 없습니다.'));
window.Observer.receive(runtime); assert.ok(detail.textContent.includes('파일을 찾을 수 없습니다.'));
runtime.capabilities.task_lifecycle = true; window.Observer.receive(runtime);
assert.equal(toolbar.hidden, false);
ids.get('back').fire('click'); ids.get('task-list').children.find(card => card.dataset.taskId === 'a').fire('click');
form.fire('submit'); const conflictRequest = sent.at(-1);
window.Observer.actionResult({request_id: conflictRequest.request_id, ok: false, error: {code: 'revision_conflict'}});
assert.equal(draft.value, 'edited after request');
form.fire('submit');
assert.equal(sent.at(-1).expected_revision, 3);
const historyEntry = revision => ({revision, title: 'T', updated_at: '2026-10-31T00:00:00Z', updated_by: {kind: 'human'}, items: [], notes: []});
const historyLoad = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '이력 불러오기');
historyLoad.fire('click');
assert.equal(sent.at(-1).action, 'history');
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {entries: [historyEntry(3), historyEntry(2)], has_more: true, before_revision: 2}});
const latestHistory = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '최신 이력');
assert.equal(latestHistory.hidden, false);
walk(detail).find(element => element.dataset.revision === '3').open = true;
latestHistory.fire('click');
assert.equal(sent.at(-1).before_revision, undefined);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {entries: [historyEntry(5), historyEntry(4), historyEntry(3)], has_more: true, before_revision: 3}});
assert.equal(walk(detail).find(element => element.dataset.revision === '3').open, true);
assert.equal(detail.scrollTop, 70);
historyLoad.fire('click');
assert.equal(sent.at(-1).before_revision, 2);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {entries: [historyEntry(1)], has_more: false, before_revision: 1}});
assert.equal(historyLoad.hidden, true);
assert.equal(latestHistory.hidden, false);
latestHistory.fire('click');
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {entries: [historyEntry(7)], has_more: true, before_revision: 7}});
assert.equal(historyLoad.hidden, false);
historyLoad.fire('click');
assert.equal(sent.at(-1).before_revision, 7);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {entries: [historyEntry(6), historyEntry(5)], has_more: true, before_revision: 5}});
assert.equal(historyLoad.hidden, true);
assert.equal(latestHistory.hidden, false);
assert.equal(ui.historyChanges(historyEntry(7), historyEntry(5)), null);
runtime.tasks.a.metadata.resources = [JSON.parse(JSON.stringify(actual.tasks['ui-test'].metadata.resources[0]))];
runtime.resource_candidates[candidate.candidate_id] = JSON.parse(JSON.stringify(candidate));
runtime.resource_candidates[candidate.candidate_id].sources = [...candidate.sources.map(source => ({...source, session_id: 's'})), {...candidate.sources[0], session_id: 'unrelated'}];
runtime.tasks.a.resource_candidate_ids = [candidate.candidate_id];
window.Observer.receive(runtime);
const resourceCard = walk(detail).find(element => element.className.includes('resource-card'));
const resourceOpen = walk(resourceCard).find(element => element.tagName === 'BUTTON' && element.textContent === '열기 ↗');
const resourceCopy = walk(resourceCard).find(element => element.tagName === 'BUTTON' && element.textContent === '복사');
resourceOpen.fire('click');
assert.equal(sent.at(-1).action, 'resource_open');
assert.equal(sent.at(-1).resource_id, 'resource-core');
assert.equal(sent.at(-1).target, undefined);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: false, error: {code: 'open_failed', message: 'Synthetic app failure'}});
assert.equal(resourceCopy.disabled, false);
assert.ok(resourceCard.textContent.includes('복사를 사용할 수 있습니다'));
resourceCopy.fire('click');
assert.equal(sent.at(-1).action, 'resource_copy');
const copyCount = sent.length;
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {copied: true}});
assert.equal(sent.length, copyCount);
const candidateRegister = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '이 태스크에 등록');
const beforeCandidate = sent.length; candidateRegister.fire('click');
assert.equal(sent.length, beforeCandidate);
const resourceForm = walk(detail).find(element => element.className === 'resource-form');
const resourceLabel = walk(resourceForm).find(element => element.attributes['aria-label'] === '리소스 표시 이름');
const resourceTarget = walk(resourceForm).find(element => element.attributes['aria-label'] === '리소스 주소 또는 값');
assert.equal(resourceTarget.value, candidate.value);
resourceLabel.value = 'Review PR'; resourceLabel.fire('input');
runtime.tasks.a.displayed_revision = 4; window.Observer.receive(runtime);
assert.equal(resourceLabel.value, 'Review PR');
resourceForm.fire('submit');
assert.equal(sent.at(-1).action, 'resource_put');
assert.equal(sent.at(-1).expected_revision, 3);
assert.deepEqual(sent.at(-1).resource.evidence, candidateResource.evidence.map(evidence => ({...evidence, session_id: 's'})));
assert.equal(runtime.resource_candidates[candidate.candidate_id].sources.length, 3);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: false, error: {code: 'revision_conflict'}});
assert.equal(resourceLabel.value, 'Review PR');
resourceForm.fire('submit');
assert.equal(sent.at(-1).expected_revision, 4);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
assert.equal(resourceLabel.value, '');
const resourceEdit = walk(resourceCard).find(element => element.tagName === 'BUTTON' && element.textContent === '수정');
resourceEdit.fire('click'); const editLabel = resourceLabel.value;
candidateRegister.fire('click');
assert.equal(resourceLabel.value, editLabel);
assert.ok(resourceForm.textContent.includes('먼저 저장하거나 입력을 취소'));
const cancelResource = walk(resourceForm).find(element => element.tagName === 'BUTTON' && element.textContent === '입력 취소'); cancelResource.fire('click');
const archiveResource = walk(resourceCard).find(element => element.tagName === 'BUTTON' && element.textContent === '보관');
archiveResource.fire('click');
assert.equal(sent.at(-1).action, 'resource_archive');
assert.equal(sent.at(-1).archived, true);
assert.equal(sent.at(-1).expected_revision, 4);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
runtime.tasks.a.metadata.resources[0].archived = true; window.Observer.receive(runtime);
assert.equal(archiveResource.textContent, '복원');
archiveResource.fire('click');
assert.equal(sent.at(-1).archived, false);
const observationButton = walk(toolbar).find(element => element.tagName === 'BUTTON' && element.textContent === '자동 반영 켜짐');
assert.equal(observationButton.attributes['aria-pressed'], 'true');
assert.equal(observationButton.attributes['aria-label'], '태스크 자동 반영 끄기');
observationButton.fire('click');
assert.equal(sent.at(-1).action, 'observation');
assert.equal(sent.at(-1).enabled, false);
assert.equal(sent.at(-1).expected_revision, 4);
const managementRequest = sent.at(-1);
ids.get('back').fire('click');
assert.equal(toolbar.hidden, true);
ids.get('task-list').children.find(card => card.dataset.taskId === 'b').fire('click');
const controlsB = toolbar.firstChild;
assert.notEqual(controlsB, controlsA);
window.Observer.actionResult({request_id: managementRequest.request_id, ok: false, error: {code: 'revision_conflict'}});
assert.equal(toolbar.firstChild, controlsB);
assert.equal(toolbar.textContent.includes('그사이 다른 업데이트'), false);
assert.equal(walk(toolbar).find(element => element.attributes.role === 'status').hidden, true);
ids.get('back').fire('click'); ids.get('task-list').children.find(card => card.dataset.taskId === 'a').fire('click');
assert.equal(toolbar.firstChild, controlsA);
assert.ok(toolbar.textContent.includes('그사이 다른 업데이트'));
assert.equal(walk(toolbar).find(element => element.attributes.role === 'status').hidden, false);
window.Observer.receive(runtime);
assert.ok(toolbar.textContent.includes('그사이 다른 업데이트'));
assert.equal(draft.value, 'edited after request');
observationButton.fire('click');
assert.equal(sent.at(-1).task_id, 'a'); assert.equal(sent.at(-1).expected_revision, 4);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
assert.equal(walk(toolbar).find(element => element.attributes.role === 'status').hidden, true);
runtime.tasks.a.management = coreBoundary.off.tasks['ui-test'].management; runtime.tasks.a.effective_observation_enabled = false; runtime.tasks.a.displayed_revision = 5;
window.Observer.receive(runtime);
assert.equal(observationButton.textContent, '자동 반영 꺼짐');
assert.equal(observationButton.attributes['aria-pressed'], 'false');
assert.equal(observationButton.attributes['aria-label'], '태스크 자동 반영 켜기');
assert.ok(detail.textContent.includes('태스크 반영 중지'));
const lifecycleButton = walk(toolbar).find(element => element.tagName === 'BUTTON' && element.textContent === '완료');
assert.equal(lifecycleButton.attributes['aria-label'], '태스크 완료하고 보관');
lifecycleButton.fire('click');
assert.equal(sent.at(-1).action, 'lifecycle');
assert.equal(sent.at(-1).completed, true);
assert.equal(sent.at(-1).expected_revision, 5);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
runtime.tasks.a.management = coreBoundary.completed.tasks['ui-test'].management; runtime.tasks.a.displayed_revision = 6;
window.Observer.receive(runtime);
assert.equal(observationButton.hidden, true);
assert.equal(lifecycleButton.textContent, '복원');
assert.equal(lifecycleButton.attributes['aria-label'], '태스크 활성으로 복원');
ids.get('back').fire('click');
assert.equal(ids.get('task-list').children.some(card => card.dataset.taskId === 'a'), false);
ids.get('filter-archive').fire('click');
assert.equal(ids.get('task-list').children.length, 1);
ids.get('task-list').children[0].fire('click');
lifecycleButton.fire('click');
assert.equal(sent.at(-1).completed, false);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
runtime.tasks.a.management = coreBoundary.restored.tasks['ui-test'].management; runtime.tasks.a.displayed_revision = 7;
window.Observer.receive(runtime);
assert.equal(observationButton.textContent, '자동 반영 꺼짐');
assert.equal(observationButton.hidden, false);
assert.equal(draft.value, 'edited after request');
runtime.sessions.s.usage = JSON.parse(JSON.stringify(measured.usage)); runtime.sessions.s.usage_stale = false;
runtime.sessions.s.task_ids = ['a', 'b']; runtime.contributions.b.s = {reporting: false, last_report_at: null}; runtime.tasks.b.session_ids = ['s'];
window.Observer.receive(runtime);
const linkedSession = walk(detail).find(element => element.tagName === 'BUTTON' && element.className.includes('session-button'));
linkedSession.fire('click');
assert.equal(toolbar.hidden, true);
const usagePanel = walk(detail).find(element => element.className.includes('usage-panel')); usagePanel.open = true;
assert.ok(usagePanel.textContent.includes('1,000'));
assert.ok(usagePanel.textContent.includes('캐시 입력 · 입력 합계에 포함900'));
assert.ok(usagePanel.textContent.includes('청구 사용량이 아닙니다'));
assert.ok(usagePanel.textContent.includes('devin_local_sqlite_v17'));
assert.equal(walk(detail).filter(element => element.className.includes('usage-grid')).length, 1);
const copySessionId = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === 'ID 복사');
const activateDevin = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === 'Devin Desktop으로 이동');
copySessionId.fire('click');
assert.equal(sent.at(-1).action, 'session_copy');
assert.equal(sent.at(-1).task_id, 'a');
assert.equal(sent.at(-1).session_id, 's');
assert.deepEqual(Object.keys(sent.at(-1)).sort(), ['action', 'request_id', 'session_id', 'task_id']);
const nativeCount = sent.length;
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {copied: true}});
assert.equal(sent.length, nativeCount);
activateDevin.fire('click');
assert.equal(sent.at(-1).action, 'devin_activate');
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: false, error: {code: 'open_failed', message: 'Devin Desktop이 실행 중이 아닙니다.'}});
assert.equal(copySessionId.disabled, false);
assert.ok(detail.textContent.includes('Devin Desktop이 실행 중이 아닙니다.'));
assert.ok(detail.textContent.includes('세션 ID로 열린 Devin에서 찾을 수 있습니다.'));
const reportingToggle = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '자동보고 끄기');
reportingToggle.focus(); detail.scrollTop = 31;
runtime.sessions.s.usage_stale = true; runtime.sessions.s.usage_error = 'source_read_failed';
window.Observer.receive(runtime);
assert.equal(usagePanel.open, true);
assert.equal(document.activeElement, reportingToggle);
assert.equal(detail.scrollTop, 31);
assert.ok(usagePanel.textContent.includes('마지막 측정값'));
reportingToggle.fire('click');
assert.equal(sent.at(-1).action, 'reporting');
assert.equal(sent.at(-1).task_id, 'a');
assert.equal(sent.at(-1).session_id, 's');
assert.equal(sent.at(-1).enabled, false);
assert.equal(sent.at(-1).expected_revision, 7);
const reportingRequest = sent.at(-1);
ids.get('back').fire('click');
window.Observer.actionResult({request_id: reportingRequest.request_id, ok: true, result: {}});
assert.equal(sent.at(-1).action, 'state_refresh');
assert.equal(ids.get('detail-title').textContent, '같은 제목');
assert.equal(draft.value, 'edited after request');
runtime.contributions.a.s.reporting = false; window.Observer.receive(runtime); linkedSession.fire('click');
assert.equal(detail.scrollTop, 31);
assert.equal(usagePanel.open, true);
assert.equal(reportingToggle.textContent, '자동보고 켜기');
runtime.sessions.s.usage = null; runtime.health.usage = {error: 'source_unavailable'}; window.Observer.receive(runtime);
assert.ok(usagePanel.textContent.includes('사용량 정보 없음'));
assert.ok(usagePanel.textContent.includes('source_read_failed'));
assert.equal(walk(usagePanel).find(element => element.className === 'usage-grid').hidden, true);
ids.get('back').fire('click');
runtime.capabilities.task_read_cursor = true;
runtime.tasks.a.read_state = {status: 'unread', last_report_revision: 9, through_report_revision: null}; runtime.tasks.a.displayed_revision = 10;
const beforeReadRefresh = sent.length; window.Observer.receive(runtime);
assert.equal(sent.length, beforeReadRefresh);
assert.equal(ids.get('new-count').textContent, '1?');
const markRead = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '여기까지 확인');
assert.equal(markRead.disabled, false);
ids.get('back').fire('click'); ids.get('filter-new').fire('click');
assert.equal(ids.get('task-list').children.length, 1);
ids.get('task-list').children[0].fire('click');
assert.equal(sent.length, beforeReadRefresh);
markRead.fire('click');
assert.equal(sent.at(-1).action, 'mark_read');
assert.equal(sent.at(-1).expected_revision, 10);
assert.equal(sent.at(-1).through_report_revision, 9);
const markRequest = sent.at(-1);
runtime.tasks.a.read_state = {status: 'unread', last_report_revision: 11, through_report_revision: 9}; runtime.tasks.a.displayed_revision = 11;
window.Observer.receive(runtime);
window.Observer.actionResult({request_id: markRequest.request_id, ok: true, result: {changed: true}});
assert.equal(sent.at(-1).action, 'state_refresh');
assert.equal(runtime.tasks.a.read_state.status, 'unread');
assert.ok(detail.textContent.includes('새 보고가 있습니다.'));
assert.equal(draft.value, 'edited after request');
markRead.fire('click');
assert.equal(sent.at(-1).expected_revision, 11);
assert.equal(sent.at(-1).through_report_revision, 11);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: false, error: {code: 'revision_conflict'}});
assert.ok(detail.textContent.includes('다른 업데이트가 있었습니다'));
runtime.tasks.a.read_state.status = 'unavailable'; runtime.health.read_cursor_error = {code: 'invalid_read_cursors', message: 'Synthetic damaged cursor'};
window.Observer.receive(runtime);
assert.equal(markRead.disabled, true);
assert.equal(ids.get('new-count').textContent, '—');
assert.ok(ids.get('diagnostic-text').textContent.includes('Synthetic damaged cursor'));
delete runtime.health.read_cursor_error;
runtime.tasks.a.display_origin = 'last_good'; runtime.tasks.a.health = 'invalid';
window.Observer.receive(runtime);
assert.ok(detail.textContent.includes('마지막 정상 기록을 표시하고 있어 새 보고의 확인 상태를 판단할 수 없습니다.'));
assert.ok(!detail.textContent.includes('확인 위치를 읽지 못했습니다. 진단을 확인하세요.'));
assert.equal(markRead.disabled, true);
runtime.read_only = true; window.Observer.receive(runtime);
assert.equal(draft.disabled, true);
assert.equal(revealA.disabled, true); assert.equal(ids.get('state-folder').disabled, true);
const beforePreviewNavigation = sent.length;
revealA.fire('click'); ids.get('state-folder').fire('click');
assert.equal(sent.length, beforePreviewNavigation);
assert.equal(window.Observer.receive({projection_version: 99}), false);
assert.equal(ids.get('message').hidden, false);
let prevented = false;
for (const handler of documentEvents.keydown) handler({key: 'Tab', preventDefault() { prevented = true; }});
assert.equal(prevented, false);
const beforeEscape = sent.length;
for (const handler of documentEvents.keydown) handler({key: 'Escape', preventDefault() { prevented = true; }});
assert.equal(prevented, true);
assert.equal(sent.length, beforeEscape);
for (const handler of documentEvents.keydown) handler({key: 'Escape', preventDefault() {}});
assert.equal(sent.at(-1).action, 'close');
assert.equal(sent.some(message => message.action === 'quit'), false);
ids.get('filter-all').fire('click');
ids.get('task-list').children.find(card => card.dataset.taskId === 'a').fire('click');
assert.equal(walk(detail).find(element => element.tagName === 'TEXTAREA'), draft);
assert.equal(draft.value, 'edited after request');
ids.get('back').fire('click');
const clean = {projection_version: 1, tasks: {}, items: {}, sessions: {}, capabilities: {}, health: {runtime: {running: true, collector: {last_success: '1793447700000000001'}}}};
window.Observer.receive(clean);
assert.equal(document.documentElement.dataset.appearance, 'system');
assert.equal(ids.get('state-folder').disabled, false);
ids.get('state-folder').fire('click'); const folderRequest = sent.at(-1);
assert.deepEqual(Object.keys(folderRequest).sort(), ['action', 'request_id']);
assert.equal(folderRequest.action, 'state_folder');
assert.equal(ids.get('state-folder').disabled, true);
const beforeFolderFailure = sent.length;
window.Observer.actionResult({request_id: folderRequest.request_id, ok: false, error: {code: 'open_failed', message: '데이터 폴더를 찾을 수 없습니다.'}});
assert.equal(sent.length, beforeFolderFailure);
assert.equal(ids.get('location-message').hidden, false);
assert.equal(ids.get('location-message').textContent, '데이터 폴더를 찾을 수 없습니다.');
window.Observer.receive(clean);
assert.equal(ids.get('location-message').textContent, '데이터 폴더를 찾을 수 없습니다.');
ids.get('state-folder').fire('click'); const successfulFolder = sent.at(-1); const beforeFolderSuccess = sent.length;
window.Observer.actionResult({request_id: successfulFolder.request_id, ok: true, result: {opened: true}});
assert.equal(sent.length, beforeFolderSuccess);
assert.equal(ids.get('location-message').textContent, '데이터 폴더를 열었습니다.');
window.Observer.receive({...clean, appearance: 'light'});
assert.equal(document.documentElement.dataset.appearance, 'light');
window.Observer.receive({...clean, appearance: 'dark'});
assert.equal(document.documentElement.dataset.appearance, 'dark');
window.Observer.receive({...clean, appearance: 'unexpected'});
assert.equal(document.documentElement.dataset.appearance, 'system');
const css = fs.readFileSync(path.join(__dirname, '../observer/ui_v2/style.css'), 'utf8');
assert.ok(css.includes(':root[data-appearance="light"] { color-scheme: light; }'));
assert.ok(css.includes('@media (prefers-color-scheme: dark) { :root:not([data-appearance="light"])'));
ids.get('show-diagnostics').fire('click');
assert.equal(ids.get('diagnostics').hidden, false);
assert.ok(ids.get('diagnostic-text').textContent.includes('현재 보고된 관측 오류가 없습니다.'));
window.Observer.receive(clean);
assert.equal(ids.get('diagnostics').open, true);
assert.equal(ids.get('show-diagnostics').attributes['aria-expanded'], 'true');
ids.get('show-diagnostics').fire('click');
assert.equal(ids.get('diagnostics').hidden, true);
ids.get('quit').fire('click');
assert.equal(sent.at(-1).action, 'quit');
assert.equal(sent.filter(message => message.action === 'quit').length, 1);
console.log('UI V2 frontend helpers and DOM refresh/action regression checks passed');
