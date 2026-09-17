/* U3 UI script (no frameworks, no build step, local assets only).
 *
 * Interface strings: U3_STRINGS, embedded into every page as JSON from
 * app/web/strings.py (criterion U3-1) -- this file contains no
 * user-visible text literals. Status values, language codes, task/file
 * names and rejection reasons are rendered verbatim (3.1.3).
 *
 * Contracts:
 *   [data-u1-fetch] + [data-u1-render] -- the page consumes its own
 *   endpoint (every /api endpoint has a UI consumer, U1-6);
 *   [data-u1-poll] -- job progress polling (U1-1);
 *   auto-refresh (3.11): interval ui.refresh_seconds, stop on terminal
 *   status, pause while document.hidden (one immediate poll on return),
 *   exponential backoff up to ui.refresh_max_seconds on API error with a
 *   visible message, last-success timestamp, status change announced once
 *   (aria-live).
 * Mutating requests (POST/DELETE) carry the X-U3-CSRF header (3.9.41).
 */
"use strict";

/* ---------------------------------------------------------------- base */

const S = JSON.parse(document.getElementById("u3-strings").textContent);
const ui = JSON.parse(document.getElementById("u3-ui").textContent);

function csrfToken() {
  const meta = document.querySelector('meta[name="u3-csrf"]');
  return meta ? meta.getAttribute("content") || "" : "";
}

function esc(value) {
  return String(value == null ? S.common_not_set : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmt(template, values) {
  return String(template).replace(/\{(\w+)\}/g, (match, key) =>
    key in values ? String(values[key]) : match
  );
}

function humanSize(numBytes) {
  if (numBytes == null) return S.common_not_set;
  const units = [
    [1024 * 1024 * 1024, S.common_size_gb],
    [1024 * 1024, S.common_size_mb],
    [1024, S.common_size_kb],
  ];
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
  } catch {
    /* non-JSON response body */
  }
  if (!response.ok) {
    const detail = (data && data.detail) || text || String(response.status);
    throw new Error(detail);
  }
  return data;
}

/* The current page filter (GET form on /jobs) is applied to the API fetch
 * as well, so auto-refresh does not drop the active filter. `page` is a
 * UI-only parameter and is not forwarded. */
function filteredApiUrl(url) {
  if (!url || url.indexOf("?") !== -1) return url;
  const params = new URLSearchParams(window.location.search || "");
  if (!params.size) return url;
  params.delete("page");
  const query = params.toString();
  return query ? url + "?" + query : url;
}

/* The jobs list is paginated server-side (U3 3.4.16): the current `page`
 * from the URL is translated to limit/offset (ui.page_size), so the
 * auto-refresh renders exactly the server-rendered slice of the list. */
function jobsFetchUrl(baseUrl) {
  const params = new URLSearchParams(window.location.search || "");
  const pageRaw = params.get("page");
  params.delete("page");
  const page = Math.max(1, parseInt(pageRaw, 10) || 1);
  params.set("limit", String(ui.page_size));
  params.set("offset", String((page - 1) * ui.page_size));
  return baseUrl + "?" + params.toString();
}

/* The jobs list shows the "filtered" empty message when a real filter
 * (status / book / pair / created range) is active in the URL. */
function jobsFiltered() {
  const params = new URLSearchParams(window.location.search || "");
  return ["status", "book", "pair", "created_from", "created_to"].some((key) =>
    params.has(key)
  );
}

/* ------------------------------------------------------ dual rendering */

function cardHtml(fields, actionsHtml) {
  const dl = fields
    .map(
      (field) =>
        "<div class='u3-field'><dt>" + esc(field[0]) + "</dt>" +
        "<dd" + (field[2] ? " class='" + field[2] + "'" : "") + ">" +
        field[1] + "</dd></div>"
    )
    .join("");
  const actions = actionsHtml
    ? "<div class='u3-card-actions'>" + actionsHtml + "</div>"
    : "";
  return (
    "<article class='u3-card'><dl class='u3-card-fields'>" + dl + "</dl>" +
    actions + "</article>"
  );
}

function tableHtml(columns, rowsHtml) {
  return (
    "<table class='u3-table'><thead><tr>" +
    columns.map((c) => "<th scope='col'>" + esc(c) + "</th>").join("") +
    "</tr></thead><tbody>" + rowsHtml + "</tbody></table>"
  );
}

function cardsHtml(cards) {
  return "<div class='u3-cards'>" + cards.join("") + "</div>";
}

const statusClass = (status) => "status-" + String(status).toLowerCase();

function jobMode(j) {
  return (
    esc(j.mode) +
    (j.learning_level
      ? " / " + esc(j.learning_level) +
        (j.learning_cefr ? " (" + esc(j.learning_cefr) + ")" : "")
      : "")
  );
}

function jobPair(j) {
  return esc(j.source_language || S.common_not_set) + " → " + esc(j.target_language);
}

function alignmentValue(coverage) {
  return coverage != null ? (coverage * 100).toFixed(1) + "%" : S.common_not_set;
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
    { html: "<a href='/jobs/" + esc(j.id) + "'>" + esc(j.id) + "</a>" },
    { html: esc(j.book_name) },
    { html: jobPair(j) },
    { html: jobMode(j) },
    { html: esc(j.status), cls: statusClass(j.status) },
    { html: esc(j.progress_display) },
    { html: esc(alignmentValue(j.alignment_coverage)) },
    { html: esc(j.created_at_human) },
    { html: esc(j.updated_at_human) },
  ];
}

function jobTableRow(j) {
  return (
    "<tr>" +
    jobCells(j)
      .map(
        (cell) =>
          "<td" + (cell.cls ? " class='" + cell.cls + "'" : "") + ">" +
          cell.html + "</td>"
      )
      .join("") +
    "</tr>"
  );
}

function jobCard(j) {
  return cardHtml(
    jobCells(j).map((cell, index) => [JOBS_COLS[index], cell.html, cell.cls])
  );
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
  const notSet = S.common_not_set;
  return [
    "<span class='u3-mono'>" + esc(b.name) + "</span>",
    esc(b.title || notSet),
    esc(b.dc_language || notSet),
    esc(b.detected || notSet),
    esc(b.confidence == null ? notSet : b.confidence),
    esc(b.size_human || notSet),
    esc(b.chapters == null ? notSet : b.chapters),
    esc(b.segments == null ? notSet : b.segments),
    esc(b.uploaded_at_human || notSet),
  ];
}

const bookDeleteButton = (b) =>
  "<button type='button' class='btn u3-danger u3-row-action' data-book-delete='" +
  esc(b.name) +
  "'>" + esc(S.books_delete_btn) + "</button>";

/* ------------------------------------------------------------- renderers */

const RENDERERS = {
  jobs(el, rows) {
    if (!rows.length) {
      el.innerHTML =
        "<p class='u3-empty'>" +
        esc(jobsFiltered() ? S.jobs_empty_filtered : S.jobs_empty) + "</p>";
      return;
    }
    el.innerHTML =
      tableHtml(JOBS_COLS, rows.map(jobTableRow).join("")) +
      cardsHtml(rows.map(jobCard));
  },

  books(el, books) {
    if (!books.length) {
      el.innerHTML = "<p class='u3-empty'>" + esc(S.books_empty) + "</p>";
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
    const langCols = [
      S.languages_col_code,
      S.languages_col_name,
      S.languages_col_spacy,
      S.languages_col_cefr,
    ];
    const notSet = S.common_not_set;
    const langRows = data.languages
      .map(
        (l) =>
          "<tr><td class='u3-mono'>" + esc(l.code) + "</td><td>" + esc(l.name) +
          "</td><td class='u3-mono'>" + esc(l.spacy_model || notSet) +
          "</td><td>" + esc(l.cefr_label || notSet) + "</td></tr>"
      )
      .join("");
    const langCards = data.languages.map((l) =>
      cardHtml([
        [S.languages_col_code, "<span class='u3-mono'>" + esc(l.code) + "</span>"],
        [S.languages_col_name, esc(l.name)],
        [S.languages_col_spacy, "<span class='u3-mono'>" + esc(l.spacy_model || notSet) + "</span>"],
        [S.languages_col_cefr, esc(l.cefr_label || notSet)],
      ])
    );
    const pairCols = [
      S.pairs_col_source,
      S.pairs_col_target,
      S.pairs_col_status,
      S.pairs_col_creatable,
      S.pairs_col_missing,
    ];
    const pairRows = data.pairs
      .map(
        (p) =>
          "<tr><td class='u3-mono'>" + esc(p.source) + "</td><td class='u3-mono'>" +
          esc(p.target) + "</td><td>" + esc(p.status) + "</td><td>" +
          (p.creatable ? esc(S.pairs_creatable_yes) : esc(S.pairs_creatable_no)) +
          "</td><td class='u3-mono'>" + esc((p.missing || []).join("; ") || notSet) +
          "</td></tr>"
      )
      .join("");
    const pairCards = data.pairs.map((p) =>
      cardHtml([
        [S.pairs_col_source, "<span class='u3-mono'>" + esc(p.source) + "</span>"],
        [S.pairs_col_target, "<span class='u3-mono'>" + esc(p.target) + "</span>"],
        [S.pairs_col_status, esc(p.status)],
        [S.pairs_col_creatable, p.creatable ? esc(S.pairs_creatable_yes) : esc(S.pairs_creatable_no)],
        [S.pairs_col_missing, "<span class='u3-mono'>" + esc((p.missing || []).join("; ") || notSet) + "</span>"],
      ])
    );
    el.innerHTML =
      "<h2>" + esc(S.languages_h2) + "</h2>" +
      tableHtml(langCols, langRows) + cardsHtml(langCards) +
      "<h2>" + esc(S.pairs_h2) + "</h2>" +
      tableHtml(pairCols, pairRows) + cardsHtml(pairCards);
  },

  models(el, models) {
    const cols = [
      S.models_col_profile,
      S.models_col_endpoint,
      S.models_col_model,
      S.models_col_key,
      S.models_col_timeout,
      S.models_col_retry,
      S.models_col_concurrency,
      S.models_col_cost,
    ];
    const rows = models
      .map(
        (m) =>
          "<tr><td class='u3-mono'>" + esc(m.name) + "</td><td class='u3-mono'>" +
          esc(m.base_url) + "</td><td class='u3-mono'>" + esc(m.model) +
          "</td><td>" + (m.api_key_set ? esc(S.models_key_set) : esc(S.models_key_unset)) +
          "</td><td>" + esc(m.timeout_seconds) + "</td><td>" + esc(m.retry_times) +
          "</td><td>" + esc(m.concurrency) + "</td><td>" + esc(m.cost) + "</td></tr>"
      )
      .join("");
    const cards = models.map((m) =>
      cardHtml([
        [S.models_col_profile, "<span class='u3-mono'>" + esc(m.name) + "</span>"],
        [S.models_col_endpoint, "<span class='u3-mono'>" + esc(m.base_url) + "</span>"],
        [S.models_col_model, "<span class='u3-mono'>" + esc(m.model) + "</span>"],
        [S.models_col_key, m.api_key_set ? esc(S.models_key_set) : esc(S.models_key_unset)],
        [S.models_col_timeout, esc(m.timeout_seconds)],
        [S.models_col_retry, esc(m.retry_times)],
        [S.models_col_concurrency, esc(m.concurrency)],
        [S.models_col_cost, esc(m.cost)],
      ])
    );
    el.innerHTML = tableHtml(cols, rows) + cardsHtml(cards);
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
      "<table class='u3-static'><thead><tr><th scope='col'>" +
      esc(S.settings_health_col_component) + "</th><th scope='col'>" +
      esc(S.settings_health_col_status) + "</th></tr></thead><tbody>";
    for (const [name, status] of entries) {
      const text = String(status);
      const cls = text.startsWith("ok") ? "ok" : text.startsWith("error") ? "err" : "";
      html +=
        "<tr><td>" + esc(name) + "</td><td" +
        (cls ? " class='" + cls + "'" : "") + ">" + esc(status) + "</td></tr>";
    }
    html += "</tbody></table>";
    const detail = data.detail;
    if (detail && Object.keys(detail).length) {
      html += "<pre>" + esc(JSON.stringify(detail, null, 2)) + "</pre>";
    }
    el.innerHTML = html;
  },

  config(el, data) {
    el.innerHTML = "<pre>" + esc(JSON.stringify(data, null, 2)) + "</pre>";
  },

  quality(el, data) {
    let html = "";
    if (data.qc && Object.keys(data.qc).length) {
      html +=
        "<h3>" + esc(S.qc_title) + "</h3><pre>" +
        esc(JSON.stringify(data.qc, null, 2)) + "</pre>";
    }
    const a = data.alignment || {};
    html +=
      "<h3>" + esc(S.qc_alignment_title) + "</h3><p>" +
      (a.coverage != null
        ? esc(fmt(S.qc_alignment_coverage, {
            pct: (a.coverage * 100).toFixed(1),
            aligned: a.aligned,
            total: a.total,
          }))
        : esc(S.qc_alignment_na)) +
      "</p>";
    const g = data.glossary_consistency;
    html +=
      "<h3>" + esc(S.qc_glossary_title) + "</h3><p>" +
      (g && typeof g === "object" && g.consistent_share != null
        ? esc(fmt(S.qc_glossary_value, {
            share: (g.consistent_share * 100).toFixed(1),
          }))
        : esc(S.qc_glossary_na)) +
      "</p>";
    if (Array.isArray(data.warnings) && data.warnings.length) {
      html +=
        "<h3>" + esc(S.qc_warnings) + "</h3><ul>" +
        data.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>";
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
          "<table class='u3-static'><thead><tr>" +
          cols.map((c) => "<th scope='col'>" + esc(c) + "</th>").join("") +
          "</tr></thead><tbody>";
        for (const f of l3.fixes) {
          html +=
            "<tr><td>" + esc(f.segment_id) + "</td><td>" + esc(f.doc) +
            "</td><td>" + esc(f.term) + "</td><td>" +
            (f.applied ? esc(S.common_yes) : esc(S.common_no)) +
            "</td><td>" + esc(f.old_text) + "</td><td>" +
            esc(f.new_text) + "</td><td>" + esc(f.reason || S.common_not_set) + "</td></tr>";
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
          "<h3>" + name + "</h3><pre>" +
          (Array.isArray(lines) && lines.length
            ? esc(lines.join("\n"))
            : esc(S.job_logs_empty)) +
          "</pre>"
      )
      .join("");
  },
};

/* ------------------------------------------------------- job page facts */

function renderProgress(f) {
  const notSet = S.common_not_set;
  const seg = f.segments
    ? f.segments.processed + " / " + f.segments.total
    : notSet;
  const align = f.alignment.coverage != null
    ? (f.alignment.coverage * 100).toFixed(1) + "% (" +
      f.alignment.aligned + " / " + f.alignment.total + ")"
    : notSet;
  const tokens = Object.entries(f.tokens)
    .filter(([name]) => name !== "total")
    .map(([name, t]) => esc(name) + ": " + esc(t.total))
    .join(" · ");
  const timeCell =
    fmt(S.job_time_elapsed, { n: f.elapsed_seconds }) +
    (f.eta_seconds != null
      ? " · " + fmt(S.job_time_eta, { n: f.eta_seconds })
      : "");
  const rows = [
    [
      S.job_row_status,
      "<span class='" + statusClass(f.status) + "'>" + esc(f.status) +
        (f.error ? " — <span class='err'>" + esc(f.error) + "</span>" : "") +
        "</span>",
    ],
    [S.job_row_pair, jobPair(f)],
    [S.job_row_mode, jobMode(f)],
    [S.job_row_progress, esc(f.progress_display)],
    [
      S.job_row_phase,
      (f.phase ? esc(f.phase) : notSet) +
        " (" + esc(f.phases.completed) + " / " + esc(f.phases.total) + ")",
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
      fmt(S.job_errors_segments, { n: f.failed_segments }) + " · " +
        fmt(S.job_errors_retries, { n: f.retries }),
    ],
    [S.job_row_created, esc(f.created_at_human)],
    [S.job_row_updated, esc(f.updated_at_human)],
  ];
  let html = "<table class='u3-facts'>";
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
          "<tr><td>" + esc(st.at_human) + "</td><td>" + esc(st.from) +
          " → " + esc(st.to) + "</td><td>" +
          esc(st.reason || notSet) + "</td></tr>"
      )
      .join("");
    const cards = f.stages.map((st) =>
      cardHtml([
        [S.job_stages_col_time, esc(st.at_human)],
        [S.job_stages_col_transition, esc(st.from) + " → " + esc(st.to)],
        [S.job_stages_col_reason, esc(st.reason || notSet)],
      ])
    );
    html +=
      "<details id='u1-stages-details'><summary>" +
      esc(fmt(S.job_stages, { n: f.stages.length })) + "</summary>" +
      tableHtml(cols, tableRows) + cardsHtml(cards) +
      "</details>";
  }
  if (f.warnings && f.warnings.length) {
    html +=
      "<p>" + esc(S.job_warnings) + "</p><ul>" +
      f.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>";
  }
  return html;
}

/* ------------------------------------------------------- auto-refresh (3.11) */

/* Statuses that need no further polling: terminal + PAUSED (waits for the
 * user's "resume", no progress in between). INTERRUPTED is NOT here: the
 * worker may reclaim an interrupted job without any user action, so the
 * list keeps polling it (U1 behavior preserved, 3.11.47). */
const INACTIVE_JOB_STATUSES = new Set([
  "COMPLETED",
  "COMPLETED_WITH_WARNINGS",
  "FAILED",
  "CANCELLED",
  "PAUSED",
]);

function startJobsAutoRefresh() {
  const el = document.getElementById("u1-jobs");
  if (!el || !el.dataset.u1Fetch) return;
  const refreshEl = document.getElementById("u3-jobs-refresh");
  const announceEl = document.getElementById("u3-jobs-announce");
  const baseDelayMs = () => ui.refresh_seconds * 1000;
  let timer = null;
  let stopped = false;
  let inFlight = false;
  let backoffMs = baseDelayMs();
  const lastAnnounced = new Map();

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
      /* paused while hidden: no requests (3.11.48) */
      schedule(baseDelayMs());
      return;
    }
    inFlight = true;
    try {
      const rows = await fetchJSON(jobsFetchUrl(el.dataset.u1Fetch));
      backoffMs = baseDelayMs();
      RENDERERS.jobs(el, rows);
      if (refreshEl) {
        refreshEl.textContent = fmt(S.jobs_refresh_last, {
          time: new Date().toLocaleTimeString(),
        });
      }
      for (const j of rows) {
        const previous = lastAnnounced.get(j.id);
        if (announceEl && previous !== undefined && previous !== j.status) {
          announceEl.textContent = fmt(S.jobs_announce_status, {
            id: j.id,
            status: j.status,
            time: new Date().toLocaleTimeString(),
          });
        }
        lastAnnounced.set(j.id, j.status);
      }
      if (!rows.some((j) => !INACTIVE_JOB_STATUSES.has(j.status))) {
        stop();
      }
    } catch (error) {
      /* visible error + exponential backoff up to ui.refresh_max_seconds */
      backoffMs = Math.min(backoffMs * 2, ui.refresh_max_seconds * 1000);
      if (refreshEl) {
        refreshEl.textContent = fmt(S.jobs_refresh_error, {
          msg: error.message,
          wait: Math.round(backoffMs / 1000),
        });
      }
    } finally {
      inFlight = false;
    }
    schedule(backoffMs);
  };

  document.addEventListener("visibilitychange", () => {
    if (!stopped && document.visibilityState === "visible") {
      tick();
    }
  });
  tick();
}

function startJobPolling() {
  const el = document.querySelector("[data-u1-poll]");
  if (!el) return;
  const jobId = el.getAttribute("data-u1-poll");
  const delayMs = () => ui.refresh_seconds * 1000;
  let timer = null;
  let stopped = false;
  let inFlight = false;
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
      schedule(delayMs());
      return;
    }
    inFlight = true;
    try {
      const facts = await fetchJSON("/api/jobs/" + encodeURIComponent(jobId));
      el.innerHTML = renderProgress(facts);
      if (facts.status_terminal) stop();
    } catch (error) {
      /* API error: show the reason, do not fake live progress (T-U1-10) */
      stop();
      el.innerHTML +=
        "<p class='err'>" +
        esc(fmt(S.common_poll_stopped, { msg: error.message })) + "</p>";
    } finally {
      inFlight = false;
    }
    if (!stopped) schedule(delayMs());
  };
  document.addEventListener("visibilitychange", () => {
    if (!stopped && document.visibilityState === "visible") {
      tick();
    }
  });
  tick();
}

/* ------------------------------------------------------- containers/feeds */

function refreshContainer(id) {
  const el = document.getElementById(id);
  if (!el || !el.dataset.u1Fetch) return;
  const render = RENDERERS[el.dataset.u1Render];
  if (!render) return;
  let url = filteredApiUrl(el.dataset.u1Fetch);
  if (id === "u1-logs") {
    const select = document.getElementById("u3-logs-lines");
    if (select) url += "?lines=" + encodeURIComponent(select.value);
  }
  fetchJSON(url)
    .then((data) => render(el, data))
    .catch((error) => {
      el.textContent = fmt(S.common_err_load, { msg: error.message });
    });
}

function initFetchContainers() {
  for (const el of document.querySelectorAll("[data-u1-fetch]")) {
    if (el.id === "u1-jobs") continue; /* owned by startJobsAutoRefresh */
    const render = RENDERERS[el.dataset.u1Render];
    if (!render) continue;
    let url = filteredApiUrl(el.dataset.u1Fetch);
    if (el.id === "u1-logs") {
      const select = document.getElementById("u3-logs-lines");
      if (select) url += "?lines=" + encodeURIComponent(select.value);
    }
    fetchJSON(url)
      .then((data) => render(el, data))
      .catch((error) => {
        el.textContent = fmt(S.common_err_load, { msg: error.message });
      });
  }
}

/* Logs: line-count options (default from ui.log_tail_lines) + "more". */
function initLogsControls() {
  const el = document.getElementById("u1-logs");
  const select = document.getElementById("u3-logs-lines");
  const more = document.getElementById("u3-logs-more");
  if (!el || !select || !more) return;
  const base = parseInt(select.value, 10) || ui.log_tail_lines;
  const options = [];
  for (let value = base; value <= 5000; value *= 2) options.push(value);
  select.innerHTML = options
    .map(
      (value) =>
        "<option value='" + value + "'" +
        (value === base ? " selected" : "") + ">" + value + "</option>"
    )
    .join("");
  const fetchLogs = () => {
    const lines = parseInt(select.value, 10) || base;
    fetchJSON(el.dataset.u1Fetch + "?lines=" + lines)
      .then((data) => RENDERERS.logs(el, data))
      .catch((error) => {
        el.textContent = fmt(S.common_err_load, { msg: error.message });
      });
  };
  more.addEventListener("click", () => {
    const current = parseInt(select.value, 10) || base;
    const next = Math.min(current * 2, 5000);
    if (next === current) return;
    select.value = String(next);
    fetchLogs();
  });
  select.addEventListener("change", fetchLogs);
}

/* ------------------------------------------------------------- forms */

function bindUpload() {
  const form = document.getElementById("u1-upload");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("u1-upload-file");
    const msg = document.getElementById("u1-upload-msg");
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      msg.textContent = S.common_select_file;
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    msg.textContent = S.common_uploading;
    try {
      await fetchJSON("/api/books", { method: "POST", body: formData });
      msg.textContent = S.common_uploaded;
      refreshContainer("u1-books");
      fileInput.value = "";
    } catch (error) {
      msg.textContent = fmt(S.common_err, { msg: error.message });
    }
  });
}

function bindCreate() {
  const form = document.getElementById("u1-create");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const msg = document.getElementById("u1-create-msg");
    const data = Object.fromEntries(new FormData(form).entries());
    if (!data.learning_level) delete data.learning_level;
    if (!data.learning_cefr) delete data.learning_cefr;
    delete data.u3_csrf;
    msg.textContent = S.common_creating;
    try {
      const result = await fetchJSON("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      window.location.href = "/jobs/" + result.id;
    } catch (error) {
      msg.textContent = fmt(S.common_err, { msg: error.message });
    }
  });
}

function bindJobActions() {
  const confirmForm = document.getElementById("u1-confirm");
  if (confirmForm) {
    confirmForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const msg = document.getElementById("u1-confirm-msg");
      const sourceLanguage = document.getElementById("u1-confirm-lang").value;
      msg.textContent = S.common_sending;
      try {
        await fetchJSON(
          "/api/jobs/" + encodeURIComponent(confirmForm.dataset.job) + "/confirm",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source_language: sourceLanguage }),
          }
        );
        window.location.reload();
      } catch (error) {
        msg.textContent = fmt(S.common_err, { msg: error.message });
      }
    });
  }
  for (const [id, action] of [
    ["u1-pause", "pause"],
    ["u1-resume", "resume"],
    ["u1-cancel", "cancel"],
  ]) {
    const button = document.getElementById(id);
    if (!button) continue;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await fetchJSON(
          "/api/jobs/" + encodeURIComponent(button.dataset.job) + "/" + action,
          { method: "POST" }
        );
        window.location.reload();
      } catch (error) {
        button.disabled = false;
        alert(fmt(S.common_cmd_failed, { msg: error.message }));
      }
    });
  }
  const jobMsg = document.getElementById("u3-job-msg");
  const clearButton = document.getElementById("u3-clear-logs");
  if (clearButton) {
    clearButton.addEventListener("click", async () => {
      const job = clearButton.dataset.job;
      if (!confirm(fmt(S.job_clear_logs_confirm, { id: job }))) return;
      clearButton.disabled = true;
      try {
        await fetchJSON(
          "/api/jobs/" + encodeURIComponent(job) + "/logs/clear",
          { method: "POST" }
        );
        if (jobMsg) jobMsg.textContent = S.jobs_logs_cleared;
        refreshContainer("u1-logs");
      } catch (error) {
        if (jobMsg) jobMsg.textContent = fmt(S.common_cmd_failed, { msg: error.message });
      } finally {
        clearButton.disabled = false;
      }
    });
  }
  const deleteButton = document.getElementById("u3-delete-job");
  if (deleteButton) {
    deleteButton.addEventListener("click", async () => {
      const job = deleteButton.dataset.job;
      if (!confirm(fmt(S.job_delete_confirm, { id: job }))) return;
      deleteButton.disabled = true;
      try {
        await fetchJSON("/api/jobs/" + encodeURIComponent(job), { method: "DELETE" });
        window.location.href = "/jobs";
      } catch (error) {
        deleteButton.disabled = false;
        alert(fmt(S.common_cmd_failed, { msg: error.message }));
      }
    });
  }
}

function bindBookDeletes() {
  const container = document.getElementById("u1-books");
  if (!container) return;
  container.addEventListener("click", async (event) => {
    const target = event.target;
    if (!target || !target.getAttribute) return;
    const name = target.getAttribute("data-book-delete");
    if (!name) return;
    if (!confirm(fmt(S.books_delete_confirm, { name: name }))) return;
    try {
      await fetchJSON("/api/books/" + encodeURIComponent(name), { method: "DELETE" });
      refreshContainer("u1-books");
    } catch (error) {
      alert(fmt(S.common_cmd_failed, { msg: error.message }));
    }
  });
}

function bindPurge() {
  const form = document.getElementById("u3-purge-form");
  const dry = document.getElementById("u3-purge-dry");
  const confirmButton = document.getElementById("u3-purge-confirm");
  const msg = document.getElementById("u3-purge-msg");
  if (!form || !dry || !confirmButton || !msg) return;
  let token = null;
  const collect = () => ({
    statuses: Array.from(
      form.querySelectorAll("input[name='status']:checked")
    ).map((input) => input.value),
    book: form.querySelector("select[name='book']").value || null,
    pair: form.querySelector("select[name='pair']").value || null,
  });
  dry.addEventListener("click", async () => {
    token = null;
    confirmButton.disabled = true;
    msg.textContent = S.common_sending;
    try {
      const result = await fetchJSON("/api/jobs/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collect()),
      });
      token = result.confirm_token;
      confirmButton.disabled = false;
      msg.textContent = fmt(S.jobs_purge_result, {
        count: result.count,
        bytes: humanSize(result.total_bytes),
      });
    } catch (error) {
      msg.textContent = fmt(S.common_err, { msg: error.message });
    }
  });
  confirmButton.addEventListener("click", async () => {
    if (!token) return;
    confirmButton.disabled = true;
    msg.textContent = S.common_sending;
    try {
      const body = collect();
      body.confirm_token = token;
      const result = await fetchJSON("/api/jobs/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      msg.textContent = fmt(S.jobs_purge_removed, {
        count: result.count,
        bytes: humanSize(result.total_bytes),
      });
      token = null;
      refreshContainer("u1-jobs");
    } catch (error) {
      token = null;
      msg.textContent = fmt(S.common_err, { msg: error.message });
    }
  });
}

function bindPrompts() {
  const form = document.getElementById("u3-prompt-form");
  const msg = document.getElementById("u3-prompt-msg");
  if (!form || !msg) return;
  const titleEl = document.getElementById("u3-prompt-form-title");
  const submitButton = document.getElementById("u3-prompt-submit");
  const resetButton = document.getElementById("u3-prompt-reset");
  const dialog = document.getElementById("u3-prompt-view");
  const dialogTitle = document.getElementById("u3-prompt-view-title");
  const dialogContent = document.getElementById("u3-prompt-view-content");

  const resetForm = () => {
    form.dataset.mode = "create";
    form.reset();
    if (titleEl) titleEl.textContent = S.prompts_create_title;
    if (submitButton) submitButton.textContent = S.prompts_create_btn;
    msg.textContent = "";
  };

  const viewPrompt = async (path) => {
    try {
      const data = await fetchJSON(
        "/api/prompts/content?path=" + encodeURIComponent(path)
      );
      if (dialogTitle) {
        dialogTitle.textContent = fmt(S.prompts_content_title, { file: data.path });
      }
      if (dialogContent) dialogContent.textContent = data.content;
      if (dialog && dialog.showModal) dialog.showModal();
    } catch (error) {
      alert(fmt(S.common_err, { msg: error.message }));
    }
  };

  const startEdit = async (button) => {
    try {
      const path = button.getAttribute("data-prompt-edit");
      const data = await fetchJSON(
        "/api/prompts/content?path=" + encodeURIComponent(path)
      );
      form.dataset.mode = "edit";
      form.elements.task.value = button.getAttribute("data-task") || "";
      form.elements.level.value = button.getAttribute("data-level") || "";
      form.elements.source.value = button.getAttribute("data-source") || "";
      form.elements.target.value = button.getAttribute("data-target") || "";
      form.elements.content.value = data.content;
      const version = parseInt(button.getAttribute("data-version"), 10) || 0;
      if (titleEl) titleEl.textContent = fmt(S.prompts_edit_title, { file: path });
      if (submitButton) {
        submitButton.textContent = fmt(S.prompts_save_btn, {
          next: "v" + (version + 1) + ".yaml",
        });
      }
    } catch (error) {
      alert(fmt(S.common_err, { msg: error.message }));
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    msg.textContent = S.common_sending;
    const data = {
      task: form.elements.task.value,
      level: form.elements.level.value,
      source: form.elements.source.value,
      target: form.elements.target.value,
      content: form.elements.content.value,
    };
    try {
      const result = await fetchJSON("/api/prompts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      msg.textContent = fmt(S.prompts_created, { file: result.path });
      setTimeout(() => window.location.reload(), 1000);
    } catch (error) {
      msg.textContent = fmt(S.common_err, { msg: error.message });
    }
  });

  if (resetButton) resetButton.addEventListener("click", resetForm);

  document.addEventListener("click", (event) => {
    const target = event.target;
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
    if (!confirm(fmt(S.prompts_delete_confirm, { file: deletePath }))) return;
    fetchJSON("/api/prompts/version?path=" + encodeURIComponent(deletePath), {
      method: "DELETE",
    })
      .then(() => window.location.reload())
      .catch((error) => {
        alert(fmt(S.common_cmd_failed, { msg: error.message }));
      });
  });
}

/* ------------------------------------------------------------- bootstrap */

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
