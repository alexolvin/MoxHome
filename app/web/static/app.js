"use strict";
const $=id=>document.getElementById(id);
const S=JSON.parse($("u3-strings").textContent);
const ui=JSON.parse($("u3-ui").textContent);
const enc=encodeURIComponent;
const $ce=t=>document.createElement(t);
const NS=S.common_not_set;
const ICO=(n,s)=>"<svg class='u3-ico"+(s?" u3-ico-"+s:"")+"' aria-hidden='true'><use href='/static/icons.svg#"+n+"'></use></svg>";
const ST_ICO={wait:"pause",run:"play",ok:"check",warn:"triangle-alert",err:"x"};
const cmdFail=e=>fmt(S.common_cmd_failed,{msg:e.message});
const apiErr=e=>fmt(S.common_err,{msg:e.message});
const JH={"Content-Type":"application/json"};
const nowTime=()=>new Date().toLocaleTimeString();
const RETRY=a=>" <button type='button' data-"+a+">"+esc(S.common_retry)+"</button>";
const RFR=()=>ui.refresh_seconds*1000;
const onVis=(ok,fn)=>document.addEventListener("visibilitychange",()=>{if(ok()&&document.visibilityState==="visible")fn();});
const csrfToken=()=>(document.querySelector('meta[name="u3-csrf"]')||{}).content||"";
const ESCM={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"};
function esc(value){
return String(value==null?NS:value).replace(/[&<>"]/g,c=>ESCM[c]);
}
function fmt(t,v){
return String(t).replace(/\{(\w+)\}/g,(m,k)=>k in v?String(v[k]):m);
}
function humanSize(numBytes){
if(numBytes==null)return NS;
const units=[[1024**3,S.common_size_gb],[1024**2,S.common_size_mb],[1024,S.common_size_kb]];
for(const [divider,unit] of units){
if(numBytes>=divider)return (numBytes/divider).toFixed(1)+" "+unit;
}
return String(numBytes)+" "+S.common_size_b;
}
async function fetchJSON(url,options){
options=options||{};
const method=options.method||"GET";
if(method!=="GET"&&method!=="HEAD"){
const headers=Object.assign({},options.headers);
const token=csrfToken();
if(token)headers["X-U3-CSRF"]=token;
options.headers=headers;
}
const response=await fetch(url,options);
const text=await response.text();
let data=null;
try{
data=JSON.parse(text);
}catch{}
if(!response.ok){
const detail=(data&&data.detail)||text||String(response.status);
const error=new Error(detail);
error.status=response.status;
throw error;
}
return data;
}
function filteredApiUrl(url){
if(!url||url.indexOf("?")!==-1)return url;
const params=new URLSearchParams(window.location.search||"");
if(!params.size)return url;
params.delete("page");
const query=params.toString();
return query?url+"?"+query:url;
}
function fromHtml(html){
const t=document.createElement("template");
t.innerHTML=html.trim();
return t.content.firstElementChild;
}
function isNetErr(e){
return e instanceof TypeError||/failed to fetch|networkerror/i.test(String(e.message));
}
function netbar(el){
const old=el.querySelector(":scope > .u3-netbar");
if(old&&old.remove)old.remove();
const bar=$ce("div");
bar.className="u3-netbar";
bar.setAttribute("role","status");
bar.textContent=S.state_netbar;
if(el.prepend)el.prepend(bar);
}
function cardHtml(fields,actionsHtml){
const dl=fields.map(field=>"<div class='u3-field'><dt>"+esc(field[0])+"</dt><dd"+(field[2]?" class='"+field[2]+"'":"")+">"+field[1]+"</dd></div>").join("");
const actions=actionsHtml?"<div class='u3-card-actions'>"+actionsHtml+"</div>":"";
return "<article class='u3-card'><dl class='u3-card-fields'>"+dl+"</dl>"+actions+"</article>";
}
function tableHtml(columns,rowsHtml){
return "<table class='u3-table'><thead><tr>"+columns.map(c=>"<th scope='col'>"+esc(c)+"</th>").join("")+"</tr></thead><tbody>"+rowsHtml+"</tbody></table>";
}
function cardsHtml(cards){
return "<div class='u3-cards'>"+cards.join("")+"</div>";
}
function jobMode(j){
return esc(j.mode)+(j.learning_level?" / "+esc(j.learning_level)+(j.learning_cefr?" ("+esc(j.learning_cefr)+")":""):"");
}
function jobPair(j){
return esc(j.source_display||j.source_language||NS)+" → "+esc(j.target_language);
}
const TERMINAL_JOB_STATUSES=new Set(["COMPLETED","COMPLETED_WITH_WARNINGS","FAILED","CANCELLED"]);
function rowActionHtml(j){
const st=j.status;
const id=esc(j.id);
const csrf="<input type='hidden' name='u3_csrf' value='"+esc(csrfToken())+"'>";
const F=(action,url,label,glyph)=>"<form class='u3-row-form' method='post' action='"+url+"'>"+csrf+"<button type='submit' class='u3-row-btn' data-row-action='"+action+"' aria-label='"+esc(label)+"'><span aria-hidden='true'>"+glyph+"</span></button></form>";
if(st==="NEEDS_CONFIRMATION")return F("confirm","/jobs/"+id+"/confirm",S.job_row_confirm,ICO("play",18));
if(st==="CREATED"||j.status_group==="run")return F("pause","/api/jobs/"+id+"/pause",st==="CREATED"?S.job_row_postpone:S.job_row_pause,ICO("pause",18));
if(st==="PAUSED"||st==="INTERRUPTED")return F("resume","/api/jobs/"+id+"/resume",S.job_row_resume,ICO("play",18));
if(st==="COMPLETED"||st==="COMPLETED_WITH_WARNINGS")return "<a class='u3-row-btn' href='/jobs?sheet=export&job="+id+"' data-sheet-open='u3-sheet-export' data-job='"+id+"' data-book-name='"+esc(j.book_name)+"' aria-label='"+esc(S.job_row_export)+"'><span aria-hidden='true'>"+ICO("download",18)+"</span></a>";
return "<a class='u3-row-btn' data-row-retry='"+id+"' href='/jobs?sheet=create&retry_of="+id+"' aria-label='"+esc(S.job_row_retry)+"'><span aria-hidden='true'>"+ICO("rotate-ccw",18)+"</span></a>";
}
function rowSwipeHtml(j){
const id=esc(j.id);
return "<span class='u3-row-swipe' aria-hidden='true'><button type='button' class='u3-swipe-btn u3-danger' data-swap='delete' data-job='"+id+"' data-book-name='"+esc(j.book_name)+"'>"+esc(S.job_swipe_delete)+"</button></span>";
}
function rowActsTail(j){
const st=j.status;
const id=esc(j.id);
let h="";
if(!TERMINAL_JOB_STATUSES.has(st))h+="<button type='button' class='u3-row-btn' data-swap='cancel' data-job='"+id+"' aria-label='"+esc(S.job_swipe_cancel)+"'><span aria-hidden='true'>"+ICO("x",18)+"</span></button>";
h+="<a class='u3-row-btn' href='/jobs/"+id+"#logs' aria-label='"+esc(S.job_swipe_logs)+"'><span aria-hidden='true'>"+ICO("file-text",18)+"</span></a>";
return h;
}
const GROUP_KEYS=["active","queue","done"];
const GROUP_LABELS={active:"jobs_group_active",queue:"jobs_group_queue",done:"jobs_group_done"};
const GROUP_SORT={
active:(a,b)=>((a.status_group==="run"?0:1)-(b.status_group==="run"?0:1))||b.updated_at-a.updated_at,
queue:(a,b)=>(b.queue_rank||0)-(a.queue_rank||0)||a.created_at-b.created_at||(a.id<b.id?-1:1),
done:(a,b)=>b.updated_at-a.updated_at,
};
function jobLine2(j){
return (j.source_display||j.source_language||NS)+"→"+j.target_language+" · "+j.mode+(j.learning_cefr?" · "+j.learning_cefr:"");
}
function fmtDuration(totalSeconds){
const m=Math.max(0,Math.floor((totalSeconds||0)/60));
if(m<60)return fmt(S.time_min,{n:m});
return fmt(S.time_hm,{h:Math.floor(m/60),m:m%60});
}
function jobLine3(j){
const pct=j.progress_pct==null?"":"<span class='u3-row-sep'> · </span>"+j.progress_pct+"%";
return "<span class='u3-status status-"+esc(j.status_group)+"' title='"+esc(j.status)+"'><span class='u3-status-ico' aria-hidden='true'>"+ICO(ST_ICO[j.status_group],14)+"</span></span>"+pct+"<span class='u3-row-sep'> · </span>"+esc(j.time_label||NS);
}
function jobRowHtml(j,group){
const strip=j.progress_pct==null?"<span class='u3-row-strip is-ind' aria-hidden='true'></span>":"<span class='u3-row-strip' aria-hidden='true'"+(j.progress_pct>0?" style='width: "+j.progress_pct+"%'":"")+"></span>";
return "<article class='u3-row u3-row-job status-"+esc(j.status_group)+"' data-id='"+esc(j.id)+"' data-group='"+esc(group)+"' data-status='"+esc(j.status)+"' data-status-group='"+esc(j.status_group)+"' data-rev='"+esc(j.updated_at)+"' data-book='"+esc(j.book_name)+"' data-queue-rank='"+esc(j.queue_rank)+"'><div class='u3-row-slide'><a class='u3-row-body' href='/jobs/"+esc(j.id)+"'><span class='u3-row-line1' title='"+esc(j.book_title||j.book_name)+"'>"+esc(j.book_title||j.book_name)+"</span><span class='u3-row-line2'>"+esc(jobLine2(j))+"</span><span class='u3-row-line3'>"+jobLine3(j)+"</span></a><span class='u3-row-acts'>"+rowActionHtml(j)+rowActsTail(j)+"</span></div>"+rowSwipeHtml(j)+strip+"</article>";
}
function bookCoverHtml(b,eager){
if(b.has_cover&&b.cover_v){
const base="/books/"+enc(b.name)+"/cover?v="+encodeURIComponent(b.cover_v);
return "<span class='u3-book-cover-img'><img srcset='"+base+"&w=256 256w, "+base+"&w=512 512w' sizes='(max-width: 640px) 33vw, 160px' width='256' height='384' loading='"+(eager?"eager":"lazy")+"' decoding='async' alt=''></span>";
}
const grad=(b.cover_grad||0)+1;
const author=b.author?"<span class='u3-book-ph-author'>"+esc(b.author)+"</span>":"";
return "<span class='u3-book-ph' style='background:var(--cover-g"+grad+")'><svg class='u3-book-ph-ico' aria-hidden='true'><use href='/static/icons.svg#book-open'></use></svg><span class='u3-book-ph-title'>"+esc(b.title||b.name)+"</span>"+author+"</span>";
}
function bookCardHtml(b,eager){
const title=b.title||b.name;
const cover=bookCoverHtml(b,eager);
const g=b.book_status_group||"none";
const stl={wait:S.book_st_wait,run:S.book_st_run,ok:S.book_st_ok,warn:S.book_st_warn,err:S.book_st_err,none:S.book_st_none}[g]||S.book_st_none;
return "<article class='u3-book-card' data-name='"+esc(b.name)+"'><a class='u3-book-card-link' href='/books/"+enc(b.name)+"'>"+cover+"<span class='u3-book-card-title' title='"+esc(title)+"'>"+esc(title)+"</span><span class='u3-book-card-st u3-st-"+g+"'>"+esc(stl)+"</span></a></article>";
}
const RENDERERS={
quality(el,data){
let html="";
if(data.qc&&Object.keys(data.qc).length){
html+="<h3>"+esc(S.qc_title)+"</h3><pre>"+esc(JSON.stringify(data.qc))+"</pre>";
}
const a=data.alignment||{};
html+="<h3>"+esc(S.qc_alignment_title)+"</h3><p>"+(a.coverage!=null?esc(fmt(S.qc_alignment_coverage,{pct:(a.coverage*100).toFixed(1),aligned:a.aligned,total:a.total})):esc(S.qc_alignment_na))+"</p>";
const g=data.glossary_consistency;
html+="<h3>"+esc(S.qc_glossary_title)+"</h3><p>"+(g&&typeof g==="object"&&g.consistent_share!=null?esc(fmt(S.qc_glossary_value,{share:(g.consistent_share*100).toFixed(1)})):esc(S.qc_glossary_na))+"</p>";
if(Array.isArray(data.warnings)&&data.warnings.length){
html+="<h3>"+esc(S.qc_warnings)+"</h3><ul>"+data.warnings.map(w=>"<li>"+esc(w)+"</li>").join("")+"</ul>";
}
const l3=data.l3;
html+="<h3>"+esc(S.qc_l3_title)+"</h3>";
if(l3){
html+="<p>"+esc(fmt(S.qc_l3_stats,{attempted:l3.attempted,applied:l3.applied,rejected:l3.rejected,halted:l3.halted}))+"</p>";
if(Array.isArray(l3.fixes)&&l3.fixes.length){
const cols=[S.qc_l3_col_segment,S.qc_l3_col_doc,S.qc_l3_col_term,S.qc_l3_col_applied,S.qc_l3_col_old,S.qc_l3_col_new,S.qc_l3_col_reason];
html+="<table class='u3-static'><thead><tr>"+cols.map(c=>"<th scope='col'>"+esc(c)+"</th>").join("")+"</tr></thead><tbody>";
for(const f of l3.fixes){
html+="<tr><td>"+esc(f.segment_id)+"</td><td>"+esc(f.doc)+"</td><td>"+esc(f.term)+"</td><td>"+(f.applied?esc(S.common_yes):esc(S.common_no))+"</td><td>"+esc(f.old_text)+"</td><td>"+esc(f.new_text)+"</td><td>"+esc(f.reason||NS)+"</td></tr>";
}
html+="</tbody></table>";
}
}else{
html+="<p>"+esc(S.qc_l3_none)+"</p>";
}
el.innerHTML=html;
},
logs(el,data){
const blocks=[["job.log",data.job],["errors.log",data.errors],["quality.log",data.quality]];
el.innerHTML=blocks.map(([name,lines])=>"<h3>"+name+"</h3><pre>"+(Array.isArray(lines)&&lines.length?esc(lines.join("\n")):esc(S.job_logs_empty))+"</pre>").join("");
},
};
const reqRow=text=>"<p id='u3-job-requested' role='status' class='u3-warn'>"+esc(text)+"</p>";
function renderProgress(f){
const seg=f.segments?f.segments.processed+" / "+f.segments.total:NS;
const align=f.alignment.coverage!=null?(f.alignment.coverage*100).toFixed(1)+"% ("+f.alignment.aligned+" / "+f.alignment.total+")":NS;
const tokens=Object.entries(f.tokens).filter(([name])=>name!=="total").map(([name,t])=>esc(name)+": "+esc(t.total)).join(" · ");
const timeCell=fmt(S.job_time_elapsed,{n:f.elapsed_seconds})+(f.status==="PAUSED"&&f.paused_at_human?" · "+fmt(S.job_paused_since,{t:f.paused_at_human}):f.eta_seconds!=null?" · "+fmt(S.job_time_eta,{n:f.eta_seconds}):"");
const rows=[
[S.job_row_status,"<span class='u3-status status-"+esc(f.status_group)+"'><span class='u3-status-ico' aria-hidden='true'>"+ICO(ST_ICO[f.status_group],14)+"</span>"+esc(f.status)+(f.error?" — <span class='err'>"+esc(f.error)+"</span>":"")+"</span>"],
[S.job_row_pair,jobPair(f)],
[S.job_row_mode,jobMode(f)],
[S.job_row_progress,esc(f.progress_display)],
[S.job_row_phase,(f.phase?esc(f.phase):NS)+" ("+esc(f.phases.completed)+" / "+esc(f.phases.total)+")"],
[S.job_row_segments,seg],
[S.job_row_alignment,align],
[S.job_row_tokens,(tokens?tokens+" · ":"")+esc(S.job_tokens_total)+": "+esc(f.tokens.total)],
[S.job_row_time,timeCell],
[S.job_row_errors,fmt(S.job_errors_segments,{n:f.failed_segments})+" · "+fmt(S.job_errors_retries,{n:f.retries})],
[S.job_row_created,esc(f.created_at_human)],
[S.job_row_updated,esc(f.updated_at_human)],
];
let html=f.cancel_requested?reqRow(S.job_cancel_requested):f.pause_requested?reqRow(S.job_pause_requested):"";
html+="<table class='u3-facts'>";
for(const [label,cell] of rows){
html+="<tr><th scope='row'>"+esc(label)+"</th><td>"+cell+"</td></tr>";
}
html+="</table>";
if(f.stages&&f.stages.length){
const cols=[S.job_stages_col_time,S.job_stages_col_transition,S.job_stages_col_reason];
const tableRows=f.stages.map(st=>"<tr><td>"+esc(st.at_human)+"</td><td>"+esc(st.from)+" → "+esc(st.to)+"</td><td>"+esc(st.reason||NS)+"</td></tr>").join("");
const cards=f.stages.map(st=>cardHtml([[S.job_stages_col_time,esc(st.at_human)],[S.job_stages_col_transition,esc(st.from)+" → "+esc(st.to)],[S.job_stages_col_reason,esc(st.reason||NS)]]));
html+="<details id='u1-stages-details'><summary>"+esc(fmt(S.job_stages,{n:f.stages.length}))+"</summary>"+tableHtml(cols,tableRows)+cardsHtml(cards)+"</details>";
}
if(f.warnings&&f.warnings.length){
html+="<p>"+esc(S.job_warnings)+"</p><ul>"+f.warnings.map(w=>"<li>"+esc(w)+"</li>").join("")+"</ul>";
}
return html;
}
const INACTIVE_JOB_STATUSES=new Set(["COMPLETED","COMPLETED_WITH_WARNINGS","FAILED","CANCELLED","PAUSED"]);
let jobsListRetry;
let jobsRefresh;
let jobsGetRow;
function startJobsList(){
const el=$("u1-jobs");
if(!el||!el.dataset.u1Fetch)return;
const body=el.querySelector(":scope > .u3-list-body");
if(!body)return;
const announceEl=$("u3-jobs-announce");
const refreshEl=$("u3-jobs-refresh");
const group=el.dataset.u1Group||"all";
const statusSet=(el.dataset.u1Status||"").split(",").filter(Boolean);
const bookFilter=el.dataset.u1Book||"";
const sortKey=el.dataset.u1Sort==="created_at"?"created_at":"updated_at";
const sortDir=el.dataset.u1Direction==="asc"?"asc":"desc";
const GROUP_OF_PILL={active:["run"],waiting:["wait"],done:["ok","warn"],errors:["err"]};
const allowGroups=GROUP_OF_PILL[group]||null;
const rowsById=new Map();
const nodesById=new Map();
jobsGetRow=id=>rowsById.get(id);
const rowsEls={};
const secEls={};
let lastRev=0;
let timer;
let stopped=false;
let inFlight=false;
let backoffMs=RFR();
let hasData=false;
const prevStatus=new Map();
const activeIds=new Set();
const isInactive=s=>INACTIVE_JOB_STATUSES.has(s);
const matches=j=>{
if(bookFilter&&j.book_name!==bookFilter)return false;
if(allowGroups&&allowGroups.indexOf(j.status_group)===-1)return false;
if(statusSet.length&&statusSet.indexOf(j.status)===-1)return false;
return true;
};
const filtered=()=>statusSet.length>0||group!=="all"||bookFilter!=="";
const sorted=list=>{
if(group==="waiting"){
return list.slice().sort((a,b)=>(b.queue_rank||0)-(a.queue_rank||0)||a.created_at-b.created_at||(a.id<b.id?-1:1));
}
return list.slice().sort((a,b)=>sortDir==="asc"?a[sortKey]-b[sortKey]:b[sortKey]-a[sortKey]);
};
const touchSection=gname=>{
if(secEls[gname])secEls[gname].classList.toggle("is-empty",!rowsEls[gname].children.length);
};
const ensureSections=()=>{
body.innerHTML="";
body.setAttribute("data-state","ok");
for(const gname of GROUP_KEYS){
const sec=$ce("section");
sec.className="u3-group";
sec.dataset.group=gname;
const h=$ce("h2");
h.className="u3-group-head";
h.textContent=S[GROUP_LABELS[gname]];
const rows=$ce("div");
rows.className="u3-group-rows";
rows.dataset.group=gname;
sec.append(h,rows);
body.appendChild(sec);
secEls[gname]=sec;
rowsEls[gname]=rows;
}
};
const stateHtml=(state,line,actionHtml)=>"<div class='u3-state' data-state='"+state+"'><span class='u3-state-ico' aria-hidden='true'>"+ICO("list-checks",22)+"</span><p class='u3-state-line'>"+esc(line)+"</p>"+actionHtml+"</div>";
const showEmpty=()=>{
const f=filtered();
body.setAttribute("data-state",f?"empty-filter":"empty");
body.innerHTML=f?stateHtml("empty-filter",S.state_not_found,"<a class='u3-btn u3-primary' href='/jobs'>"+esc(S.state_reset_filter)+"</a>"):stateHtml("empty",S.jobs_empty_line,"<a class='u3-btn u3-primary' href='/books'>"+esc(S.jobs_empty_btn)+"</a>");
rowsById.clear();
nodesById.clear();
if(swipeClose)swipeClose();
};
const showError=e=>{
body.setAttribute("data-state","error");
body.innerHTML=stateHtml("error",fmt(S.common_err,{msg:e.message}),"<button type='button' class='u3-btn u3-primary' data-u3-jobs-retry>"+esc(S.common_retry)+"</button>");
rowsById.clear();
nodesById.clear();
if(swipeClose)swipeClose();
};
const announceChange=j=>{
if(announceEl)announceEl.textContent=fmt(S.jobs_announce_status,{id:j.id,status:j.status,time:nowTime()});
};
const resortSection=gname=>{
const list=[...rowsEls[gname].children].map(n=>rowsById.get(n.dataset.id)).filter(Boolean);
for(const j of list.sort(GROUP_SORT[gname]))rowsEls[gname].appendChild(nodesById.get(j.id));
};
const applyFull=rows=>{
hasData=true;
if(refreshEl)refreshEl.textContent="";
activeIds.clear();
for(const j of rows){
if(!isInactive(j.status))activeIds.add(j.id);
if(j.updated_at>lastRev)lastRev=j.updated_at;
}
if(!rows.filter(matches).length){
showEmpty();
return;
}
ensureSections();
const matched=rows.filter(matches);
for(const gname of GROUP_KEYS){
for(const j of matched.filter(x=>x.group===gname).sort(GROUP_SORT[gname])){
const node=fromHtml(jobRowHtml(j,j.group));
rowsEls[gname].appendChild(node);
rowsById.set(j.id,j);
nodesById.set(j.id,node);
}
}
for(const gname of GROUP_KEYS)touchSection(gname);
};
const updateRow=(node,j)=>{
node.className="u3-row u3-row-job status-"+j.status_group;
node.dataset.status=j.status;
node.dataset.statusGroup=j.status_group;
node.dataset.rev=String(j.updated_at);
node.dataset.book=j.book_name;
node.dataset.queueRank=String(j.queue_rank);
const l1=node.querySelector(".u3-row-line1");
if(l1){
l1.textContent=j.book_title||j.book_name;
l1.title=j.book_title||j.book_name;
}
const l2=node.querySelector(".u3-row-line2");
if(l2)l2.textContent=jobLine2(j);
const l3=node.querySelector(".u3-row-line3");
if(l3)l3.innerHTML=jobLine3(j);
const acts=node.querySelector(".u3-row-acts");
if(acts)acts.innerHTML=rowActionHtml(j)+rowActsTail(j);
const oldSwipe=node.querySelector(".u3-row-swipe");
if(oldSwipe)oldSwipe.remove();
const strip=node.querySelector(".u3-row-strip");
if(strip){
strip.insertAdjacentHTML("beforebegin",rowSwipeHtml(j));
strip.classList.toggle("is-ind",j.progress_pct==null);
strip.style.width=j.progress_pct==null||j.progress_pct<=0?"":j.progress_pct+"%";
}
if(swipeClose)swipeClose();
};
const applyDelta=rows=>{
for(const j of rows){
if(j.updated_at>lastRev)lastRev=j.updated_at;
if(isInactive(j.status))activeIds.delete(j.id);
else activeIds.add(j.id);
const node=nodesById.get(j.id);
const was=prevStatus.get(j.id);
const changed=was!==undefined&&was!==j.status;
if(!node){
if(matches(j)){
if(body.getAttribute("data-state")!=="ok")ensureSections();
const nn=fromHtml(jobRowHtml(j,j.group));
rowsEls[j.group].appendChild(nn);
rowsById.set(j.id,j);
nodesById.set(j.id,nn);
touchSection(j.group);
}
}else if(!matches(j)){
const from=node.dataset.group;
node.remove();
rowsById.delete(j.id);
nodesById.delete(j.id);
touchSection(from);
if(!rowsById.size)showEmpty();
if(changed)announceChange(j);
}else{
let from=null;
if(node.dataset.group!==j.group){
from=node.dataset.group;
rowsEls[j.group].appendChild(node);
touchSection(from);
}
updateRow(node,j);
if(from){
node.dataset.group=j.group;
touchSection(j.group);
}
rowsById.set(j.id,j);
if(from||sortKey==="updated_at")resortSection(j.group);
if(changed)announceChange(j);
}
}
};
const stop=()=>{
stopped=true;
if(timer)clearTimeout(timer);
};
const schedule=ms=>{
if(stopped)return;
if(timer)clearTimeout(timer);
timer=setTimeout(tick,ms);
};
const tickInterval=()=>document.hidden?ui.refresh_seconds_max*1000:RFR();
const tick=async()=>{
if(stopped||inFlight)return;
inFlight=true;
try{
const rows=await fetchJSON(el.dataset.u1Fetch+(lastRev?"?since="+lastRev:""));
backoffMs=RFR();
if(lastRev===0)applyFull(rows);
else applyDelta(rows);
for(const j of rows)prevStatus.set(j.id,j.status);
if(activeIds.size===0)stop();
}catch(e){
let nextMs=backoffMs;
if(isNetErr(e)){
netbar(el);
nextMs=5000;
}else{
backoffMs=Math.min(backoffMs*2,ui.refresh_seconds_max*1000);
nextMs=backoffMs;
if(hasData&&refreshEl){
refreshEl.innerHTML=esc(fmt(S.jobs_refresh_error,{msg:e.message,wait:Math.round(backoffMs/1000)}))+RETRY("u3-jobs-retry");
}else if(!hasData){
showError(e);
}
}
schedule(nextMs);
return;
}finally{
inFlight=false;
}
schedule(tickInterval());
};
jobsListRetry=()=>{
backoffMs=RFR();
if(refreshEl)refreshEl.textContent="";
tick();
};
jobsRefresh=()=>{
stopped=false;
lastRev=0;
backoffMs=RFR();
tick();
};
document.addEventListener("visibilitychange",()=>{
if(stopped)return;
if(document.visibilityState==="visible")tick();
else schedule(tickInterval());
});
tick();
}
let booksRefresh;
function startBooksList(){
const el=$("u1-books");
if(!el||!el.dataset.u1Fetch)return;
const body=el.querySelector(":scope > .u3-list-body");
if(!body)return;
let q=el.dataset.u1Q||"";
let url=el.dataset.u1Fetch+(q?"?q="+enc(q):"");
let timer;
let inFlight=false;
let hasData=false;
const stateHtml=(state,line,actionHtml)=>"<div class='u3-state' data-state='"+state+"'><span class='u3-state-ico' aria-hidden='true'>"+ICO("book-open",22)+"</span><p class='u3-state-line'>"+esc(line)+"</p>"+actionHtml+"</div>";
const showEmpty=()=>{
body.setAttribute("data-state",q?"empty-filter":"empty");
body.innerHTML=q?stateHtml("empty-filter",S.books_empty_filtered,"<a class='u3-btn u3-primary' href='/books'>"+esc(S.state_reset_filter)+"</a>"):stateHtml("empty",S.books_empty_line,"<button type='button' class='u3-btn u3-primary' data-book-upload>"+esc(S.fab_add_book)+"</button>");
};
const showError=e=>{
body.setAttribute("data-state","error");
body.innerHTML=stateHtml("error",fmt(S.common_err,{msg:e.message}),"<button type='button' class='u3-btn u3-primary' data-u3-books-retry>"+esc(S.common_retry)+"</button>");
};
const tick=async()=>{
if(inFlight)return;
inFlight=true;
try{
const books=await fetchJSON(url);
const old=el.querySelector(":scope > .u3-netbar");
if(old&&old.remove)old.remove();
hasData=true;
if(!books.length)showEmpty();
else{
body.setAttribute("data-state","ok");
body.innerHTML="<div class='u3-book-grid'>"+books.map((b,i)=>bookCardHtml(b,i<9)).join("")+"</div>";
}
}catch(e){
if(isNetErr(e)){
netbar(el);
timer=setTimeout(tick,5000);
}else if(!hasData){
showError(e);
}
}finally{
inFlight=false;
}
};
const searchForm=$("u1-books-search");
if(searchForm)searchForm.addEventListener("submit",e=>{
e.preventDefault();
q=(searchForm.q.value||"").trim();
url=el.dataset.u1Fetch+(q?"?q="+enc(q):"");
tick();
});
booksRefresh=tick;
tick();
}
let jobPolling;
function startJobPolling(){
const el=document.querySelector("[data-u1-poll]");
if(!el)return;
const jobId=el.dataset.u1Poll;
let timer;
let stopped=false;
let inFlight=false;
let flight;
const stop=()=>{
stopped=true;
if(timer)clearTimeout(timer);
};
const schedule=ms=>{
if(!stopped)timer=setTimeout(tick,ms);
};
const tick=async()=>{
if(stopped||inFlight)return;
if(document.hidden){
schedule(RFR());
return;
}
inFlight=true;
const operation=(async()=>{
const facts=await fetchJSON("/api/jobs/" + encodeURIComponent(jobId));
el.innerHTML=renderProgress(facts);
if(facts.status_terminal)stop();
})();
flight=operation;
try{
await operation;
}catch(e){
stop();
el.innerHTML+="<p class='err'>"+esc(fmt(S.common_poll_stopped,{msg:e.message}))+RETRY("u3-poll-retry")+"</p>";
}finally{
inFlight=false;
flight=null;
}
if(!stopped)schedule(RFR());
};
const retry=()=>{
stopped=false;
tick();
};
const quiesce=async()=>{
stop();
if(flight)await flight;
};
onVis(()=>!stopped,tick);
jobPolling={quiesce,retry};
tick();
}
function containerError(el,e){
el.innerHTML="<p class='err'>"+esc(fmt(S.common_err_load,{msg:e.message}))+RETRY("u3-retry")+"</p>";
}
function loadContainer(el){
const render=RENDERERS[el.dataset.u1Render];
if(!render)return;
let url=filteredApiUrl(el.dataset.u1Fetch);
if(el.id==="u1-logs"){
const select=$("u3-logs-lines");
if(select)url+="?lines="+enc(select.value);
}
fetchJSON(url).then(data=>render(el,data)).catch(error=>containerError(el,error));
}
function refreshContainer(id){
const el=$(id);
if(!el||!el.dataset.u1Fetch)return;
loadContainer(el);
}
function initFetchContainers(){
for(const el of document.querySelectorAll("[data-u1-fetch]")){
if(el.id==="u1-jobs"||el.id==="u1-books")continue;
loadContainer(el);
}
}
function initLogsControls(){
const el=$("u1-logs");
const select=$("u3-logs-lines");
const more=$("u3-logs-more");
if(!el||!select||!more)return;
const base=parseInt(select.value,10)||ui.log_tail_lines;
const options=[];
for(let value=base;value<=5000;value*=2)options.push(value);
select.innerHTML=options.map(value=>"<option value='"+value+"'"+(value===base?" selected":"")+">"+value+"</option>").join("");
const fetchLogs=()=>loadContainer(el);
more.addEventListener("click",()=>{
const current=parseInt(select.value,10)||base;
const next=Math.min(current*2,5000);
if(next===current)return;
select.value=next;
fetchLogs();
});
select.addEventListener("change",fetchLogs);
}
function failWithRetry(el,text,retry){
const n=$ce("span");
n.className="err";
const b=$ce("button");
b.type="button";
b.textContent=S.common_retry;
b.addEventListener("click",()=>{
n.remove();
retry();
});
n.textContent=text;
n.append(" ",b);
el.innerHTML="";
el.appendChild(n);
}
function bindImportSheet(){
const drop=$("u3-import-drop");
const fi=$("u3-import-files");
const listEl=$("u3-import-list");
const sumEl=$("u3-import-summary");
if(!drop||!fi||!listEl||!sumEl)return;
const sheet=drop.closest("dialog");
const STEPS=["checking","converting","validating","language"];
const STEP_LABELS={checking:S.import_step_check,converting:S.import_step_convert,validating:S.import_step_validate,language:S.import_step_language};
const POLL_MS=750;
const stepsHtml=idx=>{
let html="";
for(let i=0;i<STEPS.length;i++){
const cls=i<idx?" u3-import-step-done":i===idx?" u3-import-step-active":"";
html+="<span class='u3-import-step"+cls+"'></span>";
}
return html;
};
let total=0;
let done=0;
let okCount=0;
let errLines=[];
let lastOkName="";
let busy=false;
const setRow=(li,stepIdx,text,cls)=>{
const steps=li.querySelector(".u3-import-row-steps");
if(steps)steps.innerHTML=stepsHtml(stepIdx==null?-1:stepIdx);
const st=li.querySelector(".u3-import-row-status");
if(st){
st.textContent=text||"";
st.className="u3-import-row-status"+(cls?" "+cls:"");
}
};
const renderSummary=activeStep=>{
const line=$("u3-import-summary-line");
const cap=$("u3-import-caption");
let text="";
if(total>0){
if(done<total)text=fmt(S.import_progress_of,{done:done,total:total});
else text=fmt(S.import_summary_line,{ok:okCount,err:errLines.length});
}
line.textContent=text;
cap.textContent=activeStep==="language"?S.import_detecting:"";
cap.hidden=activeStep!=="language";
sumEl.hidden=!text&&activeStep!=="language";
};
const pollImport=(id,li)=>new Promise(resolve=>{
const tick=()=>{
fetchJSON("/api/imports/" + enc(id)).then(rec=>{
if(rec.status==="done"||rec.status==="error")resolve(rec);
else{
const idx=STEPS.indexOf(rec.step);
setRow(li,idx===-1?null:idx,STEP_LABELS[rec.step]||rec.step);
renderSummary(rec.step);
setTimeout(tick,POLL_MS);
}
}).catch(()=>setTimeout(tick,POLL_MS*2));
};
tick();
});
const uploadOne=async file=>{
const li=$ce("li");
li.className="u3-import-row";
li.innerHTML="<span class='u3-import-row-name' title='"+esc(file.name)+"'>"+esc(file.name)+"</span><span class='u3-import-row-steps'>"+stepsHtml(-1)+"</span><span class='u3-import-row-status'></span>";
listEl.appendChild(li);
const fd=new FormData();
fd.append("file",file);
try{
const data=await fetchJSON("/api/books", { method: "POST",body:fd});
let rec=data;
if(data&&data.import_id)rec=await pollImport(data.import_id,li);
if(rec&&rec.status==="error")throw new Error(rec.error||NS);
okCount+=1;
lastOkName=(rec&&rec.name)||(data&&data.name)||file.name;
setRow(li,STEPS.length,S.import_toast_added,"u3-import-row-ok");
}catch(e){
errLines.push(file.name+": "+(e.message||NS));
setRow(li,null,e.message||NS,"u3-import-row-err");
}
};
const queueFiles=list=>{
if(busy)return;
const files=Array.prototype.slice.call(list||[]);
if(!files.length)return;
busy=true;
drop.classList.add("is-busy");
fi.value="";
total=files.length;
done=0;
okCount=0;
errLines=[];
lastOkName="";
listEl.innerHTML="";
listEl.hidden=false;
(async()=>{
for(const file of files){
await uploadOne(file);
done+=1;
renderSummary(null);
}
busy=false;
drop.classList.remove("is-busy");
renderSummary(null);
if(total===1&&okCount===1){
if(sheet&&sheet.close)sheet.close();
if(booksRefresh)booksRefresh();
toast(S.import_toast_added,{actionLabel:S.import_toast_translate,actionHref:"/jobs?sheet=create&book="+enc(lastOkName)});
}else if(okCount>0){
if(booksRefresh)booksRefresh();
}
})();
};
drop.addEventListener("click",()=>{
if(!busy)fi.click();
});
drop.addEventListener("keydown",e=>{
if((e.key==="Enter"||e.key===" ")&&!busy){
e.preventDefault();
fi.click();
}
});
fi.addEventListener("change",()=>queueFiles(fi.files));
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,e=>{
e.preventDefault();
drop.classList.add("u3-import-drop-over");
}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{
e.preventDefault();
drop.classList.remove("u3-import-drop-over");
}));
drop.addEventListener("drop",e=>{
if(e.dataTransfer&&e.dataTransfer.files.length)queueFiles(e.dataTransfer.files);
});
const closeBtn=$("u3-import-close");
if(closeBtn&&sheet&&sheet.close)closeBtn.addEventListener("click",()=>sheet.close());
}
function bindJobActions(){
const jobMsg=$("u3-job-msg");
const cf=$("u1-confirm");
if(cf){
const msg=$("u1-confirm-msg");
const run=async()=>{
const sourceLanguage=$("u1-confirm-lang").value;
msg.textContent=S.common_sending;
try{
await fetchJSON("/api/jobs/"+enc(cf.dataset.job)+"/confirm",{method:"POST",headers:JH,body:JSON.stringify({source_language:sourceLanguage})});
window.location.reload();
}catch(e){
failWithRetry(msg,apiErr(e),run);
}
};
cf.addEventListener("submit",e=>{
e.preventDefault();
run();
});
}
for(const form of document.querySelectorAll(".u3-inline-form")){
const action=(form.getAttribute("action")||"").split("/").pop();
if(action!=="pause"&&action!=="resume"&&action!=="cancel")continue;
const btn=form.querySelector("button[data-job]");
if(!btn)continue;
const id=btn.dataset.job;
form.addEventListener("submit",e=>{
e.preventDefault();
if(action==="cancel"){
requestCancel(id,()=>window.location.reload());
return;
}
btn.disabled=true;
fetchJSON("/api/jobs/" + enc(id) + "/" + action,{method:"POST"}).then(()=>window.location.reload()).catch(err=>{
btn.disabled=false;
failWithRetry(jobMsg,cmdFail(err),()=>form.requestSubmit());
});
});
}
const clr=$("u3-clear-logs");
if(clr){
const run=async()=>{
const job=clr.dataset.job;
if(!confirm(fmt(S.job_clear_logs_confirm,{id:job})))return;
clr.disabled=true;
try{
await fetchJSON("/api/jobs/"+enc(job)+"/logs/clear",{method:"POST"});
jobMsg.textContent=S.jobs_logs_cleared;
refreshContainer("u1-logs");
}catch(e){
failWithRetry(jobMsg,cmdFail(e),run);
}finally{
clr.disabled=false;
}
};
clr.addEventListener("click",run);
}
}
function bindBookDeletes(){
const container=$("u1-books");
if(container)container.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.hasAttribute)return;
if(target.hasAttribute("data-book-upload")){
const fab=document.querySelector('[data-sheet-open="u3-sheet-import"]');
if(fab)fab.click();
}
});
bindBookDeleteFlow();
}
function bindBookDeleteFlow(){
const dialog=$("u3-sheet-book-delete");
if(!dialog||!dialog.showModal)return;
const body=$("u3-book-del-body");
const isCard=(window.location.pathname||"").indexOf("/books/")===0;
const finish=msg=>{
dialog.close();
if(msg)toast(msg);
if(isCard)window.location.href="/books";
else if(booksRefresh)booksRefresh();
};
const conflict=()=>{
dialog.close();
toast(S.book_busy_toast);
if(isCard)window.location.reload();
else if(booksRefresh)booksRefresh();
};
const btn=(name,data,cls,label)=>"<button type='button' class='"+cls+"' data-name='"+esc(name)+"' "+data+">"+esc(label)+"</button>";
const jobLines=jobs=>"<ul class='u3-sheet-list'>"+jobs.map(j=>"<li><span class='u3-status status-"+esc(j.status_group)+"'>"+esc(j.status)+"</span><span class='u3-mono'>"+esc(j.id)+"</span></li>").join("")+"</ul>";
const simpleHtml=name=>"<p class='u3-book-del-q'>"+esc(fmt(S.book_delete_simple_q,{name}))+"</p><span class='u3-filter-actions'>"+btn(name,"data-del-cancel","u3-btn",S.book_delete_cancel)+btn(name,"data-del-simple","u3-btn u3-primary",S.book_delete_yes)+"</span>";
const usageHtml=(name,usage)=>{
const nonterminal=usage.jobs.filter(j=>!j.terminal);
const cascade=nonterminal.length?"<button type='button' class='u3-btn u3-primary' disabled>"+esc(fmt(S.book_delete_cascade,{n:usage.count}))+"</button>":btn(name,"data-del-cascade","u3-btn u3-primary",fmt(S.book_delete_cascade,{n:usage.count}));
return "<p class='u3-book-del-q'>"+esc(fmt(S.book_delete_referenced,{n:usage.count}))+"</p>"+jobLines(usage.jobs)+(nonterminal.length?"<p class='u3-warn'>"+esc(fmt(S.book_delete_nonterminal,{n:usage.count,ids:nonterminal.map(j=>j.id).join(", ")}))+"</p>":"")+"<span class='u3-filter-actions'>"+btn(name,"data-del-file","u3-btn",S.book_delete_file_only)+cascade+btn(name,"data-del-cancel","u3-btn",S.book_delete_cancel)+"</span>";
};
const openFor=async name=>{
body.innerHTML="";
try{
const usage=await fetchJSON("/api/books/" + enc(name) + "/usage");
body.innerHTML=usage.count?usageHtml(name,usage):simpleHtml(name);
dialog.showModal();
}catch(e){
toast(apiErr(e));
}
};
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.getAttribute)return;
const name=target.getAttribute("data-book-delete");
if(name){
e.preventDefault();
const det=target.closest("details");
if(det)det.removeAttribute("open");
openFor(name);
return;
}
if(!dialog.open)return;
const cancel=target.closest("[data-del-cancel]");
if(cancel){
dialog.close();
return;
}
const simple=target.closest("[data-del-simple]");
if(simple){
simple.disabled=true;
fetchJSON("/api/books/" + enc(simple.dataset.name), { method: "DELETE"}).then(()=>finish(S.books_deleted)).catch(err=>{
if(err.status===409)conflict();
else{
simple.disabled=false;
toast(apiErr(err));
}
});
return;
}
const fileOnly=target.closest("[data-del-file]");
if(fileOnly){
fileOnly.disabled=true;
fetchJSON("/api/books/"+enc(fileOnly.dataset.name)+"?file_only=1", { method: "DELETE"}).then(()=>finish(S.book_file_deleted)).catch(err=>{
if(err.status===409)conflict();
else{
fileOnly.disabled=false;
toast(apiErr(err));
}
});
return;
}
const cascade=target.closest("[data-del-cascade]");
if(!cascade)return;
const purgeUrl="/api/books/" + enc(cascade.dataset.name) + "/purge";
if(!cascade.dataset.armed){
cascade.disabled=true;
fetchJSON(purgeUrl,{method:"POST",headers:JH,body:"{}"}).then(dry=>{
cascade.dataset.armed="1";
cascade.dataset.token=dry.confirm_token;
cascade.textContent=S.book_delete_cascade_confirm;
cascade.disabled=false;
}).catch(err=>{
if(err.status===409)conflict();
else{
cascade.disabled=false;
toast(apiErr(err));
}
});
}else{
cascade.disabled=true;
fetchJSON(purgeUrl,{method:"POST",headers:JH,body:JSON.stringify({confirm_token:cascade.dataset.token})}).then(()=>finish(S.books_deleted)).catch(err=>{
if(err.status===409)conflict();
else{
cascade.disabled=false;
toast(apiErr(err));
}
});
}
});
}
function bindBookMeta(){
const dialog=$("u3-sheet-book-meta");
if(!dialog||!dialog.showModal)return;
const title=$("u3-book-meta-title");
const form=$("u3-book-meta-form");
const fieldEl=$("u3-book-meta-field");
const input=$("u3-book-meta-input");
const select=$("u3-book-meta-select");
const msg=$("u3-book-meta-msg");
const FIELD_KEYS={title:"title",author:"author",note:"note",source_language:"source",target_default:"target",cefr_default:"cefr"};
const FIELD_KIND={title:"str",author:"str",note:"str",source_language:"lang",target_default:"lang",cefr_default:"cefr"};
const bookName=(window.location.pathname||"").split("/")[2]||"";
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.getAttribute)return;
const field=target.getAttribute("data-book-field");
if(!field||!(field in FIELD_KIND))return;
e.preventDefault();
const value=target.getAttribute("data-book-value")||"";
title.textContent=S["book_field_"+FIELD_KEYS[field]];
fieldEl.value=field;
if(FIELD_KIND[field]==="str"){
select.hidden=true;
input.hidden=false;
input.value=value;
}else{
input.hidden=true;
select.hidden=false;
const codes=JSON.parse(FIELD_KIND[field]==="lang"?dialog.dataset.langs:dialog.dataset.cefrs);
select.innerHTML="<option value=''>"+esc(S.common_not_set)+"</option>"+codes.map(c=>"<option value='"+esc(c)+"'"+(c===value?" selected":"")+">"+esc(c)+"</option>").join("");
}
msg.textContent="";
dialog.showModal();
});
form.addEventListener("submit",e=>{
e.preventDefault();
const field=fieldEl.value;
const value=select.hidden?input.value:select.value;
const patch={};
patch[field]=value;
const run=()=>{
fetchJSON("/api/books/" + enc(bookName), { method: "PATCH",headers:JH,body:JSON.stringify(patch)}).then(()=>{
const node=document.querySelector("[data-book-field='"+field+"']");
if(node){
node.setAttribute("data-book-value",value);
const vv=node.querySelector(".u3-book-pill-v");
if(vv){vv.textContent=value||NS;}else{node.textContent=value||NS;}
}
dialog.close();
toast(S.book_meta_saved);
}).catch(err=>failWithRetry(msg,apiErr(err),run));
};
run();
});
}
function bindPurge(){
const form=$("u3-purge-form");
const dry=$("u3-purge-dry");
const conf=$("u3-purge-confirm");
const msg=$("u3-purge-msg");
if(!form||!dry||!conf||!msg)return;
let token;
const collect=()=>{
const pairSel=form.querySelector("select[name='pair']");
return{
statuses:[...form.querySelectorAll("input[name='status']:checked")].map(i=>i.value),
book:form.querySelector("select[name='book']").value||null,
pair:pairSel?pairSel.value||null:null,
};
};
const runDry=async()=>{
token=null;
conf.disabled=true;
msg.textContent=S.common_sending;
try{
const result=await fetchJSON("/api/jobs/purge", {method:"POST",headers:JH,body:JSON.stringify(collect())});
token=result.confirm_token;
conf.disabled=false;
const ids=result.job_ids||[];
msg.innerHTML=esc(fmt(S.jobs_purge_result,{count:result.count,bytes:humanSize(result.total_bytes)}))+" <span class='u3-warn'>"+esc(S.jobs_purge_irreversible)+"</span><details open><summary>"+esc(fmt(S.jobs_purge_ids,{n:ids.length}))+"</summary>"+ids.map(id=>"<code class='u3-mono'>"+esc(id)+"</code>").join("<br>")+"</details>";
}catch(e){
failWithRetry(msg,apiErr(e),runDry);
}
};
dry.addEventListener("click",runDry);
const runConfirm=async()=>{
if(!token)return;
conf.disabled=true;
msg.textContent=S.common_sending;
try{
const body=collect();
body.confirm_token=token;
const result=await fetchJSON("/api/jobs/purge", {method:"POST",headers:JH,body:JSON.stringify(body)});
msg.textContent=fmt(S.jobs_purge_removed,{count:result.count,bytes:humanSize(result.total_bytes)});
token=null;
if(jobsRefresh)jobsRefresh();
}catch(e){
token=null;
failWithRetry(msg,apiErr(e),runConfirm);
}
};
conf.addEventListener("click",runConfirm);
}
function toast(msg,opts){
const zone=$("u3-toasts");
if(!zone)return null;
opts=opts||{};
const el=$ce("div");
el.className="u3-toast";
const span=$ce("span");
span.textContent=msg;
el.appendChild(span);
let removed=false;
const remove=()=>{
if(!removed){
removed=true;
el.remove();
}
};
if(opts.onUndo){
const b=$ce("button");
b.type="button";
b.className="u3-toast-undo";
b.textContent=S.toast_undo;
b.addEventListener("click",()=>{
opts.onUndo();
remove();
});
el.appendChild(b);
}
if(opts.actionLabel&&opts.actionHref){
const a=$ce("a");
a.className="u3-toast-undo";
a.href=opts.actionHref;
a.textContent=opts.actionLabel;
a.addEventListener("click",()=>remove());
el.appendChild(a);
}
zone.appendChild(el);
setTimeout(remove,opts.duration||5000);
return{el,remove};
}
function requestCancel(jobId,onDone){
let timer;
toast(S.toast_cancelled,{duration:5000,onUndo:()=>{
if(timer)clearTimeout(timer);
}});
timer=setTimeout(()=>{
fetchJSON("/api/jobs/"+enc(jobId)+"/cancel",{method:"POST"}).then(()=>onDone&&onDone()).catch(e=>toast(apiErr(e)));
},5000);
}
function initRowActions(){
const list=$("u1-jobs");
if(!list)return;
list.addEventListener("submit",e=>{
const form=e.target;
if(!form||!form.classList||!form.classList.contains("u3-row-form"))return;
e.preventDefault();
const btn=form.querySelector("[data-row-action]");
if(!btn)return;
const row=form.closest(".u3-row-job");
const id=row?row.dataset.id:"";
const action=btn.dataset.rowAction;
btn.disabled=true;
fetchJSON("/api/jobs/" + enc(id) + "/" + action,{method:"POST",headers:action==="confirm"?JH:undefined,body:action==="confirm"?"{}":undefined}).then(()=>{
if(swipeClose)swipeClose();
if(jobsRefresh)jobsRefresh();
}).catch(err=>{
btn.disabled=false;
toast(apiErr(err));
});
});
list.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.closest)return;
const a=target.closest("[data-row-retry]");
if(!a)return;
e.preventDefault();
const row=a.closest(".u3-row-job");
const id=row?row.dataset.id:a.dataset.rowRetry;
const j=jobsGetRow?jobsGetRow(id):null;
if(!j){
window.location.href=a.href;
return;
}
const body={book:j.book_name,target_language:j.target_language,mode:j.mode};
if(j.learning_level)body.learning_level=j.learning_level;
if(j.learning_cefr)body.learning_cefr=j.learning_cefr;
fetchJSON("/api/jobs", { method: "POST",headers:JH,body:JSON.stringify(body)}).then(()=>{
if(jobsRefresh)jobsRefresh();
}).catch(()=>{
window.location.href=a.href;
});
});
}
const EXPORT_OBJECTS=["translation","learning","cards","reports"];
const EXPORT_FORMATS=["epub","pdf","docx","fb2","azw3","txt","md","htmlz"];
const EXPORT_LAYOUTS=["append_block","translation_only","two_columns"];
const LAYOUT_KEY={append_block:"append",translation_only:"only",two_columns:"columns"};
const dec=v=>{
try{
return decodeURIComponent(v);
}catch{
return v;
}
};
function exportOptsHtml(objects,layouts,converterOk){
let h="<fieldset class='u3-exp-section'><legend>"+esc(S.job_export_section_objects)+"</legend>";
for(const o of EXPORT_OBJECTS){
const ok=!!objects[o];
h+="<label class='u3-exp-item'><input type='checkbox' class='u3-switch' name='objects' value='"+o+"'"+(ok?" checked":" disabled")+"> "+esc(S["job_export_obj_"+o])+(ok?"":" <span class='hint'>"+esc(S.job_export_obj_missing)+"</span>")+"</label>";
}
h+="</fieldset>";
h+="<fieldset class='u3-exp-section'><legend>"+esc(S.job_export_section_formats)+"</legend><div class='u3-exp-grid'>";
for(const f of EXPORT_FORMATS){
const dis=f!=="epub"&&!converterOk;
h+="<label class='u3-exp-item'><input type='radio' name='formats' value='"+f+"'"+(f==="epub"?" checked":dis?" disabled":"")+"> "+esc(S["job_export_fmt_"+f])+"</label>";
}
h+="</div>"+(converterOk?"":"<p class='hint'>"+esc(S.err_converter_unavailable)+"</p>")+"</fieldset>";
if(layouts){
h+="<fieldset class='u3-exp-section'><legend>"+esc(S.job_export_section_layout)+"</legend>";
for(const l of EXPORT_LAYOUTS)h+="<label class='u3-exp-item'><input type='radio' name='layout' value='"+l+"'"+(l==="append_block"?" checked":"")+(l==="two_columns"?" data-u3-exp-two":"")+"> "+esc(S["job_export_layout_"+LAYOUT_KEY[l]])+"</label>";
h+="</fieldset>";
}
return h;
}
function syncExportTwo(body){
const two=body.querySelector("[data-u3-exp-two]");
if(!two)return;
const pdf=body.querySelector("[name='formats'][value='pdf']");
const hz=body.querySelector("[name='formats'][value='htmlz']");
const ok=(pdf&&pdf.checked)||(hz&&hz.checked);
two.disabled=!ok;
if(two.disabled&&two.checked){
const first=body.querySelector("[name='layout'][value='append_block']");
if(first)first.checked=true;
}
}
function exportPoll(res,mode,arg,btn){
const base=(mode==="job"?"/api/jobs/":"/api/books/")+enc(arg)+"/export";
const tick=()=>{
fetchJSON(base).then(rec=>{
if(rec.status==="done"){
let h="<p class='hint'>"+esc(S.job_export_status_done)+"</p><div class='u3-export-files'>";
for(const n of rec.files||[])h+="<a class='u3-btn' href='"+base + "/" + enc(n)+"' download='"+enc(n)+"'>"+esc(n)+"</a>";
res.innerHTML=h+"</div>";
}else if(rec.status==="error"){
btn.disabled=false;
res.textContent=S.job_export_status_error+": "+(rec.error||NS);
}else{
res.textContent=fmt(S.job_export_progress,{done:rec.done||0,total:rec.total||0});
setTimeout(tick,1000);
}
}).catch(e=>{
btn.disabled=false;
res.textContent=apiErr(e);
});
};
tick();
}
function exportRunClick(btn){
const body=$("u3-export-body");
const res=$("u3-exp-result");
if(!body||!res)return;
const mode=btn.dataset.expMode||"job";
const arg=dec(btn.dataset.expArg||"");
btn.disabled=true;
res.textContent=fmt(S.job_export_progress,{done:0,total:1});
if(mode==="bookfile"){
const fr=body.querySelector("[name='xformat']:checked");
const format=fr?fr.value:"epub";
fetchJSON("/api/books/" + enc(arg) + "/export", { method: "POST",headers:JH,body:JSON.stringify({format})}).then(()=>exportPoll(res,"book",arg,btn)).catch(e=>{
btn.disabled=false;
res.textContent=apiErr(e);
});
return;
}
let jobId=arg;
if(mode==="book"){
const r=body.querySelector("[name='xjob']:checked");
if(!r){
btn.disabled=false;
res.textContent=NS;
return;
}
jobId=r.value;
}
const objects=Array.from(body.querySelectorAll("[name='objects']:checked")).map(c=>c.value);
const formats=Array.from(body.querySelectorAll("[name='formats']:checked")).map(c=>c.value);
const lr=body.querySelector("[name='layout']:checked");
const layout=lr?lr.value:"append_block";
if(!objects.length||!formats.length){
btn.disabled=false;
res.textContent=S.err_export_no_selection;
return;
}
fetchJSON("/api/jobs/" + enc(jobId) + "/export", { method: "POST",headers:JH,body:JSON.stringify({objects,formats,layout})}).then(()=>exportPoll(res,"job",jobId,btn)).catch(e=>{
btn.disabled=false;
res.textContent=apiErr(e);
});
}
function exportFail(body,e,mode,arg){
const ds=mode==="job"?"data-job='"+enc(arg)+"'":"data-book='"+enc(arg)+"'";
body.innerHTML=esc(apiErr(e))+" <button type='button' data-u3-export-retry "+ds+">"+esc(S.common_retry)+"</button>";
}
function fillExportBody(jobId,bookName){
const body=$("u3-export-body");
if(!body)return;
body.textContent=S.common_loading;
Promise.all([fetchJSON("/api/jobs/"+enc(jobId)),fetchJSON("/api/health")]).then(([facts,health])=>{
const ex=facts.export||{objects:{},layouts:false};
let h="<p class='u3-export-book'>"+esc(dec(bookName)||facts.book_name||NS)+"</p>";
if(!facts.result_ready)h+="<p class='hint'>"+esc(S.job_result_pending)+"</p>";
h+=exportOptsHtml(ex.objects||{},!!ex.layouts,health.converter==="ok");
h+="<button type='button' class='u3-btn u3-primary u3-sheet-action' data-u3-exp-run data-exp-mode='job' data-exp-arg='"+enc(jobId)+"'>"+esc(S.job_export_btn)+"</button>";
h+="<div id='u3-exp-result' role='status'></div>";
body.innerHTML=h;
syncExportTwo(body);
}).catch(e=>exportFail(body,e,"job",jobId));
}
function exportBookOpts(jobId){
const body=$("u3-export-body");
const opts=$("u3-exp-job-opts");
if(!body||!opts)return;
const ok=body.dataset.expOk==="1";
opts.textContent=S.common_loading;
fetchJSON("/api/jobs/"+enc(jobId)).then(facts=>{
if(opts.parentNode!==body)return;
const ex=facts.export||{objects:{},layouts:false};
opts.innerHTML=exportOptsHtml(ex.objects||{},!!ex.layouts,ok);
syncExportTwo(body);
}).catch(e=>{
opts.innerHTML="";
opts.textContent=apiErr(e);
});
}
function fillExportBook(bookName){
const body=$("u3-export-body");
if(!body)return;
body.textContent=S.common_loading;
const q="/api/jobs?book="+enc(bookName)+"&status=COMPLETED&status=COMPLETED_WITH_WARNINGS&sort=updated_at&direction=desc";
Promise.all([fetchJSON(q),fetchJSON("/api/health")]).then(([jobs,health])=>{
const ok=health.converter==="ok";
body.dataset.expOk=ok?"1":"0";
let h="<p class='u3-export-book'>"+esc(bookName)+"</p>";
if(!jobs.length){
h+="<p class='hint'>"+esc(S.job_export_book_hint)+"</p><fieldset class='u3-exp-section'><legend>"+esc(S.job_export_book_block)+"</legend><div class='u3-exp-grid'>";
for(const f of EXPORT_FORMATS){
const dis=f!=="epub"&&!ok;
h+="<label class='u3-exp-item'><input type='radio' name='xformat' value='"+f+"'"+(f==="epub"?" checked":dis?" disabled":"")+"> "+esc(S["job_export_fmt_"+f])+"</label>";
}
h+="</div></fieldset><button type='button' class='u3-btn u3-primary u3-sheet-action' data-u3-exp-run data-exp-mode='bookfile' data-exp-arg='"+enc(bookName)+"'>"+esc(S.job_export_btn)+"</button>";
}else{
h+="<fieldset class='u3-exp-section'><legend>"+esc(S.job_export_section_job)+"</legend>";
for(const j of jobs)h+="<label class='u3-exp-item'><input type='radio' name='xjob' value='"+j.id+"'"+(jobs.indexOf(j)===0?" checked":"")+"> "+esc(j.book_name||NS)+" · "+esc(j.status)+" · "+esc(j.updated_at_human||NS)+"</label>";
h+="</fieldset><div id='u3-exp-job-opts'></div><button type='button' class='u3-btn u3-primary u3-sheet-action' data-u3-exp-run data-exp-mode='book'>"+esc(S.job_export_btn)+"</button>";
}
h+="<div id='u3-exp-result' role='status'></div>";
body.innerHTML=h;
if(jobs.length)exportBookOpts(jobs[0].id);
}).catch(e=>exportFail(body,e,"book",bookName));
}
document.addEventListener("change",e=>{
const t=e&&e.target;
if(!t||!t.name)return;
const body=$("u3-export-body");
if(!body)return;
if(t.name==="formats")syncExportTwo(body);
else if(t.name==="xjob")exportBookOpts(t.value);
});
let swipeClose;
let queueDragActive=false;
function initQueueDrag(){
const list=$("u1-jobs");
if(!list)return;
const HOLD_MS=450,CANCEL_PX=12;
let pending=null,timer=null,drag=null;
const qbody=()=>list.querySelector('.u3-group[data-group="queue"] .u3-group-rows');
const clearPending=()=>{if(timer){clearTimeout(timer);timer=null;}pending=null;};
const lift=article=>{
const body=qbody();
if(!body||!body.contains(article)){clearPending();return;}
const items=[...body.children].filter(n=>n.classList.contains("u3-row-job")&&n!==article);
drag={article,items,target:0};
article.classList.add("is-dragging");
queueDragActive=true;
if(navigator.vibrate)try{navigator.vibrate(12);}catch{}
};
const drop=commit=>{
if(!drag){clearPending();return;}
drag.article.classList.remove("is-dragging");
drag=null;clearPending();queueDragActive=false;
if(commit){
const body=qbody();
if(!body)return;
const order=[...body.children].filter(n=>n.classList.contains("u3-row-job"));
const n=order.length;let changed=false;
order.forEach((row,i)=>{
const rank=n-i;
if(parseInt(row.dataset.queueRank||"0",10)!==rank){
row.dataset.queueRank=String(rank);changed=true;
fetchJSON("/api/jobs/"+enc(row.dataset.id),{method:"PATCH",headers:JH,body:JSON.stringify({queue_rank:rank})}).catch(()=>{});
}
});
if(changed&&jobsRefresh)jobsRefresh();
}
};
list.addEventListener("touchstart",e=>{
if(e.touches.length!==1)return;
const article=e.target.closest(".u3-row-job");
if(!article||article.dataset.group!=="queue")return;
if(e.target.closest(".u3-row-btn,form,a,button,input,select,textarea"))return;
const t=e.touches[0];
clearPending();
pending={article,ty:t.clientY};
timer=setTimeout(()=>{timer=null;lift(article);},HOLD_MS);
},{passive:true});
list.addEventListener("touchmove",e=>{
const t=e.touches[0];
if(pending&&Math.abs(t.clientY-pending.ty)>CANCEL_PX)clearPending();
if(!drag)return;
e.preventDefault();
const y=t.clientY;
let target=0;
for(const it of drag.items){
const r=it.getBoundingClientRect();
if(y<r.top+r.height/2)break;
target++;
}
if(target!==drag.target){
drag.target=target;
const body=qbody();
if(target<drag.items.length)body.insertBefore(drag.article,drag.items[target]);
else body.appendChild(drag.article);
}
},{passive:false});
list.addEventListener("touchend",()=>{if(drag)drop(true);else clearPending();});
list.addEventListener("touchcancel",()=>{if(drag)drop(false);else clearPending();});
}
function initSwipe(){
const list=$("u1-jobs");
if(!list)return;
let open;
swipeClose=()=>{
if(!open)return;
open.slide.style.transform="";
open.article.classList.remove("is-swiped");
open=null;
};
const done=()=>{
swipeClose();
if(jobsRefresh)jobsRefresh();
};
const handleSwap=b=>{
const id=b.dataset.job;
const fail=e2=>{
b.disabled=false;
toast(apiErr(e2));
};
if(b.dataset.swap==="pause"){
b.disabled=true;
fetchJSON("/api/jobs/"+enc(id)+"/pause",{method:"POST"}).then(done).catch(fail);
}else if(b.dataset.swap==="cancel"){
swipeClose();
requestCancel(id,()=>jobsRefresh&&jobsRefresh());
}else if(b.dataset.swap==="delete"){
swipeClose();
openDeleteJobSheet(id,b.dataset.bookName||"");
}else{
b.disabled=true;
let maxRank=0;
for(const n of list.querySelectorAll(".u3-row-job")){
const r=parseInt(n.dataset.queueRank||"0",10);
if(!Number.isNaN(r)&&r>maxRank)maxRank=r;
}
fetchJSON("/api/jobs/"+enc(id),{method:"PATCH",headers:JH,body:JSON.stringify({queue_rank:maxRank+1})}).then(()=>{
swipeClose();
toast(S.toast_queue_front);
if(jobsRefresh)jobsRefresh();
}).catch(fail);
}
};
let start;
const parts=article=>[article.querySelector(".u3-row-slide"),article.querySelector(".u3-row-swipe")];
list.addEventListener("touchstart",e=>{
const article=e.target.closest?e.target.closest(".u3-row-job"):null;
if(!article||e.touches.length!==1){
start=null;
return;
}
const t=e.touches[0];
start={article,x:t.clientX,y:t.clientY,horizontal:null};
},{passive:true});
list.addEventListener("touchmove",e=>{
if(!start||queueDragActive)return;
const t=e.touches[0];
const dx=t.clientX-start.x;
const dy=t.clientY-start.y;
if(start.horizontal===null){
if(Math.abs(dx)<12&&Math.abs(dy)<12)return;
start.horizontal=Math.abs(dx)>Math.abs(dy);
}
if(!start.horizontal)return;
const [slide,panel]=parts(start.article);
if(!slide||!panel)return;
if(open&&open.article!==start.article)swipeClose();
const w=panel.offsetWidth;
slide.style.transform="translateX("+Math.min(0,Math.max(-w,dx))+"px)";
e.preventDefault();
},{passive:false});
list.addEventListener("touchend",e=>{
if(!start)return;
const s=start;
start=null;
const article=s.article;
const dx=e.changedTouches[0].clientX-s.x;
const [slide,panel]=parts(article);
if(!slide||!panel)return;
const wasOpen=open&&open.article===article;
if(wasOpen){
if(dx>80)swipeClose();
return;
}
if(dx>80){
window.location.href="/jobs/"+enc(article.dataset.id);
return;
}
const w=panel.offsetWidth;
if(dx<-Math.min(80,w*0.4)){
slide.style.transform="translateX(-"+w+"px)";
article.classList.add("is-swiped");
open={article,slide,panel};
}else{
slide.style.transform="";
}
});
list.addEventListener("touchcancel",()=>{
if(!start)return;
const s=start;
start=null;
const [slide]=parts(s.article);
if(slide)slide.style.transform="";
});
list.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.closest)return;
const swap=target.closest("[data-swap]");
if(swap){
handleSwap(swap);
return;
}
if(!open)return;
if(open.article.contains(target)){
if(target.closest(".u3-row-swipe"))return;
e.preventDefault();
e.stopPropagation();
swipeClose();
}else{
swipeClose();
}
});
document.addEventListener("touchstart",e=>{
if(open&&!open.article.contains(e.target))swipeClose();
},{passive:true});
}
function openDeleteJobSheet(jobId,bookName){
const body=$("u3-del-job-body");
if(body)body.innerHTML="<p class='u3-sheet-del-line'>"+esc(fmt(S.job_delete_sheet_line,{book:bookName||NS}))+"</p><p class='hint'>"+esc(S.job_delete_sheet_irreversible)+"</p><form class='u3-sheet-form' id='u3-del-job-form' data-job='"+esc(jobId)+"'><button type='button' id='u3-del-job-confirm' class='u3-btn u3-danger u3-sheet-action'>"+esc(S.job_delete_sheet_btn)+"</button><button type='button' id='u3-del-job-cancel' class='u3-btn u3-sheet-action'>"+esc(S.job_delete_sheet_cancel)+"</button><span id='u3-del-job-msg' role='status'></span></form>";
const d=$("u3-sheet-delete-job");
if(!d||!d.showModal||d.open)return;
d.showModal();
openSheetEl=d;
history.pushState({u3sheet:d.id},"");
}
function initSelection(){
const list=$("u1-jobs");
if(!list)return;
const title=document.querySelector(".u3-appbar-title");
const actions=document.querySelector(".u3-appbar-actions");
if(!title||!actions)return;
const selected=new Set();
let selecting=false;
let originalTitle=title.textContent;
const selBar=$ce("div");
selBar.className="u3-selbar";
const btns={};
for(const [key,label,danger] of [["pause",S.jobs_sel_pause,false],["cancel",S.jobs_sel_cancel,true],["delete",S.jobs_sel_delete,true],["done",S.jobs_sel_done,false]]){
const b=$ce("button");
b.type="button";
b.className="u3-btn"+(danger?" u3-danger":"");
b.textContent=label;
btns[key]=b;
selBar.append(b);
}
const updateHeader=()=>{
title.textContent=selecting?fmt(S.jobs_selected,{n:selected.size}):originalTitle;
};
const updateRows=()=>{
for(const node of list.querySelectorAll(".u3-row-job"))node.classList.toggle("is-selected",selected.has(node.dataset.id));
updateHeader();
};
const enter=firstId=>{
if(selecting)return;
selecting=true;
originalTitle=title.textContent;
for(const child of actions.children)child.hidden=true;
actions.appendChild(selBar);
if(firstId)selected.add(firstId);
updateRows();
};
const exit=()=>{
selecting=false;
selected.clear();
selBar.remove();
for(const child of actions.children)child.hidden=false;
updateRows();
};
const runBulk=action=>{
const listIds=[...selected];
if(!listIds.length)return;
fetchJSON("/api/jobs/bulk", {method:"POST",headers:JH,body:JSON.stringify({action,ids:listIds})}).then(result=>{
toast(fmt(S.toast_bulk_result,{applied:(result.applied||[]).length,skipped:(result.skipped||[]).length}));
exit();
if(jobsRefresh)jobsRefresh();
}).catch(e=>toast(apiErr(e)));
};
btns.pause.addEventListener("click",()=>runBulk("pause"));
btns.cancel.addEventListener("click",()=>runBulk("cancel"));
btns.delete.addEventListener("click",()=>{
if(bulkDeleteStart)bulkDeleteStart([...selected]);
});
btns.done.addEventListener("click",exit);
let pressTimer;
let pressId;
const clearPress=()=>{
if(pressTimer)clearTimeout(pressTimer);
pressTimer=null;
};
list.addEventListener("touchstart",e=>{
const article=e.target.closest?e.target.closest(".u3-row-job"):null;
if(!article||e.touches.length!==1)return;
pressId=article.dataset.id;
const sx=e.touches[0].clientX,sy=e.touches[0].clientY;
clearPress();
pressTimer=setTimeout(()=>{
pressTimer=null;
enter(pressId);
},500);
const onMove=ev=>{
const t=ev.touches[0];
if(t&&(Math.abs(t.clientX-sx)>10||Math.abs(t.clientY-sy)>10))cleanup();
};
const cleanup=()=>{
clearPress();
list.removeEventListener("touchmove",onMove);
list.removeEventListener("touchend",cleanup);
list.removeEventListener("touchcancel",cleanup);
};
list.addEventListener("touchmove",onMove);
list.addEventListener("touchend",cleanup);
list.addEventListener("touchcancel",cleanup);
},{passive:true});
list.addEventListener("touchend",e=>{
clearPress();
if(!selecting)return;
const article=e.target.closest?e.target.closest(".u3-row-job"):null;
if(!article)return;
const id=article.dataset.id;
if(selected.has(id))selected.delete(id);
else selected.add(id);
updateRows();
if(!selected.size)exit();
},{passive:true});
list.addEventListener("click",e=>{
if(selecting&&e.target.closest&&e.target.closest("a"))e.preventDefault();
});
list.addEventListener("contextmenu",e=>{
if((pressTimer||selecting)&&e.target.closest&&e.target.closest(".u3-row-job"))e.preventDefault();
});
}
let bulkDeleteStart;
function bindBulkDelete(){
const form=$("u3-bulk-del-form");
const conf=$("u3-bulk-del-confirm");
const cancel=$("u3-bulk-del-cancel");
const msg=$("u3-bulk-del-msg");
if(!form||!conf||!cancel||!msg)return;
const d=$("u3-sheet-bulk-delete");
let ids=[];
let token;
const post=body=>fetchJSON("/api/jobs/bulk", {method:"POST",headers:JH,body:JSON.stringify(body)});
const runDry=()=>{
token=null;
conf.disabled=true;
msg.textContent=S.common_sending;
post({action:"delete",ids}).then(result=>{
token=result.confirm_token;
conf.disabled=false;
msg.innerHTML=esc(fmt(S.jobs_del_question,{n:result.count}))+" <span class='u3-warn'>"+esc(S.jobs_del_irreversible)+"</span>"+(result.total_bytes!=null?" · "+esc(humanSize(result.total_bytes)):"")+"<details open><summary>"+esc(fmt(S.jobs_del_ids,{n:result.job_ids.length}))+"</summary>"+result.job_ids.map(id=>"<code class='u3-mono'>"+esc(id)+"</code>").join("<br>")+"</details>";
}).catch(e=>failWithRetry(msg,apiErr(e),runDry));
};
const runConfirm=()=>{
if(!token)return;
conf.disabled=true;
msg.textContent=S.common_sending;
post({action:"delete",ids,confirm_token:token}).then(result=>{
toast(fmt(S.jobs_del_done,{n:result.count}));
token=null;
if(d&&d.open)d.close();
if(jobsRefresh)jobsRefresh();
}).catch(e=>{
token=null;
failWithRetry(msg,apiErr(e),runDry);
});
};
conf.addEventListener("click",runConfirm);
cancel.addEventListener("click",()=>{
token=null;
if(d&&d.open)d.close();
});
bulkDeleteStart=listIds=>{
if(!listIds.length||!d||!d.showModal||d.open)return;
ids=listIds;
msg.textContent="";
conf.disabled=true;
d.showModal();
openSheetEl=d;
history.pushState({u3sheet:d.id},"");
runDry();
};
}
function highlightNewJob(id){
if(!id)return;
const t0=Date.now();
const tick=()=>{
const node=document.querySelector("#u1-jobs article[data-id='"+enc(id)+"']");
if(node){
const slide=node.querySelector(".u3-row-slide")||node;
node.scrollIntoView({block:"center"});
slide.classList.add("u3-row-new");
setTimeout(()=>slide.classList.remove("u3-row-new"),1500);
return;
}
if(Date.now()-t0<5000)setTimeout(tick,200);
};
tick();
}
function bindCreateForm(){
const form=$("u3-create-form");
if(!form)return;
const msg=$("u3-create-msg");
const list=$("u3-wiz-book-list");
const pick=$("u3-wiz-book-pick");
const pickName=$("u3-wiz-pick-name");
const pickSub=$("u3-wiz-pick-sub");
const pickCover=$("u3-wiz-pick-cover");
const search=$("u3-wiz-book-q");
const langSrc=$("u3-lang-src");
const cefrLabel=$("u3-cefr-label");
const mats=$("u3-materials-value");
const volHint=$("u3-vol-hint");
const cefrLabels=JSON.parse(form.dataset.cefrLabels||"{}");
const volHints=JSON.parse(form.dataset.volHints||"{}");
const radios=Array.from(form.querySelectorAll('input[name="book"]'));
const targetSel=form.target_language;
const levelSel=form.learning_level;
const cefrSel=form.learning_cefr;
const selRadio=()=>radios.find(r=>r.checked)||null;
const setRadio=(name,val)=>{
if(!val)return;
const grp=form.elements[name];
if(!grp||!grp.length)return;
const r=[...grp].find(i=>i.value===val);
if(r)r.checked=true;
};
const applyBook=r=>{
if(!r)return;
pickName.textContent=r.dataset.title||r.value;
pickSub.textContent=r.dataset.src||r.dataset.detected||r.dataset.dc||NS;
if(pickCover)pickCover.textContent=(r.dataset.title||r.value).slice(0,1).toUpperCase();
list.hidden=true;
pick.hidden=false;
if(langSrc)langSrc.textContent=r.dataset.src||r.dataset.detected||r.dataset.dc||"—";
setRadio("target_language",r.dataset.targetDefault||"");
setRadio("learning_cefr",r.dataset.cefrDefault||"");
refreshSummary();
};
const showList=()=>{
pick.hidden=true;
list.hidden=false;
if(search)search.focus();
};
const changeBtn=$("u3-wiz-book-change");
if(changeBtn)changeBtn.addEventListener("click",e=>{
e.preventDefault();
showList();
});
radios.forEach(r=>r.addEventListener("change",()=>applyBook(r)));
if(search)search.addEventListener("input",()=>{
const q=search.value.trim().toLowerCase();
radios.forEach(r=>{
const row=r.closest(".u3-wiz-row");
if(!row)return;
const hay=(r.dataset.title+" "+r.value).toLowerCase();
row.hidden=q!==""&&!hay.includes(q);
});
});
const refreshSummary=()=>{
if(cefrLabel&&targetSel)cefrLabel.textContent=cefrLabels[targetSel.value]||"";
if(mats&&levelSel)mats.textContent=levelSel.value?S.create_materials_cards:S.create_materials_translation;
if(volHint&&levelSel)volHint.textContent=volHints[levelSel.value]||volHints[""]||"";
};
if(targetSel)targetSel.addEventListener("change",refreshSummary);
if(levelSel)levelSel.addEventListener("change",refreshSummary);
refreshSummary();
const pre=selRadio();
if(pre)applyBook(pre);
form.addEventListener("submit",e=>{
e.preventDefault();
const r=selRadio();
if(!r){
msg.textContent=S.create_book_none;
return;
}
const body={book:r.value,target_language:targetSel?targetSel.value:"",mode:form.mode?form.mode.value:"bilingual",learning_level:levelSel?levelSel.value:"",learning_cefr:cefrSel?cefrSel.value:""};
const run=()=>{
msg.textContent=S.common_sending;
fetchJSON("/api/jobs", { method: "POST",headers:JH,body:JSON.stringify(body)}).then(created=>{
const d=$("u3-sheet-create");
if(d&&d.open)d.close();
if(jobsRefresh)jobsRefresh();
highlightNewJob(created.id);
}).catch(err=>failWithRetry(msg,apiErr(err),run));
};
run();
});
}
function bindDeleteJobSheet(){
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.closest)return;
if(target.closest("#u3-del-job-cancel")){
const d=$("u3-sheet-delete-job");
if(d&&d.open)d.close();
return;
}
const btn=target.closest("#u3-del-job-confirm");
if(!btn)return;
const formEl=btn.closest("[data-job]");
const id=formEl?formEl.dataset.job:"";
const msg=$("u3-del-job-msg");
const run=()=>{
btn.disabled=true;
(async()=>{
try{
if(jobPolling)await jobPolling.quiesce();
await fetchJSON("/api/jobs/" + enc(id), { method: "DELETE"});
const d=$("u3-sheet-delete-job");
if(d&&d.open)d.close();
if(window.location.pathname.indexOf("/jobs/")===0)window.location.href="/jobs";
else if(jobsRefresh)jobsRefresh();
}catch(e2){
btn.disabled=false;
if(msg)failWithRetry(msg,cmdFail(e2),run);
}
})();
};
run();
});
}
function bindPrompts(){
const form=$("u3-prompt-form");
const msg=$("u3-prompt-msg");
if(!form||!msg)return;
const fe=form.elements;
const titleEl=$("u3-prompt-form-title");
const sub=$("u3-prompt-submit");
const rst=$("u3-prompt-reset");
const dialog=$("u3-prompt-view");
const dtit=$("u3-prompt-view-title");
const dcon=$("u3-prompt-view-content");
const resetForm=()=>{
form.reset();
form.dataset.mode="create";
titleEl.textContent=S.prompts_create_title;
sub.textContent=S.prompts_create_btn;
msg.textContent="";
};
const viewPrompt=async path=>{
try{
const data=await fetchJSON("/api/prompts/content?path="+enc(path));
dtit.textContent=fmt(S.prompts_content_title,{file:data.path});
dcon.textContent=data.content;
if(dialog.showModal)dialog.showModal();
}catch(e){
failWithRetry(msg,apiErr(e),()=>viewPrompt(path));
}
};
const startEdit=async b=>{
try{
const path=b.getAttribute("data-prompt-edit");
const data=await fetchJSON("/api/prompts/content?path="+enc(path));
fe.task.value=b.getAttribute("data-task")||"";
fe.level.value=b.getAttribute("data-level")||"";
fe.source.value=b.getAttribute("data-source")||"";
fe.target.value=b.getAttribute("data-target")||"";
fe.content.value=data.content;
form.dataset.mode="edit";
const version=parseInt(b.getAttribute("data-version"),10)||0;
if(titleEl)titleEl.textContent=fmt(S.prompts_edit_title,{file:path});
sub.textContent=fmt(S.prompts_save_btn,{next:"v"+(version+1)+".yaml"});
}catch(e){
failWithRetry(msg,apiErr(e),()=>startEdit(b));
}
};
const submitRun=async()=>{
msg.textContent=S.common_sending;
const data={task:fe.task.value,level:fe.level.value,source:fe.source.value,target:fe.target.value,content:fe.content.value};
try{
const result=await fetchJSON("/api/prompts", {method:"POST",headers:JH,body:JSON.stringify(data)});
msg.textContent=fmt(S.prompts_created,{file:result.path});
setTimeout(()=>window.location.reload(),1000);
}catch(e){
failWithRetry(msg,apiErr(e),submitRun);
}
};
form.addEventListener("submit",e=>{
e.preventDefault();
submitRun();
});
if(rst)rst.addEventListener("click",resetForm);
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.getAttribute)return;
const viewPath=target.getAttribute("data-prompt-view");
if(viewPath){
viewPrompt(viewPath);
return;
}
if(target.getAttribute("data-prompt-edit")){
startEdit(target);
return;
}
const deletePath=target.getAttribute("data-prompt-delete");
if(!deletePath)return;
const runDelete=async()=>{
if(!confirm(fmt(S.prompts_delete_confirm,{file:deletePath})))return;
try{
await fetchJSON("/api/prompts/version?path="+enc(deletePath),{method:"DELETE"});
window.location.reload();
}catch(e){
failWithRetry(msg,cmdFail(e),runDelete);
}
};
runDelete();
});
const versionsEl=$("u3-prompt-versions");
const versions=versionsEl?JSON.parse(versionsEl.textContent):[];
const updateCreateLabel=()=>{
if(form.dataset.mode!=="create")return;
const task=fe.task.value;
if(!task){
sub.textContent=S.prompts_create_btn;
return;
}
const level=fe.level.value;
const source=fe.source.value.trim();
const target=fe.target.value.trim();
const maxV=versions.filter(r=>r.task===task&&r.level===level&&(level==="generic"||(r.source||"")===source)&&(level==="pair"||(r.target||"")===target)).reduce((mx,r)=>Math.max(mx,r.version||0),0);
sub.textContent=fmt(S.prompts_create_btn_v,{next:maxV+1});
};
for(const field of ["task","level","source","target"]){
fe[field].addEventListener("change",updateCreateLabel);
}
updateCreateLabel();
}
const LLM_PROFILES=["translation","fill","analysis"];
function bindSettingsLLM(){
const form=$("u3-llm-form");
if(!form)return;
const fe=form.elements;
const msg=$("u3-llm-msg");
const probeMsg=$("u3-llm-probe-msg");
const saveBtn=$("u3-llm-save");
const sharedEl=fe.shared_endpoint;
const numOr=v=>v===""?null:Number(v);
const profileData=name=>({
base_url:fe["base_url_"+name].value.trim(),
model:fe["model_"+name].value.trim(),
api_key:fe["api_key_"+name].value,
temperature:numOr(fe["temperature_"+name].value),
retry_times:numOr(fe["retry_times_"+name].value),
concurrency:numOr(fe["concurrency_"+name].value),
extra_body:fe["extra_body_"+name].value.trim(),
});
const syncShared=()=>form.classList.toggle("u3-llm-shared-on",sharedEl.checked);
sharedEl.addEventListener("change",syncShared);
syncShared();
const setProbeBanner=(name,ok,text)=>{
if(!probeMsg)return;
probeMsg.hidden=!text;
probeMsg.classList.toggle("u3-ok",Boolean(ok));
probeMsg.classList.toggle("u3-warn",!ok);
probeMsg.textContent=text?fmt(S.llm_profile_title,{profile:name})+": "+text:"";
};
const runProbe=async name=>{
setProbeBanner(name,null,S.common_sending);
const data=Object.assign({profile:name},profileData(name));
if(sharedEl.checked)data.base_url=fe.base_url.value.trim();
try{
const result=await fetchJSON("/api/llm/probe",{method:"POST",headers:JH,body:JSON.stringify(data)});
setProbeBanner(name,result.ok,result.message);
}catch(e){
setProbeBanner(name,false,e.message);
}
};
const runSave=async()=>{
msg.textContent=S.common_sending;
saveBtn.disabled=true;
const data={shared_endpoint:sharedEl.checked,base_url:fe.base_url.value.trim(),profiles:{}};
for(const name of LLM_PROFILES)data.profiles[name]=profileData(name);
try{
await fetchJSON("/api/config/llm",{method:"PUT",headers:JH,body:JSON.stringify(data)});
msg.textContent=S.llm_saved;
setTimeout(()=>window.location.reload(),1000);
}catch(e){
saveBtn.disabled=false;
failWithRetry(msg,apiErr(e),runSave);
}
};
let modelsTimer;
const loadModels=url=>{
if(modelsTimer)clearTimeout(modelsTimer);
if(!url)return;
modelsTimer=setTimeout(async()=>{
try{
const data=await fetchJSON("/api/llm/models?base_url="+enc(url));
const list=$("u3-llm-models-list");
if(list)list.innerHTML=(data.models||[]).map(m=>"<option value='"+esc(m)+"'>").join("");
}catch(e){
msg.textContent=fmt(S.llm_models_unavailable,{reason:e.message});
}
},500);
};
fe.base_url.addEventListener("input",()=>{
if(sharedEl.checked)loadModels(fe.base_url.value.trim());
});
for(const name of LLM_PROFILES){
fe["base_url_"+name].addEventListener("input",()=>{
if(!sharedEl.checked)loadModels(fe["base_url_"+name].value.trim());
});
}
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.getAttribute)return;
const name=target.getAttribute("data-u3-llm-extra-reset");
if(!name)return;
fe["extra_body_"+name].value=target.getAttribute("data-saved")||"";
});
if(sharedEl.checked)loadModels(fe.base_url.value.trim());
form.addEventListener("submit",e=>{
e.preventDefault();
const btn=e.submitter;
if(btn&&btn.getAttribute("data-u3-llm-probe"))runProbe(btn.getAttribute("data-u3-llm-probe"));
else runSave();
});
}
function bindAppearance(){
const form=$("u3-appearance-form");
if(!form)return;
form.addEventListener("change",e=>{
if(e.target&&e.target.hasAttribute("data-u3-appearance"))form.submit();
});
}
function bindMaintenance(){
const clearForm=document.querySelector("[data-u3-logs-clear]");
if(clearForm){
const msg=$("u3-logs-clear-msg");
clearForm.addEventListener("submit",e=>{
e.preventDefault();
(async()=>{
try{
const data=await fetchJSON("/api/logs/clear",{method:"POST",headers:JH,body:"{}"});
if(msg)msg.textContent=fmt(S.maint_clear_logs_done,{bytes:data.bytes});
}catch(e2){
if(msg)msg.textContent=apiErr(e2);
}
})();
});
}
const purgeForm=document.querySelector("[data-u3-purge]");
if(purgeForm){
const fe=purgeForm.elements;
const msg=$("u3-purge-msg");
purgeForm.addEventListener("submit",e=>{
e.preventDefault();
(async()=>{
const raw=fe.days.value.trim();
const days=raw===""?null:Number(raw);
if(days!=null&&(!Number.isFinite(days)||days<0)){
msg.textContent=S.err_purge_bad_days;
return;
}
msg.textContent=S.common_sending;
try{
const dry=await fetchJSON("/api/jobs/purge", {method:"POST",headers:JH,body:JSON.stringify(days==null?{}:{days:days})});
if(!confirm(fmt(S.maint_purge_confirm_info,{count:dry.count,bytes:humanSize(dry.total_bytes)}))){
msg.textContent="";
return;
}
const result=await fetchJSON("/api/jobs/purge", {method:"POST",headers:JH,body:JSON.stringify({days:days,confirm_token:dry.confirm_token})});
msg.textContent=fmt(S.maint_purge_done,{count:result.count,bytes:humanSize(result.total_bytes)});
}catch(e2){
msg.textContent=apiErr(e2);
}
})();
});
}
}
let openSheetEl;
function initSheets(){
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.closest)return;
const a=target.closest("[data-sheet-open]");
if(!a)return;
e.preventDefault();
const d=$(a.dataset.sheetOpen);
if(!d||!d.showModal||d.open)return;
if(d.id==="u3-sheet-export"){
if(a.dataset.job)fillExportBody(a.dataset.job,dec(a.dataset.bookName||""));
else if(a.dataset.book)fillExportBook(dec(a.dataset.book||""));
}
d.showModal();
openSheetEl=d;
const det=a.closest("details");
if(det)det.removeAttribute("open");
history.pushState({u3sheet:d.id},"");
});
for(const d of document.querySelectorAll(".u3-sheet")){
d.addEventListener("click",e=>{
if(e.target===d&&d.open)d.close();
});
d.addEventListener("close",()=>{
if(openSheetEl===d){
openSheetEl=null;
history.back();
}
});
}
window.addEventListener("popstate",()=>{
if(!openSheetEl)return;
const d=openSheetEl;
openSheetEl=null;
if(d.open)d.close();
});
}
document.addEventListener("click",e=>{
const target=e.target;
if(!target||!target.hasAttribute)return;
if(target.hasAttribute("data-u3-poll-retry")&&jobPolling){
jobPolling.retry();
}else if(target.hasAttribute("data-u3-jobs-retry")&&jobsListRetry){
jobsListRetry();
}else if(target.hasAttribute("data-u3-books-retry")&&booksRefresh){
booksRefresh();
}else if(target.hasAttribute("data-u3-export-retry")){
if(target.dataset.job)fillExportBody(target.dataset.job,dec(target.dataset.book||""));
else if(target.dataset.book)fillExportBook(dec(target.dataset.book||""));
}else if(target.hasAttribute("data-u3-exp-run")){
exportRunClick(target);
}else if(target.hasAttribute("data-u3-retry")){
const container=target.closest("[data-u1-fetch]");
if(container)loadContainer(container);
}
});
document.addEventListener("DOMContentLoaded",()=>{
initLogsControls();
initFetchContainers();
startJobPolling();
startJobsList();
startBooksList();
initSheets();
bindImportSheet();
bindJobActions();
bindBookDeletes();
bindBookMeta();
bindPurge();
bindPrompts();
bindSettingsLLM();
bindAppearance();
bindMaintenance();
initRowActions();
initSwipe();
initQueueDrag();
initSelection();
bindCreateForm();
bindBulkDelete();
bindDeleteJobSheet();
});
