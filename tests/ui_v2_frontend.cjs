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
console.log('UI V2 frontend helpers: 18 assertions passed');
