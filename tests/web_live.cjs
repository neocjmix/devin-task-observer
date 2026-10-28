const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ids=new Map();
class Element {
  constructor(){this.children=[];this.dataset={};this.replacements=0;this.value='';}
  set id(x){ids.set(x,this);this._id=x;} get id(){return this._id;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.replacements++;this.children=items;}
}
const context=vm.createContext({console,Map,Array,JSON,encodeURIComponent,URLSearchParams});
Object.assign(context,{$:id=>ids.get(id),el:(_,text)=>Object.assign(new Element(),{text}),btn:(text,onclick)=>Object.assign(new Element(),{text,onclick}),run:fn=>fn(),evidenceElements:x=>[x],current:'a',currentSession:null,evidenceSession:null,evidenceNodes:[],reviewSession:'s',reviewData:null});
vm.runInContext(fs.readFileSync('observer/web/live.js','utf8'),context);
const call=x=>vm.runInContext(x,context),change=id=>({id,node_id:id,kinds:['user'],transition:'added',content_hash:'hash'+id});
const page=(n,start=1)=>({revision:0,last_observation_id:start-1,changes:Array.from({length:n},(_,i)=>change(i+start)),has_more:n===50});
(async()=>{
  const root=new Element();root.id='review';const draft=new Element();draft.value='unsaved user input';const detail=new Element();detail.id='detail';detail.append(root,draft);
  context.api=async path=>path.startsWith('/api/task-observer')?{resources:[],plans:[],health:[]}:page(1);await call('refreshOpen()');
  const first=ids.get('review-items').children[0],expanded=new Element();first.append(expanded);
  context.api=async path=>path.startsWith('/api/task-observer')?{resources:[],plans:[],health:[]}:page(2);await call('refreshOpen()');
  assert.equal(ids.get('review-items').children.length,2);assert.equal(ids.get('review-items').children[0],first);assert(first.children.includes(expanded));assert.equal(draft.value,'unsaved user input');assert(detail.children.includes(draft));assert.equal(detail.replacements,0);
  context.api=async path=>path.startsWith('/api/task-observer')?{resources:[],plans:[],health:[]}:page(50);await call('refreshOpen()');assert.equal(ids.get('review-items').children.length,50);
  context.api=async path=>path.startsWith('/api/task-observer')?{resources:[],plans:[],health:[]}:page(1,51);await call('refreshOpen()');assert.equal(context.reviewData.last_observation_id,50);assert.equal(ids.get('review-items').children[0].dataset.observation,'51');
  let resolve;context.api=path=>path.startsWith('/api/task-observer')?Promise.resolve({resources:[],plans:[],health:[]}):new Promise(r=>resolve=r);const stale=call('refreshOpen()');call("navigateTask('b');navigateTask('a')");resolve(page(3));await stale;assert.equal(context.reviewData,null);
  const pending=[];context.api=()=>new Promise(r=>pending.push(r));const a=call("loadReview('s1')"),b=call("loadReview('s2')");pending[1](page(2));await b;pending[0](page(1));await a;assert.equal(context.reviewSession,'s2');assert.equal(context.reviewData.changes.length,2);
  const html=fs.readFileSync('observer/web/index.html','utf8');
  const handler=html.match(/\$\('refresh'\)\.onclick=([\s\S]*?);run\(loadState\)/);assert(handler);
  context.api=async path=>path==='/api/refresh'?{ok:true}:path.startsWith('/api/task-observer')?{resources:[],plans:[],health:[]}:page(3);
  context.loadState=()=>call('refreshOpen()');context.loadSession=async()=>{};
  const refresh=vm.runInContext('('+handler[1]+')',context);await refresh();assert.equal(context.reviewData.changes.length,3);assert(detail.children.includes(draft));
  const detailSource=html.split('\n').find(line=>line.startsWith('async function loadDetail()'));
  vm.runInContext(detailSource,context);
  const detailRequests=[];context.api=()=>new Promise(r=>detailRequests.push(r));context.render=()=>{};context.refreshOpen=async()=>{};
  const older=call('loadDetail()'),newer=call('loadDetail()');
  detailRequests[1]({view:{id:'v',revision:2}});await newer;detailRequests[0]({view:{id:'v',revision:1}});await older;
  assert.equal(context.view.revision,2);
  const apiSource=html.split('\n').find(line=>line.startsWith('async function api('));
  vm.runInContext(apiSource,context);context.token='old';context.tokenRefresh=null;
  const requests=[];context.fetch=async(path,options)=>{
    requests.push([path,{...options.headers}]);
    if(path==='/api/bootstrap')return {ok:true,status:200,json:async()=>({token:'a'.repeat(64)})};
    if(options.headers['X-Observer-Token']==='old')return {ok:false,status:403,json:async()=>({error:'forbidden'})};
    return {ok:true,status:200,json:async()=>({ok:true})};
  };
  await call("api('/api/state')");assert.equal(context.token,'a'.repeat(64));assert.equal(requests.length,3);assert(detail.children.includes(draft));
  console.log('Live/manual refresh, retained evidence/drafts, pagination and navigation races passed');
})().catch(e=>{console.error(e);process.exitCode=1});
