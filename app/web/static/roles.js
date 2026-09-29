"use strict";
// TZ-UI-AUDIT R-T1/R-T4: uniform data-role layer, kept in a separate file so
// app.js stays within the M2 JS budget (test_m2_8, 81920 B). A distinct a11y
// concern: assign data-role by component class, idempotent (never overrides an
// already-set role); MutationObserver covers the initial render and every
// dynamic innerHTML (lists, states, sheets). Role tokens match the CSS.
// Self-contained (no app.js globals); loaded after app.js in base.html.
function _hexRgb(h){
h=String(h).replace("#","").trim();
if(h.length===3)h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
const n=parseInt(h,16);
return "rgb("+((n>>16)&255)+", "+((n>>8)&255)+", "+(n&255)+")";
}
// Order: specific classes first, fallbacks (bare button/input) last, so the
// specific role wins. R-T4 flags elements whose token diverges from the
// standard role (swipe/.btn/textarea) -- honest findings, not noise.
const ROLE_RULES=[
[".u3-btn.u3-primary","action.primary"],
[".u3-btn.u3-danger","action.destructive"],
[".u3-btn.u3-text","action.text"],
[".u3-btn.u3-tertiary","action.tertiary"],
[".u3-btn:not(.u3-primary):not(.u3-danger):not(.u3-text):not(.u3-tertiary)","action.secondary"],
[".u3-row-btn","action.icon"],
[".u3-fab","action.fab"],
[".u3-swipe-btn.u3-danger","action.destructive"],
[".u3-swipe-btn","action.secondary"],
[".u3-chip-select.is-selected,.u3-chip-select.is-current","chip.selected"],
[".u3-chip-select:not(.is-selected):not(.is-current)","chip.default"],
[".u3-seg-pill.is-current","seg.pill.current"],
[".u3-seg-pill:not(.is-current)","seg.pill"],
[".u3-switch","switch"],
[".u3-fld input","input.field"],
["textarea","input.field"],
["select","select"],
[".u3-tab.is-current","link.accent"],
[".u3-fstrip-reset,.u3-book-all","link.accent"],
[".u3-tab:not(.is-current),.u3-llm-keydel","link.muted"],
["button","action.secondary"],
["input","input.field"],
];
function applyRoles(root){
const scope=root&&root.querySelectorAll?root:document;
for(const [sel,role] of ROLE_RULES){
for(const el of scope.querySelectorAll(sel)){ if(!el.dataset.role)el.dataset.role=role; }
}
// remaining links: role by actual color (fg/fgm/acf from :root); guarded so a
// minimal DOM env without getComputedStyle still runs (ROLE_RULES roles set).
if(typeof getComputedStyle==="function"){
const rs=getComputedStyle(document.documentElement);
const cFg=_hexRgb(rs.getPropertyValue("--fg"));
const cFgm=_hexRgb(rs.getPropertyValue("--fgm"));
const cAcf=_hexRgb(rs.getPropertyValue("--acf"));
for(const a of scope.querySelectorAll("a[href]")){
if(a.dataset.role)continue;
const c=getComputedStyle(a).color;
a.dataset.role=c===cAcf?"link.accent":(c===cFgm?"link.muted":"link");
}
}
}
let _rolesQueued=false;
function queueRoles(){
if(_rolesQueued)return;
_rolesQueued=true;
requestAnimationFrame(()=>{ _rolesQueued=false; applyRoles(document.body); });
}
function initRoles(){
applyRoles(document.body);
// MutationObserver attached only in a real browser (live re-apply).
if(typeof MutationObserver==="function"){
new MutationObserver(queueRoles).observe(document.body,{childList:true,subtree:true});
}
}
if(document.readyState==="loading"){
document.addEventListener("DOMContentLoaded",initRoles);
}else{initRoles();}
