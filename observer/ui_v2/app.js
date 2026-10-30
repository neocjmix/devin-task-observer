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
  function management(task) {
    return task.management || task.metadata?.management || {lifecycle: task.metadata ? 'active' : null, observation_enabled: Boolean(task.metadata), previous_observation_enabled: null};
  }
  function observing(task) {
    const value = management(task);
    return task.effective_observation_enabled ?? (value.lifecycle === 'active' && value.observation_enabled);
  }
  function taskRows(projection) {
    return Object.values(projection.tasks || {}).map(task => {
      const items = (projection.items || {})[task.task_id] || {};
      const active = (task.item_ids || []).map(id => items[id]).filter(item => item && !item.archived);
      const attention = active.filter(item => item.role === 'intervention' && item.attention_required === true);
      const preferred = attention.length ? attention : active.filter(item => item.role === 'situation');
      const latest = (preferred.length ? preferred : active)
        .sort((a, b) => (milliseconds(b.updated_at) || 0) - (milliseconds(a.updated_at) || 0) || String(a.item_id).localeCompare(String(b.item_id)))[0];
      const reportTimes = Object.values(projection.contributions?.[task.task_id] || {}).map(edge => edge.last_report_at).filter(stamp => milliseconds(stamp) != null);
      const readStatus = task.read_state?.status || 'unknown';
      return {id: task.task_id, title: task.metadata ? task.metadata.title : task.task_id,
        summary: latest ? latest.title + ' · ' + valueText(latest.value) : task.display_origin === 'unavailable' ? '표시할 정상 기록 없음' : '아직 추적 항목이 없습니다.',
        source: latest ? actorName(latest.updated_by, projection) : null,
        updatedAt: latest ? latest.updated_at : null,
        attention: attention.length > 0, lifecycle: management(task).lifecycle, observing: observing(task),
        unread: readStatus === 'unread', readStatus, lastReportTime: reportTimes.length ? Math.max(...reportTimes.map(milliseconds)) : null,
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
    if (health.read_cursor_error) diagnostics.push('확인 위치: ' + valueText(health.read_cursor_error));
    const taskErrors = taskRows(projection).filter(row => row.error);
    taskErrors.forEach(row => diagnostics.push(row.title + ': ' + valueText([...(row.task.errors || []), ...(row.task.projection_errors || [])])));
    const failed = Boolean(collector.last_error || (runtime.refresh && runtime.refresh.ok === false));
    return {lastSuccess: collector.last_success, diagnostics: [...new Set(diagnostics)],
      warning: diagnostics.length > 0,
      label: failed ? '수집 실패' : taskErrors.length || (health.state_errors || []).length ? '기록 확인 필요' : health.read_cursor_error ? '확인 위치를 읽지 못했습니다' : (observations.warnings || []).length ? '관측 확인 필요' : runtime.running === false ? '관측 중지' : collector.last_success ? '관측 정상' : '수집 기록 없음'};
  }
  function filterRows(rows, query, filter) {
    const needle = query.trim().toLocaleLowerCase();
    return rows.filter(row => (filter === 'archive' ? row.lifecycle === 'completed' : row.lifecycle !== 'completed') && (filter !== 'errors' || row.error) && (filter !== 'attention' || row.attention) && (filter !== 'new' || row.unread) && (!needle || (row.title + '\n' + row.summary).toLocaleLowerCase().includes(needle)))
      .sort((a, b) => Number(Boolean(b.attention)) - Number(Boolean(a.attention)) || Number(Boolean(b.unread)) - Number(Boolean(a.unread)) || (b.lastReportTime || 0) - (a.lastReportTime || 0) || a.id.localeCompare(b.id));
  }
  function roleGroups(projection, taskId) {
    const items = Object.values((projection.items || {})[taskId] || {}).filter(item => !item.archived);
    return ['situation', 'intervention', 'next_action', 'resume'].map(role => ({role, items: items.filter(item => item.role === role && (role !== 'intervention' || item.attention_required === true))})).filter(group => group.items.length);
  }
  const RESOURCE_LABELS = {issue: '이슈', document: '문서·위키', environment: '환경', repository: '저장소', branch: '브랜치', pr: 'PR', communication: '소통', deliverable: '산출물', other: '기타'};
  function candidateDraft(candidate, resourceId, sessionIds) {
    const kind = ({ticket: 'issue', issue: 'issue', pr: 'pr', repo: 'repository', repository: 'repository', branch: 'branch', commit: 'other', worktree: 'environment'})[candidate.kind] || 'other';
    const target = candidate.value;
    return {resource_id: resourceId, kind, label: target.length > 300 ? target.slice(0, 297) + '…' : target, target,
      open_mode: /^https?:\/\//i.test(target) ? 'url' : target.startsWith('/') && candidate.kind !== 'worktree' ? 'file' : 'copy',
      evidence: (candidate.sources || []).filter(source => !sessionIds || sessionIds.includes(source.session_id)).map(source => ({session_id: source.session_id, kind: 'session_event', reference: JSON.stringify({source_ref: source.source_ref, node_id: source.node_id, source_hash: source.source_hash, workspace: source.workspace, verification: source.verification})}))};
  }
  function sessionActivity(session) {
    return [{at: session?.activity?.last_seen, source: '보고 훅'}, {at: session?.usage?.source_activity_at, source: 'Devin Local', stale: Boolean(session?.usage_stale)}]
      .filter(entry => milliseconds(entry.at) != null).sort((a, b) => milliseconds(b.at) - milliseconds(a.at))[0] || null;
  }
  function usageRows(session) {
    const usage = session?.usage;
    return [['input_total', '입력 합계'], ['input_new', '새 입력'], ['input_cached', '캐시 입력 · 입력 합계에 포함'], ['output', '출력']]
      .map(([key, label]) => ({key, label, value: usage?.status === 'available' && Number.isSafeInteger(usage[key]) && usage[key] >= 0 ? usage[key] : null}));
  }
  function readDescription(task, health) {
    const status = task.read_state?.status || 'unknown';
    if (status === 'unavailable') {
      if (health?.read_cursor_error) return '확인 위치를 읽지 못했습니다. 진단을 확인하세요.';
      if (task.display_origin === 'last_good') return '마지막 정상 기록을 표시하고 있어 새 보고의 확인 상태를 판단할 수 없습니다.';
      if (task.display_origin === 'unavailable') return '태스크 기록을 읽을 수 없어 보고의 확인 상태를 판단할 수 없습니다.';
      return '현재 보고의 확인 상태를 판단할 수 없습니다.';
    }
    return ({unread: '새 보고가 있습니다.', read: '현재 보고를 확인했습니다.', unknown: '아직 보고의 확인 위치를 알 수 없습니다.'})[status] || '확인 위치 정보 없음';
  }
  function historyChanges(current, previous) {
    if (!previous || current.revision !== previous.revision + 1) return null;
    const changes = [];
    const oldItems = new Map((previous.items || []).map(item => [item.item_id, item]));
    for (const item of current.items || []) {
      const before = oldItems.get(item.item_id);
      if (!before) changes.push({title: item.title, field: '추가', before: null, after: item.value});
      else for (const field of ['title', 'value', 'status', 'role', 'attention_required', 'pinned', 'archived']) {
        if (JSON.stringify(before[field]) !== JSON.stringify(item[field])) changes.push({title: item.title, field, before: before[field], after: item[field]});
      }
      oldItems.delete(item.item_id);
    }
    for (const item of oldItems.values()) changes.push({title: item.title, field: '제거', before: item.value, after: null});
    const oldNotes = new Set((previous.notes || []).map(note => note.note_id));
    for (const note of current.notes || []) if (!oldNotes.has(note.note_id)) changes.push({title: '메모', field: '추가', before: null, after: note.text});
    for (const field of ['title', 'view', 'contributions', 'resources', 'management']) if (JSON.stringify(previous[field]) !== JSON.stringify(current[field])) changes.push({title: '태스크', field, before: previous[field], after: current[field]});
    return changes;
  }
  const helpers = {milliseconds, time, valueText, actorName, taskRows, collectionHealth, filterRows, roleGroups, historyChanges, candidateDraft, RESOURCE_LABELS, management, observing, sessionActivity, usageRows, readDescription};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (!root.document) return;

  const document = root.document;
  const state = {projection: null, query: '', filter: 'all', selected: null, session: null, homeScroll: 0, tasks: new Map(), sessions: new Map(), pending: new Map(), sequence: 0};
  const $ = id => document.getElementById(id);
  function text(node, value) { const next = value == null ? '' : String(value); if (node.textContent !== next) node.textContent = next; }
  function post(action, payload) {
    const handler = root.webkit && root.webkit.messageHandlers && root.webkit.messageHandlers.observer;
    if (handler) handler.postMessage(JSON.stringify(Object.assign({action}, payload || {})));
  }
  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content != null) text(element, content);
    return element;
  }
  function timeLabel(value) { const result = time(value); return typeof result === 'string' ? result : result.relative; }
  function absoluteTime(value) { const result = time(value); return typeof result === 'string' ? result : result.absolute; }
  function canWrite(task) { return task && task.display_origin === 'current' && task.health === 'ok' && !state.projection.read_only && state.projection.capabilities?.writes !== false; }
  function request(action, taskId, payload, onResult) {
    const requestId = 'ui-' + Date.now().toString(36) + '-' + (++state.sequence);
    state.pending.set(requestId, {action, taskId, onResult});
    post(action, Object.assign({request_id: requestId, task_id: taskId}, payload || {}));
    return requestId;
  }
  function actionResult(response) {
    const pending = response && state.pending.get(response.request_id);
    if (!pending) return false;
    state.pending.delete(response.request_id);
    pending.onResult(response);
    if (response.ok && !['history', 'resource_open', 'resource_copy', 'session_copy', 'devin_activate'].includes(pending.action)) post('state_refresh');
    return true;
  }
  function actionMessage(element, message, error) { text(element, message); element.classList.toggle('error', Boolean(error)); }
  function failure(response) { return response.error?.code === 'revision_conflict' || response.error?.code === 'conflict' ? '그사이 다른 업데이트가 있었습니다. 최신 내용을 확인한 뒤 다시 저장하세요.' : response.error?.message || valueText(response.error) || '처리하지 못했습니다.'; }
  function openTask(id) {
    if (!state.selected) state.homeScroll = $('task-list').scrollTop;
    saveDetailScroll();
    state.selected = id;
    state.session = null;
    $('home').hidden = true;
    $('detail').hidden = false;
    renderDetail();
    $('back').focus();
  }
  function back() {
    saveDetailScroll();
    if (state.session) {
      state.session = null;
      renderDetail();
      $('back').focus();
      return;
    }
    $('home').hidden = false;
    $('detail').hidden = true;
    const selected = state.selected;
    state.selected = null;
    renderHome();
    $('task-list').scrollTop = state.homeScroll;
    Array.from($('task-list').children).find(card => card.dataset.taskId === selected)?.focus({preventScroll: true});
  }
  function saveDetailScroll() {
    const sessionView = state.sessions.get(state.session);
    if (sessionView) { sessionView.scroll = $('detail-body').scrollTop; return; }
    const view = state.tasks.get(state.selected);
    if (view && !state.session) view.scroll = $('detail-body').scrollTop;
  }
  function openSession(id) { saveDetailScroll(); state.session = id; renderDetail(); $('back').focus(); }
  function renderHome() {
    if (!state.projection) return;
    const all = taskRows(state.projection);
    const rows = filterRows(all, state.query, state.filter);
    const lifecycle = state.projection.capabilities?.task_lifecycle;
    text($('all-label'), lifecycle ? '활성' : '전체');
    text($('task-count'), lifecycle ? all.filter(row => row.lifecycle === 'active').length : all.length);
    text($('error-count'), all.filter(row => row.error && row.lifecycle !== 'completed').length);
    text($('attention-count'), all.filter(row => row.attention && row.lifecycle !== 'completed').length);
    text($('archive-count'), all.filter(row => row.lifecycle === 'completed').length);
    const current = all.filter(row => row.lifecycle !== 'completed');
    const unknownReads = current.filter(row => !['read', 'unread'].includes(row.readStatus)).length;
    const unreadCount = current.filter(row => row.unread).length;
    text($('new-count'), unknownReads && unknownReads === current.length ? '—' : String(unreadCount) + (unknownReads ? '?' : ''));
    $('filter-new').title = unknownReads ? '확인된 새 보고 태스크 ' + unreadCount + '개 · 확인 위치를 알 수 없는 태스크 ' + unknownReads + '개' : '새 보고가 있는 태스크 ' + unreadCount + '개';
    $('filter-new').hidden = !state.projection.capabilities?.task_read_cursor;
    $('filter-archive').hidden = !lifecycle;
    $('filter-attention').hidden = !state.projection.capabilities?.typed_item_roles;
    $('filter-all').setAttribute('aria-pressed', String(state.filter === 'all'));
    $('filter-errors').setAttribute('aria-pressed', String(state.filter === 'errors'));
    $('filter-attention').setAttribute('aria-pressed', String(state.filter === 'attention'));
    $('filter-archive').setAttribute('aria-pressed', String(state.filter === 'archive'));
    $('filter-new').setAttribute('aria-pressed', String(state.filter === 'new'));
    const list = $('task-list');
    const existing = new Map(Array.from(list.children).map(card => [card.dataset.taskId, card]));
    rows.forEach((row, index) => {
      let card = existing.get(row.id);
      if (!card) {
        card = node('button', 'task-card'); card.type = 'button'; card.dataset.taskId = row.id;
        const heading = node('div', 'task-heading'); heading.append(node('span', 'task-icon'), node('span', 'task-title'), node('span', 'task-new'), node('span', 'task-observation'));
        card.append(heading, node('p', 'task-summary'), node('div', 'task-source'), node('div', 'task-warning'));
        card.addEventListener('click', () => openTask(row.id));
      }
      text(card.querySelector('.task-icon'), row.error ? '!' : '◌');
      card.querySelector('.task-icon').setAttribute('aria-hidden', 'true');
      text(card.querySelector('.task-title'), row.title);
      text(card.querySelector('.task-new'), '새 보고'); card.querySelector('.task-new').hidden = !row.unread;
      const pause = card.querySelector('.task-observation'); text(pause, '⏸'); pause.hidden = row.observing || row.lifecycle == null; pause.title = '태스크 자동 반영 꺼짐'; pause.setAttribute('aria-label', '태스크 자동 반영 꺼짐');
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
  function section(parent, title) {
    const heading = node('h3', 'section-title', title); const content = node('div'); parent.append(heading, content);
    return {heading, content};
  }
  function createResourceView(view, taskId) {
    view.resourceGroups = new Map(); view.resourceCards = new Map(); view.candidateCards = new Map();
    view.resourceList = node('div'); view.resourceSection.content.append(view.resourceList);
    view.resourceEmpty = node('p', 'muted', '아직 연결된 리소스가 없습니다.'); view.resourceSection.content.append(view.resourceEmpty);
    view.resourceArchived = node('details', 'resource-group'); view.resourceArchived.append(node('summary', '', '보관한 리소스')); view.resourceArchivedList = node('div'); view.resourceArchived.append(view.resourceArchivedList); view.resourceSection.content.append(view.resourceArchived);
    view.candidates = node('details', 'resource-group'); view.candidateSummary = node('summary', '', '발견된 후보'); view.candidateList = node('div'); view.candidates.append(view.candidateSummary, view.candidateList); view.resourceSection.content.append(view.candidates);
    view.legacyResources = node('details', 'resource-group'); view.legacyResources.append(node('summary', '', '이전 등록 기록')); view.legacyResourceList = node('div'); view.legacyResources.append(view.legacyResourceList); view.resourceSection.content.append(view.legacyResources);
    view.resourceEditor = node('details', 'panel resource-editor'); view.resourceEditor.append(node('summary', '', '리소스 등록'));
    view.resourceForm = node('form', 'resource-form'); view.resourceInputs = {};
    const fields = [['kind', '분류', Object.entries(RESOURCE_LABELS)], ['label', '표시 이름'], ['target', '주소 또는 값'], ['open_mode', '사용 방법', [['url', 'URL 열기'], ['file', '로컬 파일 열기'], ['copy', '값 복사']]]];
    for (const [key, label, options] of fields) {
      const wrapper = node('label', 'form-field'); wrapper.append(node('span', '', label));
      const input = node(options ? 'select' : 'input'); input.setAttribute('aria-label', '리소스 ' + label);
      if (options) for (const [value, title] of options) { const option = node('option', '', title); option.value = value; input.append(option); }
      else { input.type = 'text'; input.maxLength = key === 'label' ? 300 : 4000; input.required = true; }
      input.value = key === 'kind' ? 'other' : key === 'open_mode' ? 'url' : '';
      input.addEventListener(options ? 'change' : 'input', () => { view.resourceDirty = true; if (view.resourceRevision == null) view.resourceRevision = state.projection.tasks[taskId]?.displayed_revision; });
      view.resourceInputs[key] = input; wrapper.append(input); view.resourceForm.append(wrapper);
    }
    view.resourceEvidence = node('p', 'muted'); view.resourceForm.append(view.resourceEvidence);
    const controls = node('div', 'button-row'); view.resourceSave = node('button', 'action-button', '리소스 저장'); view.resourceSave.type = 'submit';
    view.resourceCancel = node('button', 'action-button', '입력 취소'); view.resourceCancel.type = 'button'; view.resourceCancel.addEventListener('click', () => { if (!view.resourcePending) resetResourceForm(view); });
    controls.append(view.resourceSave, view.resourceCancel); view.resourceForm.append(controls);
    view.resourceMessage = node('p', 'action-message'); view.resourceMessage.setAttribute('role', 'status'); view.resourceForm.append(view.resourceMessage);
    view.resourceForm.addEventListener('submit', event => {
      event.preventDefault(); const task = state.projection.tasks[taskId];
      if (!canWrite(task) || view.resourcePending) return;
      const resource = resourceFormValue(view);
      if (!resource.label.trim() || !resource.target.trim()) { actionMessage(view.resourceMessage, '표시 이름과 주소 또는 값을 입력하세요.', true); return; }
      if (resource.evidence.length > 100 || resource.evidence.some(evidence => evidence.reference.length > 2000)) { actionMessage(view.resourceMessage, '근거가 저장 한도를 넘습니다. 근거를 줄이거나 누락하지 않고 등록하려면 별도 정리가 필요합니다.', true); return; }
      view.resourceId = resource.resource_id; view.resourcePending = true; view.resourceSave.disabled = true; view.resourceCancel.disabled = true;
      const submitted = JSON.stringify(resource); actionMessage(view.resourceMessage, '저장 중…');
      const revision = view.resourceConflict ? task.displayed_revision : view.resourceRevision ?? task.displayed_revision;
      request('resource_put', taskId, {resource, expected_revision: revision}, response => {
        view.resourcePending = false; view.resourceCancel.disabled = false;
        if (response.ok && JSON.stringify(resourceFormValue(view)) === submitted) resetResourceForm(view);
        view.resourceConflict = !response.ok && response.error?.code === 'revision_conflict';
        text(view.resourceSave, view.resourceConflict ? '최신 기록에 리소스 저장' : '리소스 저장');
        actionMessage(view.resourceMessage, response.ok ? '리소스를 저장했습니다.' : failure(response), !response.ok);
        view.resourceSave.disabled = !canWrite(state.projection.tasks[taskId]);
      });
    });
    view.resourceEditor.append(view.resourceForm); view.resourceSection.content.append(view.resourceEditor);
  }
  function resourceFormValue(view) {
    const resource = {resource_id: view.resourceId || 'ui-resource-' + Date.now().toString(36) + '-' + (++state.sequence)};
    for (const key of ['kind', 'label', 'target', 'open_mode']) resource[key] = view.resourceInputs[key].value;
    resource.evidence = view.resourceEvidenceValues || []; return resource;
  }
  function resetResourceForm(view) {
    view.resourceId = null; view.resourceRevision = null; view.resourceDirty = false; view.resourceConflict = false; view.resourceEvidenceValues = [];
    for (const [key, input] of Object.entries(view.resourceInputs)) input.value = key === 'kind' ? 'other' : key === 'open_mode' ? 'url' : '';
    text(view.resourceEvidence, ''); text(view.resourceSave, '리소스 저장'); actionMessage(view.resourceMessage, '');
  }
  function editResource(view, taskId, resource) {
    view.resourceEditor.open = true;
    if (view.resourceDirty || view.resourcePending) { actionMessage(view.resourceMessage, '작성 중인 리소스를 먼저 저장하거나 입력을 취소하세요.', true); view.resourceInputs.label.focus(); return; }
    view.resourceId = resource.resource_id; view.resourceRevision = state.projection.tasks[taskId]?.displayed_revision;
    view.resourceEvidenceValues = resource.evidence || []; view.resourceDirty = true; view.resourceConflict = false;
    for (const key of ['kind', 'label', 'target', 'open_mode']) view.resourceInputs[key].value = resource[key];
    text(view.resourceEvidence, view.resourceEvidenceValues.length ? '이 태스크에 연결된 세션의 근거 ' + view.resourceEvidenceValues.length + '개를 함께 보존합니다. 다른 세션의 발견 근거는 후보에 남습니다.' : '');
    text(view.resourceSave, '리소스 저장'); actionMessage(view.resourceMessage, ''); view.resourceInputs.label.focus();
  }
  function renderResources(view, task) {
    const taskId = task.task_id; const resources = task.metadata?.resources || [];
    const candidates = (task.resource_candidate_ids || []).map(id => state.projection.resource_candidates?.[id]).filter(Boolean);
    const legacy = task.metadata?.legacy?.resources || [];
    for (const resource of resources) {
      let card = view.resourceCards.get(resource.resource_id);
      if (!card) {
        card = {root: node('article', 'panel resource-card'), pending: false}; const heading = node('div', 'item-heading'); card.title = node('strong'); heading.append(card.title); card.root.append(heading);
        card.target = node('div', 'resource-target'); card.root.append(card.target);
        const actions = node('div', 'button-row'); card.open = node('button', 'action-button', '열기 ↗'); card.copy = node('button', 'action-button', '복사'); card.edit = node('button', 'action-button', '수정'); card.archive = node('button', 'action-button'); actions.append(card.open, card.copy, card.edit, card.archive); card.root.append(actions);
        card.source = node('p', 'item-provenance'); card.root.append(card.source);
        card.evidence = node('details', 'item-details'); card.evidence.append(node('summary', '', '등록 근거')); card.evidenceText = node('pre'); card.evidence.append(card.evidenceText); card.root.append(card.evidence);
        card.message = node('p', 'action-message'); card.message.setAttribute('role', 'status'); card.root.append(card.message);
        const current = () => state.projection.tasks[taskId]?.metadata?.resources?.find(entry => entry.resource_id === resource.resource_id);
        const activate = action => { if (card.pending || !current()) return; card.pending = true; card.open.disabled = true; card.copy.disabled = true; request(action, taskId, {resource_id: resource.resource_id}, response => { card.pending = false; actionMessage(card.message, response.ok ? action === 'resource_copy' ? '복사했습니다.' : '기본 앱에서 열었습니다.' : (action === 'resource_copy' ? '복사하지 못했습니다. ' : '링크를 열지 못했습니다. 복사를 사용할 수 있습니다. ') + failure(response), !response.ok); card.open.disabled = Boolean(state.projection.read_only); card.copy.disabled = Boolean(state.projection.read_only); }); };
        card.open.addEventListener('click', () => activate(current()?.open_mode === 'copy' ? 'resource_copy' : 'resource_open'));
        card.copy.addEventListener('click', () => activate('resource_copy'));
        card.edit.addEventListener('click', () => { const value = current(); if (value && canWrite(state.projection.tasks[taskId])) editResource(view, taskId, value); });
        card.archive.addEventListener('click', () => { const value = current(); const owner = state.projection.tasks[taskId]; if (!value || !canWrite(owner) || card.pending) return; card.pending = true; card.archive.disabled = true; request('resource_archive', taskId, {resource_id: resource.resource_id, archived: !value.archived, expected_revision: owner.displayed_revision}, response => { card.pending = false; actionMessage(card.message, response.ok ? '리소스 보관 설정을 저장했습니다.' : failure(response), !response.ok); card.archive.disabled = !canWrite(state.projection.tasks[taskId]); }); });
        view.resourceCards.set(resource.resource_id, card);
      }
      text(card.title, resource.label); text(card.target, resource.target); text(card.open, resource.open_mode === 'file' ? '파일 열기 ↗' : '열기 ↗'); card.open.hidden = resource.open_mode === 'copy';
      text(card.archive, resource.archived ? '복원' : '보관'); card.open.disabled = card.copy.disabled = card.pending || Boolean(state.projection.read_only);
      card.edit.disabled = card.archive.disabled = card.pending || !canWrite(task);
      text(card.source, actorName(resource.updated_by, state.projection) + ' · ' + timeLabel(resource.updated_at));
      text(card.evidenceText, valueText(resource.evidence || [])); card.evidence.hidden = !resource.evidence?.length;
      let parent = view.resourceArchivedList;
      if (!resource.archived) {
        if (!view.resourceGroups.has(resource.kind)) { const group = node('details', 'resource-group'); group.open = true; group.append(node('summary', '', RESOURCE_LABELS[resource.kind] || '기타')); const content = node('div'); group.append(content); view.resourceGroups.set(resource.kind, {root: group, content}); view.resourceList.append(group); }
        parent = view.resourceGroups.get(resource.kind).content;
      }
      if (card.root.parentNode !== parent) parent.append(card.root);
    }
    const resourceIds = new Set(resources.map(resource => resource.resource_id));
    for (const [id, card] of view.resourceCards) if (!resourceIds.has(id)) { card.root.remove(); view.resourceCards.delete(id); }
    for (const group of view.resourceGroups.values()) group.root.hidden = !group.content.children.length;
    view.resourceArchived.hidden = !resources.some(resource => resource.archived);
    for (const candidate of candidates) {
      let card = view.candidateCards.get(candidate.candidate_id);
      if (!card) { card = {root: node('article', 'panel'), heading: node('strong'), target: node('pre'), evidence: node('pre'), add: node('button', 'action-button', '이 태스크에 등록')}; const disclosure = node('details', 'item-details'); disclosure.append(node('summary', '', '발견 근거'), card.evidence); card.root.append(card.heading, card.target, node('p', 'muted', '후보 · 태스크 소속 미확정'), card.add, disclosure); card.add.addEventListener('click', () => { const current = state.projection.resource_candidates[candidate.candidate_id]; if (current && canWrite(state.projection.tasks[taskId])) editResource(view, taskId, candidateDraft(current, 'ui-resource-' + Date.now().toString(36) + '-' + (++state.sequence), state.projection.tasks[taskId]?.session_ids || [])); }); view.candidateCards.set(candidate.candidate_id, card); view.candidateList.append(card.root); }
      text(card.heading, RESOURCE_LABELS[candidateDraft(candidate, '').kind]); text(card.target, candidate.value); text(card.evidence, valueText(candidate.sources)); card.add.disabled = !canWrite(task); card.add.title = '이 태스크에 연결된 세션의 근거만 등록합니다. 전체 발견 근거는 보존됩니다.';
    }
    const candidateIds = new Set(candidates.map(candidate => candidate.candidate_id));
    for (const [id, card] of view.candidateCards) if (!candidateIds.has(id)) { card.root.remove(); view.candidateCards.delete(id); }
    view.candidates.hidden = !candidates.length; text(view.candidateSummary, '발견된 후보 ' + candidates.length + '개');
    const legacySignature = JSON.stringify(legacy); if (view.legacyResourceSignature !== legacySignature) { view.legacyResourceSignature = legacySignature; view.legacyResourceList.replaceChildren(); for (const resource of legacy) { const card = node('div', 'panel'); card.append(node('strong', '', resource.kind), node('pre', '', resource.uri || valueText(resource)), node('p', 'muted', '이관 당시의 등록 정보')); view.legacyResourceList.append(card); } }
    view.legacyResources.hidden = !legacy.length; view.resourceEmpty.hidden = Boolean(resources.length || candidates.length || legacy.length);
    for (const input of Object.values(view.resourceInputs)) input.disabled = !canWrite(task);
    view.resourceSave.disabled = view.resourcePending || !canWrite(task); view.resourceCancel.disabled = view.resourcePending;
  }
  function createTaskView(taskId) {
    const view = {root: node('div'), scroll: 0, items: new Map(), sessions: new Map(), history: [], historyLoaded: false, historyMore: true, historyBefore: null, historyPending: false, notePending: false, draftRevision: null, noteConflict: false};
    view.notice = node('p', 'panel'); view.root.append(view.notice);
    view.report = node('p', 'muted'); view.root.append(view.report);
    view.readPanel = node('div', 'read-panel'); view.readStatus = node('p', 'muted'); view.markRead = node('button', 'action-button', '여기까지 확인'); view.readMessage = node('p', 'action-message'); view.readMessage.setAttribute('role', 'status'); view.readPanel.append(view.readStatus, view.markRead, view.readMessage); view.root.append(view.readPanel);
    view.markRead.addEventListener('click', () => {
      const task = state.projection.tasks[taskId]; const position = task?.read_state;
      if (!canWrite(task) || view.readPending || position?.status === 'unavailable' || !Number.isSafeInteger(position?.last_report_revision) || position.last_report_revision < 1) return;
      view.readPending = true; view.markRead.disabled = true;
      request('mark_read', taskId, {expected_revision: task.displayed_revision, through_report_revision: position.last_report_revision}, response => {
        view.readPending = false; actionMessage(view.readMessage, response.ok ? '확인 위치를 저장했습니다.' : failure(response), !response.ok);
        const latest = state.projection.tasks[taskId]; view.markRead.disabled = !canWrite(latest) || latest?.read_state?.status === 'unavailable';
      });
    });
    view.roles = node('div'); view.root.append(view.roles);
    view.resourceSection = section(view.root, '작업 리소스');
    createResourceView(view, taskId);
    view.itemSection = section(view.root, '추적 항목');
    view.sessionSection = section(view.root, '연결 세션');
    view.noteSection = section(view.root, '메모');
    view.notes = node('div'); view.noteSection.content.append(view.notes);
    view.form = node('form', 'panel note-form');
    const label = node('label', 'muted', '메모 추가');
    view.draft = node('textarea'); view.draft.maxLength = 16000; view.draft.setAttribute('aria-label', '메모 추가');
    view.draft.addEventListener('input', () => {
      if (view.draftRevision == null) view.draftRevision = state.projection.tasks[taskId]?.displayed_revision;
      if (!view.draft.value && !view.notePending) { view.draftRevision = null; view.noteConflict = false; text(view.submit, '메모 저장'); }
    });
    view.submit = node('button', 'action-button', '메모 저장'); view.submit.type = 'submit';
    view.noteMessage = node('div', 'action-message'); view.noteMessage.setAttribute('role', 'status');
    view.form.append(label, view.draft, view.submit, view.noteMessage);
    view.form.addEventListener('submit', event => {
      event.preventDefault();
      const task = state.projection.tasks[taskId];
      if (!canWrite(task) || view.notePending || !view.draft.value.trim()) return;
      const submitted = view.draft.value; view.notePending = true; view.submit.disabled = true;
      actionMessage(view.noteMessage, '저장 중…');
      const expected = view.noteConflict ? task.displayed_revision : view.draftRevision ?? task.displayed_revision;
      request('note', taskId, {expected_revision: expected, text: submitted}, response => {
        view.notePending = false;
        if (response.ok && view.draft.value === submitted) { view.draft.value = ''; view.draftRevision = null; }
        view.noteConflict = !response.ok && response.error?.code === 'revision_conflict';
        text(view.submit, view.noteConflict ? '최신 기록에 메모 저장' : '메모 저장');
        actionMessage(view.noteMessage, response.ok ? '메모를 저장했습니다.' : failure(response), !response.ok);
        view.submit.disabled = !canWrite(state.projection.tasks[taskId]);
      });
    });
    view.noteSection.content.append(view.form);
    view.markdown = node('details', 'panel'); view.markdown.append(node('summary', '', '저장된 뷰')); view.markdownText = node('pre'); view.markdown.append(view.markdownText); view.root.append(view.markdown);
    view.historySection = section(view.root, '변경 이력');
    view.historyEntries = node('div', 'panel'); view.historySection.content.append(view.historyEntries);
    view.historyButton = node('button', 'action-button', '이력 불러오기');
    view.historyRefresh = node('button', 'action-button', '최신 이력'); view.historyRefresh.hidden = true;
    view.historyMessage = node('p', 'action-message');
    const loadHistory = latest => {
      if (view.historyPending) return;
      view.historyPending = true; view.historyButton.disabled = true; view.historyRefresh.disabled = true;
      const gapCursor = historyGapCursor(view.history);
      const args = {limit: 20}; if (!latest && (gapCursor ?? view.historyBefore) != null) args.before_revision = gapCursor ?? view.historyBefore;
      request('history', taskId, args, response => {
        view.historyPending = false; view.historyButton.disabled = false; view.historyRefresh.disabled = false;
        if (!response.ok) { actionMessage(view.historyMessage, failure(response), true); return; }
        const page = response.result || {};
        const entries = Array.isArray(page.entries) ? page.entries : [];
        const byRevision = new Map([...view.history, ...entries].map(entry => [entry.revision, entry]));
        view.history = [...byRevision.values()].sort((a, b) => b.revision - a.revision);
        if (!view.historyLoaded || (!latest && gapCursor == null)) {
          view.historyMore = Boolean(page.has_more);
          view.historyBefore = page.before_revision ?? (entries.length ? Math.min(...entries.map(entry => entry.revision)) : null);
        }
        view.historyLoaded = true;
        actionMessage(view.historyMessage, ''); renderHistory(view);
      });
    };
    view.historyButton.addEventListener('click', () => loadHistory(false));
    view.historyRefresh.addEventListener('click', () => loadHistory(true));
    view.historySection.content.append(view.historyButton, view.historyRefresh, view.historyMessage);
    view.managementSection = section(view.root, '태스크 관리');
    view.managementStatus = node('p', 'muted'); view.managementSection.content.append(view.managementStatus);
    view.managementButtons = node('div', 'button-row');
    view.observationButton = node('button', 'action-button'); view.lifecycleButton = node('button', 'action-button');
    view.managementButtons.append(view.observationButton, view.lifecycleButton); view.managementSection.content.append(view.managementButtons);
    view.managementHelp = node('p', 'muted'); view.managementSection.content.append(view.managementHelp);
    view.managementMessage = node('p', 'action-message'); view.managementMessage.setAttribute('role', 'status'); view.managementSection.content.append(view.managementMessage);
    const changeManagement = action => {
      const task = state.projection.tasks[taskId]; if (!canWrite(task) || view.managementPending) return;
      const value = management(task); const args = {expected_revision: task.displayed_revision};
      if (action === 'lifecycle') args.completed = value.lifecycle !== 'completed';
      else { if (value.lifecycle !== 'active') return; args.enabled = !value.observation_enabled; }
      view.managementPending = true; view.lifecycleButton.disabled = view.observationButton.disabled = true;
      request(action, taskId, args, response => {
        view.managementPending = false; actionMessage(view.managementMessage, response.ok ? '태스크 설정을 저장했습니다.' : failure(response), !response.ok);
        view.lifecycleButton.disabled = view.observationButton.disabled = !canWrite(state.projection.tasks[taskId]);
      });
    };
    view.observationButton.addEventListener('click', () => changeManagement('observation'));
    view.lifecycleButton.addEventListener('click', () => changeManagement('lifecycle'));
    state.tasks.set(taskId, view); return view;
  }
  function historyGapCursor(entries) {
    for (let index = 0; index < entries.length - 1; index++) if (entries[index].revision > entries[index + 1].revision + 1) return entries[index].revision;
    return null;
  }
  function renderHistory(view) {
    const scroll = $('detail-body').scrollTop;
    const disclosed = new Set(Array.from(view.historyEntries.children).filter(entry => entry.open).map(entry => entry.dataset.revision));
    view.historyEntries.replaceChildren();
    for (let index = 0; index < view.history.length; index++) {
      const entry = view.history[index]; const details = node('details', 'history-entry');
      details.dataset.revision = String(entry.revision); details.open = disclosed.has(String(entry.revision));
      details.append(node('summary', '', absoluteTime(entry.updated_at) + ' · ' + actorName(entry.updated_by, state.projection) + ' · 변경 ' + entry.revision));
      const changes = historyChanges(entry, view.history[index + 1]);
      if (changes == null) details.append(node('p', 'muted', view.historyMore || view.history[index + 1] ? '이전 이력을 더 불러오면 변경 전 값을 비교할 수 있습니다.' : '이 범위의 첫 기록입니다.'));
      else if (!changes.length) details.append(node('p', 'muted', '추적값 외 상태가 갱신되었습니다.'));
      else for (const change of changes) {
        details.append(node('strong', '', change.title + ' · ' + change.field));
        details.append(node('pre', '', '이전: ' + valueText(change.before) + '\n이후: ' + valueText(change.after)));
      }
      view.historyEntries.append(details);
    }
    view.historyEntries.hidden = !view.history.length;
    view.historyButton.hidden = view.historyLoaded && !view.historyMore && historyGapCursor(view.history) == null;
    view.historyRefresh.hidden = !view.historyLoaded;
    text(view.historyButton, view.historyLoaded ? '이전 이력 더 보기' : '이력 불러오기');
    if (view.historyLoaded && !view.history.length) actionMessage(view.historyMessage, '저장된 이력이 없습니다.');
    $('detail-body').scrollTop = scroll;
  }
  function itemCard(view, taskId, item) {
    let card = view.items.get(item.item_id);
    if (!card) {
      card = {root: node('article', 'panel'), signature: null, pending: false};
      const heading = node('div', 'item-heading'); card.title = node('strong'); card.status = node('span', 'item-status'); heading.append(card.title, card.status);
      card.value = node('div', 'item-value item-preview'); card.source = node('p', 'item-provenance');
      card.details = node('details', 'item-details'); card.details.append(node('summary', '', '전체 내용과 근거'));
      card.full = node('pre'); card.evidence = node('div'); card.diagnostics = node('p', 'muted');
      card.pin = node('button', 'action-button'); card.pin.type = 'button'; card.message = node('div', 'action-message'); card.message.setAttribute('role', 'status');
      card.pin.addEventListener('click', () => {
        const task = state.projection.tasks[taskId]; const current = state.projection.items[taskId]?.[item.item_id];
        if (!current || !canWrite(task) || card.pending) return;
        card.pending = true; card.pin.disabled = true; actionMessage(card.message, '저장 중…');
        request('pin', taskId, {expected_revision: task.displayed_revision, item_id: item.item_id, pinned: !current.pinned}, response => {
          card.pending = false; actionMessage(card.message, response.ok ? '고정 설정을 저장했습니다.' : failure(response), !response.ok);
          card.pin.disabled = !canWrite(state.projection.tasks[taskId]);
        });
      });
      card.details.append(card.full, card.evidence, card.diagnostics, card.pin, card.message);
      card.root.append(heading, card.value, card.source, card.details); view.items.set(item.item_id, card);
    }
    text(card.title, item.title); text(card.status, (item.archived ? '보관 · ' : '') + item.status + (item.pinned ? ' · 고정' : ''));
    text(card.value, valueText(item.value)); text(card.full, valueText(item.value));
    text(card.source, actorName(item.updated_by, state.projection) + ' · ' + timeLabel(item.updated_at)); card.source.title = absoluteTime(item.updated_at);
    text(card.diagnostics, '항목 ' + item.item_id + ' · 변경 ' + item.item_revision + ' · 보고 ' + item.report_id + '\n' + absoluteTime(item.updated_at));
    text(card.pin, item.pinned ? '고정 해제' : '값 고정'); card.pin.disabled = card.pending || !canWrite(state.projection.tasks[taskId]);
    const evidenceSignature = JSON.stringify(item.evidence);
    if (card.signature !== evidenceSignature) {
      card.signature = evidenceSignature; card.evidence.replaceChildren();
      if (!item.evidence?.length) card.evidence.append(node('p', 'muted', '연결된 근거 없음'));
      for (const evidence of item.evidence || []) {
        const entry = node('div', 'fact');
        const button = node('button', 'action-button', actorName({kind: 'session', session_id: evidence.session_id}, state.projection));
        button.addEventListener('click', () => openSession(evidence.session_id));
        entry.append(button, node('pre', '', evidence.kind + ' · ' + evidence.reference)); card.evidence.append(entry);
      }
    }
    return card.root;
  }
  function renderTask(view, task) {
    const taskId = task.task_id;
    const notice = task.display_origin === 'last_good' ? '마지막 정상 기록 · ' + absoluteTime(task.displayed_updated_at) : task.display_origin === 'unavailable' ? '표시할 정상 기록 없음' : state.projection.read_only ? '읽기 전용 미리보기' : '';
    text(view.notice, notice); view.notice.hidden = !notice;
    const edges = state.projection.contributions?.[taskId] || {};
    const reports = Object.values(edges).map(edge => edge.last_report_at).filter(Boolean).sort((a, b) => milliseconds(b) - milliseconds(a));
    text(view.report, reports.length ? '최근 보고 · ' + timeLabel(reports[0]) : '아직 보고된 내용이 없습니다.');
    const read = task.read_state || {status: 'unknown', last_report_revision: null};
    view.readPanel.hidden = !state.projection.capabilities?.task_read_cursor;
    text(view.readStatus, readDescription(task, state.projection.health));
    view.markRead.disabled = view.readPending || !canWrite(task) || ['read', 'unavailable'].includes(read.status) || !Number.isSafeInteger(read.last_report_revision) || read.last_report_revision < 1;
    const groups = roleGroups(state.projection, taskId);
    const roleSignature = JSON.stringify(groups);
    if (view.roleSignature !== roleSignature) {
      view.roleSignature = roleSignature; view.roles.replaceChildren();
      const labels = {situation: '현재 상황', intervention: '내가 할 일', next_action: '다음 행동', resume: '복귀 지점'};
      for (const group of groups) {
        const part = section(view.roles, labels[group.role]);
        for (const item of group.items) {
          const panel = node('div', 'panel'); panel.append(node('div', 'item-value', valueText(item.value)), node('p', 'item-provenance', actorName(item.updated_by, state.projection) + ' · ' + timeLabel(item.updated_at))); part.content.append(panel);
        }
      }
    }
    renderResources(view, task);
    text(view.itemSection.heading, '추적 항목 ' + task.item_ids.length + '개');
    const activeIds = new Set(task.item_ids);
    task.item_ids.forEach((id, index) => { const item = state.projection.items[taskId]?.[id]; if (!item) return; const card = itemCard(view, taskId, item); if (view.itemSection.content.children[index] !== card) view.itemSection.content.insertBefore(card, view.itemSection.content.children[index] || null); });
    for (const [id, card] of view.items) if (!activeIds.has(id)) { card.root.remove(); view.items.delete(id); }
    text(view.sessionSection.heading, '연결 세션 ' + task.session_ids.length + '개');
    for (const id of task.session_ids) {
      let card = view.sessions.get(id);
      if (!card) { card = node('button', 'panel session-button'); card.type = 'button'; card.append(node('strong'), node('div', 'muted'), node('div', 'muted')); card.addEventListener('click', () => openSession(id)); view.sessions.set(id, card); view.sessionSection.content.append(card); }
      const session = state.projection.sessions[id]; const edge = edges[id];
      text(card.children[0], session?.observation?.title || id);
      text(card.children[1], '자동보고 ' + (edge?.reporting ? '켜짐' : '꺼짐') + (edge?.reporting && !observing(task) ? ' · 태스크 반영 중지' : '') + ' · 최근 보고 ' + (edge?.last_report_at ? timeLabel(edge.last_report_at) : '없음'));
      const activity = sessionActivity(session);
      text(card.children[2], '최근 활동 · ' + (activity ? timeLabel(activity.at) + ' · ' + activity.source : '정보 없음'));
    }
    for (const [id, card] of view.sessions) if (!task.session_ids.includes(id)) { card.remove(); view.sessions.delete(id); }
    const notesSignature = JSON.stringify(task.metadata?.notes || []);
    if (view.notesSignature !== notesSignature) { view.notesSignature = notesSignature; view.notes.replaceChildren(); for (const note of task.metadata?.notes || []) { const card = node('div', 'panel'); card.append(node('div', 'item-value', note.text), node('div', 'muted', absoluteTime(note.created_at))); view.notes.append(card); } }
    view.draft.disabled = !canWrite(task); view.submit.disabled = view.notePending || !canWrite(task);
    text(view.markdownText, task.metadata?.view?.markdown || ''); view.markdown.hidden = !task.metadata?.view?.markdown;
    view.historyButton.disabled = view.historyPending || Boolean(state.projection.read_only);
    view.historyRefresh.disabled = view.historyPending || Boolean(state.projection.read_only);
    if (state.projection.read_only && !view.historyLoaded) actionMessage(view.historyMessage, '미리보기에서는 저장 이력을 불러오지 않습니다.');
    const settings = management(task); const completed = settings.lifecycle === 'completed';
    view.managementSection.heading.hidden = view.managementSection.content.hidden = !state.projection.capabilities?.task_lifecycle;
    text(view.managementStatus, completed ? '완료 · 보관됨' : '활성 · 자동 반영 ' + (settings.observation_enabled ? '켜짐' : '꺼짐'));
    text(view.lifecycleButton, completed ? '활성으로 복원' : '완료하고 보관');
    text(view.observationButton, settings.observation_enabled ? '자동 반영 끄기' : '자동 반영 켜기'); view.observationButton.hidden = completed;
    view.lifecycleButton.disabled = view.observationButton.disabled = view.managementPending || !canWrite(task);
    text(view.managementHelp, completed ? '복원하면 자동 반영이 ' + (settings.previous_observation_enabled ? '켜집니다.' : '꺼진 상태로 돌아갑니다.') : '자동 반영을 꺼도 공유 세션 관측과 저장된 기록은 유지됩니다.');
  }
  function createSessionView(sessionId) {
    const view = {root: node('div'), scroll: 0, edges: new Map()};
    view.notice = node('p', 'panel'); view.root.append(view.notice);
    const facts = node('dl', 'panel'); view.facts = {};
    for (const [key, title] of [['id', '세션 ID'], ['activity', '최근 활동'], ['report', '최근 보고 · 연결 태스크 중'], ['workspace', '작업 공간']]) {
      const part = node('div', 'fact'); const value = node('dd'); part.append(node('dt', '', title), value); facts.append(part); view.facts[key] = value;
    }
    view.root.append(facts);
    view.resume = node('div', 'panel'); view.resume.append(node('p', 'muted', '세션 ID로 열린 Devin에서 찾을 수 있습니다.'));
    const resumeActions = node('div', 'button-row'); view.copyId = node('button', 'action-button', 'ID 복사'); view.activateDevin = node('button', 'action-button', 'Devin Desktop으로 이동');
    resumeActions.append(view.copyId, view.activateDevin); view.resume.append(resumeActions); view.resumeMessage = node('p', 'action-message'); view.resumeMessage.setAttribute('role', 'status'); view.resume.append(view.resumeMessage); view.root.append(view.resume);
    const sessionAction = action => {
      if (view.resumePending || state.projection.read_only) return;
      const taskId = state.selected; view.resumePending = true; view.copyId.disabled = view.activateDevin.disabled = true;
      request(action, taskId, {session_id: sessionId}, response => {
        view.resumePending = false; view.copyId.disabled = view.activateDevin.disabled = Boolean(state.projection.read_only);
        actionMessage(view.resumeMessage, response.ok ? action === 'session_copy' ? '세션 ID를 복사했습니다.' : '열린 Devin Desktop으로 이동했습니다.' : failure(response), !response.ok);
      });
    };
    view.copyId.addEventListener('click', () => sessionAction('session_copy'));
    view.activateDevin.addEventListener('click', () => sessionAction('devin_activate'));
    view.usage = node('details', 'panel usage-panel'); view.usage.append(node('summary', '', '토큰 사용량'));
    view.usageStatus = node('p', 'muted'); view.usage.append(view.usageStatus);
    view.usageGrid = node('dl', 'usage-grid'); view.metrics = {};
    for (const row of usageRows(null)) { const part = node('div', 'fact'); const value = node('dd'); part.append(node('dt', '', row.label), value); view.metrics[row.key] = value; view.usageGrid.append(part); }
    view.usage.append(view.usageGrid);
    view.usageTime = node('p', 'item-provenance'); view.usageSource = node('p', 'item-provenance'); view.usageBoundary = node('p', 'muted');
    view.usageDiagnostics = node('details', 'item-details'); view.usageDiagnostics.append(node('summary', '', '측정 정보')); view.usageDiagnosticText = node('pre'); view.usageDiagnostics.append(view.usageDiagnosticText);
    view.usage.append(view.usageTime, view.usageSource, view.usageBoundary, view.usageDiagnostics); view.root.append(view.usage);
    view.tasks = section(view.root, '연결 태스크'); state.sessions.set(sessionId, view); return view;
  }
  function renderSession() {
    const sessionId = state.session; const session = state.projection.sessions[sessionId];
    const view = state.sessions.get(sessionId) || createSessionView(sessionId);
    const title = session?.observation?.title || sessionId; const client = session?.client_type || session?.observation?.client_type;
    text($('detail-title'), title + (client ? ' · ' + client : ''));
    if ($('detail-body').firstChild !== view.root) { $('detail-body').replaceChildren(view.root); $('detail-body').scrollTop = view.scroll; }
    view.notice.hidden = Boolean(session); text(view.notice, '이 세션의 관측 정보가 없습니다.');
    for (const child of view.root.children) if (child !== view.notice) child.hidden = !session;
    if (!session) return;
    view.copyId.disabled = view.activateDevin.disabled = view.resumePending || Boolean(state.projection.read_only);
    text(view.facts.id, session.session_id);
    const activity = sessionActivity(session);
    text(view.facts.activity, activity ? absoluteTime(activity.at) + ' · ' + activity.source + (activity.stale ? ' · 마지막 확인 기록' : '') : '정보 없음');
    text(view.facts.workspace, session.observation?.workspace || session.usage?.workspace || session.activity?.workspace || '정보 없음');
    const reports = session.task_ids.map(taskId => state.projection.contributions?.[taskId]?.[sessionId]?.last_report_at).filter(Boolean).sort((a, b) => milliseconds(b) - milliseconds(a));
    text(view.facts.report, reports.length ? absoluteTime(reports[0]) : '보고 기록 없음');
    const usage = session.usage; const available = usage?.status === 'available';
    text(view.usageStatus, available ? session.usage_stale ? '마지막 측정값 · 갱신 확인 필요' : '현재 남아 있는 메시지의 측정값' : '사용량 정보 없음');
    view.usageStatus.classList.toggle('warning', Boolean(session.usage_stale && available));
    view.usageGrid.hidden = !available;
    for (const row of usageRows(session)) text(view.metrics[row.key], row.value == null ? '정보 없음' : row.value.toLocaleString('ko-KR'));
    text(view.usageTime, usage?.measured_at ? '측정 · ' + absoluteTime(usage.measured_at) : '');
    text(view.usageSource, usage?.source ? '출처 · ' + usage.source : '');
    text(view.usageBoundary, usage ? '세션 단위 기록이며 연결 태스크에 나누어 합산하지 않습니다.' + (usage.may_include_inherited_history ? ' 포크 이전 기록이 포함될 수 있습니다.' : '') + (usage.billing_usage === false ? ' 청구 사용량이 아닙니다.' : '') : '');
    const diagnostics = {status: usage?.status || 'unavailable', reason: usage?.reason || null, coverage: usage?.coverage || null, last_read_error: session.usage_error || state.projection.health?.usage?.error || null, lifetime_complete: usage?.lifetime_complete ?? null, message_count: usage?.message_count ?? null, metric_node_count: usage?.metric_node_count ?? null, duplicate_node_count: usage?.duplicate_node_count ?? null};
    text(view.usageDiagnosticText, valueText(diagnostics));
    text(view.tasks.heading, '연결 태스크 ' + session.task_ids.length + '개');
    for (const taskId of session.task_ids) {
      const task = state.projection.tasks[taskId]; const edge = state.projection.contributions?.[taskId]?.[sessionId];
      let card = view.edges.get(taskId);
      if (!card) {
        card = {root: node('article', 'panel session-task-card'), pending: false}; card.title = node('button', 'session-task-title'); card.title.addEventListener('click', () => openTask(taskId));
        card.status = node('p', 'muted'); card.report = node('p', 'muted'); card.toggle = node('button', 'action-button'); card.message = node('p', 'action-message'); card.message.setAttribute('role', 'status');
        card.toggle.addEventListener('click', () => {
          const currentTask = state.projection.tasks[taskId]; const currentEdge = state.projection.contributions?.[taskId]?.[sessionId];
          if (!currentEdge || !canWrite(currentTask) || card.pending) return;
          card.pending = true; card.toggle.disabled = true;
          request('reporting', taskId, {session_id: sessionId, enabled: !currentEdge.reporting, expected_revision: currentTask.displayed_revision}, response => {
            card.pending = false; actionMessage(card.message, response.ok ? '자동보고 설정을 저장했습니다.' : failure(response), !response.ok); card.toggle.disabled = !canWrite(state.projection.tasks[taskId]);
          });
        });
        card.root.append(card.title, card.status, card.report, card.toggle, card.message); view.edges.set(taskId, card); view.tasks.content.append(card.root);
      }
      text(card.title, task?.metadata?.title || taskId);
      text(card.status, '자동보고 ' + (edge?.reporting ? '켜짐' : '꺼짐') + (task && !observing(task) ? ' · 태스크 자동 반영 중지' : ''));
      text(card.report, '최근 보고 · ' + (edge?.last_report_at ? absoluteTime(edge.last_report_at) : '없음'));
      text(card.toggle, edge?.reporting ? '자동보고 끄기' : '자동보고 켜기'); card.toggle.setAttribute('aria-pressed', String(Boolean(edge?.reporting))); card.toggle.disabled = card.pending || !canWrite(task);
    }
    for (const [id, card] of view.edges) if (!session.task_ids.includes(id)) { card.root.remove(); view.edges.delete(id); }
  }
  function renderDetail() {
    if (!state.selected || !state.projection) return;
    if (state.session) { renderSession(); return; }
    const task = state.projection.tasks[state.selected];
    const view = state.tasks.get(state.selected) || createTaskView(state.selected);
    text($('detail-title'), task?.metadata?.title || state.selected);
    const mounted = $('detail-body').firstChild === view.root;
    if (!mounted) { $('detail-body').replaceChildren(view.root); $('detail-body').scrollTop = view.scroll; }
    for (const child of view.root.children) if (child !== view.notice) child.hidden = !task;
    if (!task) { text(view.notice, '현재 목록에서 이 태스크를 찾을 수 없습니다.'); view.notice.hidden = false; view.draft.disabled = true; view.submit.disabled = true; return; }
    renderTask(view, task);
  }
  function render() {
    const health = collectionHealth(state.projection);
    text($('health'), health.label); $('health').classList.toggle('warning', health.warning);
    $('diagnostics').hidden = !health.diagnostics.length && !$('diagnostics').open;
    text($('diagnostic-summary'), health.diagnostics.length ? '진단 보기' : '관측 정보');
    text($('diagnostic-text'), health.diagnostics.length ? health.diagnostics.join('\n\n') : '현재 보고된 관측 오류가 없습니다.\n태스크 ' + Object.keys(state.projection.tasks).length + '개 · 연결·관측 세션 ' + Object.keys(state.projection.sessions || {}).length + '개\n마지막 성공 수집 · ' + (health.lastSuccess ? absoluteTime(health.lastSuccess) : '기록 없음'));
    $('show-diagnostics').setAttribute('aria-expanded', String(Boolean($('diagnostics').open)));
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
    const appearance = ['light', 'dark', 'system'].includes(projection.appearance) ? projection.appearance : 'system';
    if (document.documentElement && document.documentElement.dataset.appearance !== appearance) document.documentElement.dataset.appearance = appearance;
    state.projection = projection;
    $('message').hidden = true;
    render(); return true;
  }
  function start() {
    $('search').addEventListener('input', event => { state.query = event.target.value; renderHome(); });
    ['all', 'errors', 'attention', 'new', 'archive'].forEach(filter => $('filter-' + filter).addEventListener('click', () => { state.filter = filter; renderHome(); }));
    $('refresh').addEventListener('click', () => post('refresh'));
    $('show-diagnostics').addEventListener('click', () => { $('diagnostics').open = !$('diagnostics').open; if (state.projection) render(); else { $('diagnostics').hidden = !$('diagnostics').open; text($('diagnostic-text'), '아직 관측 정보를 받지 못했습니다.'); $('show-diagnostics').setAttribute('aria-expanded', String($('diagnostics').open)); } });
    $('diagnostics').addEventListener('toggle', () => { $('show-diagnostics').setAttribute('aria-expanded', String(Boolean($('diagnostics').open))); if (state.projection && !collectionHealth(state.projection).diagnostics.length) $('diagnostics').hidden = !$('diagnostics').open; });
    $('quit').addEventListener('click', () => post('quit'));
    $('back').addEventListener('click', back);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); if (state.selected) back(); else post('close'); }
      if (event.key === 'ArrowLeft' && event.altKey && state.selected) { event.preventDefault(); back(); }
    });
    post('ready');
  }
  root.Observer = {receive, actionResult, helpers};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})(typeof window === 'undefined' ? globalThis : window);
