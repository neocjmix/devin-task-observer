// Update observational regions without replacing any user-editable DOM.
let navigationEpoch = 0, reviewLoadSeq = 0, liveLoadSeq = 0, detailLoadSeq = 0;
let sidebarKey = '';
function navigateTask(id) {
  navigationEpoch++;
  current=id; currentSession=null; evidenceSession=null; evidenceNodes=[];
  reviewSession=null; reviewData=null;
}
function navigateSession(id) {
  navigationEpoch++;
  current=null; currentSession=id; sessionOffset=0; sessionMode='index';
}
async function loadReview(sid) {
  const task=current, epoch=navigationEpoch, seq=++reviewLoadSeq;
  reviewSession=sid;
  const data=await api('/api/task/changes?task='+encodeURIComponent(task)+'&session='+encodeURIComponent(sid));
  if(current!==task || navigationEpoch!==epoch || reviewSession!==sid || seq!==reviewLoadSeq)return;
  reviewData=data;
  renderReview();
}
function renderReview() {
  const root=$('review');
  if(!root)return;
  if(!reviewSession || !reviewData){root.replaceChildren();return;}
  const key=current+'|'+reviewSession;
  if(root.dataset.key!==key){
    root.replaceChildren(); root.dataset.key=key;
    root.append(el('h3','태스크에서 확인할 변경'),el('p','수집 순서이며 실제 대화 순서는 아닙니다. 동일하거나 확인된 타이밍 값만 다른 반복 노드는 생략하지만 원본 관측은 보존합니다. 확인 위치는 태스크별로 별도 저장됩니다.','muted'));
    const list=el('div');list.id='review-items';root.append(list);
    const controls=el('div');controls.id='review-controls';root.append(controls);
  }
  const list=$('review-items'), old=new Map(Array.from(list.children).map(n=>[n.dataset.observation,n]));
  const task=current,sid=reviewSession,epoch=navigationEpoch;
  const rows=reviewData.changes.map(change=>{
    const key=String(change.id);if(old.has(key))return old.get(key);
    const card=el('div');card.dataset.observation=key;
    const line=el('div',undefined,'row');
    line.append(el('span',change.transition,'badge'),el('span',change.kinds.join(', ')+' · 노드 '+change.node_id,'muted'),btn('원본 보기',()=>run(async()=>{
      const q=new URLSearchParams({session:sid,node:String(change.node_id),hash:change.content_hash});
      const data=await api('/api/session-evidence?'+q);
      if(current===task && reviewSession===sid && navigationEpoch===epoch)card.append(...evidenceElements(data));
    })));card.append(line);return card;
  });
  // Reuse rows, including any expanded evidence, when new observations arrive.
  if(rows.length!==list.children.length || rows.some((n,i)=>n!==list.children[i]))list.replaceChildren(...rows);
  const controls=$('review-controls');
  const signature=JSON.stringify([reviewData.revision,reviewData.changes.map(c=>c.id),reviewData.has_more]);
  if(controls.dataset.signature===signature)return;
  controls.dataset.signature=signature;controls.replaceChildren();
  if(!rows.length)controls.append(el('p','확인할 변경이 없습니다.','muted'));
  else {
    const through=reviewData.changes.at(-1).id,revision=reviewData.revision;
    controls.append(btn('표시된 변경 확인 처리',()=>run(async()=>{
      reviewLoadSeq++;liveLoadSeq++;
      await api('/api/task/changes/acknowledge',{task_id:task,session_id:sid,revision,through_id:through});
      if(current!==task || navigationEpoch!==epoch)return;
      await loadState();
    })));
    if(reviewData.has_more)controls.append(el('p','이후 변경이 더 있습니다.','muted'));
  }
}
async function refreshOpen() {
  if(!current)return;
  const task=current,sid=reviewSession,epoch=navigationEpoch,seq=++liveLoadSeq,reviewSeq=reviewLoadSeq;
  const [data,projection]=await Promise.all([
    sid?api('/api/task/changes?task='+encodeURIComponent(task)+'&session='+encodeURIComponent(sid)):null,
    api('/api/task-observer?task='+encodeURIComponent(task))
  ]);
  if(current!==task || navigationEpoch!==epoch || seq!==liveLoadSeq || reviewSession!==sid)return;
  if(data && reviewLoadSeq===reviewSeq){reviewData=data;renderReview();}
  renderProjection(projection);
}

function sourceButton(evidence,container) {
  if(!evidence.session_id)return el('p','로컬 Git에서 확인 · 현재 작업 공간의 값이며 세션 당시 상태나 태스크 소속은 미검증','muted');
  const task=current,epoch=navigationEpoch;
  return btn('근거 · '+(evidence.session_title||evidence.session_id||'세션')+' · 노드 '+evidence.node_id,()=>run(async()=>{
    const q=new URLSearchParams({session:evidence.session_id,node:String(evidence.node_id),hash:evidence.content_hash});
    const data=await api('/api/session-evidence?'+q);
    if(current===task && navigationEpoch===epoch)container.append(...evidenceElements(data));
  }));
}
function renderProjection(data) {
  renderTracking(data.semantic);
  const {semantic,...observed}=data;data=observed;
  const root=$('observer-summary');if(!root)return;
  const signature=JSON.stringify(data);if(root.dataset.signature===signature)return;
  root.dataset.signature=signature;root.replaceChildren();
  root.append(el('h3','자동 관측 요약'),el('p','아래 정보는 사용자 메모와 별도로 유지합니다. 후보 확인 없이도 새 관측은 자동 표시됩니다.','muted'));
  for(const h of data.health){if(h.state!=='readable')root.append(el('p',(h.title||'세션')+' · '+({unavailable:'수집 실패 · 이전 상태 유지',readable_ambiguous:'원본은 관측 가능 · 해석 모호',readable_changed:'원본 구조 변경 · 검토 필요'}[h.state]),'muted'))}
  for(const git of data.git||[]){if(!git.available)root.append(el('p','Git 관측 미확인 · '+git.workspace+' · 개인 Git 작업 공간 루트와 Git 실행 환경을 확인하세요.','muted'))}
  const resources=el('div',undefined,'section');resources.append(el('h3','자동 발견 리소스'));
  if(!data.resources.length)resources.append(el('p','아직 발견한 리소스가 없습니다.','muted'));
  const task=current,epoch=navigationEpoch;
  for(const resource of data.resources){
    const card=el('div',undefined,'item');card.append(el('p',resource.kind+(resource.number?' #'+resource.number:'')+' · '+resource.value));if(resource.workspace)card.append(el('p','작업 공간 · '+resource.workspace,'muted'));
    card.append(el('p',(resource.association==='confirmed'?'연결 확인됨':resource.association==='confirmed_resource_edited'?'연결된 자료가 수동 수정됨':resource.association==='confirmed_archived'?'확인한 연결이 보관됨':'연결 후보')+' · 외부 상태 unknown'+(resource.current?'':' · 과거 근거 또는 관측 확인 필요'),'muted'));
    if(resource.evidence.some(e=>e.verification==='local_git'&&e.current))card.append(el('p','로컬 Git에서 값 확인 · 작업 공간 범위이며 태스크 소속과 외부 상태는 미검증','muted'));
    if(resource.evidence.some(e=>e.shared_session))card.append(el('p','여러 태스크에 연결된 세션에서 발견 · 이 태스크 소속으로 자동 확정하지 않습니다.','muted'));
    if(resource.association==='candidate'&&resource.current)card.append(btn('이 태스크의 자료로 연결 확인',()=>run(async()=>{
      await api('/api/resource/confirm',{task_id:task,key:resource.key});
      if(current===task&&navigationEpoch===epoch)await refreshOpen();
    })));
    const details=el('details');details.append(el('summary','발견 근거 '+resource.evidence.length+'개'));
    for(const e of resource.evidence){const line=el('div');line.append(el('p',(e.current?'현재 원본':'이전 관측')+' · '+(e.verification==='local_git'?'로컬에서 값 확인':'텍스트 언급 · 미검증'),'muted'),sourceButton(e,line));details.append(line)}
    card.append(details);resources.append(card);
  }root.append(resources);
  const plans=el('div',undefined,'section');plans.append(el('h3','세션별 작업 계획'));
  const labels={added:'항목 추가',removed_or_replaced:'목록에서 제거 또는 문구 대체',agent_reported_completed:'에이전트가 완료 보고',agent_reported_reopened:'에이전트가 재개 보고',reported_status_changed:'보고 상태 변경',ambiguous_item_identity:'동일 문구 항목이 있어 변경 대응 불명확',ambiguous_predecessors:'이전 계획의 순서를 확정할 수 없음',unsupported_shape:'계획 형식 미지원'};
  for(const session of data.plans){
    const box=el('div',undefined,'item');box.append(el('h3',session.title||'세션'),el('p','에이전트가 보고한 계획이며 실제 완료는 미검증 · '+(session.ordering==='uncertain'?'순서 또는 원본 해석 불확실 · 최신 계획을 자동 확정하지 않음':session.ordering==='source_lineage'?'원본의 연결 관계로 계획 변경을 구분':'계획 근거 없음'),'muted'));
    function versionCard(version){
      const card=el('div',undefined,'section');
      if(version.items===null)card.append(el('p','관측된 계획 형식을 해석할 수 없습니다.','muted'));
      else for(const item of version.items)card.append(el('p',item.source_status+' · '+item.content));
      for(const change of version.changes)card.append(el('p',(labels[change.type]||change.type)+(change.content?' · '+change.content:'')+(change.previous_status?' ('+change.previous_status+' → '+(change.source_status||'목록에 없음')+')':''),'muted'));
      for(const e of version.evidence)card.append(sourceButton(e,card));return card;
    }
    for(const v of session.versions.filter(v=>session.ordering==='uncertain'||session.heads.includes(v.id)))box.append(versionCard(v));
    const history=session.ordering==='uncertain'?[]:session.versions.filter(v=>!session.heads.includes(v.id));
    if(history.length){const detail=el('details');detail.append(el('summary','이전 계획 근거 '+history.length+'개'));for(const v of history)detail.append(versionCard(v));box.append(detail)}
    plans.append(box);
  }root.append(plans);
}

function renderTracking(data) {
  const root=$('semantic-summary');if(!root||!data)return;
  let state=$('semantic-state');
  if(!state){state=el('div');state.id='semantic-state';const editor=el('div');editor.id='semantic-editor';root.append(state,editor)}
  const signature=JSON.stringify(data);if(state.dataset.signature===signature)return;
  state.dataset.signature=signature;state.replaceChildren();
  const task=current,epoch=navigationEpoch;
  state.append(el('h3','태스크 현재 상태 · 의미 분석'),el('p','저장된 모델 해석입니다. 보고·가설과 실제 검증은 다릅니다. 화면 조회와 일반 새로고침은 모델을 호출하지 않습니다.','muted'));
  if(data.enabled)state.append(el('p','분석 경로: '+(data.adapter==='codex_exec'?'개인 Codex CLI · 선택된 근거를 개인 계정으로 전달':'승인된 Devin ACP · 독립 분석 세션'),'muted'));
  const freshness={current:'처리한 관측까지 반영',pending:'새 변경 또는 근거 확인 필요',partial:'미지원 관측 유형이 있어 부분 해석',never_analyzed:'아직 의미 분석하지 않음'};
  state.append(el('p',(data.enabled?'수동 Semantic Refresh 사용':'의미 분석 비활성')+' · '+freshness[data.freshness]+' · 대기 관측 '+data.pending_observations+(data.pending_count_capped?'+':'')+(data.last_success?' · 마지막 반영 '+new Date(data.last_success/1000000).toLocaleString():''),'muted'));
  const errors={semantic_disabled:'의미 분석이 꺼져 있습니다.',semantic_budget_exhausted:'오늘의 호출·예약 예산을 소진했습니다. 기존 상태와 미처리 변경은 유지됩니다.',semantic_source_gap:'아직 해석하지 못한 원본이 변경되거나 사라졌습니다. 의미 분석 위치를 진행하지 않았습니다.',semantic_event_too_large:'한 원본 항목이 분석 크기 제한을 초과했습니다. 자동으로 잘라 해석하지 않습니다.',explicit_retry_required:'실패 기록이 있습니다. 원인을 확인한 뒤 명시적으로 재시도하세요.',semantic_revision_conflict:'분석 중 태스크나 사용자 정보가 변경됐습니다. 이전 결과를 적용하지 않았습니다.',semantic_busy:'다른 의미 분석이 진행 중입니다.',timeout:'모델 실행 시간이 초과됐습니다.',codex_auth_required:'개인 Codex 인증이 필요합니다.',acp_auth_or_request_failed:'승인된 Devin ACP의 인증 또는 요청이 실패했습니다.'};
  if(data.last_error)state.append(el('p',errors[data.last_error]||('분석 미반영 · '+data.last_error),'message'));
  if(data.unsupported_observations)state.append(el('p','미지원 관측 '+data.unsupported_observations+'건 · 의미 정보 포함 여부를 확정할 수 없습니다.','muted'));
  if(data.enabled){
    const action=retry=>run(async()=>{
      show('연결된 세션의 새 변경을 분석하고 있습니다.');
      const result=await api('/api/semantic/refresh',{task_id:task,retry});
      if(current!==task||navigationEpoch!==epoch)return;
      await refreshOpen();
      show(result.model_called?'의미 분석 반영 · '+(result.operations||0)+'개 연산'+(result.usage?.input_tokens!=null?' · 입력 '+result.usage.input_tokens+' / 출력 '+result.usage.output_tokens+' 토큰':' · 사용량 미확인'):'새로운 분석 대상 없음 · 모델 호출 0회');
    });
    state.append(btn('Semantic Refresh',()=>action(false),'primary'));
    if(data.last_error||data.runs.some(r=>['failed','interrupted'].includes(r.status)))state.append(btn('실패한 증분 재시도',()=>action(true)));
  }
  const labels={progress:'진행 상황',blocker:'블로커',decision:'결정',question:'미해결 질문',next_action:'다음 행동',finding:'발견',hypothesis:'가설'};
  if(!data.items.length)state.append(el('p','저장된 의미 분석 상태가 없습니다. 구조화 계획과 리소스는 아래에서 계속 확인할 수 있습니다.','muted'));
  for(const category of Object.keys(labels)){
    const items=data.items.filter(i=>i.category===category);if(!items.length)continue;
    const section=el('div',undefined,'section');section.append(el('h3',labels[category]));
    const historical=items.filter(i=>['resolved','superseded'].includes(i.status));
    const history=el('details');if(historical.length)history.append(el('summary','해결·이전 기록 '+historical.length+'개'));
    if(historical.length===items.length)section.append(el('p','현재 열린 항목 없음 · 이전 보고와 해결 근거는 아래에 보존합니다.','muted'));
    for(const item of items){
      const card=el('div',undefined,'item');card.append(el('p',item.content),el('p',item.status+' · '+(item.epistemic==='hypothesis'?'가설':'출처의 보고에 대한 해석')+' · '+(item.owner==='human'?'사용자 수정 우선':'자동 관리')+(item.pinned?' · 고정됨':'')+(item.source_current?'':' · 원본 변경·연결 해제 등 근거 재확인 필요'),'muted'));
      if(item.conflicts.length)card.append(el('p','상충하는 주장 '+item.conflicts.length+'개 · 어느 쪽도 자동 확정하지 않았습니다.','message'));
      if(item.attribution_uncertain)card.append(el('p','공유 세션 근거 포함 · 태스크 소속에 대한 해석은 미확정입니다.','muted'));
      card.append(btn('수정',()=>{
        if(current!==task||navigationEpoch!==epoch)return;
        const editor=$('semantic-editor');editor.replaceChildren();
        const input=el('textarea'),key='semantic:'+item.id;input.setAttribute('aria-label','의미 분석 항목 수정');input.value=drafts.get(key)??item.content;input.oninput=()=>drafts.set(key,input.value);
        editor.append(el('p','수정하면 이 항목은 사용자 소유로 유지됩니다.','muted'),input,btn('수정 저장',()=>run(async()=>{
          const latest=await api('/api/task-observer?task='+encodeURIComponent(task));
          const stored=latest.semantic.items.find(i=>i.id===item.id);
          if(!stored||stored.revision!==item.revision)throw Error('이 항목이 변경됐습니다. 초안은 유지했습니다. 현재 항목을 확인한 뒤 수정 화면을 다시 열어 주세요.');
          if(current!==task||navigationEpoch!==epoch)return;
          await api('/api/semantic/item',{task_id:task,item_id:item.id,revision:latest.semantic.revision,content:input.value});
          drafts.delete(key);if(current!==task||navigationEpoch!==epoch)return;editor.replaceChildren();await refreshOpen();
        })),btn('닫기',()=>editor.replaceChildren()));input.focus();
      }),btn(item.pinned?'고정 해제':'고정',()=>run(async()=>{
        await api('/api/semantic/item',{task_id:task,item_id:item.id,revision:data.revision,pinned:!item.pinned});
        if(current===task&&navigationEpoch===epoch)await refreshOpen();
      })));
      for(const evidence of item.evidence)card.append(sourceButton(evidence,card));
      if(['resolved','superseded'].includes(item.status))history.append(card);else section.append(card);
    }if(historical.length)section.append(history);state.append(section);
  }
  for(const p of data.proposals.filter(p=>p.status==='pending')){
    const box=el('div',undefined,'section');box.append(el('p','검토가 필요한 변경 · '+p.operation.op+' · '+p.operation.content,'muted'));
    const choices=['supersede','propose_section'].includes(p.operation.op)?[[true,'변경 승인'],[false,'거절']]:[[false,'제안 거절']];
    if(choices.length===1)box.append(el('p','이 변경은 해당 항목의 수정 기능에서 직접 판단할 수 있습니다.','muted'));
    for(const [approve,label] of choices)box.append(btn(label,()=>run(async()=>{
      await api('/api/semantic/proposal',{task_id:task,proposal_id:p.id,revision:data.revision,approve});
      if(current===task&&navigationEpoch===epoch)await refreshOpen();
    })));state.append(box);
  }
  if(data.runs.length){const last=data.runs[0];state.append(el('p','최근 실행 '+last.status+' · 예약 '+last.reserved_tokens+' 토큰 · '+(last.input_tokens===null?'실제 사용량 미확인':('실제 입력 '+last.input_tokens+' / 출력 '+last.output_tokens)),'muted'))}
}
