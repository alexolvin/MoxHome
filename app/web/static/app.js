/* U3 UI: no frameworks/build, local assets only.
* Strings from app/web/strings.py (U3-1); statuses/codes/reasons verbatim.
* [data-u1-fetch]+[data-u1-render] (U1-6); [data-u1-poll] job polling (U1-1).
* auto-refresh (3.11): stop on terminal, pause hidden, backoff, aria-live.
* POST/DELETE carry X-U3-CSRF (3.9.41).
*/
"use strict";
/* base */
const $ = (id) => document.getElementById(id);
const S = JSON.parse($("u3-strings").textContent);
const ui = JSON.parse($("u3-ui").textContent);
const enc = encodeURIComponent;
const $ce = (t) => document.createElement(t);
const NS = S.common_not_set;
const cmdFail = (e) => fmt(S.common_cmd_failed, { msg: e.message });
const apiErr = (e) => fmt(S.common_err, { msg: e.message });
const JH = { "Content-Type": "application/json" };
const nowTime = () => new Date().toLocaleTimeString();
const RETRY = (a) => " <button type='button' data-" + a + ">" + esc(S.common_retry) + "</button>";
const RFR = () => ui.refresh_seconds * 1000;
const onVis = (ok, fn) => document.addEventListener("visibilitychange", () => { if (ok() && document.visibilityState === "visible") fn(); });
const csrfToken = () => (document.querySelector('meta[name="u3-csrf"]') || {}).content || "";
const ESCM = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
function esc(value) {
 return String(value == null ? NS : value).replace(/[&<>"]/g, (c) => ESCM[c]);
}
function fmt(t, v) {
 return String(t).replace(/\{(\w+)\}/g, (m, k) => (k in v ? String(v[k]) : m));
}
function humanSize(numBytes) {
 if (numBytes == null) return NS;
 const units = [[1024 ** 3, S.common_size_gb], [1024 ** 2, S.common_size_mb], [1024, S.common_size_kb]];
 for (const [divider, unit] of units) {
  if (numBytes >= divider) return (numBytes / divider).toFixed(1) + " " + unit;
 }
 return String(numBytes) + " " + S.common_size_b;
}
async function fetchJSON(url, options) {
 options = options || {};
 const method = options.method || "GET";
 if (method !== "GET" && method !== "HEAD") {
  const headers = Object.assign({}, options.headers);
  const token = csrfToken();
  if (token) headers["X-U3-CSRF"] = token;
  options.headers = headers;
 }
 const response = await fetch(url, options);
 const text = await response.text();
 let data = null;
 try {
  data = JSON.parse(text);
 } catch {}
 if (!response.ok) {
  const detail = (data && data.detail) || text || String(response.status);
  throw new Error(detail);
 }
 return data;
}
/* page filter goes to the API; page is UI-only */
function filteredApiUrl(url) {
 if (!url || url.indexOf("?") !== -1) return url;
 const params = new URLSearchParams(window.location.search || "");
 if (!params.size) return url;
 params.delete("page");
 const query = params.toString();
 return query ? url + "?" + query : url;
}
/* R2: build a DOM node from an html string (list row builders) */
function fromHtml(html) {
 const t = document.createElement("template");
 t.innerHTML = html.trim();
 return t.content.firstElementChild;
}
/* R2 §14: state 5 (no network) = fetch rejection; state 4 (HTTP error)
   = Error thrown by fetchJSON on !ok */
function isNetErr(e) {
 return e instanceof TypeError || /failed to fetch|networkerror/i.test(String(e.message));
}
/* R2 §14: sticky "no connection" bar above the list; data is kept */
function netbar(el) {
 const old = el.querySelector(":scope > .u3-netbar");
 if (old && old.remove) old.remove();
 const bar = $ce("div");
 bar.className = "u3-netbar";
 bar.setAttribute("role", "status");
 bar.textContent = S.state_netbar;
 if (el.prepend) el.prepend(bar);
}
/* dual rendering */
function cardHtml(fields, actionsHtml) {
 const dl = fields
  .map(
   (field) =>
    "<div class='u3-field'><dt>" + esc(field[0]) + "</dt>" + "<dd" + (field[2] ? " class='" + field[2] + "'" : "") + ">" + field[1] + "</dd></div>"
  )
  .join("");
 const actions = actionsHtml
  ? "<div class='u3-card-actions'>" + actionsHtml + "</div>"
  : "";
 return (
  "<article class='u3-card'><dl class='u3-card-fields'>" + dl + "</dl>" + actions + "</article>"
 );
}
function tableHtml(columns, rowsHtml) {
 return (
  "<table class='u3-table'><thead><tr>" + columns.map((c) => "<th scope='col'>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>" + rowsHtml + "</tbody></table>"
 );
}
function cardsHtml(cards) {
 return "<div class='u3-cards'>" + cards.join("") + "</div>";
}
/* [html, cls?] per column: one source for table + cards (cardHtml gets
   [label=COLS[i], html, cls] with the same layout) */
function fieldsRows(fieldsList) {
 return fieldsList
  .map(
   (f) =>
    "<tr>" + f.map((c) => "<td" + (c[1] ? " class='" + c[1] + "'" : "") + ">" + c[0] + "</td>").join("") + "</tr>"
  )
  .join("");
}
function jobMode(j) {
 return (
  esc(j.mode) + (j.learning_level
   ? " / " + esc(j.learning_level) + (j.learning_cefr ? " (" + esc(j.learning_cefr) + ")" : "")
   : "")
 );
}
function jobPair(j) {
 return esc(j.source_language || NS) + " → " + esc(j.target_language);
}
function alignmentValue(coverage) {
 return coverage != null ? (coverage * 100).toFixed(1) + "%" : NS;
}
/* R2 §10: job list row (builder #2; #1 — _job_row.html, same markup).
   Three text lines + 44px action zone (buttons — R3); strip at the
   bottom edge; id not printed. */
const GROUP_KEYS = ["now", "today", "earlier"];
const GROUP_LABELS = { now: "jobs_group_now", today: "jobs_group_today", earlier: "jobs_group_earlier" };
function jobLine2(j) {
 return (
  (j.source_language || NS) + "→" + j.target_language + " · " + j.mode +
  (j.learning_cefr ? " · " + j.learning_cefr : "")
 );
}
/* R2 §10: "12 min", after an hour "1 h 20 min" (single format point,
   same rules as views.human_duration) */
function fmtDuration(totalSeconds) {
 const m = Math.max(0, Math.floor((totalSeconds || 0) / 60));
 if (m < 60) return fmt(S.time_min, { n: m });
 return fmt(S.time_hm, { h: Math.floor(m / 60), m: m % 60 });
}
function jobLine3(j) {
 const pct = j.progress_pct == null ? "" : "<span class='u3-row-sep'> · </span>" + j.progress_pct + "%";
 return (
  "<span class='u3-status status-" + esc(j.status_group) + "'><span class='u3-status-ico' aria-hidden='true'>" + esc(j.status_glyph) + "</span>" +
  esc(j.status) + "</span>" + pct +
  "<span class='u3-row-sep'> · </span>" + fmtDuration(j.elapsed_seconds)
 );
}
function jobRowHtml(j, group) {
 const strip =
  j.progress_pct == null
   ? "<span class='u3-row-strip is-ind' aria-hidden='true'></span>"
   : "<span class='u3-row-strip' aria-hidden='true'" + (j.progress_pct > 0 ? " style='width: " + j.progress_pct + "%'" : "") + "></span>";
 return (
  "<article class='u3-row u3-row-job status-" + esc(j.status_group) + "' data-id='" + esc(j.id) + "' data-group='" + esc(group) + "' data-status='" + esc(j.status) + "' data-status-group='" + esc(j.status_group) + "' data-rev='" + esc(j.updated_at) + "'>" +
  "<a class='u3-row-body' href='/jobs/" + esc(j.id) + "'>" +
  "<span class='u3-row-line1' title='" + esc(j.book_name) + "'>" + esc(j.book_name) + "</span>" +
  "<span class='u3-row-line2'>" + esc(jobLine2(j)) + "</span>" +
  "<span class='u3-row-line3'>" + jobLine3(j) + "</span>" +
  "</a>" +
  "<span class='u3-row-action' aria-hidden='true'></span>" +
  strip +
  "</article>"
 );
}
/* R2 §6: book list row (builder #2; #1 — _book_row.html) */
function bookRowHtml(b) {
 const title = b.title || b.name;
 const dot = b.has_active_job ? "<span class='u3-row-dot' aria-hidden='true'></span>" : "";
 return (
  "<article class='u3-row u3-row-book' data-name='" + esc(b.name) + "'>" +
  "<span class='u3-row-cover' aria-hidden='true'>" + esc(title.charAt(0).toUpperCase()) + "</span>" +
  "<span class='u3-row-body'>" +
  "<span class='u3-row-line1' title='" + esc(title) + "'>" + esc(title) + "</span>" +
  "<span class='u3-row-line2'>" + esc(b.detected || b.dc_language || NS) + "→? · " + esc(b.size_human || NS) + "</span>" +
  "</span>" +
  dot +
  "<details class='u3-row-menu'><summary class='u3-row-more' aria-label='" + esc(S.row_actions_label) + "'>&#8943;</summary><menu>" +
  "<button type='button' class='u3-row-menu-item u3-danger' data-book-delete='" + esc(b.name) + "'>" + esc(S.books_delete_btn) + "</button>" +
  "</menu></details>" +
  "</article>"
 );
}
/* renderers (R2: u1-jobs/u1-books lists are owned by their engines —
   startJobsList/startBooksList; table+cards renderers left for settings) */
const RENDERERS = {
 languages(el, data) {
  const M = "u3-mono";
  const L = [S.languages_col_code, S.languages_col_name, S.languages_col_spacy, S.languages_col_cefr];
  const langF = data.languages.map((l) => [
   [S.languages_col_code, esc(l.code), M],
   [S.languages_col_name, esc(l.name)],
   [S.languages_col_spacy, esc(l.spacy_model || NS), M],
   [S.languages_col_cefr, esc(l.cefr_label || NS)],
  ]);
  const P = [S.pairs_col_source, S.pairs_col_target, S.pairs_col_status, S.pairs_col_creatable, S.pairs_col_missing];
  const pairF = data.pairs.map((p) => [
   [S.pairs_col_source, esc(p.source), M],
   [S.pairs_col_target, esc(p.target), M],
   [S.pairs_col_status, esc(p.status)],
   [S.pairs_col_creatable, p.creatable ? esc(S.pairs_creatable_yes) : esc(S.pairs_creatable_no)],
   [S.pairs_col_missing, esc((p.missing || []).join("; ") || NS), M],
  ]);
  el.innerHTML =
   "<h2>" + esc(S.languages_h2) + "</h2>" + tableHtml(L, fieldsRows(langF)) + cardsHtml(langF.map((f) => cardHtml(f))) + "<h2>" + esc(S.pairs_h2) + "</h2>" + tableHtml(P, fieldsRows(pairF)) + cardsHtml(pairF.map((f) => cardHtml(f)));
 },
 models(el, models) {
  const M = "u3-mono";
  const C = [S.models_col_profile, S.models_col_endpoint, S.models_col_model, S.models_col_key, S.models_col_timeout, S.models_col_retry, S.models_col_concurrency, S.models_col_cost];
  const F = models.map((m) => [
   [S.models_col_profile, esc(m.name), M],
   [S.models_col_endpoint, esc(m.base_url), M],
   [S.models_col_model, esc(m.model), M],
   [S.models_col_key, m.api_key_set ? esc(S.models_key_set) : esc(S.models_key_unset)],
   [S.models_col_timeout, esc(m.timeout_seconds)],
   [S.models_col_retry, esc(m.retry_times)],
   [S.models_col_concurrency, esc(m.concurrency)],
   [S.models_col_cost, esc(m.cost)],
  ]);
  el.innerHTML = tableHtml(C, fieldsRows(F)) + cardsHtml(F.map((f) => cardHtml(f)));
 },
 health(el, data) {
  const entries = [];
  for (const [name, value] of Object.entries(data)) {
   if (name === "detail") continue;
   if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [sub, status] of Object.entries(value)) {
     entries.push([name + "/" + sub, status]);
    }
   } else {
    entries.push([name, value]);
   }
  }
  let html =
   "<table class='u3-static'><thead><tr><th scope='col'>" + esc(S.settings_health_col_component) + "</th><th scope='col'>" + esc(S.settings_health_col_status) + "</th></tr></thead><tbody>";
  for (const [name, status] of entries) {
   const text = String(status);
   const cls = text.startsWith("ok") ? "ok" : text.startsWith("error") ? "err" : "";
   html +=
    "<tr><td>" + esc(name) + "</td><td" + (cls ? " class='" + cls + "'" : "") + ">" + esc(status) + "</td></tr>";
  }
  html += "</tbody></table>";
  const detail = data.detail;
  if (detail && Object.keys(detail).length) {
   html += "<pre>" + esc(JSON.stringify(detail, null, 2)) + "</pre>";
  }
  el.innerHTML = html;
 },
 config(el, data) {
  el.innerHTML = "<pre>" + esc(JSON.stringify(data)) + "</pre>";
 },
 quality(el, data) {
  let html = "";
  if (data.qc && Object.keys(data.qc).length) {
   html +=
    "<h3>" + esc(S.qc_title) + "</h3><pre>" + esc(JSON.stringify(data.qc)) + "</pre>";
  }
  const a = data.alignment || {};
  html +=
   "<h3>" + esc(S.qc_alignment_title) + "</h3><p>" + (a.coverage != null
    ? esc(fmt(S.qc_alignment_coverage, {
      pct: (a.coverage * 100).toFixed(1),
      aligned: a.aligned,
      total: a.total,
     }))
    : esc(S.qc_alignment_na)) + "</p>";
  const g = data.glossary_consistency;
  html +=
   "<h3>" + esc(S.qc_glossary_title) + "</h3><p>" + (g && typeof g === "object" && g.consistent_share != null
    ? esc(fmt(S.qc_glossary_value, {
      share: (g.consistent_share * 100).toFixed(1),
     }))
    : esc(S.qc_glossary_na)) + "</p>";
  if (Array.isArray(data.warnings) && data.warnings.length) {
   html +=
    "<h3>" + esc(S.qc_warnings) + "</h3><ul>" + data.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>";
  }
  const l3 = data.l3;
  html += "<h3>" + esc(S.qc_l3_title) + "</h3>";
  if (l3) {
   html +=
    "<p>" + esc(fmt(S.qc_l3_stats, {
     attempted: l3.attempted,
     applied: l3.applied,
     rejected: l3.rejected,
     halted: l3.halted,
    })) + "</p>";
   if (Array.isArray(l3.fixes) && l3.fixes.length) {
    const cols = [
     S.qc_l3_col_segment,
     S.qc_l3_col_doc,
     S.qc_l3_col_term,
     S.qc_l3_col_applied,
     S.qc_l3_col_old,
     S.qc_l3_col_new,
     S.qc_l3_col_reason,
    ];
    html +=
     "<table class='u3-static'><thead><tr>" + cols.map((c) => "<th scope='col'>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>";
    for (const f of l3.fixes) {
     html +=
      "<tr><td>" + esc(f.segment_id) + "</td><td>" + esc(f.doc) + "</td><td>" + esc(f.term) + "</td><td>" + (f.applied ? esc(S.common_yes) : esc(S.common_no)) + "</td><td>" + esc(f.old_text) + "</td><td>" + esc(f.new_text) + "</td><td>" + esc(f.reason || NS) + "</td></tr>";
    }
    html += "</tbody></table>";
   }
  } else {
   html += "<p>" + esc(S.qc_l3_none) + "</p>";
  }
  el.innerHTML = html;
 },
 logs(el, data) {
  const blocks = [
   ["job.log", data.job],
   ["errors.log", data.errors],
   ["quality.log", data.quality],
  ];
  el.innerHTML = blocks
   .map(
    ([name, lines]) =>
     "<h3>" + name + "</h3><pre>" + (Array.isArray(lines) && lines.length
      ? esc(lines.join("\n"))
      : esc(S.job_logs_empty)) + "</pre>"
   )
   .join("");
 },
};
/* job page facts */
/* E6: requested pause/cancel row (role=status), survives poll re-render */
const reqRow = (text) =>
 "<p id='u3-job-requested' role='status' class='u3-warn'>" + esc(text) + "</p>";
function renderProgress(f) {
 const seg = f.segments
  ? f.segments.processed + " / " + f.segments.total
  : NS;
 const align = f.alignment.coverage != null
  ? (f.alignment.coverage * 100).toFixed(1) + "% (" + f.alignment.aligned + " / " + f.alignment.total + ")"
  : NS;
 const tokens = Object.entries(f.tokens)
  .filter(([name]) => name !== "total")
  .map(([name, t]) => esc(name) + ": " + esc(t.total))
  .join(" · ");
 const timeCell =
  fmt(S.job_time_elapsed, { n: f.elapsed_seconds }) + (f.status === "PAUSED" && f.paused_at_human
   ? " · " + fmt(S.job_paused_since, { t: f.paused_at_human })
   : f.eta_seconds != null
    ? " · " + fmt(S.job_time_eta, { n: f.eta_seconds })
    : "");
 const rows = [
  [
   S.job_row_status,
   /* R1 §4: group+glyph from API (single table views.STATUS_GROUP) */
   "<span class='u3-status status-" + esc(f.status_group) + "'><span class='u3-status-ico' aria-hidden='true'>" + esc(f.status_glyph) + "</span>" + esc(f.status) + (f.error ? " — <span class='err'>" + esc(f.error) + "</span>" : "") + "</span>",
  ],
  [S.job_row_pair, jobPair(f)],
  [S.job_row_mode, jobMode(f)],
  [S.job_row_progress, esc(f.progress_display)],
  [
   S.job_row_phase,
   (f.phase ? esc(f.phase) : NS) + " (" + esc(f.phases.completed) + " / " + esc(f.phases.total) + ")",
  ],
  [S.job_row_segments, seg],
  [S.job_row_alignment, align],
  [
   S.job_row_tokens,
   (tokens ? tokens + " · " : "") + esc(S.job_tokens_total) + ": " + esc(f.tokens.total),
  ],
  [S.job_row_time, timeCell],
  [
   S.job_row_errors,
   fmt(S.job_errors_segments, { n: f.failed_segments }) + " · " + fmt(S.job_errors_retries, { n: f.retries }),
  ],
  [S.job_row_created, esc(f.created_at_human)],
  [S.job_row_updated, esc(f.updated_at_human)],
 ];
 let html = f.cancel_requested
  ? reqRow(S.job_cancel_requested)
  : f.pause_requested
   ? reqRow(S.job_pause_requested)
   : "";
 html += "<table class='u3-facts'>";
 for (const [label, cell] of rows) {
  html += "<tr><th scope='row'>" + esc(label) + "</th><td>" + cell + "</td></tr>";
 }
 html += "</table>";
 if (f.stages && f.stages.length) {
  const cols = [
   S.job_stages_col_time,
   S.job_stages_col_transition,
   S.job_stages_col_reason,
  ];
  const tableRows = f.stages
   .map(
    (st) =>
     "<tr><td>" + esc(st.at_human) + "</td><td>" + esc(st.from) + " → " + esc(st.to) + "</td><td>" + esc(st.reason || NS) + "</td></tr>"
   )
   .join("");
  const cards = f.stages.map((st) =>
   cardHtml([
    [S.job_stages_col_time, esc(st.at_human)],
    [S.job_stages_col_transition, esc(st.from) + " → " + esc(st.to)],
    [S.job_stages_col_reason, esc(st.reason || NS)],
   ])
  );
  html +=
   "<details id='u1-stages-details'><summary>" + esc(fmt(S.job_stages, { n: f.stages.length })) + "</summary>" + tableHtml(cols, tableRows) + cardsHtml(cards) + "</details>";
 }
 if (f.warnings && f.warnings.length) {
  html +=
   "<p>" + esc(S.job_warnings) + "</p><ul>" + f.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>";
 }
 return html;
}
/* auto-refresh (3.11) */
/* no polling: terminal + PAUSED; INTERRUPTED excluded: worker may reclaim it (3.11.47) */
const INACTIVE_JOB_STATUSES = new Set([
 "COMPLETED",
 "COMPLETED_WITH_WARNINGS",
 "FAILED",
 "CANCELLED",
 "PAUSED",
]);
let jobsListRetry = null;
let jobsRefresh = null;
function startJobsList() {
 /* R2 §10: boot fetch (full) + delta ?since=<rev>; partial updates —
    status/percent/time/group per row, no full re-render; pill + exact
    status filter client-side (delta must be unfiltered: a row leaving
    the filter has to be visible in the delta to be removed); stop when
    all jobs inactive; visible — refresh_seconds, hidden —
    refresh_seconds_max (TZ §10); states per §14 on .u3-list-body. */
 const el = $("u1-jobs");
 if (!el || !el.dataset.u1Fetch) return;
 const body = el.querySelector(":scope > .u3-list-body");
 if (!body) return;
 const announceEl = $("u3-jobs-announce");
 const refreshEl = $("u3-jobs-refresh");
 const group = el.dataset.u1Group || "all";
 const statusSet = (el.dataset.u1Status || "").split(",").filter(Boolean);
 const sortKey = el.dataset.u1Sort === "created_at" ? "created_at" : "updated_at";
 const sortDir = el.dataset.u1Direction === "asc" ? "asc" : "desc";
 const GROUP_OF_PILL = { active: ["run"], waiting: ["wait"], done: ["ok", "warn"], errors: ["err"] };
 const allowGroups = GROUP_OF_PILL[group] || null;
 const rowsById = new Map();
 const rowsEls = {};
 const secEls = {};
 let lastRev = 0;
 let timer = null;
 let stopped = false;
 let inFlight = false;
 let backoffMs = RFR();
 let hasData = false;
 const prevStatus = new Map();
 /* ids of non-terminal jobs; a delta batch may be empty, so stopping is
    judged on this set (full fetch seeds it, deltas maintain it) */
 const activeIds = new Set();
 const isInactive = (s) => INACTIVE_JOB_STATUSES.has(s);

 const matches = (j) => {
  if (allowGroups && allowGroups.indexOf(j.status_group) === -1) return false;
  if (statusSet.length && statusSet.indexOf(j.status) === -1) return false;
  return true;
 };
 const filtered = () => statusSet.length > 0 || group !== "all";
 const sorted = (list) =>
  list.slice().sort((a, b) => (sortDir === "asc" ? a[sortKey] - b[sortKey] : b[sortKey] - a[sortKey]));
 const touchSection = (gname) => {
  if (secEls[gname]) secEls[gname].classList.toggle("is-empty", !rowsEls[gname].children.length);
 };
 const ensureSections = () => {
  body.innerHTML = "";
  body.setAttribute("data-state", "ok");
  for (const gname of GROUP_KEYS) {
   const sec = $ce("section");
   sec.className = "u3-group";
   sec.dataset.group = gname;
   const h = $ce("h2");
   h.className = "u3-group-head";
   h.textContent = S[GROUP_LABELS[gname]];
   const rows = $ce("div");
   rows.className = "u3-group-rows";
   rows.dataset.group = gname;
   sec.append(h, rows);
   body.appendChild(sec);
   secEls[gname] = sec;
   rowsEls[gname] = rows;
  }
 };
 const stateHtml = (state, line, actionHtml) =>
  "<div class='u3-state' data-state='" + state + "'>" +
  "<span class='u3-state-ico' aria-hidden='true'>&#9675;</span>" +
  "<p class='u3-state-line'>" + esc(line) + "</p>" + actionHtml + "</div>";
 const showEmpty = () => {
  const f = filtered();
  body.setAttribute("data-state", f ? "empty-filter" : "empty");
  body.innerHTML = f
   ? stateHtml("empty-filter", S.state_not_found, "<a class='u3-btn u3-primary' href='/jobs'>" + esc(S.state_reset_filter) + "</a>")
   : stateHtml("empty", S.jobs_empty_line, "<a class='u3-btn u3-primary' href='/books'>" + esc(S.jobs_empty_btn) + "</a>");
  rowsById.clear();
 };
 const showError = (e) => {
  body.setAttribute("data-state", "error");
  body.innerHTML = stateHtml("error", fmt(S.common_err, { msg: e.message }), "<button type='button' class='u3-btn u3-primary' data-u3-jobs-retry>" + esc(S.common_retry) + "</button>");
  rowsById.clear();
 };
 const announceChange = (j) => {
  if (announceEl)
   announceEl.textContent = fmt(S.jobs_announce_status, { id: j.id, status: j.status, time: nowTime() });
 };
 const resortSection = (gname) => {
  const list = [...rowsEls[gname].children]
   .map((n) => rowsById.get(n.dataset.id))
   .filter(Boolean);
  for (const j of sorted(list)) rowsEls[gname].appendChild(j);
 };
 const applyFull = (rows) => {
  hasData = true;
  if (refreshEl) refreshEl.textContent = "";
  activeIds.clear();
  for (const j of rows) {
   if (!isInactive(j.status)) activeIds.add(j.id);
   if (j.updated_at > lastRev) lastRev = j.updated_at;
  }
  if (!rows.filter(matches).length) {
   showEmpty();
   return;
  }
  ensureSections();
  for (const j of sorted(rows.filter(matches))) {
   const node = fromHtml(jobRowHtml(j, j.group));
   rowsEls[j.group].appendChild(node);
   rowsById.set(j.id, j);
  }
  for (const gname of GROUP_KEYS) touchSection(gname);
 };
 const updateRow = (node, j) => {
  node.className = "u3-row u3-row-job status-" + j.status_group;
  node.dataset.status = j.status;
  node.dataset.statusGroup = j.status_group;
  node.dataset.rev = String(j.updated_at);
  const l1 = node.querySelector(".u3-row-line1");
  if (l1) {
   l1.textContent = j.book_name;
   l1.title = j.book_name;
  }
  const l2 = node.querySelector(".u3-row-line2");
  if (l2) l2.textContent = jobLine2(j);
  const l3 = node.querySelector(".u3-row-line3");
  if (l3) l3.innerHTML = jobLine3(j);
  const strip = node.querySelector(".u3-row-strip");
  if (strip) {
   strip.classList.toggle("is-ind", j.progress_pct == null);
   strip.style.width = j.progress_pct == null || j.progress_pct <= 0 ? "" : j.progress_pct + "%";
  }
 };
 const applyDelta = (rows) => {
  for (const j of rows) {
   if (j.updated_at > lastRev) lastRev = j.updated_at;
   if (isInactive(j.status)) activeIds.delete(j.id);
   else activeIds.add(j.id);
   const node = rowsById.get(j.id);
   const was = prevStatus.get(j.id);
   const changed = was !== undefined && was !== j.status;
   if (!node) {
    if (matches(j)) {
     if (body.getAttribute("data-state") !== "ok") ensureSections();
     rowsEls[j.group].appendChild(fromHtml(jobRowHtml(j, j.group)));
     rowsById.set(j.id, j);
     touchSection(j.group);
    }
   } else if (!matches(j)) {
    const from = node.dataset.group;
    node.remove();
    rowsById.delete(j.id);
    touchSection(from);
    if (!rowsById.size) showEmpty();
    if (changed) announceChange(j);
   } else {
    let from = null;
    if (node.dataset.group !== j.group) {
     from = node.dataset.group;
     rowsEls[j.group].appendChild(node);
     touchSection(from);
    }
    updateRow(node, j);
    if (from) {
     node.dataset.group = j.group;
     touchSection(j.group);
    }
    rowsById.set(j.id, j);
    if (from || sortKey === "updated_at") resortSection(j.group);
    if (changed) announceChange(j);
   }
  }
 };
 const stop = () => {
  stopped = true;
  if (timer) {
   clearTimeout(timer);
   timer = null;
  }
 };
 const schedule = (ms) => {
  if (stopped) return;
  if (timer) clearTimeout(timer);
  timer = setTimeout(tick, ms);
 };
 const tickInterval = () => (document.hidden ? ui.refresh_seconds_max * 1000 : RFR());
 const tick = async () => {
  if (stopped || inFlight) return;
  inFlight = true;
  try {
   const rows = await fetchJSON(el.dataset.u1Fetch + (lastRev ? "?since=" + lastRev : ""));
   backoffMs = RFR();
   if (lastRev === 0) applyFull(rows);
   else applyDelta(rows);
   for (const j of rows) prevStatus.set(j.id, j.status);
   if (activeIds.size === 0) stop();
  } catch (e) {
   let nextMs = backoffMs;
   if (isNetErr(e)) {
    /* state 5: sticky bar, data preserved, auto-retry 5s */
    netbar(el);
    nextMs = 5000;
   } else {
    backoffMs = Math.min(backoffMs * 2, ui.refresh_seconds_max * 1000);
    nextMs = backoffMs;
    if (hasData && refreshEl) {
     /* data on screen: reason + retry on the status line */
     refreshEl.innerHTML = esc(fmt(S.jobs_refresh_error, { msg: e.message, wait: Math.round(backoffMs / 1000) })) + RETRY("u3-jobs-retry");
    } else if (!hasData) {
     /* state 4: initial load failed — reason line + "Retry" */
     showError(e);
    }
   }
   schedule(nextMs);
   return;
  } finally {
   inFlight = false;
  }
  schedule(tickInterval());
 };
 jobsListRetry = () => {
  backoffMs = RFR();
  if (refreshEl) refreshEl.textContent = "";
  tick();
 };
 /* full refetch (after purge / after a sheet that mutates the list) */
 jobsRefresh = () => {
  lastRev = 0;
  backoffMs = RFR();
  tick();
 };
 document.addEventListener("visibilitychange", () => {
  if (stopped) return;
  if (document.visibilityState === "visible") tick();
  else schedule(tickInterval());
 });
 tick();
}
/* R2 §6: book list — single fetch (no worker-side changes), same five
   states; re-fetch exposed for upload/delete flows */
let booksRefresh = null;
function startBooksList() {
 const el = $("u1-books");
 if (!el || !el.dataset.u1Fetch) return;
 const body = el.querySelector(":scope > .u3-list-body");
 if (!body) return;
 const q = el.dataset.u1Q || "";
 const url = el.dataset.u1Fetch + (q ? "?q=" + enc(q) : "");
 let timer = null;
 let inFlight = false;
 let hasData = false;
 const stateHtml = (state, line, actionHtml) =>
  "<div class='u3-state' data-state='" + state + "'>" +
  "<span class='u3-state-ico' aria-hidden='true'>&#9675;</span>" +
  "<p class='u3-state-line'>" + esc(line) + "</p>" + actionHtml + "</div>";
 const showEmpty = () => {
  body.setAttribute("data-state", q ? "empty-filter" : "empty");
  body.innerHTML = q
   ? stateHtml("empty-filter", S.books_empty_filtered, "<a class='u3-btn u3-primary' href='/books'>" + esc(S.state_reset_filter) + "</a>")
   : stateHtml("empty", S.books_empty_line, "<button type='button' class='u3-btn u3-primary' data-book-upload>" + esc(S.fab_add_book) + "</button>");
 };
 const showError = (e) => {
  body.setAttribute("data-state", "error");
  body.innerHTML = stateHtml("error", fmt(S.common_err, { msg: e.message }), "<button type='button' class='u3-btn u3-primary' data-u3-books-retry>" + esc(S.common_retry) + "</button>");
 };
 const tick = async () => {
  if (inFlight) return;
  inFlight = true;
  try {
   const books = await fetchJSON(url);
   const old = el.querySelector(":scope > .u3-netbar");
   if (old && old.remove) old.remove();
   hasData = true;
   if (!books.length) showEmpty();
   else {
    body.setAttribute("data-state", "ok");
    body.innerHTML = books.map(bookRowHtml).join("");
   }
  } catch (e) {
   if (isNetErr(e)) {
    /* state 5: sticky bar, data preserved, auto-retry 5s */
    netbar(el);
    timer = setTimeout(tick, 5000);
   } else if (!hasData) {
    /* state 4: initial load failed — reason line + "Retry" */
    showError(e);
   }
  } finally {
   inFlight = false;
  }
 };
 booksRefresh = tick;
 tick();
}
let jobPolling = null;
function startJobPolling() {
 const el = document.querySelector("[data-u1-poll]");
 if (!el) return;
 const jobId = el.dataset.u1Poll;
 let timer = null;
 let stopped = false;
 let inFlight = false;
 let flight = null;
 const stop = () => {
  stopped = true;
  if (timer) {
   clearTimeout(timer);
   timer = null;
  }
 };
 const schedule = (ms) => {
  if (!stopped) timer = setTimeout(tick, ms);
 };
 const tick = async () => {
  if (stopped || inFlight) return;
  if (document.hidden) {
   schedule(RFR());
   return;
  }
  inFlight = true;
  const operation = (async () => {
   const facts = await fetchJSON("/api/jobs/" + encodeURIComponent(jobId));
   el.innerHTML = renderProgress(facts);
   if (facts.status_terminal) stop();
  })();
  flight = operation;
  try {
   await operation;
  } catch (e) {
   /* API error: reason + retry, never fake progress (T-U1-10) */
   stop();
   el.innerHTML +=
    "<p class='err'>" + esc(fmt(S.common_poll_stopped, { msg: e.message })) + RETRY("u3-poll-retry") + "</p>";
  } finally {
   inFlight = false;
   flight = null;
  }
  if (!stopped) schedule(RFR());
 };
 const retry = () => {
  stopped = false;
  tick();
 };
 /* E8: stop polling and await in-flight GET before DELETE (race) */
 const quiesce = async () => {
  stop();
  if (flight) await flight;
 };
 onVis(() => !stopped, tick);
 jobPolling = { quiesce, retry };
 tick();
}
/* containers/feeds */
function containerError(el, e) {
 el.innerHTML =
  "<p class='err'>" + esc(fmt(S.common_err_load, { msg: e.message })) + RETRY("u3-retry") + "</p>";
}
function loadContainer(el) {
 const render = RENDERERS[el.dataset.u1Render];
 if (!render) return;
 let url = filteredApiUrl(el.dataset.u1Fetch);
 if (el.id === "u1-logs") {
  const select = $("u3-logs-lines");
  if (select) url += "?lines=" + enc(select.value);
 }
 fetchJSON(url)
  .then((data) => render(el, data))
  .catch((error) => containerError(el, error));
}
function refreshContainer(id) {
 const el = $(id);
 if (!el || !el.dataset.u1Fetch) return;
 loadContainer(el);
}
function initFetchContainers() {
 for (const el of document.querySelectorAll("[data-u1-fetch]")) {
  /* u1-jobs/u1-books — owned by startJobsList/startBooksList (R2) */
  if (el.id === "u1-jobs" || el.id === "u1-books") continue;
  loadContainer(el);
 }
}
/* logs: line-count options + "more" */
function initLogsControls() {
 const el = $("u1-logs");
 const select = $("u3-logs-lines");
 const more = $("u3-logs-more");
 if (!el || !select || !more) return;
 const base = parseInt(select.value, 10) || ui.log_tail_lines;
 const options = [];
 for (let value = base; value <= 5000; value *= 2) options.push(value);
 select.innerHTML = options
  .map(
   (value) =>
    "<option value='" + value + "'" + (value === base ? " selected" : "") + ">" + value + "</option>"
  )
  .join("");
 const fetchLogs = () => loadContainer(el);
 more.addEventListener("click", () => {
  const current = parseInt(select.value, 10) || base;
  const next = Math.min(current * 2, 5000);
  if (next === current) return;
  select.value = next;
  fetchLogs();
 });
 select.addEventListener("change", fetchLogs);
}
/* forms */
/* 7.1: network-error state: reason + retry button */
function failWithRetry(el, text, retry) {
 const n = $ce("span");
 n.className = "err";
 const b = $ce("button");
 b.type = "button";
 b.textContent = S.common_retry;
 b.addEventListener("click", () => {
  n.remove();
  retry();
 });
 n.textContent = text;
 n.append(" ", b);
 el.innerHTML = "";
 el.appendChild(n);
}
function bindUpload() {
 const form = $("u1-upload");
 if (!form) return;
 const fi = $("u1-upload-file");
 const msg = $("u1-upload-msg");
 const run = async () => {
  const file = fi.files && fi.files[0];
  if (!file) {
   msg.textContent = S.common_select_file;
   return;
  }
  const fd = new FormData();
  fd.append("file", file);
  msg.textContent = S.common_uploading;
  try {
   await fetchJSON("/api/books", { method: "POST", body: fd });
   msg.textContent = S.common_uploaded;
   if (booksRefresh) booksRefresh();
   fi.value = "";
  } catch (e) {
   failWithRetry(msg, apiErr(e), run);
  }
 };
 form.addEventListener("submit", (e) => {
  e.preventDefault();
  run();
 });
}
function bindJobActions() {
 const jobMsg = $("u3-job-msg");
 const cf = $("u1-confirm");
 if (cf) {
  const msg = $("u1-confirm-msg");
  const run = async () => {
   const sourceLanguage = $("u1-confirm-lang").value;
   msg.textContent = S.common_sending;
   try {
    await fetchJSON(
     "/api/jobs/" + enc(cf.dataset.job) + "/confirm",
     {
      method: "POST",
      headers: JH,
      body: JSON.stringify({ source_language: sourceLanguage }),
     }
    );
    window.location.reload();
   } catch (e) {
    failWithRetry(msg, apiErr(e), run);
   }
  };
  cf.addEventListener("submit", (e) => {
   e.preventDefault();
   run();
  });
 }
 for (const [id, action] of [
  ["u1-pause", "pause"],
  ["u1-resume", "resume"],
  ["u1-cancel", "cancel"],
 ]) {
  const btn = $(id);
  if (!btn) continue;
  const run = async () => {
   btn.disabled = true;
   try {
    await fetchJSON(
     "/api/jobs/" + enc(btn.dataset.job) + "/" + action,
     { method: "POST" }
    );
    window.location.reload();
   } catch (e) {
    btn.disabled = false;
    failWithRetry(jobMsg, cmdFail(e), run);
   }
  };
  btn.addEventListener("click", run);
 }
 const clr = $("u3-clear-logs");
 if (clr) {
  const run = async () => {
   const job = clr.dataset.job;
   if (!confirm(fmt(S.job_clear_logs_confirm, { id: job }))) return;
   clr.disabled = true;
   try {
    await fetchJSON(
     "/api/jobs/" + enc(job) + "/logs/clear",
     { method: "POST" }
    );
    jobMsg.textContent = S.jobs_logs_cleared;
    refreshContainer("u1-logs");
   } catch (e) {
    failWithRetry(jobMsg, cmdFail(e), run);
   } finally {
    clr.disabled = false;
   }
  };
  clr.addEventListener("click", run);
 }
 const del = $("u3-delete-job");
 if (del) {
  const run = async () => {
   const job = del.dataset.job;
   if (!confirm(fmt(S.job_delete_confirm, { id: job }))) return;
   del.disabled = true;
   try {
    /* E8: DELETE with no in-flight polls */
    if (jobPolling) await jobPolling.quiesce();
    await fetchJSON("/api/jobs/" + encodeURIComponent(job), { method: "DELETE" });
    window.location.href = "/jobs";
   } catch (e) {
    del.disabled = false;
    failWithRetry(jobMsg, cmdFail(e), run);
   }
  };
  del.addEventListener("click", run);
 }
}
function bindBookDeletes() {
 const container = $("u1-books");
 if (!container) return;
 container.addEventListener("click", (e) => {
  const target = e.target;
  if (!target || !target.getAttribute) return;
  if (target.hasAttribute("data-book-upload")) {
   const fi = $("u1-upload-file");
   if (fi) fi.click();
   return;
  }
  const name = target.getAttribute("data-book-delete");
  if (!name) return;
  const run = async () => {
   if (!confirm(fmt(S.books_delete_confirm, { name }))) return;
   try {
    await fetchJSON("/api/books/" + encodeURIComponent(name), { method: "DELETE" });
    if (booksRefresh) booksRefresh();
   } catch (e) {
    const old = container.querySelector("p.err");
    if (old) old.remove();
    const line = $ce("p");
    line.className = "err";
    line.textContent = cmdFail(e);
    const b = $ce("button");
    b.type = "button";
    b.textContent = S.common_retry;
    b.addEventListener("click", run);
    line.append(" ", b);
    container.prepend(line);
   }
  };
  run();
 });
}
function bindPurge() {
 const form = $("u3-purge-form");
 const dry = $("u3-purge-dry");
 const conf = $("u3-purge-confirm");
 const msg = $("u3-purge-msg");
 if (!form || !dry || !conf || !msg) return;
 let token = null;
 const collect = () => {
  const pairSel = form.querySelector("select[name='pair']");
  return {
   statuses: [...form.querySelectorAll("input[name='status']:checked")].map((i) => i.value),
   book: form.querySelector("select[name='book']").value || null,
   /* R2: the pair select is no longer on the sheet (API param kept) */
   pair: pairSel ? pairSel.value || null : null,
  };
 };
 const runDry = async () => {
  token = null;
  conf.disabled = true;
  msg.textContent = S.common_sending;
  try {
   const result = await fetchJSON("/api/jobs/purge", {
    method: "POST",
    headers: JH,
    body: JSON.stringify(collect()),
   });
   token = result.confirm_token;
   conf.disabled = false;
   /* E2: real list: all job_ids + irreversibility warning */
   const ids = result.job_ids || [];
   msg.innerHTML =
    esc(fmt(S.jobs_purge_result, {
     count: result.count,
     bytes: humanSize(result.total_bytes),
    })) + " <span class='u3-warn'>" + esc(S.jobs_purge_irreversible) + "</span>" + "<details open><summary>" + esc(fmt(S.jobs_purge_ids, { n: ids.length })) + "</summary>" + ids.map((id) => "<code class='u3-mono'>" + esc(id) + "</code>").join("<br>") + "</details>";
  } catch (e) {
   failWithRetry(msg, apiErr(e), runDry);
  }
 };
 dry.addEventListener("click", runDry);
 const runConfirm = async () => {
  if (!token) return;
  conf.disabled = true;
  msg.textContent = S.common_sending;
  try {
   const body = collect();
   body.confirm_token = token;
   const result = await fetchJSON("/api/jobs/purge", {
    method: "POST",
    headers: JH,
    body: JSON.stringify(body),
   });
   msg.textContent = fmt(S.jobs_purge_removed, {
    count: result.count,
    bytes: humanSize(result.total_bytes),
   });
   token = null;
   /* R2: the jobs list is owned by startJobsList — full refetch */
   if (jobsRefresh) jobsRefresh();
  } catch (e) {
   token = null;
   failWithRetry(msg, apiErr(e), runConfirm);
  }
 };
 conf.addEventListener("click", runConfirm);
}
function bindPrompts() {
 const form = $("u3-prompt-form");
 const msg = $("u3-prompt-msg");
 if (!form || !msg) return;
 const fe = form.elements;
 const titleEl = $("u3-prompt-form-title");
 const sub = $("u3-prompt-submit");
 const rst = $("u3-prompt-reset");
 const dialog = $("u3-prompt-view");
 const dtit = $("u3-prompt-view-title");
 const dcon = $("u3-prompt-view-content");
 const resetForm = () => {
  form.reset();
  titleEl.textContent = S.prompts_create_title;
  sub.textContent = S.prompts_create_btn;
  msg.textContent = "";
 };
 const viewPrompt = async (path) => {
  try {
   const data = await fetchJSON(
    "/api/prompts/content?path=" + enc(path)
   );
   dtit.textContent = fmt(S.prompts_content_title, { file: data.path });
   dcon.textContent = data.content;
   if (dialog.showModal) dialog.showModal();
  } catch (e) {
   failWithRetry(msg, apiErr(e), () => viewPrompt(path));
  }
 };
 const startEdit = async (b) => {
  try {
   const path = b.getAttribute("data-prompt-edit");
   const data = await fetchJSON(
    "/api/prompts/content?path=" + enc(path)
   );
   fe.task.value = b.getAttribute("data-task") || "";
   fe.level.value = b.getAttribute("data-level") || "";
   fe.source.value = b.getAttribute("data-source") || "";
   fe.target.value = b.getAttribute("data-target") || "";
   fe.content.value = data.content;
   const version = parseInt(b.getAttribute("data-version"), 10) || 0;
   if (titleEl) titleEl.textContent = fmt(S.prompts_edit_title, { file: path });
   sub.textContent = fmt(S.prompts_save_btn, { next: "v" + (version + 1) + ".yaml" });
  } catch (e) {
   failWithRetry(msg, apiErr(e), () => startEdit(b));
  }
 };
 const submitRun = async () => {
  msg.textContent = S.common_sending;
  const data = {
   task: fe.task.value,
   level: fe.level.value,
   source: fe.source.value,
   target: fe.target.value,
   content: fe.content.value,
  };
  try {
   const result = await fetchJSON("/api/prompts", {
    method: "POST",
    headers: JH,
    body: JSON.stringify(data),
   });
   msg.textContent = fmt(S.prompts_created, { file: result.path });
   setTimeout(() => window.location.reload(), 1000);
  } catch (e) {
   failWithRetry(msg, apiErr(e), submitRun);
  }
 };
 form.addEventListener("submit", (e) => {
  e.preventDefault();
  submitRun();
 });
 if (rst) rst.addEventListener("click", resetForm);
 document.addEventListener("click", (e) => {
  const target = e.target;
  if (!target || !target.getAttribute) return;
  const viewPath = target.getAttribute("data-prompt-view");
  if (viewPath) {
   viewPrompt(viewPath);
   return;
  }
  if (target.getAttribute("data-prompt-edit")) {
   startEdit(target);
   return;
  }
  const deletePath = target.getAttribute("data-prompt-delete");
  if (!deletePath) return;
  const runDelete = async () => {
   if (!confirm(fmt(S.prompts_delete_confirm, { file: deletePath }))) return;
   try {
    await fetchJSON("/api/prompts/version?path=" + enc(deletePath), {
     method: "DELETE",
    });
    window.location.reload();
   } catch (e) {
    failWithRetry(msg, cmdFail(e), runDelete);
   }
  };
  runDelete();
 });
}
/* R2 §14: bottom sheets (<dialog>) — open 180 ms, close on backdrop/
   Esc/back-button; pushState keeps the back button in contract.
   No-JS: the same form is a server page (?sheet=), not a dialog. */
let openSheetEl = null;
function initSheets() {
 document.addEventListener("click", (e) => {
  const target = e.target;
  if (!target || !target.closest) return;
  const a = target.closest("[data-sheet-open]");
  if (!a) return;
  e.preventDefault();
  const d = $(a.dataset.sheetOpen);
  if (!d || !d.showModal || d.open) return;
  d.showModal();
  openSheetEl = d;
  const det = a.closest("details");
  if (det) det.removeAttribute("open");
  history.pushState({ u3sheet: d.id }, "");
 });
 for (const d of document.querySelectorAll(".u3-sheet")) {
  d.addEventListener("click", (e) => {
   /* backdrop click: the native close event rolls the state back */
   if (e.target === d && d.open) d.close();
  });
  d.addEventListener("close", () => {
   if (openSheetEl === d) {
    openSheetEl = null;
    history.back();
   }
  });
 }
 window.addEventListener("popstate", () => {
  if (!openSheetEl) return;
  const d = openSheetEl;
  openSheetEl = null;
  if (d.open) d.close();
 });
}
/* bootstrap */
/* 7.1: retry buttons restart the source */
document.addEventListener("click", (e) => {
 const target = e.target;
 if (!target || !target.hasAttribute) return;
 if (target.hasAttribute("data-u3-poll-retry") && jobPolling) {
  jobPolling.retry();
 } else if (target.hasAttribute("data-u3-jobs-retry") && jobsListRetry) {
  jobsListRetry();
 } else if (target.hasAttribute("data-u3-books-retry") && booksRefresh) {
  booksRefresh();
 } else if (target.hasAttribute("data-u3-retry")) {
  const container = target.closest("[data-u1-fetch]");
  if (container) loadContainer(container);
 }
});
document.addEventListener("DOMContentLoaded", () => {
 initLogsControls();
 initFetchContainers();
 startJobPolling();
 startJobsList();
 startBooksList();
 initSheets();
 bindUpload();
 bindJobActions();
 bindBookDeletes();
 bindPurge();
 bindPrompts();
});