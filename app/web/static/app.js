/* U1 — минимальный JS (ТЗ: без фреймворков и build-инструментов).
 * Контракты:
 *   [data-u1-fetch]+[data-u1-render] — страница тянет данные СВОЕГО endpoint'а
 *   (каждый /api-endpoint из ТЗ имеет потребителя, U1-6);
 *   [data-u1-poll="<job_id>"] — поллинг прогресса задания, остановка в
 *   терминальном состоянии (U1-1: отображаемое — только из GET /api/jobs/{id});
 *   формы/кнопки — командные endpoint'ы (create/confirm/pause/resume/cancel).
 * Проценты — только для измеренных величин (охват выравнивания, U1-2).
 */
"use strict";

function esc(value) {
  return String(value == null ? "—" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    /* не-JSON-ответ */
  }
  if (!response.ok) {
    const detail = (data && data.detail) || text || String(response.status);
    throw new Error(detail);
  }
  return data;
}

async function refresh(id) {
  const el = document.getElementById(id);
  if (!el || !el.dataset.u1Fetch) return;
  try {
    const data = await fetchJSON(el.dataset.u1Fetch);
    RENDERERS[el.dataset.u1Render](el, data);
  } catch (error) {
    el.textContent = "Ошибка загрузки: " + error.message;
  }
}

const RENDERERS = {
  books(el, books) {
    if (!books.length) {
      el.innerHTML = "<p>В input/ нет книг.</p>";
      return;
    }
    let html =
      "<table><tr><th>файл</th><th>название</th><th>dc:language</th><th>детекция</th>" +
      "<th>уверенность</th><th>главы</th><th>сегменты</th><th>слов</th></tr>";
    for (const b of books) {
      html +=
        "<tr><td>" + esc(b.name) + "</td><td>" + esc(b.title) + "</td><td>" +
        esc(b.dc_language) + "</td><td>" + esc(b.detected) + "</td><td>" +
        esc(b.confidence) + "</td><td>" + esc(b.chapters) + "</td><td>" +
        esc(b.segments) + "</td><td>" + esc(b.words) + "</td></tr>";
    }
    el.innerHTML = html + "</table>";
  },

  jobs(el, rows) {
    if (!rows.length) {
      el.innerHTML = "<tr><td colspan='8'>Заданий нет.</td></tr>";
      return;
    }
    let html =
      "<tr><th>задание</th><th>статус</th><th>пара</th><th>режим / уровень</th>" +
      "<th>прогресс (реальные единицы)</th><th>выравнивание</th><th>токены</th><th>ошибка</th></tr>";
    for (const j of rows) {
      html +=
        "<tr><td><a href='/jobs/" + esc(j.id) + "'>" + esc(j.id) + "</a></td>" +
        "<td class='status-" + esc(String(j.status).toLowerCase()) + "'>" + esc(j.status) + "</td>" +
        "<td>" + esc(j.source_language) + " → " + esc(j.target_language) + "</td>" +
        "<td>" + esc(j.mode) +
        (j.learning_level ? " / " + esc(j.learning_level) +
          (j.learning_cefr ? " (" + esc(j.learning_cefr) + ")" : "") : "") + "</td>" +
        "<td>" + esc(j.progress_display) + "</td>" +
        "<td>" + (j.alignment_coverage != null
          ? (j.alignment_coverage * 100).toFixed(1) + "%" : "—") + "</td>" +
        "<td>" + esc(j.tokens_total) + "</td><td class='err'>" + esc(j.error) + "</td></tr>";
    }
    el.innerHTML = html;
  },

  languages(el, data) {
    let html =
      "<h2>Языки</h2><table><tr><th>код</th><th>название</th><th>spaCy</th><th>CEFR</th></tr>";
    for (const l of data.languages) {
      html +=
        "<tr><td>" + esc(l.code) + "</td><td>" + esc(l.name) + "</td><td>" +
        esc(l.spacy_model) + "</td><td>" + esc(l.cefr_label) + "</td></tr>";
    }
    html +=
      "</table><h2>Пары (статусы — резолвер L0)</h2>" +
      "<table><tr><th>исходный</th><th>целевой</th><th>статус</th><th>задание</th><th>не хватает</th></tr>";
    for (const p of data.pairs) {
      html +=
        "<tr><td>" + esc(p.source) + "</td><td>" + esc(p.target) + "</td><td>" +
        esc(p.status) + "</td><td>" + (p.creatable ? "можно создавать" : "нет") +
        "</td><td>" + esc(p.missing.join("; ")) + "</td></tr>";
    }
    el.innerHTML = html + "</table>";
  },

  models(el, data) {
    let html =
      "<table><tr><th>профиль</th><th>endpoint</th><th>модель</th><th>API-ключ</th>" +
      "<th>timeout, c</th><th>retry</th><th>конкурентность</th><th>стоимость</th></tr>";
    for (const m of data) {
      html +=
        "<tr><td>" + esc(m.name) + "</td><td>" + esc(m.base_url) + "</td><td>" +
        esc(m.model) + "</td><td>" + (m.api_key_set ? "задан" : "не задан") +
        "</td><td>" + esc(m.timeout_seconds) + "</td><td>" + esc(m.retry_times) +
        "</td><td>" + esc(m.concurrency) + "</td><td>" + esc(m.cost) + "</td></tr>";
    }
    el.innerHTML = html + "</table>";
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
    let html = "<table><tr><th>компонент</th><th>состояние</th></tr>";
    for (const [name, status] of entries) {
      const cls = String(status).startsWith("ok") ? "ok"
        : String(status).startsWith("error") ? "err" : "";
      html += "<tr><td>" + esc(name) + "</td><td class='" + cls + "'>" + esc(status) + "</td></tr>";
    }
    html += "</table>";
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
      html += "<h3>QC</h3><pre>" + esc(JSON.stringify(data.qc, null, 2)) + "</pre>";
    }
    const a = data.alignment || {};
    html += "<h3>Выравнивание</h3><p>" +
      (a.coverage != null
        ? "покрытие: " + (a.coverage * 100).toFixed(1) + "% (" + a.aligned + " / " + a.total + ")"
        : "—") + "</p>";
    if (Array.isArray(data.warnings) && data.warnings.length) {
      html += "<h3>Предупреждения</h3><ul>" +
        data.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>";
    }
    const l3 = data.l3;
    if (l3) {
      html += "<h3>Правки L3 (отчёт)</h3><p>попыток: " + esc(l3.attempted) +
        " · применено: " + esc(l3.applied) + " · отклонено: " + esc(l3.rejected) +
        " · остановлено: " + esc(l3.halted) + "</p>";
      if (l3.fixes.length) {
        html += "<table><tr><th>сегмент</th><th>документ</th><th>термин</th>" +
          "<th>применено</th><th>было</th><th>стало</th><th>причина</th></tr>";
        for (const f of l3.fixes) {
          html += "<tr><td>" + esc(f.segment_id) + "</td><td>" + esc(f.doc) + "</td><td>" +
            esc(f.term) + "</td><td>" + (f.applied ? "да" : "нет") + "</td><td>" +
            esc(f.old_text) + "</td><td>" + esc(f.new_text) + "</td><td>" +
            esc(f.reason) + "</td></tr>";
        }
        html += "</table>";
      }
    } else {
      html += "<h3>Правки L3</h3><p>—</p>";
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
      .map(([name, lines]) =>
        "<h3>" + name + "</h3><pre>" +
        (lines && lines.length ? esc(lines.join("\n")) : "(пусто)") + "</pre>")
      .join("");
  },
};

/* ---------------------------------------------------------------- прогресс (U1-1) */

function renderProgress(f) {
  const seg = f.segments
    ? f.segments.processed + " / " + f.segments.total : "—";
  const align = f.alignment.coverage != null
    ? (f.alignment.coverage * 100).toFixed(1) + "% (" +
      f.alignment.aligned + " / " + f.alignment.total + ")" : "—";
  const tokens = Object.entries(f.tokens)
    .filter(([name]) => name !== "total")
    .map(([name, t]) => name + ": " + t.total)
    .join(" · ");
  const eta = f.eta_seconds != null
    ? " · осталось (оценка): " + f.eta_seconds + " c" : "";
  return (
    "<table>" +
    "<tr><th>статус</th><td class='status-" + esc(String(f.status).toLowerCase()) +
    "'>" + esc(f.status) +
    (f.error ? " — <span class='err'>" + esc(f.error) + "</span>" : "") + "</td></tr>" +
    "<tr><th>прогресс (реальные единицы)</th><td>" + esc(f.progress_display) + "</td></tr>" +
    "<tr><th>фаза</th><td>" + (f.phase ? esc(f.phase) : "—") +
    " (" + f.phases.completed + " / " + f.phases.total + ")</td></tr>" +
    "<tr><th>сегменты</th><td>" + seg + "</td></tr>" +
    "<tr><th>выравнивание</th><td>" + align + "</td></tr>" +
    "<tr><th>токены</th><td>" +
    (tokens ? tokens + " · " : "") + "всего: " + esc(f.tokens.total) + "</td></tr>" +
    "<tr><th>время</th><td>прошло: " + f.elapsed_seconds + " c" + eta + "</td></tr>" +
    "<tr><th>ошибки / повторы</th><td>сегментов с ошибками: " + f.failed_segments +
    " · повторов: " + f.retries + "</td></tr>" +
    "</table>" +
    (f.warnings && f.warnings.length
      ? "<p><b>предупреждения:</b></p><ul>" +
        f.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>" : "")
  );
}

function startJobPolling() {
  const el = document.querySelector("[data-u1-poll]");
  if (!el) return;
  const jobId = el.getAttribute("data-u1-poll");
  let timer = null;
  const stop = () => {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  };
  const tick = async () => {
    try {
      const facts = await fetchJSON("/api/jobs/" + encodeURIComponent(jobId));
      el.innerHTML = renderProgress(facts);
      if (facts.status_terminal) stop();
    } catch (error) {
      /* API-ошибка: показываем причину и НЕ изображаем живой прогресс (T-U1-10). */
      stop();
      el.innerHTML += "<p class='err'>Опрос остановлен: " + esc(error.message) + "</p>";
    }
  };
  timer = setInterval(tick, 2000);
  tick();
}

/* ------------------------------------------------------------- формы и команды */

function bindUpload() {
  const form = document.getElementById("u1-upload");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("u1-upload-file");
    const msg = document.getElementById("u1-upload-msg");
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      msg.textContent = "Выберите файл";
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    msg.textContent = "Загрузка…";
    try {
      await fetchJSON("/api/books", { method: "POST", body: formData });
      msg.textContent = "Загружено.";
      refresh("u1-books");
      fileInput.value = "";
    } catch (error) {
      msg.textContent = "Ошибка: " + error.message;
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
    msg.textContent = "Создание…";
    try {
      const result = await fetchJSON("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      window.location.href = "/jobs/" + result.id;
    } catch (error) {
      msg.textContent = "Ошибка: " + error.message;
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
      msg.textContent = "Отправка…";
      try {
        await fetchJSON("/api/jobs/" + encodeURIComponent(confirmForm.dataset.job) + "/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_language: sourceLanguage }),
        });
        window.location.reload();
      } catch (error) {
        msg.textContent = "Ошибка: " + error.message;
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
          { method: "POST" });
        window.location.reload();
      } catch (error) {
        button.disabled = false;
        alert("Команда не выполнена: " + error.message);
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  for (const el of document.querySelectorAll("[data-u1-fetch]")) {
    const render = RENDERERS[el.dataset.u1Render];
    if (!render) continue;
    fetchJSON(el.dataset.u1Fetch)
      .then((data) => render(el, data))
      .catch((error) => {
        el.textContent = "Ошибка загрузки: " + error.message;
      });
  }
  startJobPolling();
  bindUpload();
  bindCreate();
  bindJobActions();
});
