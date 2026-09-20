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
/* server-side pagination (3.4.16) */
function jobsFetchUrl(baseUrl) {
 const params = new URLSearchParams(window.location.search || "");
 const pageRaw = params.get("page");
 params.delete("page");
 const page = Math.max(1, parseInt(pageRaw, 10) || 1);
 params.set("limit", ui.page_size);
 params.set("offset", (page - 1) * ui.page_size);
 return baseUrl + "?" + params.toString();
}
/* "filtered" empty message when filtering */
function jobsFiltered() {
 const params = new URLSearchParams(window.location.search || "");
 return ["status", "book", "pair", "created_from", "created_to"].some((k) => params.has(k));
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
/* [[label, html, cls?]] per row: one source for table + cards */
function fieldsRows(fieldsList) {
 return fieldsList
  .map(
   (f) =>
    "<tr>" + f.map((c) => "<td" + (c[2] ? " class='" + c[2] + "'" : "") + ">" + c[1] + "</td>").join("") + "</tr>"
  )
  .join("");
}
const statusClass = (status) => "status-" + String(status).toLowerCase();
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
const JOBS_COLS = [
 S.jobs_col_id,
 S.jobs_col_book,
 S.jobs_col_pair,
 S.jobs_col_mode,
 S.jobs_col_status,
 S.jobs_col_progress,
 S.jobs_col_alignment,
 S.jobs_col_created,
 S.jobs_col_updated,
];
function jobCells(j) {
 return [
  ["<a href='/jobs/" + esc(j.id) + "'>" + esc(j.id) + "</a>"],
  [esc(j.book_name)],
  [jobPair(j)],
  [jobMode(j)],
  [esc(j.status), statusClass(j.status)],
  [esc(j.progress_display)],
  [esc(alignmentValue(j.alignment_coverage))],
  [esc(j.created_at_human)],
  [esc(j.updated_at_human)],
 ];
}
const BOOKS_COLS = [
 S.books_col_file,
 S.books_col_title,
 S.books_col_dc_lang,
 S.books_col_detected,
 S.books_col_confidence,
 S.books_col_size,
 S.books_col_chapters,
 S.books_col_segments,
 S.books_col_uploaded,
];
function bookCells(b) {
 return [
  "<span class='u3-mono'>" + esc(b.name) + "</span>",
  esc(b.title || NS),
  esc(b.dc_language || NS),
  esc(b.detected || NS),
  esc(b.confidence == null ? NS : b.confidence),
  esc(b.size_human || NS),
  esc(b.chapters == null ? NS : b.chapters),
  esc(b.segments == null ? NS : b.segments),
  esc(b.uploaded_at_human || NS),
 ];
}
const bookDeleteButton = (b) =>
 "<button type='button' class='btn u3-danger u3-row-action' data-book-delete='" + esc(b.name) + "'>" + esc(S.books_delete_btn) + "</button>";
/* renderers */
const RENDERERS = {
 jobs(el, rows) {
  if (!rows.length) {
   if (!jobsFiltered() && el.dataset.u1Onboarding) {
    /* 7.4: onboarding on first run */
    el.innerHTML =
     "<div class='u3-panel'><h2>" + esc(S.onb_title) + "</h2><ol>" + "<li>" + esc(S.onb_step1) + " — <a class='btn' href='/books'>" + esc(S.onb_go_books) + "</a></li>" + "<li>" + esc(S.onb_step2) + "</li>" + "<li>" + esc(S.onb_step3) + "</li></ol></div>";
   } else {
    el.innerHTML =
     "<p class='u3-empty'>" + esc(jobsFiltered() ? S.jobs_empty_filtered : S.jobs_empty) + (jobsFiltered()
      ? " <a class='btn' href='/jobs'>" + esc(S.jobs_filter_reset) + "</a>"
      : "") + "</p>";
   }
   return;
  }
  const cells = rows.map(jobCells);
  el.innerHTML =
   tableHtml(JOBS_COLS, fieldsRows(cells)) + cardsHtml(cells.map((c) => cardHtml(c.map((x, i) => [JOBS_COLS[i], x[0], x[1]]))));
 },
 books(el, books) {
  if (!books.length) {
   /* 7.1: empty state with upload action */
   el.innerHTML =
    "<p class='u3-empty'>" + esc(S.books_empty) + " <button type='button' class='btn' data-book-upload>" + esc(S.books_upload_btn) + "</button></p>";
   return;
  }
  const rows = books
   .map((b, i) => {
    const cells = bookCells(b).slice();
    cells[0] = cells[0] + bookDeleteButton(b);
    return "<tr>" + cells.map((html) => "<td>" + html + "</td>").join("") + "</tr>";
   })
   .join("");
  const cards = books.map((b) =>
   cardHtml(bookCells(b).map((html, i) => [BOOKS_COLS[i], html]), bookDeleteButton(b))
  );
  el.innerHTML = tableHtml(BOOKS_COLS, rows) + cardsHtml(cards);
 },
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
   "<span class='" + statusClass(f.status) + "'>" + esc(f.status) + (f.error ? " — <span class='err'>" + esc(f.error) + "</span>" : "") + "</span>",
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
function startJobsAutoRefresh() {
 const el = $("u1-jobs");
 if (!el || !el.dataset.u1Fetch) return;
 const refreshEl = $("u3-jobs-refresh");
 const announceEl = $("u3-jobs-announce");
 let timer = null;
 let stopped = false;
 let inFlight = false;
 let backoffMs = RFR();
 const prevAnn = new Map();
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
 const tick = async () => {
  if (stopped || inFlight) return;
  if (document.hidden) {
   /* paused while hidden (3.11.48) */
   schedule(RFR());
   return;
  }
  inFlight = true;
  try {
   const rows = await fetchJSON(jobsFetchUrl(el.dataset.u1Fetch));
   backoffMs = RFR();
   RENDERERS.jobs(el, rows);
   refreshEl.textContent = fmt(S.jobs_refresh_last, { time: nowTime() });
   for (const j of rows) {
    const prev = prevAnn.get(j.id);
    if (prev !== undefined && prev !== j.status) {
     announceEl.textContent = fmt(S.jobs_announce_status, {
      id: j.id,
      status: j.status,
      time: nowTime(),
     });
    }
    prevAnn.set(j.id, j.status);
   }
   if (!rows.some((j) => !INACTIVE_JOB_STATUSES.has(j.status))) {
    stop();
   }
  } catch (e) {
   /* error + retry + backoff to ui.refresh_seconds_max */
   backoffMs = Math.min(backoffMs * 2, ui.refresh_seconds_max * 1000);
   const msg = esc(fmt(S.jobs_refresh_error, { msg: e.message, wait: Math.round(backoffMs / 1000) }));
   refreshEl.textContent = msg;
   refreshEl.innerHTML = msg + RETRY("u3-jobs-retry");
  } finally {
   inFlight = false;
  }
  schedule(backoffMs);
 };
 jobsListRetry = () => {
  backoffMs = RFR();
  tick();
 };
 onVis(() => !stopped, tick);
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
  if (el.id === "u1-jobs") continue; /* owned by auto-refresh */
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
   refreshContainer("u1-books");
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
function bindCreate() {
 const form = $("u1-create");
 if (!form) return;
 const msg = $("u1-create-msg");
 const run = async () => {
  const data = Object.fromEntries(new FormData(form));
  if (!data.learning_level) delete data.learning_level;
  if (!data.learning_cefr) delete data.learning_cefr;
  delete data.u3_csrf;
  msg.textContent = S.common_creating;
  try {
   const result = await fetchJSON("/api/jobs", {
    method: "POST",
    headers: JH,
    body: JSON.stringify(data),
   });
   window.location.href = "/jobs/" + result.id;
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
    refreshContainer("u1-books");
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
 const collect = () => ({
  statuses: [...form.querySelectorAll("input[name='status']:checked")].map((i) => i.value),
  book: form.querySelector("select[name='book']").value || null,
  pair: form.querySelector("select[name='pair']").value || null,
 });
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
   refreshContainer("u1-jobs");
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
/* bootstrap */
/* 7.1: retry buttons restart the source */
document.addEventListener("click", (e) => {
 const target = e.target;
 if (!target || !target.hasAttribute) return;
 if (target.hasAttribute("data-u3-poll-retry") && jobPolling) {
  jobPolling.retry();
 } else if (target.hasAttribute("data-u3-jobs-retry") && jobsListRetry) {
  jobsListRetry();
 } else if (target.hasAttribute("data-u3-retry")) {
  const container = target.closest("[data-u1-fetch]");
  if (container) loadContainer(container);
 }
});
document.addEventListener("DOMContentLoaded", () => {
 initLogsControls();
 initFetchContainers();
 startJobPolling();
 startJobsAutoRefresh();
 bindUpload();
 bindCreate();
 bindJobActions();
 bindBookDeletes();
 bindPurge();
 bindPrompts();
});