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
  return btn('근거 · '+(evidence.session_title||'세션')+' · 노드 '+evidence.node_id,()=>run(async()=>{
    const q=new URLSearchParams({session:evidence.session_id,node:String(evidence.node_id),hash:evidence.content_hash});
    const data=await api('/api/session-evidence?'+q);
    if(current===task && navigationEpoch===epoch)container.append(...evidenceElements(data));
  }));
}
function renderProjection(data) {
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
