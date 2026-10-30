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
import json, tempfile, sys
from observer.cooperative import CooperativeState
from observer.ui_projection import project
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
    print(json.dumps({'active': active, 'off': off, 'completed': completed, 'restored': project(core.list())}))
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
for (const match of html.matchAll(/id="([^"]+)"/g)) ids.set(match[1], new Element());
document = {readyState: 'complete', activeElement: null, getElementById: id => ids.get(id), createElement: tag => new Element(tag), addEventListener() {}};
const sent = [];
const window = {document, webkit: {messageHandlers: {observer: {postMessage: message => { assert.equal(typeof message, 'string'); sent.push(JSON.parse(message)); }}}}};
vm.runInNewContext(code, {window, Date, Map, Set, BigInt, JSON, Object, String, Boolean, Number, Array});
const runtime = JSON.parse(JSON.stringify(projection));
runtime.capabilities = {typed_item_roles: true, task_lifecycle: true}; runtime.contributions = {a: {s: {reporting: true, last_report_at: '2026-10-31T00:00:00Z'}}, b: {}}; runtime.resource_candidates = {};
runtime.sessions.s.session_id = 's'; runtime.sessions.s.task_ids = ['a']; runtime.sessions.s.activity = {last_seen: '2026-10-31T01:00:00Z'};
for (const task of Object.values(runtime.tasks)) { task.session_ids = task.task_id === 'a' ? ['s'] : []; task.displayed_revision = 2; task.resource_candidate_ids = []; task.metadata.notes = []; task.metadata.view = {markdown: ''}; }
assert.equal(window.Observer.receive(runtime), true);
assert.equal(sent[0].action, 'ready');
ids.get('task-list').children[0].fire('click');
let detail = ids.get('detail-body');
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
assert.equal(sent.at(-1).action, 'refresh');
assert.equal(window.Observer.actionResult({request_id: noteRequest.request_id, ok: true}), false);
ids.get('back').fire('click');
ids.get('task-list').children.find(card => card.dataset.taskId === 'a').fire('click');
assert.equal(walk(detail).find(element => element.tagName === 'TEXTAREA'), draft);
assert.equal(detail.scrollTop, 70);
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
assert.deepEqual(sent.at(-1).resource.evidence, candidateResource.evidence);
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
const observationButton = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '자동 반영 끄기');
observationButton.fire('click');
assert.equal(sent.at(-1).action, 'observation');
assert.equal(sent.at(-1).enabled, false);
assert.equal(sent.at(-1).expected_revision, 4);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
runtime.tasks.a.management = coreBoundary.off.tasks['ui-test'].management; runtime.tasks.a.effective_observation_enabled = false; runtime.tasks.a.displayed_revision = 5;
window.Observer.receive(runtime);
assert.equal(observationButton.textContent, '자동 반영 켜기');
assert.ok(detail.textContent.includes('태스크 반영 중지'));
const lifecycleButton = walk(detail).find(element => element.tagName === 'BUTTON' && element.textContent === '완료하고 보관');
lifecycleButton.fire('click');
assert.equal(sent.at(-1).action, 'lifecycle');
assert.equal(sent.at(-1).completed, true);
assert.equal(sent.at(-1).expected_revision, 5);
window.Observer.actionResult({request_id: sent.at(-1).request_id, ok: true, result: {}});
runtime.tasks.a.management = coreBoundary.completed.tasks['ui-test'].management; runtime.tasks.a.displayed_revision = 6;
window.Observer.receive(runtime);
assert.equal(observationButton.hidden, true);
assert.equal(lifecycleButton.textContent, '활성으로 복원');
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
assert.equal(observationButton.textContent, '자동 반영 켜기');
assert.equal(observationButton.hidden, false);
assert.equal(draft.value, 'edited after request');
runtime.read_only = true; window.Observer.receive(runtime);
assert.equal(draft.disabled, true);
assert.equal(window.Observer.receive({projection_version: 99}), false);
assert.equal(ids.get('message').hidden, false);
console.log('UI V2 frontend helpers and DOM refresh/action regression checks passed');
