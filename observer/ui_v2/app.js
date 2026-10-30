(function (root) {
  'use strict';

  function milliseconds(value) {
    if (value == null || value === '') return null;
    if (typeof value === 'string' && /^\d{16,}$/.test(value)) {
      const result = Number(BigInt(value) / 1000000n);
      return Number.isFinite(result) ? result : null;
    }
    if (typeof value === 'number') return Number.isFinite(value) ? (value > 1e15 ? value / 1e6 : value) : null;
    const result = Date.parse(value);
    return Number.isNaN(result) ? null : result;
  }
  function time(value, now) {
    const ms = milliseconds(value);
    if (ms == null) return '시각 정보 없음';
    const date = new Date(ms);
    if (Number.isNaN(date.getTime())) return '시각 정보 없음';
    const delta = Math.max(0, (now == null ? Date.now() : now) - ms);
    const relative = delta < 60000 ? '방금' : delta < 3600000 ? Math.floor(delta / 60000) + '분 전' : delta < 86400000 ? Math.floor(delta / 3600000) + '시간 전' : Math.floor(delta / 86400000) + '일 전';
    return {relative, absolute: date.toLocaleString('ko-KR'), ms};
  }
  function valueText(value) {
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }
  function actorName(actor, projection) {
    if (!actor) return '출처 정보 없음';
    if (actor.kind === 'human') return '사용자';
    if (actor.kind === 'legacy') return '이전 기록';
    const session = (projection.sessions || {})[actor.session_id];
    return (session && session.observation && session.observation.title) || actor.session_id || '세션';
  }
  function taskRows(projection) {
    return Object.values(projection.tasks || {}).map(task => {
      const items = (projection.items || {})[task.task_id] || {};
      const latest = (task.item_ids || []).map(id => items[id]).filter(item => item && !item.archived)
        .sort((a, b) => (milliseconds(b.updated_at) || 0) - (milliseconds(a.updated_at) || 0) || String(a.item_id).localeCompare(String(b.item_id)))[0];
      return {id: task.task_id, title: task.metadata ? task.metadata.title : task.task_id,
        summary: latest ? latest.title + ' · ' + valueText(latest.value) : task.display_origin === 'unavailable' ? '표시할 정상 기록 없음' : '아직 추적 항목이 없습니다.',
        source: latest ? actorName(latest.updated_by, projection) : null,
        updatedAt: latest ? latest.updated_at : null,
        origin: task.display_origin, error: task.health !== 'ok' || (task.projection_errors || []).length > 0,
        task};
    });
  }
  function collectionHealth(projection) {
    const health = projection.health || {};
    const runtime = health.runtime || {};
    const observations = health.observations || {};
    const collector = runtime.collector || observations.collector || {};
    const diagnostics = [];
    if (collector.last_error) diagnostics.push(valueText(collector.last_error));
    if (runtime.refresh && runtime.refresh.ok === false) diagnostics.push(valueText(runtime.refresh));
    diagnostics.push(...(health.state_errors || []).map(valueText));
    diagnostics.push(...(observations.warnings || []).map(valueText));
    const taskErrors = taskRows(projection).filter(row => row.error);
    taskErrors.forEach(row => diagnostics.push(row.title + ': ' + valueText([...(row.task.errors || []), ...(row.task.projection_errors || [])])));
    const failed = Boolean(collector.last_error || (runtime.refresh && runtime.refresh.ok === false));
    return {lastSuccess: collector.last_success, diagnostics: [...new Set(diagnostics)],
      warning: diagnostics.length > 0,
      label: failed ? '수집 실패' : taskErrors.length || (health.state_errors || []).length ? '기록 확인 필요' : (observations.warnings || []).length ? '관측 확인 필요' : runtime.running === false ? '관측 중지' : collector.last_success ? '관측 정상' : '수집 기록 없음'};
  }
  function filterRows(rows, query, filter) {
    const needle = query.trim().toLocaleLowerCase();
    return rows.filter(row => (filter !== 'errors' || row.error) && (!needle || (row.title + '\n' + row.summary).toLocaleLowerCase().includes(needle)));
  }
  const helpers = {milliseconds, time, valueText, actorName, taskRows, collectionHealth, filterRows};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (!root.document) return;

  const document = root.document;
  const state = {projection: null, query: '', filter: 'all', selected: null, homeScroll: 0, detailSignature: null};
  const $ = id => document.getElementById(id);
  function text(node, value) { const next = value == null ? '' : String(value); if (node.textContent !== next) node.textContent = next; }
  function post(action, payload) {
    const handler = root.webkit && root.webkit.messageHandlers && root.webkit.messageHandlers.observer;
    if (handler) handler.postMessage(Object.assign({action}, payload || {}));
  }
  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content != null) text(element, content);
    return element;
  }
  function timeLabel(value) { const result = time(value); return typeof result === 'string' ? result : result.relative; }
  function openTask(id) {
    state.homeScroll = $('task-list').scrollTop;
    state.selected = id;
    state.detailSignature = null;
    $('home').hidden = true;
    $('detail').hidden = false;
    renderDetail();
    $('back').focus();
  }
  function back() {
    $('home').hidden = false;
    $('detail').hidden = true;
    const selected = state.selected;
    state.selected = null;
    renderHome();
    $('task-list').scrollTop = state.homeScroll;
    Array.from($('task-list').children).find(card => card.dataset.taskId === selected)?.focus({preventScroll: true});
  }
  function renderHome() {
    if (!state.projection) return;
    const all = taskRows(state.projection);
    const rows = filterRows(all, state.query, state.filter);
    text($('task-count'), all.length);
    text($('error-count'), all.filter(row => row.error).length);
    $('filter-all').setAttribute('aria-pressed', String(state.filter === 'all'));
    $('filter-errors').setAttribute('aria-pressed', String(state.filter === 'errors'));
    const list = $('task-list');
    const existing = new Map(Array.from(list.children).map(card => [card.dataset.taskId, card]));
    rows.forEach((row, index) => {
      let card = existing.get(row.id);
      if (!card) {
        card = node('button', 'task-card'); card.type = 'button'; card.dataset.taskId = row.id;
        const heading = node('div', 'task-heading'); heading.append(node('span', 'task-icon'), node('span', 'task-title'));
        card.append(heading, node('p', 'task-summary'), node('div', 'task-source'), node('div', 'task-warning'));
        card.addEventListener('click', () => openTask(row.id));
      }
      text(card.querySelector('.task-icon'), row.error ? '!' : '◌');
      card.querySelector('.task-icon').setAttribute('aria-hidden', 'true');
      text(card.querySelector('.task-title'), row.title);
      text(card.querySelector('.task-summary'), row.summary);
      text(card.querySelector('.task-source'), row.source ? row.source + ' · ' + timeLabel(row.updatedAt) : '');
      const warning = row.origin === 'last_good' ? '마지막 정상 기록 · ' + timeLabel(row.task.displayed_updated_at) : row.origin === 'unavailable' ? '기록을 읽을 수 없습니다' : row.error ? '기록 확인 필요' : '';
      text(card.querySelector('.task-warning'), warning);
      card.querySelector('.task-warning').hidden = !warning;
      if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
      existing.delete(row.id);
    });
    existing.forEach(card => card.remove());
    $('empty').hidden = rows.length > 0;
    text($('empty'), all.length ? '조건에 맞는 태스크가 없습니다.' : '등록된 태스크가 없습니다.');
  }
  function renderDetail() {
    if (!state.selected || !state.projection) return;
    const task = state.projection.tasks[state.selected];
    const signature = JSON.stringify(task);
    if (signature === state.detailSignature) return;
    state.detailSignature = signature;
    text($('detail-title'), task && task.metadata ? task.metadata.title : state.selected);
    const body = $('detail-body');
    const savedScroll = body.scrollTop;
    body.replaceChildren();
    if (!task) body.append(node('p', 'panel', '현재 목록에서 이 태스크를 찾을 수 없습니다.'));
    else {
      if (task.display_origin !== 'current') body.append(node('p', 'panel', task.display_origin === 'last_good' ? '마지막 정상 기록 · ' + timeLabel(task.displayed_updated_at) : '표시할 정상 기록 없음'));
      body.append(node('p', 'panel', '추적 항목 ' + task.item_ids.length + '개 · 연결 세션 ' + task.session_ids.length + '개'));
    }
    body.scrollTop = savedScroll;
  }
  function render() {
    const health = collectionHealth(state.projection);
    text($('health'), health.label); $('health').classList.toggle('warning', health.warning);
    $('diagnostics').hidden = !health.diagnostics.length;
    text($('diagnostic-text'), health.diagnostics.join('\n\n'));
    text($('last-success'), '마지막 성공 수집 · ' + (health.lastSuccess ? timeLabel(health.lastSuccess) : '기록 없음'));
    const lastTime = time(health.lastSuccess);
    $('last-success').title = typeof lastTime === 'string' ? '' : lastTime.absolute;
    renderHome(); renderDetail();
  }
  function receive(projection) {
    if (!projection || projection.projection_version !== 1 || !projection.tasks || !projection.items) {
      text($('message'), '새 관측 정보를 표시할 수 없습니다. 마지막 표시 내용을 유지합니다.'); $('message').hidden = false;
      return false;
    }
    state.projection = projection;
    $('message').hidden = true;
    render(); return true;
  }
  function start() {
    $('search').addEventListener('input', event => { state.query = event.target.value; renderHome(); });
    ['all', 'errors'].forEach(filter => $('filter-' + filter).addEventListener('click', () => { state.filter = filter; renderHome(); }));
    $('refresh').addEventListener('click', () => post('refresh'));
    $('back').addEventListener('click', back);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); if (state.selected) back(); else post('close'); }
      if (event.key === 'ArrowLeft' && event.altKey && state.selected) { event.preventDefault(); back(); }
    });
    post('ready');
  }
  root.Observer = {receive, helpers};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})(typeof window === 'undefined' ? globalThis : window);
