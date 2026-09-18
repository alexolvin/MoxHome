# HANDOFF — EpubTranslator

Самодостаточный документ для продолжения работы в свежей сессии.
Обновляется **перед** началом каждого этапа и **сразу после** каждого измерения.

---

## Текущее состояние (2026-09-18)

- **Завершено:** E0 (фундамент) — CONTROL PASS 5/5; **E1 (ENGINE_PROBE)** — CONTROL PASS 6/6;
  **E2 (Ридер EPUB и сегментатор)** — CONTROL PASS 6/6; **E3 (Job, воркер, возобновление)** —
  CONTROL PASS 6/6; **E4 (Слой LLM)** — CONTROL PASS 6/6; **E5 (Тракт T: перевод)** —
  CONTROL PASS 8/8; **E6 (Выравнивание оригинал ↔ перевод)** — CONTROL PASS 7/7
  (+T-E6-8); **L0 (Реестр языков, матрица пар, определение языка)** — CONTROL PASS 8/8
  (L0-1…L0-6 + T-L0-7/8); **L1 (Языковые пакеты: NLP, таксономия, карточки, CEFR,
  частотность)** — CONTROL PASS 9/9 (L1-1…L1-9 + T-L1-10/11/12);
  **L2 (Глоссарий и терминология)** — CONTROL PASS 8/8 (L2-1…L2-8 + T-L2-1…11);
  **L3 (Правка перевода по глоссарию)** — CONTROL PASS 10/10 (L3-1…L3-10 +
  T-L3-1…15);
  **S1 (Отбор лексики, ТЗ_03)** — CONTROL PASS 7/7 (S1-1…S1-7 + T-S1-1…10);
  **S2 (Устойчивые выражения и MWE, ТЗ_03)** — CONTROL PASS 6/6 (S2-1…S2-6 +
  T-S2-1…7), 202/202 теста (на mock, BLOCKER-1 — реальные прогоны не
  заявляются);
  **S3 (Грамматические заметки, ТЗ_03)** — CONTROL PASS 7/7 (S3-1…S3-7 +
  T-S3-1…10), 223/223 теста (на mock, BLOCKER-1 — реальные прогоны не
  заявляются);
  **S4 (Summary, вопросы, упражнения, ТЗ_03)** — CONTROL PASS 7/7
  (S4-1…S4-7 + T-S4-8/9), 244/244 теста (на mock, BLOCKER-1 — реальные
  прогоны не заявляются);
  **S5 (Сборка learning EPUB и экспорт карточек, ТЗ_03)** — CONTROL 8/9
  PASS + S5-7 BLOCKER (Anki/pyanki отсутствуют в окружении — фактический
  импорт не проводился; формат экспорта верифицирован структурно)
  (S5-1…S5-9 + T-S5-10/11/12), 256/256 теста (на mock, BLOCKER-1 —
  реальные прогоны не заявляются);
  **U1 (Web UI и API, ТЗ_04 §U1)** — CONTROL 9/9 PASS (U1-1…U1-9 +
  T-U1-1…10), 266/266 теста (на mock, BLOCKER-1 — реальные прогоны не
  заявляются; Tailscale-привязка не проверялась — V1.11);
  **U2 (CLI, ТЗ_04 §U2)** — CONTROL 3/3 PASS (U2-1…U2-3 + T-U2-1…4),
  270/270 теста (на mock, BLOCKER-1 — реальные прогоны не
  заявляются).
  Отчёты: `docs/ENGINE_PROBE.md`, `docs/CONTROL/E2.md`, `docs/CONTROL/E3.md`,
  `docs/CONTROL/E4.md`, `docs/CONTROL/E5.md`, `docs/CONTROL/E6.md`, `docs/CONTROL/L0.md`,
  `docs/CONTROL/L1.md`, `docs/CONTROL/L2.md`, `docs/CONTROL/L3.md`,
  `docs/CONTROL/S1.md`, `docs/CONTROL/S2.md`, `docs/CONTROL/S3.md`,
  `docs/CONTROL/S4.md`, `docs/CONTROL/S5.md`, `docs/CONTROL/U1.md`,
  `docs/CONTROL/U2.md`, `docs/CONTROL/V1.md`;
  evidence:
  `docs/CONTROL/evidence/E1…E6/`, `docs/CONTROL/evidence/L0/`,
  `docs/CONTROL/evidence/L1/`, `docs/CONTROL/evidence/L2/`,
  `docs/CONTROL/evidence/L3/`, `docs/CONTROL/evidence/S1/`,
  `docs/CONTROL/evidence/S2/`, `docs/CONTROL/evidence/S3/`,
  `docs/CONTROL/evidence/S4/`, `docs/CONTROL/evidence/S5/`,
  `docs/CONTROL/evidence/U1/`, `docs/CONTROL/evidence/U2/`,
  `docs/CONTROL/evidence/V1/` (18 файлов, сводка
  `v1-evidence-summary.txt`).
- **Завершено (2026-09-14): V1 — программа приёмки** (ветка `stage/V1`,
  финальный amend коммита `ad16dd9`; merge --no-ff). CONTROL V1 —
  **13/13 блоков PASS** (отчёт `docs/CONTROL/V1.md`): V1.1 статика
  (ruff 0, mypy 0 — 75 файлов, pytest 270/270, TODO 0), V1.2 аудит
  хардкода (0 дефектов; 76 строк НК/КОНФ, 18 воспроизводимых grep-ов),
  V1.3 аудит дублирующих механизмов (0 неоправданных дублей, 7/7
  проверок), V1.4 аудит честности интерфейса (0 неподкреплённых
  утверждений), V1.5 дымовой (физический EPUB, EPUBCheck 0/0), V1.6
  инспекция EPUB (zipinfo verbatim), V1.7 билингв (16/16, 0
  missing/dup/empty), V1.8 отказы **16/16** (итоговый прогон 17:04,
  389 c; правки инструментария — D73–D75), V1.8a терминология (32
  терминов ≥ 30; 24 расхождения L2 → 24/24 правки L3,
  consistent_share 1.0, повтор L3 = 0, diff quality.log ↔ l3bak→target
  = 0), V1.9 рестарт (E3-4 = 0.0000% ≤ 5%, задание не потеряно,
  EPUBCheck 0/0), V1.10 чистая установка (git diff пуст; uv-sync-
  опасность моделей — D76, virtual-проект — D77), V1.11 Tailscale 4/4
  (08:52, с внешней машины `vzu5-claw`), V1.12 README 7/7 шагов на
  чистом инсталле (возобновление прерванного — перезапуском воркера,
  D78), V1.13 полный прогон 5 языков **5/5** (08:53, ~21,1M токенов,
  EPUBCheck 0 errors, Learning, target ru; warnings — verbatim в
  `v1-13-<lang>.txt`, ни один критерий не ослаблялся — Rule 7).
  Аудиты V1.2–V1.4 и решения по borderline — D79.
  BLOCKER-1 (реальный LLM-эндпоинт) не снят: все прогоны V1 — на
  детерминированном mock, «PASS на реальном LLM» не заявлен (Rule).
  BLOCKER-2 (книги Gutenberg) снят — ассеты `books/v1_*.epub` готовы
  (см. `.a/reports/v1_assets_prep.md`). Протокол этапов как в
  E/L/S/U-стадиях (ветка `stage/<S>`, явные пути в коммите, merge
  --no-ff, CONTROL + evidence, DECISIONS, HANDOFF).
- **Завершено (2026-09-14/15): V1R — приёмка на реальном LLM** (ветка
  `stage/V1R`; отчёт `docs/CONTROL/V1R.md`, evidence
  `docs/CONTROL/evidence/V1R/`). **CONTROL V1R — 7/7 PASS (V1R.1–V1R.5,
  V1R.6, V1R.6b); готово к merge `stage/V1R` → `main` (--no-ff).** **BLOCKER-1 снят**: реальный
  OpenAI-совместимый эндпоинт (vLLM 0.26.0, модель `qwen3.8-27b`,
  FP8, max_model_len 262144) предоставлен владельцем для тестов и
  настройки; API-ключ — в `.env` (gitignored), адрес/модель — в
  `config.yaml` (gitignored). Модель — thinking: добавлен механизм
  `extra_body` в `LLMProfile` (D80) — passthrough `chat_template_kwargs:
  {enable_thinking: false}` в оба разрешённых LLM-клиента (движковый `LLM`
  и `AnalysisClient`), без которого reasoning-поток засорял контент
  (измерено: `finish_reason: length` vs `stop`). CONTROL V1R — 7 блоков
  (V1R.1 endpoint/D80, V1R.2 реальный прогон без learning, V1R.3
  реальный прогон Learning (S-стадии), V1R.4 дефект D81, V1R.5
  реальная книга, V1R.6 регресс 273/273, V1R.6b регресс после D82–D86):
  - V1R.2/V1R.3 (`complex.epub`, en→de, реальный LLM): оба
    COMPLETED_WITH_WARNINGS. Контроль качества честно поймал поведение
    модели: 4 ячейки таблицы не переведены (E5 п.4 + E6 22/26 = 84.6%
    < 99.5%), L2 согласованность 76–78% < 95%; S1: 88/98 и S4: 38/38
    предложений реального LLM отклонены детерминированной валидацией
    (вне-схемные JSON-поля; вымышленные сущности/segment_id) —
    fail-closed подтверждён на реальной модели; S5 learning.epub + 4
    экспорта собраны.
  - V1R.4 (дефект D81, найден реальным прогоном): store L2.8
    (`books/<book_key>/…`) сталкивался с файлом-книгой в `books/` —
    `FileExistsError(17)` за 136 мс в TRANSLATING; фикс —
    зарезервированное поддерево `books/_book_store/` (одна точка,
    `BookStore.__init__`) + регрессионный тест; приёмка V1 дефект не
    поймала (изоляция storage-root в харнесах + `glossary.enabled: false`
    в модульных тестах).
  - V1R.5: реальная книга `books/v1_en.epub` (17 глав), en→ru,
    bilingual, Learning. Первый прогон `j-…af68b1` завершён
    (COMPLETED_WITH_WARNINGS, 12 357 с, 2 950 045 токенов; E6 99.93%,
    L2 44.0% < 95%, S1 893 issued/1492 rejected, S4 0/258, S5 893×4
    экспорта — метрики verbatim в `v1r-05-run3-book.txt`), НО критерий
    «EPUB валиден» не пройден: записанный «EPUBCheck 0/0» не подкреплён
    артефактами — реальный замер: `target.epub` 8 ERROR, `learning.epub`
    163 ERROR. Root cause воспроизведён: **D85** — конвейер валидует
    `<target>.partial>`, EPUBCheck имена без `.epub` не проверяет
    («Mode required for non-epub files», exit 1, без `Messages:`), и
    **D82** — старая `run_epubcheck` читала это как «0 ошибок»
    (fail-open). Т.е. ВСЕ записанные «0/0» до 2026-09-15 (включая V1.13)
    — результат отказа валидатора; в V1R.2/V1R.3 (EPUB 3-фиксер) 0/0
    реальным замером подкреплён. Фиксы **D82–D86** (D83 — OPF EPUB 2.0
    движком, D84 — S5 `<section>` в XHTML 1.1, D86 — study-answers
    `data-*`/`<section>`), тесты T-E5-12…18 + T-S5-13; офлайн на тех же
    артефактах: 8/163 → с фиксами 0/0 и 0/0. **Повторный прогон (run 4)
    запущен 2026-09-15 05:34 UTC по решению владельца (полный прогон без
    посева кэша): задание `j-20260915T053454Z-c346b1`. Run 4: реальный
    перевод+fill 1 187 576 токенов, TRANSLATING 108,7 мин, `target.epub`
    (437 200 Б) прошёл валидационный гейт (VALIDATING_OUTPUT 07:23:53Z),
    затем **FAILED 08:54:44Z** — «задача vocabulary_card не выполнена:
    после 3 попыток ответ невалиден (последняя проблема: ReadTimeout:
    timed out); пустой артефакт не создаётся (E4-4)»: загрузка общего GPU
    (3×120 c read-timeout подряд на одном вызове S1) — внешний фактор,
    fail-closed по дизайну (Rule 6), не дефект приложения. **Run 5
    `j-20260915T090235Z-5daf8e` (09:02 UTC, штатное возобновление E3:
    engine_cache c346b1 посеян — TRANSLATING 5 с / 0 токенов, 100% cache
    hit, md5 target.epub byte-идентичен run 4; S-стадии — свежие реальные
    вызовы) ТЕРМИНАЛЕН 11:17:39Z: COMPLETED_WITH_WARNINGS, 8 082 с
    (2 ч 14 мин 42 с), analysis 1 542 794 токенов; E6 1525/1526 = 99.93%,
    S1 893 issued / 1541 rejected (3719 candidates, 157 батч-вызовов по 25),
    S2 243/2, S3 110/40, S4 1/263 (Learning: только вопросы), S5 17/19 глав,
    L2 43.9% < 95%, L3 выключена (L3.10). **Независимый EPUBCheck:
    target.epub 0/0/0 и learning.epub 0/0/0 (RC=0) — V1R.5 закрыт PASS**,
    verbatim — `v1r-05b-run4-run5.txt`. Протокол фазы и «Итог» —
    `docs/CONTROL/V1R.md`; оценка ~9 ч для S1 снята (ошибка пересчёта
    «вызов на карточку»; S1 = батчи по 25, измерено 1.6–1.7 ч).
  - V1R.6: полный регресс 273/273 (270 V1 + 2 D80 + 1 D81), ruff 0,
    mypy 0 (75 файлов).
  - V1R.6b: регресс после D82–D86 — **282/282** (1042.77 s; D85 добавил
    реальный запуск EPUBCheck в pipeline-тесты), ruff 0, mypy 0
    (76 файлов) (`v1r-06b-regression.txt`).
  Правки кода V1R: `app/core/config.py`, `app/adapters/epub_engine/
  engine.py`, `app/llm/analysis.py` (D80), `app/glossary/store.py`
  (D81), `app/adapters/epub_engine/epubcheck.py` (D82/D85),
  `app/adapters/epub_engine/opf_repair.py` (новый, D83),
  `app/adapters/epub_engine/atomic.py` (D83), `app/learning/study_book.py`
  (D84/D86), `config/config.example.yaml`, `tests/probe/mock_llm_server.py`,
  тесты E4/E5/L2/S5, `tests/test_e5_epubcheck.py` (новый),
  `tests/s5_evidence.py`. Решения D80–D86 в `docs/DECISIONS.md`.
  Прочие языки V1.13 (de/fr/it/nl) на реальном LLM не перепрогонялись;
  записанный там «EPUBCheck 0 ошибок» не подкреплён (D85) — оговорка в
  `docs/CONTROL/V1R.md` «Честные пределы».
- **Завершено (2026-09-15/16): V2 — готовность продукта** (ветка
  `stage/V2`; отчёт `docs/CONTROL/V2.md`, evidence
  `docs/CONTROL/evidence/V2/` (7 файлов)). **CONTROL V2 — 8/8 PASS
  (V2.1–V2.8); merge `stage/V2` → `main` (--no-ff).** Новое требование
  владельца: «готовый продукт с удобным интерфейсом».
  - V2.1/V2.3: чистая установка из клона stage/V2 (70bc572) по README —
    без отклонений, кроме зафиксированных (EPUBCheck — локальная копия
    W3C 5.3.0; LLM — реальный 127.0.0.1:8004, сильнее V1.10; порт 8399):
    7/7 шагов дословно — UI-задание CWW 175 c (15/15), CLI-задание CWW
    RC=0 (22584 токена), 7/7 артефактов HTTP 200 (md5 main == learning),
    EPUBCheck скачанных 0/0/0/0 RC=0 (оба), kill воркера в TRANSLATING →
    перезапуск → CWW (задача не потеряна); `git diff`/`git status` клона —
    пустые (в V1.10 был `?? input/` — в V2 закрыто .gitignore).
  - V2.2: Tailscale 4/4 с vzu5-claw (100.95.195.40 → 100.124.155.106:8321);
    боевой web-сервер переведён на tailnet-bind (ограничение: без
    SERVER_AUTH_SECRET — tailnet как граница).
  - V2.4/V2.5/V2.6: механизмы удобства измерены на боевом сервере —
    статус воркера «ok (pid N)»/«оффлайн» (D87, kill 21:14:01Z →
    «оффлайн (последний heartbeat 70 c назад)» 21:15:11Z), именованные
    скачивания 6/6 md5-сверка (traversal 422), 0 сырых epoch в HTML +
    таймлайн 9/9 переходов; unit T-V2-1..5 (`tests/test_v2_web.py`).
  - V2.7: сквозной E2E через UI на реальном LLM (Tailscale-хост): прогон 1
    (21:19:51Z) нашёл **NAV-011** (EPUBCheck, EPUB 3: оглавление вне
    spine-порядка) → **D88** (S5: записи оглавления в spine-порядке,
    `update_nav`/`update_ncx`, T-S5-14) → прогон 2 (17:14:16Z): EPUBCheck
    0/0/0 на обоих артефактах (122.7 c, 22420 токенов, 15/15).
    CWW-предупреждения — поведение реального LLM (fail-closed, пороги не
    ослаблялись).
  - V2.8: регресс на финальном коде — **288/288** (1005.04 s), ruff 0,
    mypy 0 (77 файлов). Найден нюанс: pytest 9.1.1 в `-q` (addopts проекта)
    не печатает «N passed in Xs» — зафиксировано в v2-08.
  - Правки кода V2: `app/core/worker_signal.py` (новый, D87),
    `app/workers/worker.py`, `app/web/{health,views,api,pages}.py`,
    `app/web/templates/{job,jobs}.html`, `app/web/static/app.js`,
    `app/learning/study_book.py` (D88), `.gitignore` (`.worker-heartbeat`,
    `input/`), `README.md` (шаги 4/6 + D89), `tests/test_v2_web.py`
    (новый, 5 тестов), `tests/test_s5_study_book.py` (T-S5-14). Решения
    D87–D89 в `docs/DECISIONS.md`.
  - D89 (найден V2.3): строка быстрого старта `export cards <job_id>`
    исполняться не могла (`--format` обязателен с U2; V1.12 исполнял с
    `--format csv` молча) — README исправлен, команда доисполнена
    дословно (csv/anki/json RC=0).
  - Честные пределы V2 (в CONTROL): первый прогон чистой установки упал на
    ошибке скрипта автоматизации (не репозитория); wall-clock сессии после
    ~21:40Z 2026-09-15 ненадёжен (прерывания, ~20 ч offline) — evidence на
    UTC/epoch; оборванный регресс 21:21Z не используется.
- **Завершено (2026-09-17): U3 — переработка Web UI** (ветка `stage/U3`;
  отчёт `docs/CONTROL/U3.md`, evidence `docs/CONTROL/evidence/U3/` (14
  файлов)). **CONTROL U3 — 14/14 PASS (U3-1…U3-14); готово к merge
  `stage/U3` → `main` (--no-ff).** Три пробела живого стенда закрыты:
  (1) удаление заданий + очистка логов + массовый purge —
  `DELETE /api/jobs/{id}` (4 таблицы одной транзакцией + каталог после
  коммита; нетерминальный статус → 409 + причина verbatim; каталог —
  только внутри `storage.root`, D93), `POST /api/jobs/purge` (dry-run →
  `confirm_token`; только терминальные старше `ui.purge_min_age_days`),
  `POST /api/jobs/{id}/logs/clear` (`job.log`/`errors.log` = 0 + строка
  факта; `quality.log` не усекается — D92); (2) версионирование
  промптов из UI — `POST /api/prompts/versions` создаёт только `v(N+1)`,
  перезапись запрещена (D90), валидация до записи (400, 0 файлов, 0
  LLM), резолвер и `resolved_prompts` фиксируют N+1; (3) мобильная
  вёрстка — mobile-first, карточки под 640 px с полным набором полей
  (7 таблиц, расхождение 0), 0 ширин > 360 px, 0 цветов вне `:root`.
  Единый словарь строк `app/web/strings.py` (литералов вне — 0; статусы,
  коды, причины — verbatim, D94); fail-closed 403 на пустом
  `SERVER_AUTH_SECRET` + stateless HMAC CSRF (D91); доступность
  0/0/0/0 (label/scope/контраст/тап, `--u3-tap` 44px); честность 0
  недопустимых вхождений (вкл. встроенный `<script id="u3-strings">`,
  D102); регрессия U1+U2 на коде U3 — 14/14 (порядок t-u1-10 — D104).
  Решения D90–D105 в `docs/DECISIONS.md`; правки ТЗ 00/04 (граф,
  карта, §10, §U3, пометки V1.4/V1.11/V1.12), README — раздел
  «Управление заданиями и промптами в интерфейсе».
  Playwright (отдельный `.venv-u3browser` + системный Chrome,
  `channel="chrome"`) — финальный контроль вёрстки (вне CONTROL, ТЗ
  §11): 7 страниц × 360/412/1280 px — **PASS, 0 нарушений** (протокол
  + 21 скриншот — `.a/reports/u3_browser/protocol.md`); проверка
  нашла 2 дефекта вёрстки, невидимых статическому аудиту (интринсивная
  ширина select с длинными опциями и file-input) — исправлены в
  `style.css` (D105), после фикса CONTROL U3 14/14 PASS
  (evidence перегенерирован). Тесты: 305/305 (288 main + 17 T-U3),
  ruff 0, mypy app 0 (78 файлов; gate — D103: `mypy app tests` не
  gate, базовая линия 101 pre-existing ошибок в tests — исправлена
  в D106 (2026-09-18): `mypy app tests` = 0, 124 файла).
- **В работе (2026-09-18): V1F — финальный прогон приёмки на коде,
  включающем U3** (ветка `stage/V1F`; основание — пометки ТЗ 04 §9/§10
  от 2026-09-17: «финальный прогон приёмки выполняется один раз на
  коде, включающем U3»). U3 влита в main (5d0acb5) ПОСЛЕ V1/V1R/V2 —
  повторный прогон обязателен для блоков, которые U3 затрагивает:
  V1.1 (статика), V1.3 (дублирующие механизмы), V1.4 (честность
  интерфейса, вкл. `strings.py` и `<script id="u3-strings">`), V1.10
  (чистая установка — РЕАЛЬНЫЙ LLM 127.0.0.1:8004, решение владельца
  2026-09-18), V1.11 (Tailscale, внешняя машина vzu5-claw), V1.12
  (README 7/7; раздел «Управление заданиями и промптами» — U3).
  Отчёт — `docs/CONTROL/V1F.md`, evidence —
  `docs/CONTROL/evidence/V1F/`.

## Сделано в V1 (завершено, финальный amend коммита `ad16dd9`)

### Что сделано

- **Приёмочный инструментарий:** `tests/v1_evidence.py` (части v5/v6/v7/v8/
  v8a/v9 — V1.5 дымовой, V1.6 инспекция, V1.7 билингв, V1.8 16 сценариев
  отказов (независимые функции `_v8_01`…`_v8_16`), V1.8a терминология,
  V1.9 рестарт; verbatim-evidence в `docs/CONTROL/evidence/V1/`);
  `tests/v111_tailnet.py` (V1.11: прогон с внешней tailnet-машины —
  UI/upload/job/result, книга доставляется scp'ом); `tests/v113_runs.py`
  (V1.13: 5 реальных книг, Learning, target ru, сводная таблица по языкам).
- **Ассеты (BLOCKER-2 снят):** 5 книг Project Gutenberg —
  `books/v1_{en,de,fr,it,nl}.epub` (19/11/9/38/9 глав, 62441/99010/75923/
  47224/47510 слов), все 5/5 EPUBCheck 0 errors; EPUBCheck 5.3.0 заменён
  на **fat-jar** в обоих путях (см. «Среда машины» и
  `.a/reports/v1_assets_prep.md` — там же честный список отклонённых
  кандидатов по каждому языку).
- **Код (V1.1/V1.3 + правки, найденные реальными книгами в V1.13):**
  - `app/core/config.py`: `Settings.job_dir()` — ЕДИНСТВЕННЫЙ источник
    формулы job-каталога (удалены дубли `views.job_dir` и
    `Pipeline.job_dir`; V1.1/V1.3 — дублирующий механизм);
  - `app/epub/reader.py` + `app/epub/aligner.py`: блоки с пустым текстом
    (пустой `<p>`, блоки только с картинками/разделителями) больше не
    дают сегментов и блоков — у редера и аллайнера теперь ОДНО правило
    выбора, инвариант «сегментов == блоков» выдерживается на реальных
    книгах с разделителями (V1.13);
  - `app/core/config.py`: `identical_whitelist_pattern` — декоративные
    разделители (`* · • … † ‡` + кавычки) добавлены в E6-белый список
    «идентичны оригиналу» (реальные книги содержат строки «* * *»);
  - `app/glossary/candidates.py` + `consistency.py`: внутренние пробельные
    в surface кандидата схлопываются в один пробел (NER-сущность
    «Charles\n      » в v1_en.epub давала дубль кандидата и отклонение
    пометок как «дубликат»);
  - `tests/probe/mock_llm_server.py` (тестовый инструмент):
    `_sanitize_xml_fragment` — движок `encode_friendly` пишет текст без
    экранирования, сырой «&» из реальных книг («R. Bemporad & Figlio»,
    v1_it.epub) делал fill-промпт невалидным XML;
  - тесты E5/E6/L2/L3/S1/S5/U1/U2: рефакторинг под `settings.job_dir`
    (без изменения логики); `.gitignore`: `data/v1/` (артефакты приёмки).
- **Протокол прогонов 2026-09-14 (все — mock LLM, BLOCKER-1):**
  - 06:53 — первый прогон: V1.5/6/7 PASS; V1.13 — 2/4 (it rc=1, nl —
    15 нераспределённых L2-несовпадений);
  - 07:00 — V1.8 первый прогон: скрипт упал с `TypeError` (файл
    `v1-8-scenarios.txt` не записан, трейсбэк в stdout не сохранён —
    `main()` пишет в summary только строку «Type: message»); V1.8a FAIL;
    V1.9 FAIL;
  - 07:54 — V1.11 первый прогон: FAIL — сервер был привязан к 127.0.0.1
    вместо tailnet-адреса (внешняя машина `vzu5-claw` недоступна);
  - 07:56 — WIP-коммит `ad16dd9` (правки кода выше + evidence + скрипты);
  - 08:30–10:37 — повторный прогон V1.8/V1.8a/V1.9 на коде из
    `ad16dd9`: `TypeError` **не воспроизведён** (все 16 сценариев
    дошли до конца); V1.8 — 8/16 OK; V1.8a FAIL; V1.9 FAIL (причины
    ниже);
  - 08:52 — V1.11 повторный прогон: **PASS 4/4** (UI 200, upload 200,
    job COMPLETED 16/16 сегментов, result 3040 B == локальный);
  - 08:53 — V1.13 повторный прогон: **PASS 5/5** (числа — в «Фактические
    числа», сводка `v1-13-summary.txt`).

### Закрытие стадии (2026-09-14, 17:10) — итог 13/13 PASS

После итераций правок ПРИЁМОЧНОГО ИНСТРУМЕНТАРИЯ (не допусков —
Rule 7; решения D73–D79) закрыты все блоки; код приложения правками
harness-итераций не затрагивался:

- V1.8 — **16/16 PASS** (итоговый прогон 17:04, 389 s;
  `v1-8-scenarios.txt` перегенерирован): s5/s9 — живой поллинг статуса
  (`_await_live_status`, D74; окна измерены: TRANSLATING 21,3 s),
  s16 — окно GLOSSARY_FIX по наличию `l3bak`, s8 — EFBIG/27 as-is
  (D73), s5 — drain до терминала (D75), s3 — сценарий, где пуст и fill,
  s15 — ожидание FAILED по L3-6 (D72), s6/s7 — дельта LLM-запросов.
- V1.8a — **PASS** (14:28): фикстура расширена до 30 имён (32 термина),
  фильтр quality.log — по `glossary_fix`; 24 расхождения L2 → 24/24
  правки L3, consistent_share 1.0, повтор L3 = 0, diff l3bak→target = 0.
- V1.9 — **PASS** (14:32): INTERRUPTED читается из истории переходов БД
  (окно субсекундное — авто-резюме `worker._resume_if_needed`);
  E3-4 = 0.0000% ≤ 5%.
- V1.1 — **PASS** (17:00): ruff 0, mypy 0 (75 файлов), pytest 270/270
  (pytest 9 `-q` подавляет финальную строку — прогон `-o addopts=`),
  TODO/FIXME/XXX 0, AST-проверка дубликатов (только осознанные
  замыкания).
- V1.2–V1.4 — **PASS** (аудиты: рабочий отчёт
  `.a/reports/v1_audits_v12_v14.md`, закоммиченная копия
  `docs/CONTROL/evidence/V1/v1-2-4-audits.txt`): 0 дефектов хардкода /
  0 неоправданных дублей (7/7 проверок) / 0 неподкреплённых
  утверждений; borderline-позиции оставлены константами (D79).
- V1.10 — **PASS** (чистый клон `/tmp/v1-clean/et`, 16:56): установка по
  README без отклонений (порт 8399 — 8321 занят, LLM = mock per
  BLOCKER-1, EPUBCheck — локальная W3C-копия); `uv sync` реально
  деинсталлировал 5 spaCy-моделей — воспроизведено вживую и
  задокументировано (D76); uv-проект virtual, entry point не ставится
  (D77); код не правился (`git diff` пуст).
- V1.12 — **PASS** (16:58): 7/7 шагов README буквально на чистом
  инсталле; шаг 7 (возобновление прерванного) — перезапуском воркера,
  а не `job resume` (D78); задокументирован fail-closed: умерший mock →
  задание FAILED «Connection error» (поведение, не дефект).
- Свободные артефакты стадии: `docs/CONTROL/V1.md` (таблица CONTROL
  13/13), `docs/CONTROL/evidence/V1/` (18 файлов, сводка
  `v1-evidence-summary.txt`), DECISIONS D73–D79, README.md (новый файл
  репозитория), этот раздел. Финальный amend WIP-коммита `ad16dd9`.

Подразделы «V1.8/V1.8a/V1.9» ниже — ПРОМЕЖУТОЧНЫЙ разбор после
прогона 08:30 (история итерации); итог по каждому — PASS (см. выше).

### V1.8 — почему 8 из 16 сценариев «не сошлись» (evidence `v1-8-scenarios.txt`, верbatim там)

Не совпали: 3, 5, 6, 7, 8, 9, 15, 16; совпали: 1\*, 2, 4, 10, 11, 12,
13, 14. Из не совпавших: s1/s6/s7/s8/s15 — приложение сработало
корректно, дефект в проверке; s3/s5/s9/s16 — сценарий не создал
проверяемых условий (mock быстрее харнесса, fill-путь достраивает
книгу из XML блока), соответствующие механизмы приложения
**НЕ проверены** — называть их «корректными» нельзя.

| № | Сценарий | Поведение приложения (факт) | Почему «не совпало» (дефект инструментария) |
|---|----------|-----------------------------|----------------------------------------------|
| 1 | таймаут LLM | FAILED, причина verbatim «тракт T недоступен …: Request timed out.» | Проверка ищет подстроку «timeout», приложение пишет «timed out» — расхождение в отображении/вердикте САМОГО скрипта (строка «не сошелся» печатается, сценарий в итог считается совпавшим); приложение корректно |
| 3 | пустой ответ | COMPLETED_WITH_WARNINGS, `target.epub` создан | Сценарий `translate_empty` очищает только translate-ответы; в mock-мире финальный текст собирается по FILL-пути, а fill-ответы mock строит из XML блока независимо от сценария — книга переведена целиком. Нужен сценарий, где пуст и fill (ТЗ-условие «пустой артефакт НЕ создаётся» не создаётся) |
| 5 | kill -9 → INTERRUPTED | (задание уже завершено) | Момент kill: status=COMPLETED_WITH_WARNINGS, progress=1.00 — mock слишком быстр (delay 0,3 s, маленькая книга): окно TRANSLATING прошло до того, как харнесс начал поллинг; цикл поллинга не имеет терминальной защиты (крутит ещё 300+300 s в ожидании INTERRUPTED) |
| 6 | не-ZIP EPUB | Корректно: rc=3, «не является ZIP-архивом», задач в БД: 0, LLM-запросов 0 | Проверка считает КУМУЛЯТИВНЫЙ `requests_log()` mock (= 45 — запросы всех предшествующих сценариев), а не дельту за сценарий |
| 7 | DRM-EPUB | Корректно: FAILED, «книга защищена DRM (META-INF/encryption.xml); чтение отказано, обход DRM не реализуется» | То же, что №6: кумулятивный счётчик (45) |
| 8 | ENOSPC | Корректно: FAILED, причина verbatim «[Errno 27] File too large», битый выход не создан | `ulimit -f` даёт EFBIG (errno 27), а не ENOSPC (28); проверка ждёт «28»/«No space»/«ENOSPC». Механизм «не писать битый выход» сработал |
| 9 | cancel | (задание уже завершено) | Та же гонка, что №5: на момент cancel статус COMPLETED_WITH_WARNINGS, cancel rc=1 (терминальное состояние) |
| 15 | share cap | Корректно: L3 остановилась ДО правок (halted=True, 0 LLM-вызовов, l3bak не создан, книга не изменена) и задание FAILED — ровно по дизайну L3-6 (ТЗ_03: превышение `fix_max_segment_share` = FAILED с отчётом, переписывание запрещено) | Сценарий ожидает COMPLETED\*/CWW — проверка противоречит дизайну стадии L3; принять нужно FAILED + отчёт + неизменность книги |
| 16 | kill в L3 | (задание уже завершено) | Окно частично поймано (l3bak существует), но `set_delay(1.0)` mock растягивает не `glossary_fix`-запросы — стадия L3 завершается за ~1–2 s, kill в окно не попал (fixed=False), INTERRUPTED не зафиксирован |

Итог: для V1.8 нужны правки СЦЕНАРИЯ (дельты счётчика, терминальные
гарды, корректные ожидаемые errno/статусы, пустой fill для сценария 3,
растяжение glossary_fix-запросов для сценария 16) и повторный прогон;
по разбору правок приложения по этим сценариям не требуется
(подтверждено), но s3/s5/s9/s16 после исправления сценариев обязаны
быть прогнаны заново — только тогда механизмы реально проверятся.

### V1.8a — терминология (evidence `v1-8a-terminology.txt`)

Механизм L2/L3 корректен: 24 расхождения до правки (L3 `mismatches_input`
= 24; L2 `consistent_share` 0.9 < 0.95), L3: attempted=24 applied=24
rejected=0 halted=False, после — `consistent_share`=1.0 (ТЗ ≥99%),
расхождений 0, повторный L3 = 0 правок, diff `l3bak`→`target` = ровно 24
`<p>`-блока. FAIL — по двум дефектам инструментария:

- фикстура `wexley8` даёт **18** терминов глоссария (Wexley + 16 имён + 1),
  ТЗ V1.8a требует **≥30** — фикстуру нужно расширить (больше разных
  имён/терминов в тексте);
- фильтр quality.log `if "Wexley" in ln` подбирает 25 строк вместо 24
  фактических правок — лишняя строка это L2 QC-предупреждение, перечисляющее
  Wexley-сегменты; фильтровать нужно по «glossary_fix».

### V1.9 — рестарт (evidence `v1-9-restart.txt`)

Механизм корректен: kill пойман посреди перевода (progress 0.40,
X=10142), прирост токенов `max(0, X+Y−Z)/Z` = **0.0000%** (≤5%, E3-4),
переслано запросов 1 / токенов 4487 (ground truth — mock `requests_log`;
БД не видит не-flushed группу), финал COMPLETED_WITH_WARNINGS, задание не
потеряно, EPUBCheck 0 FATAL/0 ERROR. FAIL — по одной проверке:

- «INTERRUPTED зафиксирован» читает живое состояние задания поллингом
  0,5 s, но worker `--loop` при старте помечает задание с истёкшей арендой
  INTERRUPTED (единственный механизм, E3-1) и **немедленно** авто-резюмирует
  его (`worker._resume_if_needed`) — окно INTERRUPTED субсекундное, поллинг
  его не видел; CLI `resume` пришёл поздно (rc=1, задание уже в
  терминальном состоянии). Переход INTO INTERRUPTED и из него — в истории
  переходов БД: проверку нужно читать из истории, не из живого статуса.

### Честные пределы / что не проверено

- Все прогоны — на детерминированном mock (BLOCKER-1); «PASS на реальном
  LLM» не заявлен ни по одному блоку (Rule).
- EPUBCheck — локальная копия W3C 5.3.0 (дистрибутив не в git,
  `data/tools/` — gitignored); «штатный читатель» (V1.6) в headless-
  окружении заменён EPUBCheck + ридером приложения E2.5 (честно).
- V1.8 s8 — симуляция `ulimit -f` (EFBIG/27), не реальное заполнение
  диска (tmpfs требует root — проверено, D73); допуск «FAILED +
  причина ОС as-is + повреждённый выход не принят» сохранён
  (реальный ENOSPC/28 принимается).
- V1.13: содержание warnings — verbatim в `v1-13-<lang>.txt`; ни один
  критерий не ослаблялся (Rule 7).
- Аудиты V1.2–V1.4 — только чтение кода; 6 borderline-наблюдений V1.2
  и 2 V1.4 — зафиксированы и решены (D79), ни одно не является дефектом
  по букве ТЗ.
- Прогон 07:00 (до коммита): `TypeError` в `main()` v8-части не
  воспроизведён повторными прогонами (08:30, 14:28, 17:04 — код
  `ad16dd9`), трейсбэк не сохранён, конкретная причина того падения не
  установлена. Рекомендация: в `main()` evidence-скриптов писать
  полный трейсбэк в evidence (сейчас — только строка «Type: message»).

## Сделано в U2

- **`app/cli.py` (новое):** командный интерфейс по ТЗ_04 §U2 — 9 команд
  (`analyze <input.epub>`; `translate <input.epub> --source --target
  --mode [--level]`; `job list|status|resume|cancel`; `validate
  <file.epub>`; `languages`; `export cards <JOB_ID> --format
  <csv|anki|json>`) + аддитивные `--wait [SECONDS]` и `--cefr`.
  Собственной бизнес-логики нет (U2-1): те же сервисы, что API —
  `create_job`, `scan_book` (services.analyze), `views.job_facts/
  progress_facts/languages_view`, `JobRepository.list_jobs/get/
  request_resume/request_cancel`, `read_epub` + `run_epubcheck`,
  `PairResolver.resolve_pair` (предотбор пары — fail closed ДО создания;
  язык задания НЕ устанавливает, детектор L0.5 в воркере). Контроль —
  точный импорт-allowlist (12 модулей сервисного слоя) + 9/9 вызовов
  (T-U2-1). Запуск — `python -m app.cli` (воркер — отдельный процесс,
  подсказка в выводе translate). `--source` — только предпроверка пары
  (UNSUPPORTED → код 3).
- **`app/services/analyze.py` (новое, D70):** `scan_book` — единый
  источник сканирования книги для API (`POST /api/books`) и CLI
  (`analyze`); вынесен из `api._scan_book` (Rule 8), `api.py` — рефакторинг
  (−50 строк).
- **`app/learning/card_export.py`:** `CARDS_FILES` (csv/anki/json →
  имена файлов) — единая таблица имён артефактов карточек (писатель
  S5, API `/api/jobs/{id}/result`, CLI `export cards`; Rule 8);
  `app/workers/pipeline.py` — `_s5_export_cards` использует её.
- **`app/web/views.py`:** `_tail` — guard `lines <= 0` (CLI `job
  status` логи с `lines=0`).
- **`app/core/config.py` (фикс, D71):** `load_settings` — `.env`
  парсится `dotenv_values` (без мутации `os.environ`; до этого
  `load_dotenv` переносил переменные файла в окружение всего
  процесса, и при нескольких конфигурациях в одном процессе —
  тестовые профили — `SERVER_AUTH_SECRET` U1 перетекал в U2,
  401 на каждом API-вызове; приоритет os.environ > файл сохранён).
- **Коды выхода:** `AppError.exit_code` (`app/core/errors.py`): 0 —
  успех; 1 — неуспешное задание (FAILED/CANCELLED/INTERRUPTED) либо
  прочие; 2 — ConfigurationError; 3 — InputValidationError; 4 —
  LLMUnavailableError; 5 — OutputValidationFailed. Исход задания
  отражают `translate --wait` (опрос БД 0.5 c, тот же источник, что
  UI; timeout → 1) и `job status` (терминальный неуспех → 1; статус и
  причина verbatim). `validate` = `read_epub` (не-ZIP/структура → 3)
  + `run_epubcheck` (недоступен → 2, fail closed Rule 6; FATAL/ERROR →
  3 + 20 строк в stderr; иначе 0 + версия/счётчики). `export cards` —
  гейт COMPLETED/COMPLETED_WITH_WARNINGS (иначе JobStateError → 1,
  «карточки не готовы»).
- **`pyproject.toml`:** `[project.scripts] epubtranslator =
  "app.cli:main"` (установленный пакет — проверка V1.10).
- **Тесты: 4 запуска `tests/test_u2_cli.py`** (T-U2-1…T-U2-4; CLI —
  реальный subprocess `python -m app.cli` с `-u` (stdout — live), воркер
  — реальный subprocess `--loop` (D69), LLM — MockLLMServer; T-U2-1 —
  импорт-allowlist + паритет analyze/languages; T-U2-2 — 8 сценариев
  кодов выхода реальными rc (COMPLETED-прогон tiny-книги Learning
  ~20–40 s → COMPLETED_WITH_WARNINGS; CANCELLED — кооперативная отмена
  в TRANSLATING; FAILED — мёртвый профиль `127.0.0.1:1`); T-U2-3 —
  взаимная видимость + гейт `export cards`; T-U2-4 — validate
  повреждённого/валидного). Evidence-скрипт `tests/u2_evidence.py`
  (4 verbatim-файла).
- **Измерено (evidence U2, `docs/CONTROL/evidence/U2/`, 4 файла):**
  U2-1: 0 дублирования (12 импортов — точный allowlist, 9/9 вызовов,
  паритет analyze 4/4 и languages 6/6 + 11/11); U2-2: 8/8 сценариев
  (rc 0/0/1/1/2/3/3/3; причина FAILED verbatim «…ConnectError:
  [Errno 111] Connection refused…(E4-4)»; export cards: COMPLETED → 0
  (cards.csv, 3 карточки), CREATED → 1 «карточки не готовы»); U2-3:
  2/2 направления (CLI job в `GET /api/jobs` + `/{id}` 200; API job в
  `job list` + `job status`); T-U2-4: повреждённый → 3 «не является
  ZIP-архивом», валидная → 0 «EPUBCheck v5.3.0: 0 FATAL, 0 ERROR,
  WARNING 0». Регрессия U1 (рефакторинг api.py) — 10/10.
- Тесты: 270/270 (266 + 4), ruff 0 (`app/` + `tests/`), mypy strict 0
  (75 файлов).
- Дефекты этапа: 1 в ядре (не CLI), пойман полным прогоном и исправлен
  до коммита: `load_dotenv` в `load_settings` мутировал `os.environ`
  — `SERVER_AUTH_SECRET` U1-профиля «auth» перетекал в профили U2
  (401 на каждом API-вызове; изолированный запуск U2 — зелёный) —
  фикс в `app/core/config.py` (D71), последовательная регрессия
  U1+U2 — 14/14. Разработка (до коммита): гонка чтения `job id` из
  лога CLI-subprocess (stdout блок-буферизуется при редиректе в файл
  — `-u` + fallback после wait); двойная версия EPUBCheck в выводе
  `validate` (исправлено: `outcome.version` уже несёт «EPUBCheck
  v5.3.0»). Честные пределы (в CONTROL U2): коды 4/5
  не порождаются 8 сценариями ТЗ (классы и маппинг существуют в
  `AppError`; «LLM down» отражается исходом задания FAILED → 1 с
  причиной as-is); confirm подозрительной книги — через UI `/jobs/<id>`
  (команд ТЗ CLI confirm не включает); установленная команда
  `epubtranslator` — V1.10; `--wait`/`--cefr` — аддитивные расширения;
  LLM — mock (BLOCKER-1).

## Сделано в U1

- **`app/web/` (новое):** слой представления (D69) — `app.py` (фабрика
  `create_app(settings, db)`: FastAPI без docs/redoc, Basic-auth
  middleware на ВСЕХ маршрутах, include api+pages, mount `/static`),
  `auth.py` (`_authorized`: пустой `SERVER_AUTH_SECRET` → открыто;
  Basic, любое имя, пароль = секрет, `hmac.compare_digest`;
  `auth_check` → 401 + `WWW-Authenticate`), `health.py`
  (`health_report`: app/database — `repo.ping`/storage — контрольная
  запись в `storage.root`/llm — `GET {base}/models` Bearer, timeout 5 c,
  по профилям/nlp — spacy по языкам, ru = «n/a (целевой язык)»/epubcheck
  — jar+java; БЕЗ общего флага), `views.py` (чистые функции facts:
  `progress_facts(job, validation)` — отображение «глава N / M,
  сегментов X / Y», `elapsed_and_eta` (ETA = `elapsed·(1−p)/p`, метка
  «оценка»), `job_facts`, `list_books`, `languages_view`, `models_view`,
  `prompts_view`, `settings_view` (секреты — «задан»/«не задан»),
  `quality_view` (l3.fixes из `l3_fix_report.json`)), `api.py` (16
  endpoint'ов: health/languages/books GET|POST/jobs GET|POST/jobs/{id}/
  confirm/pause/resume/cancel/logs/result/quality/models/config;
  result — artifact main|cards.csv|cards.anki.tsv|cards.json, main =
  learning.epub если есть, иначе target.epub), `pages.py` (6 разделов
  Books/Jobs/Languages/Models/Prompts/Settings + 404; starlette 1.6:
  `TemplateResponse(request, name, context, status_code)`), шаблоны
  (9 HTML) + `static/style.css` + `static/app.js` (fetch по
  `data-u1-fetch`, опрос задания 2 с с остановкой по терминальному
  статусу/ошибке API — T-U1-10).
- **`app/storage/database.py` (модификация):** подключения на нить
  (`threading.local`): sync-эндпоинты FastAPI исполняются в пуле anyio,
  соединение, привязанное к нити создания → `sqlite3.ProgrammingError`
  (дефект, пойман тестами); `migrate()` fail-closed на WAL;
  `busy_timeout` + WAL сериализуют межсоединенный доступ.
- **`app/storage/jobs_repo.py`:** `list_jobs()` (ORDER BY created_at
  DESC), `ping()` (SELECT 1) — для health.
- **`app/languages/cefr.py`:** `language_cefr_label(pack)` — derived →
  «Estimated CEFR level» (L1-8, единая подпись во всех местах).
- **`app/workers/pipeline.py`:** `translation_phases(validation)` —
  модульный уровень, ЕДИНЫЙ источник фаз (callback воркера и UI; Rule 8);
  `run_translating` переиспользует его.
- **`app/__main__.py`:** E0-заглушка → запуск uvicorn-сервера
  (bind/port/auth из конфига, лог старта).
- **Тесты: 10 запусков `tests/test_u1_web.py`** (T-U1-1…T-U1-10; U1Env —
  mock-сервер + 3 профиля (main/dead/auth), книги slow-6ch/tiny-2ch/
  full-20ch; воркеры — `--loop`; T-U1-9 — полный цикл 20ch Learning/B1,
  result побайтово; T-U1-10 — `server.stop()` в окне TRANSLATING →
  FAILED + причина в HTML). Evidence-скрипт `tests/u1_evidence.py`
  (10 verbatim-файлов).
- **Измерено (evidence U1, `docs/CONTROL/evidence/U1/`, 10 файлов):**
  40/40 срезов progress(API) == progress(БД), 0 расхождений display, 0
  «%»; все «%» = измеренный E6 (100.0 при coverage 1.0), JSON — без
  «%»; LLM off (мёртвый порт) → llm/* = «error (ConnectError)» 3/3,
  «healthy» отсутствует, nlp/ru = «n/a (целевой язык)»; 11/11 пар API
  == свежему PairResolver (status/missing/creatable); 6/6 derived —
  «Estimated CEFR level»; 16 endpoint'ов (19 метод-строк), 0 без
  потребителя; 17/17 маршрутов — 401 без секрета / работают с ним,
  неверный — 401; 0 вхождений 2 секретов (13 путей) в HTML/API и в
  access log реального uvicorn; L3 (wexley +
  `glossary_inconsistent` + `fix_enabled: true`): 3 фактические правки,
  UI == quality.log 3/3 (0 расхождений в обе стороны); LLM off в
  TRANSLATING → FAILED + причина verbatim в UI.
- Тесты: 266/266 (256 + 10), ruff 0 (`app/` + `tests/`), mypy strict 0
  (73 файла).
- Дефекты: не обнаружено. Разработка (до коммита): sqlite-привязка к
  нити (поймано тестами → thread-local); гонка `--once`-воркера
  (выход после NEEDS_CONFIRMATION, подтверждённое задание без
  исполнителя — поймано evidence u1-9 → воркеры evidence `--loop`,
  зафиксировано в D69). Честные пределы (в CONTROL U1): Tailscale-
  привязка (V1.11) не проверена (нет второй машины); LLM — mock
  (BLOCKER-1); Anki-импорт не проверен (S5-7); kill -9 API-процесса —
  не в критериях U1; запуск воркера из UI — Not implemented (Rule 7).

## Сделано в S5

- **`app/learning/study_book.py` (новое):** сборка учебной книги —
  ДОПОЛНЕНИЕ `target.epub` (не с нуля): `locate_structure` (container →
  OPF → nav/NCX по manifest), `append_sections_to_doc` (секции — в КОНЦЕ
  `<body>`, без смешивания; XMLParser `remove_blank_text=False`),
  `render_sections` (порядок SECTION_ORDER = summary, vocab,
  expressions, grammar, questions, exercises; `<div
  class="s5-section" id="s5-{type}-{chapter_id}">` + h2 — D84: `<div>`,
  не `<section>`, валидно в DTD XHTML 1.1 (EPUB 2 книги) и в HTML5;
  ответы — ВНЕ; summary — при `status=="issued"`), `render_answers_document`
  (финальный документ `study-answers.xhtml`, `lang=ru`, чистый
  XHTML-default-namespace: `nsmap={None: XHTML, "xml": XML_NS}`;
  `<div id="s5-coverage">`/`<div id="s5-answers">` (D86, аналог D84),
  данные охвата — `title="s5-coverage=… s5-aligned=… s5-total=…"` на
  `<p>` (D86: `data-*` вне DTD XHTML 1.1); перечень пропущенных
  глав; «Ответ:» — только здесь), `update_opf`/`update_nav`/`update_ncx`
  (запись на каждую секцию + финальный документ; NCX — defensive,
  playOrder max+1), `assemble_learning_epub` (оригинальные записи — имя/
  порядок/дата; `mimetype` ПЕРВАЯ, принудительно STORED, остальные
  DEFLATED; новая запись — фиксированная дата 1980-01-01 —
  побайто-детерминизм), `validate_study_book` (mimetype; неструктурные
  — побайтово; оригинальные блоки — префикс; «Ответ:» в главах — 0;
  якоря; TOC; OPF; охват в финальном), `zipinfo_verbatim` (`/usr/bin/
  zipinfo`, фолбэк — `zipfile`).
- **`app/learning/card_export.py` (новое):** экспорт карточек без своего
  формата: CSV RFC 4180 (заголовок = полный фиксированный набор колонок
  языка, вложенные объекты — подстолбцы `имя_поле`), Anki text import
  (`#separator:Tab`, `#html:false`, `#columns:Front/Back/<языковые>/
  Tags`, C-escaping `\`→`\\`, `\n`, `\t`), JSON — полный массив;
  `required_cells` (обязательные схемы: объекты — по подстолбцам,
  nullable — колонка на месте, значение может быть пустым),
  `nullable_fields`, `export_columns`.
- **`app/learning/__init__.py`:** `SUBSTAGE_LEVELS["s5"]` = все 4 уровня
  (состав секций — наличие артефактов вышестоящих стадий, T-S5-10).
- **`app/workers/pipeline.py`:** подстадия `s5_assembly` в `run_learning`
  (ключ `stage_results: "s5_assembly"`, та же формула): охват —
  `jobs.alignment_coverage` (None → AppError; расхождение с E6-отчётом
  > 1e-9 → AppError), пропущенные главы — 0 выровненных пар;
  `_s5_collect_artifacts` → `_s5_build_sections` (per-chapter рендер +
  якоря + TOC-записи) → `render_answers_document` →
  `assemble_learning_epub` → `validate_study_book` → `atomic_finalize`
  (`.partial` → fsync → валидация → EPUBCheck → `.bak` → `os.replace` →
  fsync dir — тот же механизм E5/L3) → `_s5_export_cards`
  (cards.csv / cards.anki.tsv / cards.json) → `s5_report.json` (включая
  `zipinfo` verbatim, EPUBCheck-версию/итог, sections, skipped) →
  секция `quality.json s5` + warnings (пропущенные главы, частичный
  охват, EPUBCheck недоступен — Rule 6).
- **`stubs/lxml/etree.pyi` (расширение):** локальные минимальные
  стабы (mypy_path=stubs) дополнены поверхом S5: `_DocumentTree`
  (docinfo/getroot), `getroottree`, `findall`, `iter`, `XMLParser`,
  `Element`/`SubElement` c `nsmap: Mapping[str | None, str | None]`,
  overloads `tostring` (doctype) / `fromstring` (parser).
- **Тесты: 12 запусков `tests/test_s5_study_book.py`** (T-S5-1…T-S5-12;
  S5Env по образцу S1Env; kill-профиль heartbeat 0.5 c / lease 1.0 c;
  T-S5-9 — ×10 kill-итераций W1→confirm→W2→SIGKILL→W3 + in-process
  повтор сборки побайтово; T-S5-12 — white-box патч alignment после
  `run_validating_output`, до GLOSSARY_FIX/LEARNING; книги — 20-главная
  en, 1-главная en, 10×10 en, 20-главные de/fr/it/nl из языковых пулов
  S4 (число слов выше плотности S1, иначе `max_cards`=0)).
  Evidence-скрипт `tests/s5_evidence.py` (12 verbatim-файлов, 11
  in-process прогонов + ×10 kill).
- **Измерено (evidence S5, `docs/CONTROL/evidence/S5/`, 12 файлов):**
  EPUBCheck 0 ERROR ×5 языков (verbatim); mimetype первой STORED +
  zipinfo verbatim; 100% сохранность (неструктурные побайтово,
  префиксы блоков 20/20, style.css/img/container.xml явно); TOC 100%;
  «Ответ:» в главах 0 / в финальном 341; обязательные колонки карточек
  100% строк ×4 языка (артикль de/nl, род fr/it, main_forms de —
  verbatim); охват = E6 (расхождение 0); kill ×10: 0 повреждённых
  принятых, resume COMPLETED, детерминизм побайтово; Minimal — 0
  секций; Intensive — «артефакт ⇔ секция» по главам; 90% — дословно
  (`data-s5-coverage="0.900000"`, «S5 Ten — 3», «90.0%»), пропущенная
  глава без секций.
- Тесты: 256/256 (244 + 12), ruff 0, mypy 0 (66 файлов).
- Дефекты этапа: не обнаружено. Честные пределы (в CONTROL S5): S5-7 —
  фактический импорт в Anki НЕ ПРОВЕРЕН (BLOCKER: anki/pyanki
  отсутствуют; формат верифицирован структурно verbatim); LLM — mock
  (BLOCKER-1); NCX-ветка — только кодом+unit (тестовых EPUB с NCX нет);
  `.bak` (E5.9) при повторной записи той же книги в сквозном прогоне не
  моделировался (каждая kill-итерация — новое задание).

## Сделано в S4

- **`app/learning/study.py` (новое):** детерминированная валидация учебных
  материалов и конвенции: `BLANK="____"`, `split_words` (regex
  `[^\W\d_]+`), `blank_first_occurrence` (первое вхождение на границе слова,
  IGNORECASE), `check_gap` (слово ∈ словах исходного предложения + blank по
  конвенции), `check_mc` (≥2 вариантов, без дублей casefold, ровно один
  правильный, ответ = текст правильного), `check_tf_answer`, `check_entities`
  (`contains_form` по нормализованному тексту главы — Rule 8, тот же
  механизм L2/S2/S3), `validate_summary` (brief/extended непустые, списки),
  `validate_question`, `validate_exercise` (vocab `source_ref` → id
  выпущенной S1-карточки, grammar → id выпущенной S3-заметки, translation —
  prompt ∈ тексту главы, type ∈ `limits.exercises_types`).
- **`app/workers/pipeline.py`:** три подстадии в `run_learning`
  (`SUBSTAGE_LEVELS`: `s4_summary` {Intensive}, `s4_questions`
  {Learning, Intensive}, `s4_exercises` {Intensive}): `_s4_chapter_data`
  (выровненные сегменты главы, пары E6; главы БЕЗ текстовых сегментов
  исключаются ДО LLM — `skipped` с причиной + warning, S4.1/T-S4-8) → LLM
  по главам (summary — ОДНО объект-изложение; вопросы — 15: 5 easy + 5–10
  standard + 1–5 advanced; упражнения — 7 типов, состав — конфиг) →
  детерминированная валидация → дедупликация (`type|question.casefold()` /
  prompt упражнения) → `summaries.json` / `questions.json` /
  `exercises.json` (записи БЕЗ `answer`/`correct`) + `answers.json`
  `{chapters:{ch:{questions:{qid},exercises:{eid}}}}` (ВСЕ ответы —
  отдельная сущность, S4-5; mc — индекс строкой, gap — слово, tf —
  true/false, free — эталон) + секция `s4.{summary,questions,exercises}` в
  `quality.json` + `record_stage_result` (ключи `s4_summary`/
  `s4_questions`/`s4_exercises`; у упражнений — плюс hashes
  `vocabulary_cards.json`/`grammar_notes.json` — вход = выпуски S1/S3).
  Детерминированные id: `uuid5(NAMESPACE_URL, "s4:{ch}:{type}:{text.
  casefold()}").hex`.
- **`app/llm/tasks.py`:** задачи `chapter_summary` (JSON-схема — ОДИН
  объект, не items: brief/extended/key_events/entities/themes/concepts —
  семантика «одна сводка на главу» в схеме), `chapter_questions` (items:
  question/type/level/segment_id/entities/answer; опц. options/
  original_sentence/sentence_with_blank), `chapter_exercises` (items:
  type/prompt/answer/segment_id/source_ref; `source_ref.kind` ∈ {card, note}
  | null — translation/gap/mc/tf/free используют null).
- **Промпты:** `prompts/chapter_summary/v1.yaml`,
  `prompts/chapter_questions/v1.yaml`, `prompts/chapter_exercises/v1.yaml`
  (generic, маркеры payload в user_template).
- **Конфиг:** `limits.exercises_types` (7 типов, дефолт — все;
  field_validator — неизвестный тип → `ConfigurationError`),
  `config.example.yaml`.
- **Mock (D3):** три S4-обработчика — summary (всегда schema-validный
  объект, фолбэк entities=["текст"] — требуется для E4-2, которая шлёт
  plain text; генерация из фактических предложений segments), questions
  (5 gap + 5 mc + 2 tf + 3 free на главу; gap-слово/mc-варианты — из слов
  предложения, позиции `(chapter_index + k) % 4`), exercises (≤3 vocab из
  карточек payload, ≤3 grammar из заметок, 2 translation, 1 gap, 2 mc,
  1 tf, 1 free); сценарии `s4_gap_missing_word` (answer
  «ZzblqAbsentWord»), `s4_mc_two_correct`, `s4_mc_dup_option`,
  `s4_entity_ghost` (entities [«QwzxwGhost»]), `s4_vocab_ghost`
  (source_ref id «ghost_lemma_x»).
- **Измерено (evidence S4, `docs/CONTROL/evidence/S4/`, 9 файлов):**
  пустая + image-главы — 0 summary, 2/2 пропусков зафиксированы с причиной
  (verbatim); 0 gap с чужим словом (инъекция отклонена с причиной); 0 mc с
  2 правильными / с дублем (2 сценария verbatim); 0 выпущенных с
  отсутствующей сущностью (инъекция QwzxwGhost); 0 полей answer/correct в
  теле (300 вопросов + 169 упражнений), answers.json 1:1 (300/300,
  169/169); 100% vocab из выпущенных карточек (29/29,
  `vocab_from_cards`=1.0, ghost отклонён); 5/5 языков со всеми тремя
  подстадиями (en/de/fr/it/nl: 15 вопросов / 8–11 упражнений) + гейт
  уровней (Minimal/Balanced — без s4, Learning — только вопросы); mc-100:
  позиции 26/26/24/24 (топ 26%), «самый длинный» 38/100 < 60%.
- Тесты: 21 запуск в `tests/test_s4_study.py` (T-S4-1…T-S4-9; T-S4-7 —
  ×5 языков + гейт уровней; T-S4-8 — глава-изображение; T-S4-9 —
  распределение 100 mc). Evidence-скрипт `tests/s4_evidence.py`
  (9 verbatim-файлов, 15 прогонов). Полный прогон 244/244 (223 + 21),
  ruff 0, mypy 0 (64 файла).
- Дефекты этапа: не обнаружено. Честные пределы механизма (в CONTROL S4):
  LLM — mock (BLOCKER-1); фактчекинг summary — за LLM; «правильный не
  самый длинный» гейтится как РАСПРЕДЕЛЕНИЕ по книге (топ-позиция < 60%,
  «самый длинный» < 60% — пороги избраны, в ТЗ их нет); grammar-ветка
  сквозного прогона без заметок (кандидатов 0 на S4-фикстурах — покрывается
  unit); free-ответы — семантическая проверка за LLM (детерминированно —
  только наличие сущностей в главе).

## Сделано в S3

- **`app/learning/grammar.py` (новое):** `load_grammar_golden`
  (`grammar_golden.yaml` пакета: text + expected ids; отсутствует/пуст —
  пустой набор, как mwe_golden S2; `unit` — ошибка), `reference_explanation`
  (детерминированная ссылка S3.4, hex-id segment в текст не встраивается —
  langdetect сдвигает ru-фразу на bg), `check_grammar_notes` — 5-пунктная
  детерминированная валидация (id ∈ таксономии языка S3-1; поверхность
  среди детерминированных кандидатов — S1.5/S2.6; surface в сегменте
  `contains_form` S3-2; пояснение непустое и ≤ `grammar_note_max_chars` —
  превышение отклоняется, а не усекается S3.1/T-S3-10; язык = целевой
  (langdetect, seed из конфига) S3-5).
- **`app/languages/phenomena.py` (расширение, D66):** поле `stages`
  таксономии (обязательно ВМЕСТЕ с `patterns`; `for_stage(phenomena,
  "s3")` — скоп подстадии, движок агностичен к стадиям, Rule 8); новый тип
  шаблона `token` (`TokenCriterion`: И полей `pos`/`text`/`mood`/`tense`/
  `aspect`/`position` — морфология spaCy, TЗ_03 S3.5); wrap-маркеры
  расширены mood/tense/aspect; значения морфологии — данные (Rule 1).
- **`app/learning/mwe.py` (расширение):** движок отбора стал общим S2/S3
  (Rule 8): `_morph_value`/`_morph_ok` (универсальные фичи spaCy),
  `_criterion_ok` (шаблон `token`), wrap-маркеры с морфологией; `MweCandidate`
  переиспользуется S3 (unit не ожидается).
- **`app/workers/pipeline.py`:** подстадия `s3_grammar` в `run_learning`
  (`SUBSTAGE_LEVELS["s3"] = {Learning, Intensive}`): `select_mwe_candidates`
  со скопом `for_stage(…, "s3")` → `_s3_split_candidates` (лимит ≤K на главу,
  топ-K по (−вхождения, id, поверхность), K = `limits.grammar_max_notes_per_chapter`)
  → `_s3_explain_once` (первый кандидат явления — развёрнуто, остальные —
  ссылки) → LLM `grammar_note` (батчи по главам `grammar_llm_batch=25`) →
  `check_grammar_notes` → дедупликация по surface → `_s3_build_notes`
  (развёрнутая отклонена — ссылающиеся отклоняются) →
  `grammar_notes.json` + секция `s3` в `quality.json` (per_chapter,
  detailed/reference, not_selected, rejected с причинами) +
  `record_stage_result("s3_grammar", …)`.
- **`app/llm/tasks.py`:** задача `grammar_note` (`pair_required=false`,
  JSON-схема surface/phenomenon_id/explanation; лимит длины — payload
  `max_explanation_chars`).
- **Данные:** `config/languages/*/grammar_golden.yaml` (en 20, de 21,
  nl 31, fr 20, it 21 предложений; 95 пар «предложение → id», 100% покрытие
  s3-таксономий); `phenomena.yaml` — `stages: [s3]` у грамматических явлений
  + шаблоны (`token`/`wrap` с морфологией; DE-NEBENSATZ-VERBLETZT —
  dass/weil/wenn/als/ob/obwohl → VERB/AUX `end_final`); конфиг
  `limits.grammar_max_notes_per_chapter` (10), `limits.grammar_note_max_chars`
  (300), `limits.grammar_llm_batch` (25) + `config.example.yaml`.
- **Промпты:** `prompts/grammar_note/v1.yaml` (generic).
- **Mock:** сценарии `grammar_bad_id` (ZZ-S3-FAKE вне таксономии),
  `grammar_bad_quote` (surface 'Qwzxw' вне кандидатов).
- **Измерено (evidence S3, `docs/CONTROL/evidence/S3/`, 7 файлов):** 0
  принятых с id вне таксономии (инъекция отклонена, verbatim); 0 цитат вне
  сегмента (инъекция + unit-проверка); 16 кандидатов → 10 выпущенных (ch1: 10
  ≤ K=10, 6 not_selected verbatim); 6 detailed + 4 reference (ссылки
  `model=''`), повторных развёрнутых 0; 10/10 пояснений ru conf=1.00;
  recall golden 1.000 ×5 (de 21/21, nl 31/31, en 22/22, fr 20/20, it 21/21)
  ≥ 0.75 (precision справочно 1.000/1.000/0.786/1.000/0.913); 5/5 языков до
  LEARNING (en 303/16/10, de 155/3/3, fr 43/1/1, it 35/1/1, nl 67/3/3) +
  гейт уровней (Minimal/Balanced — без s3).
- Тесты: 21 запуск в `tests/test_s3_grammar.py` (T-S3-1…T-S3-10; T-S3-8 —
  de придаточное DE-NEBENSATZ-VERBLETZT; T-S3-9 — it IT-CONGIUNTIVO + fr
  FR-SUBJONCTIF; T-S3-10 — превышение лимита отклоняется). Evidence-скрипт
  `tests/s3_evidence.py` (7 verbatim-файлов). Полный прогон 223/223 (202 +
  21), ruff 0, mypy 0 (63 файла).
- Дефекты этапа: не обнаружено. Честные пределы механизма (в CONTROL S3):
  DE-NEBENSATZ-VERBLETZT — только придаточное в конце предложения
  (`end_final`); приоритет по уровню пользователя не реализован (CEFR-уровень
  грамматических явлений в таксономии не заведён — приоритет =
  повторяемость); EN-PERFECT ⊂ modal-perfect/cond-3 — дополнительные
  кандидаты (precision справочно); it CONGIUNTIVO — по Mood=Subj, fr
  SUBJONCTIF — по словоформам (fr-теггер без морфологии); LLM — mock
  (BLOCKER-1).

## Сделано в S2 (устойчивые выражения MWE, ТЗ_03 S2)

- **`app/learning/mwe.py` (новое, D65):** детерминированный предвыбор:
  `mwe_hits` (по E6-парам, в порядке alignment-map) →
  `select_mwe_candidates` (агрегация: separable — по (id, unit_lemma),
  остальные — по (id, нормализованный surface); повторное вхождение —
  существующий кандидат; детерминированный порядок (−occurrences, id,
  surface)); surface берётся из исходного текста по символьным позициям
  (fr-клише «n'est»/«s'appelle» — как в книге). Таксономические шаблоны —
  ДАННЫЕ `config/languages/<code>/phenomena.yaml` (поле `patterns`,
  валидация в `app/languages/phenomena.py`): 3 механизма — `sequence`
  (пос-последовательность 2–3), `wrap` (два маркера в одном предложении),
  `separable_units` (L1 `lexical_units`, Rule 8); `min_occurrences` —
  данные, жёсткий фильтр. Явления без `patterns` детерминированно не
  выбираются (честный предел: тип «идиом»). LLM — задача `mwe_note`
  (батчи ≤25, НИКОГДА не вся книга), модель только поясняет (номинации
  запрещены, ТЗ S2.3). Валидация 5 точек `check_mwe_records` (id в
  таксономии; surface среди детерминированных кандидатов; все части в
  сегменте `contains_form`; separable — обе части; каноническая форма
  непустая). Отклонённые → `mwe_records.json` → `rejected` + warning.
  `quality.json` → секция `s2`. Гейт уровня: S2 только Learning/Intensive
  (`SUBSTAGE_LEVELS`).
- **Промпт** `prompts/mwe_note/v1.yaml`; **golden-наборы**
  `config/languages/*/mwe_golden.yaml` (de/nl по 30, fr 14, en 12, it 12
  предложений; 84 пары) над предвыбором; сравнение по phenomenon id
  (unit_lemma — справочно).
- **Измерено (evidence S2, 6 файлов):** 0 принятых с id вне таксономии
  (инъекция `mwe_bad_id` отклонена, verbatim); recall separable de 1.000 /
  nl 1.000 (golden-30, ≥0.85), 0 записей без второй части; 0 частей вне
  сегмента (7/7 verbatim, «ghostword» отклонён); precision 1.000 на всех
  5 языках (84 пары, 0 ложных); воспроизводимость 100%; 5/5 языков до
  LEARNING с метриками.
- Тесты: 17 запусков `tests/test_s2_mwe.py` (T-S2-1…7 + ×5 языков);
  полный прогон 202/202 (185 + 17), ruff 0, mypy 0.
- Дефекты этапа: не обнаружено. Честные пределы: нет синтаксического
  парсера (POS-последовательности 2–3, wrap, lexical_units —
  «Er trifft eine Entscheidung» (VERB DET NOUN) шаблонами не покрывается);
  unit_lemma справочно (nl 20/30); идиомы без `patterns` не детектятся
  детерминированно; LLM — mock (BLOCKER-1).

## Сделано в S1 (отбор лексики, ТЗ_03 S1)

- **`app/learning/cards.py` + `selection.py` (новые):** двухступенчатый
  отбор (перестановка запрещена): детерминированная ступень — E6-пары →
  spaCy → stop-слова → POS-фильтр `pos_select` (pack.yaml, ДАННЫЕ) →
  агрегация по лемме на всю книгу (S1.2) → Zipf-полоса (wordfreq) + CEFR →
  предрейтинг; LLM-ступень — задача `vocabulary_card` (батчи ≤25, НИКОГДА
  не вся книга). Ограниченная валидация 5 точек КАЖДОЙ карты (форма
  словоформы в сегменте `contains_form`; лемма сопоставима со словоформой
  через spaCy; `card_schema.json` языка + плейсхолдеры (Rule 5); пример из
  книги или `example_generated`; лемма среди детерминированных
  кандидатов («призраки» отклоняются)). Отклонения — с причинами
  (`rejected` + warning). Плотность: `max_cards = floor(N·W/1000)`,
  N=15.0 (config), фактическая плотность меряется. Карта привязана к
  источнику и версии (глава/сегмент/hash/prompt/model/glossary_hash/
  timestamp). CEFR-диапазон — не абсолютный фильтр: вне диапазона — карта
  с `cefr_out_of_range=true` (ярко). Ключ идемпотентности
  `s1_vocabulary` = sha256(input_hash|glossary_hash|level|cefr) —
  learning-параметры НЕ в config_hash.
- **Измерено (evidence S1, 7 файлов):** 0 выпущенных с формой вне сегмента
  (24/24); 0 дублей лемм; POS-поля языков 100% в enum (de articles 27/27,
  nl 22/22); cap=27, выпущено 24, density_actual=13.17 ≤ 15; плейсхолдер
  отклонён с причиной, 23/23 выпущенных валидны по схеме;
  воспроизводимость 100% (2 прогона), «призрак» отклонён; примеры из
  книги 0.9583 ≥ 0.95; 5/5 языков до LEARNING с метриками.
- Тесты: 14 запусков `tests/test_s1_vocabulary.py` (T-S1-1…10, T-S1-7 ×5);
  полный прогон 185/185 (171 + 14), ruff 0, mypy 0.
- Дефекты этапа: не обнаружено. Честные пределы: LLM — mock (BLOCKER-1);
  CEFR — все `derived` (L1.4), уровень — частотная полоса, не «истина»;
  снапшот wordfreq ~2021; книги <~67 слов → cap=0 (семантика «не более
  N на 1000 слов»).

## Сделано в L3 (правка перевода по глоссарию, ТЗ_02 L3)

- **`app/glossary/fix.py` (новое):** отдельная стадия `GLOSSARY_FIX` после
  `validating_output` (15 статусов; T-E3-2 49/176). Всегда присутствует
  (L3.10): `fix_enabled=false` — no-op с отчётом, детект (L2) не
  отключается. На входе — свежее измерение ТЕКУЩЕГО `target.epub`
  (L3.5 идемпотентность: повтор находит 0 расхождений, устаревший L2-
  отчёт не доверяется). Исправляемое множество — 3 условия (L3.1): термин
  в исходном сегменте; ни одной формы верифицированного парадигма нет в
  переводе; парадигма `verified` (unverified — только отчёт, L3.9).
  Порог `fix_max_segment_share` (0.5, строго >) проверяется ДО LLM-вызовов:
  превышение → `AppError` «L3-6» → FAILED с отчётом, книга не
  переписывается (L3-6). Правка — патч блока по E6-XPath (подстроки
  запрещены); ответ LLM (задача `glossary_fix`) — валидация 5 точек
  (форма парадигма в переводе; Δпредложений ≤ `fix_sentence_delta`;
  сигнатура разметки сохранена; язык ≠ исходный; непусто и ≠ исходник).
  Недействительный — отклонён со списком точек, retry нет. Каждое
  применение — JSON-строка в `quality.log`. Перед пересборкой —
  стабильный бэкап `target.epub.l3bak` (до `.partial`); пересборка —
  общий `atomic_finalize`; после правок — рекомпиляция (D61):
  E6-аллайнер, `check_consistency`, QC, EPUBCheck, `quality.json`
  перезаписан. `quality.json` → секция `l3` (fixable_segment_share,
  attempted, applied, rejected, editable_share_after).
- **Измерено (evidence L3, 10 файлов):** 100% исправляемых исправлено
  (3/3 и 20/20, вкл. 3 разные неверные вариант одного имени;
  editable_share 0.0→1.0); 0 правок без записи quality.log (3/3, 20/20);
  applied только при `points==[]` (модель без терма — 3 rejected,
  `points=[1]`, retry нет); 0 потери разметки (сигнатура, ссылка+сноска
  сохранены); повтор — 0/0/0, файл байт-в-байт тот же; превышение 6/11
  (54.5%>50%) — стоп ДО LLM (0 вызовов), книга не изменена; пересборка
  EPUBCheck 0 ERROR (mimetype STORED первым); kill -9 ×10 в окне
  `.partial` — 0 битых, l3bak цел, после истечения аренды W3 → COMPLETED
  с эталонным sha; unverified — 0 автоправок, 100% в отдельном списке;
  5/5 языков; nofix — no-op, детект работает. T-L3-15: diff
  бэкап/результат = ровно список из quality.log (3 блока).
- Тесты: 14 запусков `tests/test_l3_glossary_fix.py` (T-L3-1…15,
  T-L3-13/14 в составе); полный прогон 171/171 (157 + 14), ruff 0,
  mypy 0 (57 файлов).
- Дефекты этапа: не обнаружено. Честные пределы: реальный LLM не
  верифицирован (BLOCKER-1, mock rot3 + инъекции); семантическая
  корректность не проверяется (ТЗ проверяет ФОРМУ, LLM — не арбитр);
  kill-ассерт — контентный (zip-метаданные движком не сохраняются).

## Сделано в L2

- **`app/domain/glossary.py` (новое):** `GlossaryEntry` (термин, перевод, часть
  речи, `segment_id` первого вхождения, число вхождений, примечание,
  `approved=False`, `source_forms`, `paradigm`/`paradigm_status`/`paradigm_reason`),
  `Paradigm` (inflected, forms, coincidences), `Glossary` (terms + `by_term`),
  `BookStyle` (style/region/note), статусы `PARADIGM_{VERIFIED,UNVERIFIED,NOT_APPLICABLE}`,
  `check_paradigm` (D56: число форм = числу имён, нет пустых, каждое совпадение
  ИМЁН форм объяснено, общая основа ≥1), `GlossaryError` (fail-closed, D55).
- **`app/glossary/candidates.py` (новое, D54):** `extract_candidates(book, pack,
  nlp, settings)` — 5 правил, `RULE_PRIORITY = (ner, multiword, abbreviation,
  unit, capitalized)`: NER — токены с `ent_type` (NER-токены пропускаются
  capitalized-правилом — вхождение считается один раз); многословные — n-граммы
  2..`ngram_max`, `occurrences ≥ min_occurrences`; аббревиатуры/единицы — данные
  `pack.yaml` (`units`); заглавные вне начала предложения — только если
  `pack.capitalized_candidates` (de — false); дедуп NFC+casefold, каноническая
  поверхность — самая частотная (тайбрейк — первая встреча). Детерминированно
  (L2-1: 13/13, побайтово).
- **`app/glossary/annotation.py` (новое, D55):** `annotate_candidates` — задача
  `glossary_annotation` (батчи ≤25, `pair_required=false`), JSON-массив на вход,
  отсутствующий/некорректный элемент → `GlossaryError`; `_annotation_paradigm` —
  парадигма вместе с переводом (треб. 4): `inflected=false` → `not_applicable`,
  `forms=()` (T-L2-11); `_indeclinable_paradigm` — выделенный путь.
- **`app/glossary/consistency.py` (новое, D57):** `check_consistency(glossary,
  alignment)` — presence-based по спаренным сегментам E6: термин «в сегменте» =
  любой `source_form` (NFC+casefold, граница слова); согласовано = в переводе
  есть канонический перевод или форма ПРОВЕРЕННОЙ парадигмы; `ConsistencyReport`
  (terms: варианты casefold, mismatch_segments; mismatches: term/segment_id/
  тексты; `share`; `to_dict`); L2 текст НЕ правит.
- **`app/glossary/store.py` (новое, D53):** `BookStore(books_dir)` —
  `books/<book_key>/<source_hash>/` (glossary.yaml, style.yaml, meta.yaml
  current/superseded, mismatches/); `book_key_of(path) = Path(path).name`;
  атомарная запись (врем. файл + `Path.replace`); чтение глоссария — строго по
  хэшу версии; `save_mismatches`.
- **`app/glossary/service.py` (новое):** `ensure_book_glossary(settings, repo,
  job, *, book, source_pack, target_pack)` — загрузить из store (по хэшу) ИЛИ
  построить: кандидаты → аннотация (LLM) → сохранение (глоссарий + стиль);
  `_determine_style` — задача `book_style` один раз на версию (D58); глоссарий
  отключён (`glossary.enabled=false`, D60) → None без артефактов.
- **`app/workers/pipeline.py`:** `_book_glossary_bundle` (лениво, по версии
  книги) в `run_translating` → `glossary_provider` (E5-механизм, без изменений
  E5) — `build_glossary_prompt(prefix=<стилевой профиль>)`; `run_validating_output`
  — `check_consistency` по отчёту E6 → `glossary_consistency.json` (report:
  terms + mismatches с текстами сегментов) + секция `quality.json`
  (`glossary_consistency`) + `save_mismatches` + предупреждение «L2:
  согласованность терминов ниже порога: …» при share < `consistency_min_share`
  (D57); выделенный `_glossary_consistency` (PLR0915).
- **Данные:** `config/languages/ru/pack.yaml` — `inflected: true` +
  `paradigm_forms.{noun,proper_noun}` = 12 имён форм (6 падежей × ед./мн. ч.);
  de — `capitalized_candidates: false` (явно), en/fr/it/nl — true;
  `config/languages/ru/inflection_golden.yaml` — 5 имён × 12 форм + 6
  падежных предложений (nom_sg==acc_sg, nom_pl==acc_pl — с объяснениями);
  промпты: `prompts/glossary_annotation/v1.yaml`, `prompts/book_style/v1.yaml`
  (generic, `pair_required=false`); `GlossarySection` в `app/core/config.py`
  (enabled=false по умолчанию — D60; min_occurrences=2, ngram 2..4,
  max_term_chars=80, units, style_sample_chars=5000, annotation_batch_size=25,
  consistency_min_share=0.95); `config.example.yaml` — `glossary.enabled: false`
  (локальный `config.yaml` — true, в git не попадает).
- **LLM-задачи/адаптер (расширение E4):** `glossary_annotation` и `book_style`
  в `app/llm/tasks.py` (pair_required=false); mock: ответы аннотации = эхо
  кандидатов (translation = rot3(термин); парадигма — rot3 + суффиксы
  `_MOCK_PARADIGM_SUFFIXES`, 12 различимых форм → verified; sentinel
  `badparadigm` — 12 идентичных форм без объяснений → unverified; sentinel
  `indeclinable` — inflected=false → not_applicable), `book_style` →
  `{"style": "neutral", "region": null, ...}`; НОВЫЙ сценарий
  `glossary_inconsistent` — в переводе rot3(Wexley) заменён на «Xrongy» (в
  translate-ответе И в fill-ответе — финальный текст mock-мира идёт через
  fill-путь, см. дефекты ниже).
- **NER включён (D59):** `app/languages/nlp.py` — `disable=["parser"]` (ner
  смодели есть, загрузка без exclude).
- **Измерено (evidence L2, `docs/CONTROL/evidence/L2/`, 8 файлов):** кандидаты
  13/13 идентичны; de: 0 capitalized (контрфакт — 2: 'Fluss'×4, 'Mühle'×3);
  внедрённая несогласованность 3/3 (share 0.0, «Xrongy»×3 / «Zhaohb»×0 в
  target.epub, хэши источника и target.epub на границах этапов — verbatim);
  complex en→ru 46/46 = 100.0% (13 терминов); усечение 4/13 (лимит 200,
  prompt 186), предупреждение verbatim; парадигмы 9/10 = 90.0% (Badparadigm —
  unverified с причиной verbatim, Indeclinable — not_applicable); golden-30:
  0/30 с парадигмой / 20/30 без (контроль); marlow 20× → 20/20, 1 вариант;
  5 языков → ru: 5/5.
- Тесты: 11 запусков в `tests/test_l2_glossary.py` (T-L2-1…11); evidence-скрипт
  `tests/l2_evidence.py` (8 verbatim-файлов). Полный прогон 154/154 (143 + 11),
  ruff 0, mypy 0 (54 файла).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/L2.md`): **финальный текст
  mock-мира — FILL-путь** (code() сдвигает и буквы XML-тегов → translate-ответ
  структурно невалиден, движок переключается на fill) — инъекция сценария
  добавлена и в `_fill_response`; **stale job-объект**: `run_validating` пишет
  `source_language` в БД, объект в памяти устаревает → без подтверждения
  `AppError «подтверждённый исходный язык не задан»` (фикс: `repo.require`
  после блока подтверждения — в тесте; в воркере require на каждом этапе уже
  есть, D40); **`load_settings` игнорировал `llm.prompts_root` из YAML** (поле было
  в `LLMSection`, но загрузчик не передавал его — ключ молча не действовал, Rule 1/6)
  — **исправлено 2026-09-13 (D62)**: ключ читается (относительный путь — от
  `storage.root`, абсолютный — как есть), явно заданный несуществующий корень →
  `ConfigurationError` на старте; тестовый хелпер L2 — `prompts_root` во временный
  каталог (copytree в storage убран); **NER sm-шум**: `fox`→ORG, `first`,
  `Second` в complex.epub — ложные
  сущности (детерминированность не страдает; отсев — за LLM-аннотацией);
  **NER пропускает имена в начале предложения** (sentencizer + «; ») и редкие
  выдуманные имена — в тестах имена стоят в середине предложения и частые
  (каждое вхождение гарантировано capitalized-правилом); **variant-списки
  casefold** (сравнение golden — по casefold-ожидаемым).

## Сделано в L1

- **`app/languages/nlp.py` (D46, D47):** `_NlpHolder` — один пайплайн на модель на
  процесс (класс-держатель, без `global`); `load_nlp` (загрузка `spacy.load(model,
  disable=["parser","ner"])` + добавление `sentencizer` без него `doc.sents` = E030);
  `nlp_report` — секция `nlp` отчёта задания (модель, версии через
  `importlib.metadata` — без загрузки пайплайна, D50; disabled-компоненты,
  sentence_split); `lemma_of` (один токен); `lexical_units` — отделяемые глаголы
  de/nl: голова = VERB + правило финитности (ранее в предложении другой VERB без
  CCONJ/SCONJ/PUNCT — не финитный; nl-модель размечает «vroeg» как VERB «vragen» —
  без правила ложная «opvragen»), приставка из `separable_prefixes` (данные:
  de 21, nl 18), запрет именных constituents между глаголом и приставкой,
  приставка-предлог с именным дополнением — не единица; окно
  `sentence_split` (min_chars/max_chars из pack.yaml) — D46.
- **`app/languages/phenomena.py` (L1.5/L1-6):** `load_phenomena` (`phenomena.yaml`
  каталога пакета; отсутствует/пуст — отсутствующий ресурс, резолвер перечислит;
  невалидный id/dup/пустые name/description → `ConfigurationError`),
  `PhenomenaTaxonomy` (`.validate` → (валидные, невалидные)), `validate_golden`
  (чужой id — ошибка; пустая таксономия — ошибка, а не молчание, Rule 6).
- **`app/languages/cards.py` (L1.6, D51):** `load_card_schema` (JSON draft 2020-12,
  `if/then` по части речи; невалидная схема → `ConfigurationError`), `validate_card`
  (jsonschema + отдельная проверка плейсхолдеров Rule 5: `—`/`n/a`/пустая строка —
  отклонение; JSON `null` — легитимно для nullable-полей), `issue_cards` →
  (выпущенные, отклонённые с причинами) — счётчик QC.
- **`app/languages/cefr.py` (L1.4, D45):** `CefrSource` (`lexicon:<имя>` —
  `cefr/<имя>.json` маппинг слово→уровень; файл отсутствует → derived без исключения
  (T-L1-12); невалидный JSON/уровень → `ConfigurationError`), `level_for`
  (lexicon-hit → (уровень, источник, estimated=False); miss → уровень не
  устанавливается; derived → (None, derived, True)), `label` — ЕДИНАЯ подпись
  (L1-8): derived → `Estimated CEFR level`; lexicon → `CEFR level (<источник>):
  <уровень|не установлен>`. UI (U1) читает только её.
- **`app/languages/frequency.py` (L1.3, D48, D52):** `normalize_text` (NFC/NFD/NFKC/
  NFKD + casefold + схлопывание — данные `pack.yaml: normalization`), `zipf_for`
  (wordfreq 3.1.1: `zipf_frequency(word.casefold(), lang)`; wordlist-параметр снят,
  `frequency_of_word` удалён — измерено), `band_for`/`frequency_band` — полосы
  только из данных (`BandSpec`), 0 числовых порогов в коде (L1-9, проверено тестом),
  `band_report` — справочное представление.
- **`app/languages/registry.py` (расширение):** `NormalizationSpec`,
  `SentenceSplitSpec` (max ≥ min), `BandSpec` (≥2 полосы, null-max — не более одной
  и только последней, границы строго растут; YAML-маппинг {A1: 9.23, …} —
  before-валидатор); `PackSpec` + `card_schema`, `normalization`, `sentence_split`,
  `prompt_tasks` (валидация по реестру задач, D49), `pos_select` (universal tags),
  `frequency_bands`, `separable_prefixes` (isalpha — ü в «zurück»); `GoldenEntry` +
  `phenomena`; `LemmaEntry` + `load_lemma_reference` (отсутствует/пуст — ресурс,
  не ошибка).
- **`app/languages/resolver.py` (расширение, D49):** `_l1_resource_deficits` —
  мягкие: нет таксономии/схемы карточки/эталонов лемматизации/частотных полос,
  CEFR-лексикон не найден (только при `cefr_source: lexicon:` — переход в derived
  зафиксирован в перечне, T-L1-12); golden без размеченных явлений — отдельное
  сообщение (L1/T-L1-10); `prompt_tasks` пакета источника — пространство имён
  промптов (нет пакета/список пуст — все задачи, поведение L0 без изменения).
- **`app/workers/pipeline.py`:** `_nlp_section` — секция `nlp` в `validation.json`
  (по accepted language; конфликт — None) и `plan.json` (по подтверждённому
  источнику) — `nlp_report`, без загрузки пайплайна (D50).
- **Данные (новые/перезаписанные, `config/languages/{en,de,fr,it,nl}/`):**
  `phenomena.yaml` (en 11, de 12, nl 9, fr 9, it 9 id — id вида XX-NAME,
  kind grammar/mwe/morphology, name/description — данные для S1-промптов);
  `card_schema.json` (draft 2020-12, additionalProperties false, `if/then`:
  de noun → article der/die/das + plural; de verb → auxiliary + main_forms +
  separable; nl noun → article de/het + plural + diminutive (nullable); nl verb →
  auxiliary + participle + separable; fr noun → gender; fr verb →
  conjugation_group 1-4/irregular + auxiliary avoir/etre + irregular_forms
  (nullable); it noun → gender + plural; it verb → group 1-3/irregular + auxiliary
  avere/essere + irregular_forms; en verb → past_tense/past_participle (nullable —
  регулярный глагол)); `pack.yaml` (per-языковые `card_schema`, `normalization`,
  `sentence_split` 10–400, `prompt_tasks` ×3, `pos_select` [NOUN,VERB,ADJ],
  `frequency_bands` A1≤9.23/A2≤10.64/B1≤11.97/B2≤13.29/C1≤15.29/C2 null —
  Zipf-шкала `1+log2(rank)`, de `separable_prefixes` 21, nl 18); `golden.yaml`
  (перезаписаны: по 20 предложений с размеченными явлениями, 100% покрытие
  таксономии, de-1-е = «Er steht früh auf, bevor die Arbeit beginnt.» (DE-TRENNBAR),
  nl-1-е = «Hij staat vroeg op om te joggen.» (NL-SCHEIDBAAR)); `lemma_reference.yaml`
  (по 50 словоформ: 47 проходимых + 3 известных слабых формы — эталон = стандартная
  грамматика, не вывод модели). `ru/pack.yaml` без изменений (дефолты новых полей).
- **`docs/LIMITATIONS.md`:** CEFR-аудит 2026-09-13 — таблица 5/5 (en/de/fr/nl/it —
  ресурс/лицензия/«нет»/`derived`), все 5 языков `derived` (D45); wordfreq 3.1.1 API
  (wordlist снят, `frequency_of_word` удалён); NLP: 5/5 моделей, 94%×5 лемматизация,
  слабые формы verbatim, parser/ner отключены.
- **Измерено (evidence L1, `docs/CONTROL/evidence/L1/`, 12 файлов):** 5/5 пайплайнов
  (5×3.8.0, spaCy 3.8.16, one-per-process); 47/50 (94%) ×5; de без article/plural → 0
  выпущенных (причины verbatim); nl без de/het → 0, «der» → 0, diminutive=null → 1;
  fr/it без auxiliary/group → 0; таксономии 11/12/9/9/9, чужой id → 0 принятых;
  CEFR-таблица 5/5 verbatim; 5× `Estimated CEFR level`; границы из данных, в коде 0;
  golden 20×5, покрытие 100%, чужой id отклонён verbatim; «Er steht früh auf» →
  aufstehen (1), «Hij staat vroeg op» → opstaan (1), FP-предлоги → 0; удаление
  lexicon → derived, метка `Estimated CEFR level`, 0 падений, резолвер перечисляет
  «CEFR-лексикон не найден: xx/cefr/testlex.json».
- Тесты: 27 запусков в `tests/test_l1_packages.py` (T-L1-1…12, параметризация
  5 языков); evidence-скрипт `tests/l1_evidence.py` (12 verbatim-файлов).
  Полный прогон 143/143 (116 + 27), ruff 0, mypy 0 (47 файлов).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/L1.md`): `doc.sents` без
  сегментатора → E030 (добавлен sentencizer, D46); sentencizer не принимает
  `max_len` (confection — D46: окно обработки вместо конфига); nl-модель
  размечает «vroeg» как VERB «vragen» → ложная единица «opvragen» (правило
  финитности головы, D47; первая попытка — «последний глагол» — неверна, исключала
  истинную голову); `_PREFIX_RE [a-z]+` не совпадал с «zurück» (ü) → isalpha;
  wordfreq 3.1.1 API (D48); CEFR-check резолвера срабатывал бы на `derived` (защита
  `startswith("lexicon:")`); en-эталон 49 записей (добавлен rivers→river).

## Сделано в L0

- **`app/languages/registry.py`:** `PackSpec` (спаци-модель, источник частотности,
  CEFR-диапазон, фикстура, алиасы), `load_registry`/`load_pack`/`load_golden`/
  `aliases_map` — данные `config/languages/<code>/pack.yaml` + `golden.yaml` (Rule 1).
- **`app/languages/detect.py` (D7/D41):** `langdetect 1.0.9` с фикс. сидом (seed=42):
  `_FactoryHolder` — единый фабрик процесса (профили один раз, `set_seed` на вызов),
  `detect_text` (пустой/неопределяемый → None, Rule 6), `decide()` — правила L0.5
  (совпадение → ACCEPTED; конфликт или неуверенность → NEEDS_CONFIRMATION; без dc —
  решение детектора при уверенности), `detect_book`, `normalize_code` (алиасы,
  под-теги), многоязычность L0.6 (D44: доля = помечено/ВСЕ сегменты, сегменты
  < `segment_min_chars` не детектируются, пометки — только при превышении порога).
- **`app/languages/resolver.py` (D38):** `PairResolver` — статус пары по фактическим
  ресурсам; unsupported (задание не создаётся) = нет пакета источника ∨ нет
  spaCy-модели источника ∨ нет пакета целевого языка (расширение); degraded
  (задание разрешено) = нет частотности/фикстуры/golden/промптов пары (ТЗ буквально);
  `missing` — точный перечень (L0.3), у pair_required-задач — пометка E4.3;
  шов `spacy_available` (L0-4 — симуляция удаления модели без деструктивных действий);
  `load_matrix` — неверная матрица → `ConfigurationError` (Rule 6).
- **`app/services/languages.py` (L0-5):** `pair_status`/`matrix_report` — единая точка
  для UI/API: статусы только из резолвера.
- **Данные:** `config/languages/` — 6 пакетов (en/de/fr/it/nl/ru) + 5 golden-наборов;
  `config/language_matrix.yaml` — 11 пар (5×→ru supported; 5 кросс-пар degraded —
  нет pair-промптов vocabulary/grammar; en→ja unsupported — демонстрация);
  промпты: +6 glossary-пар (`{de,fr,it,nl}/en`, `de/it`, `en/de` — со E4).
- **Интеграция пайплайна:** `run_validating` — детект (до LLM, E2.5) +
  `jobs.source_language` (колонка, миграция) + перепроверка пары (unsupported →
  AppError → FAILED с перечнем); `run_planning` — без подтверждённого языка AppError
  (fail closed) и перечитывание свежей строки (D40); `create_job` — проверка пары по
  `dc:language` ДО создания (L0.7); `confirm_job(source_language=…)` — выбор
  пользователя (D43); `read_dc_language` (reader, без распаковки); причина
  NEEDS_CONFIRMATION воркера — из `validation.json` (D42, Rule 8).
- **Фикстуры:** `conflict.epub` (немецкий текст, `dc:language=en`, 2 главы — без
  подозрительности), `bilingual.epub` (en h1+3p + de h1+2p, `dc:language=en`).
- **Конфиг:** `LanguagesSection` (`languages.root`/`matrix`, дефолты — корень
  репозитория: production-конфиг не менялся), `LanguageDetectionSection` расширен
  (seed, multilingual_share=0.2, segment_min_chars=40); `config.example.yaml`
  обновлён. Зависимости: `langdetect>=1.0.9`, `spacy>=3.8.16`, `wordfreq>=3.1.1`
  (+стансы `stubs/langdetect/` — D41; per-file-ignores ruff для `stubs/**`).
- **Тестовые среды (D39):** `make_yaml` по умолчанию кладёт реестр/матрицу
  репозитория; T-E5-10 — `server.lease_seconds=120` (heartbeat=60 по D18): с 30-с
  арендой one-shot-воркер брал «зомби» вместо текущего задания (измерено до релиза).
- **Измерено (evidence L0, `docs/CONTROL/evidence/L0/`):** 5/5 фикстур conf=1.0;
  конфликт 3/3 (100%) с причиной verbatim; 6/6 degraded/unsupported с перечнем;
  шов без spaCy → 11/11 unsupported, 0 падений; UI==резолвер diff 0 ×11;
  supported=5 (все →ru); T-L0-7: confirm('de') → PLANNING + план; T-L0-8:
  share=0.285714 > 0.2, 2 de-пометки, segment_id существуют.
- Тесты: 21 запуск в `tests/test_l0_languages.py` (T-L0-1…8 + правила `decide()` ×7,
  детерминированность, unsupported `create_job`, деформации `load_matrix` ×6);
  evidence-скрипт `tests/l0_evidence.py` (8 verbatim-файлов).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/L0.md`): `decide()` — кортеж причины
  был строкой (хвостовая запятая; смоук); «нет пакета целевого языка» не попадал в
  `missing` (поймано новым тестом L0.7); E5-10 лиза (D39); API langdetect 1.x
  (`.lang`/`.prob`, нет `.name`) и `--no-implicit-reexport` стансов (D41).

## Сделано в E6

- **`app/epub/aligner.py`** (новое, ядро E6 + общее с QC, Rule 8 / D32): пары
  `(segment_id источника, текст перевода)`. Механизм — по разметке E1 §3: оригинал в
  выходе — по `id`, иначе по документному порядку `(тег, оригинальный текст)` с пропуском
  `__translated` (порядковый номер — не единственный критерий, TZ E6.2); перевод —
  sibling / inside (контейнеры) / inplace (REPLACE). Идентичность = (doc, позиционный
  XPath сегментации, нормализованный текст); `segment_id` пересчитывается из выходного
  элемента (текст — БЕЗ вставленных поддеревьев, `original_text`, D33) и сверяется —
  несовпадение → `xpath_mismatch`, пары нет.
- **`text_root` (D34):** корень сегмента движка (E1 §3 п.5 `block_parent`) — для
  blockquote это внутренний `<p>`: тег перевода, маркер `id__translated` и ожидаемая
  позиция (`sibling`/`inside`) берутся с него. Позиция `inside` исключает «уверенные
  неверные пары» со stray-sibling'ом после контейнера.
- **`resolve_positional_xpath`:** парсер реального формата позиционного XPath
  (`/htmlbody[1]p[3]` — без разделителей, корень без индекса) + разрешение на элемент;
  карта хранит ИЗМЕРЕННЫЙ XPath перевода — адресация для L3 (E6-7: 38/38 пар).
- **Причины (TZ E6.4, фиксированный набор):** `missing_translation`, `xpath_mismatch`,
  `empty`, `identical`, `extra_block`; невыровненные — поимённо в
  `alignment_report.json` с `detail`, отбрасывания нет. Честность extra (D37):
  смещённый оригинал — `xpath_mismatch`, не «вставка».
- **Статус и охват (TZ E6.5, D36):** `jobs.alignment_coverage` (колонка, миграция для
  E5-баз) — единый источник (S-стадии/UI читают его, diff 0 с `quality.json` и отчётом);
  ниже `alignment.min_coverage` (YAML, дефолт 0.995) — предупреждение
  «E6: выравнивание ниже порога: …» → `COMPLETED_WITH_WARNINGS` (готовой логикой
  воркера E5). UI-показ — U1.
- **Белый список (TZ E6.3, D35):** `alignment.identical_whitelist_pattern` — regex
  fullmatch по нормализованному переводу (дефолт — цифры/пунктуация); неверный regex →
  `ConfigurationError` при загрузке (fail closed).
- **QC (E5) переиспользует ядро спаривания** (`blocks`/`match_originals`/
  `find_translation` из aligner; `find_translation` по умолчанию `expect="any"` —
  поведение QC не изменилось).
- **Измерено (evidence E6, `docs/CONTROL/evidence/E6/`):** coverage 1.0 ×5 (26/26 +
  3/3×4), identity 0; невыровненные 1/1 с причиной; перестановка 5/5 вариантов
  обнаружена (неверных пар нет; честно: межамена двух no-id переводов одного тега —
  не обнаруживается, E1 «без id — только позиция»); повтор → побитово идентичный
  отчёт (sha256); ниже порога 25/26 = 0.961538 + предупреждение verbatim; 38/38 (100%)
  XPath разрешимы (включая deformed table — td sibling в том же `<tr>`, сноска,
  blockquote inside); REPLACE: 26/26 inplace, источник — исходный файл (T-E6-8).
- **Измеренный факт:** позиционные XPath оригиналов в выходе СДВИГАЮТСЯ вставленными
  sibling'ами — «тот же XPath в выходе» не существует; справка = (doc, src-xpath,
  текст из выхода) (D31).
- Тесты: 10 функций в `tests/test_e6_alignment.py`; evidence-скрипт
  `tests/e6_evidence.py` (воспроизводит 7 verbatim-файлов); полный прогон 95 passed,
  ruff 0, mypy 0 (54 файла).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/E6.md`): контейнерные пары —
  `original_text` (D33); тег/маркер — `text_root` (D34); формат позиционного XPath
  (первая `resolve_positional_xpath` — 0/38); extra/`_is_original` (D37); латентный E5:
  `quality.json["warnings"]` не совпадал со статусом (исправлен: полный набор).

## Сделано в E5

- **Адаптер `app/adapters/epub_engine/`** (D2/D17 снят): `TranslationEngine` (Protocol) +
  `EpubTranslatorEngine` — единственный шов над `epub-translator 0.1.10`; прикладной код
  движок не импортирует. `_make_llms` — кэш LLM-клиентов по модели (каталоги
  `engine_cache/<модель>/`, D15); адаптер НЕ переинтерпретирует исключения движка —
  семантику «тракт T недоступен» (`LLMUnavailableError` с endpoint/model) назначает
  конвейер `run_translating`, кооперативные `WorkerCancelled/WorkerPaused/LeaseLost`
  проходят без поглощения (D23).
- **Режимы (E5.2):** `translation` → `SubmitKind.REPLACE`, `bilingual` →
  `APPEND_BLOCK` (по умолчанию; `APPEND_TEXT` — только явной настройкой
  `translation.submit_mode_bilingual`, дефолт APPEND_BLOCK).
- **Атомарный выход (E5.6):** движок пишет `target.epub.partial` (финальное имя не
  получает); финализация `_finalize`: fsync → внутренняя валидация (`validate_output_epub`
  + `find_broken_links`) → EPUBCheck (реальный jar 5.3.0, classpath-режим, версия в
  отчёте) → бэкап `target.epub.bak-<ts>` перед перезаписью → `os.replace` → fsync каталога.
  EPUBCheck недоступен → `COMPLETED_WITH_WARNINGS` + `VALIDATION_UNAVAILABLE`, результат
  всё же выдан (T-E5-11).
- **QC (E5 п.8, `app/workers/qc.py`, 10 пунктов):** пустые переводы, покрытие
  (поимённая недостача), дублирование пп.5–6 (ожидание = оригиналы+переводы, D25),
  сломанные ссылки (по имени), TOC, метаданные, прогресс (progress_mapping), структура
  (теги/атрибуты Clark + текст; ns-декларации и whitespace-tail не сравниваются, D24;
  известная ресериализация = корневой `lang`), fill-события (все, критические отдельно,
  D26), глоссарий (усечение записано, E5-7).
- **Прогресс в реальных единицах (E5 п.3, D27):** «глава k / N, сегментов A / B (doc)» —
  из последовательности `on_progress` ([TOC][meta]+главы) и кумулятива
  `per_chapter_segments` из `validation.json`; в job.log.
- **Глоссарий (E5 п.5):** `glossary_provider` → `build_glossary_prompt` (лимит
  `limits.glossary_max_user_prompt_chars`, приоритет = число вхождений, детерминизм по
  термину); `user_prompt` движок кладёт в `<rules>` системного сообщения translate (D30);
  усечение → QC-предупреждение с числами.
- **Отмена (D28):** `_finalize_cancelled` не переименовывает — прежний `target.epub`
  (валидный результат) сохраняется, `.partial` = пометка незавершённости.
- **Фикстуры:** `dcterms:modified` добавлено в OPF (EPUBCheck RSC-005 на исходниках);
  комплексная + 4 языковых, эталоны 26/3 сегментов.
- Измерено (evidence E5, `docs/CONTROL/evidence/E5/`): пустые 0×5; покрытие 26/26 +
  3/3×4 (1.0); EPUBCheck v5.3.0 0 ERROR ×5 выходов (исходники 0/0); mimetype первой
  Stored ×5; kill -9 ×10 → 0 повреждённых (target отсутствует, .partial 1018 B);
  fill_garbage: 12 событий (2 критических: TOC + глава 1) = 12 строк errors.log,
  расхождение 0; глоссарий 30→11 (лимит 300, prompt 280) + топ-11 в запросе;
  5/5 книг завершены, sha256 исходников 10/10; сбой LLM → FAILED без выхода;
  без EPUBCheck → COMPLETED_WITH_WARNINGS.
- Тесты: 13 функций (32 запуска) в `tests/test_e5_translation.py`; evidence-скрипт
  `tests/e5_evidence.py` (воспроизводит 8 верbatim-файлов); полный прогон 85 passed,
  ruff 0, mypy 0 (51 файл).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/E5.md`): QC п.6 ложные 24 (D25);
  QC п.7 ложные 65 = 49 nsmap + 16 tail (D24); адаптер поглощал `WorkerCancelled`
  (CANCELLED→FAILED) (D23); RSC-005 в фикстурах (build.py).

## Сделано в E4

- **E4.1 — единый источник LLM-настроек:** `llm.profiles.{translation,fill,analysis}` в
  YAML (`base_url`, `model`, `token_encoding`, `temperature`, `timeout_seconds`,
  `retry_times`, `concurrency`, опц. `input/output_price_per_mtok`); из `.env` убраны
  `LLM_BASE_URL`/`LLM_MODEL`/`LLM_ANALYSIS_*` — остались только секреты
  (`LLM_API_KEY`, `LLM_ANALYSIS_API_KEY`) и адрес бинда. `LLMSection.endpoint(name)` /
  `profile(name)` / `api_key_for(name)` — единственный путь к настройкам (D21).
- **E4.3 — показ профилей:** `app/llm/profiles.py::describe_profiles` (провайдер,
  эндпоинт, модель, профиль, факт ключа) — выводится CLI воркера перед стартом задания.
- **E4.4 — клиент анализа (тракт A):** `app/llm/analysis.py` — httpx, не-streaming
  (`stream:false`), валидация `jsonschema` по схеме задачи (`app/llm/tasks.py`:
  `analysis_vocabulary`, `analysis_grammar`, `analysis_glossary` — pair_required=true),
  повторы ≤ `profile.retry_times`, сырые ответы каждой попытки →
  `jobs/<job_id>/raw/`; невалидно после исчерпания → `AnalysisTaskError`,
  пустой артефакт не создаётся (D19). Зависимости: `jsonschema` + `types-jsonschema` (dev).
- **E4.5 — внешние промпты:** `prompts/<task>/<source>/<target>/vN.yaml` в storage.root
  (корень — `llm.prompts_root`, D20); разрешение пара → язык → generic, версия = max N;
  `pair_required` — без парного промпта `PromptError` до обращения к модели; применённый
  файл+версия+уровень → таблица `resolved_prompts` (E4-2: 100% задач).
  В репозитории: generic v1 ×3 + pair `analysis_glossary/en/de/v1.yaml`.
- **E4.6 — mock:** вид запросов `analysis` (системный промпт с маркером
  `You are an analysis engine`) + сценарии `empty`, `bad_json`, `refuse`;
  не-streaming JSON-ответ при `stream:false` (SSE — для движка, без изменений).
- **E4.7 — токены:** `jobs.tokens_total` (инвариант = сумма профилей; отдельный UPDATE —
  семантика SQLite SET, D22; миграция E3→E4 `ALTER`+бекфилл), разбивка
  `job_tokens(job_id, profile, input, input_cache, output)`; тракт T — из
  `llm.total_tokens/input_tokens/input_cache_tokens/output_tokens` (flush в pipeline);
  тракт A — собственные счётчики из `usage` (учитываются и неудачные попытки).
- **E4.6/E4.7 — стоимость:** `app/llm/cost.py::format_cost` — без цен `Cost: unknown`
  (выдумка запрещена), `job_cost_summary` в отчёте воркера.
- Измерено (evidence E4): 0 LLM-URL/моделей/ключей в `app/` (3 XML-неймспейса
  протокола — исключение, D21); разрешение 3/3=100% записано; pair_required fr→de —
  `PromptError`, 0 записей, 0 LLM-запросов; `bad_json` ×3 → `AnalysisTaskError`,
  0 артефактов, 3 сырых ответа, 1002 токена; 1896+8078+0=9974=`tokens_total`,
  расхождение 0 (разбивка 744+1152 / 6345+1733); 3/3 `Cost: unknown`.
- Тесты: 10 функций (15 запусков) в `tests/test_e4_llm.py` (T-E4-1..8 + резолвер +
  показ профилей); полный прогон 53 passed, ruff 0, mypy 0 (42 файла).

## Сделано в E3

- `app/domain/job.py`: машина состояний (14 статусов), явная таблица `TRANSITIONS`,
  `NEXT_STAGE`, `WORKING_STATES`, `CLAIMABLE_STATES`; resume только в сохранённый `*_from`.
- `app/storage/database.py` + `app/storage/jobs_repo.py`: SQLite WAL (единственный
  писатель — воркер), `PRAGMA busy_timeout`/`foreign_keys`, миграции; атомарные переходы
  (недопустимый → `JobStateError` + запись WARNING), `transitions`-журнал,
  lease/heartbeat, восстановление истёкших арен **только при старте воркера**,
  очередь FIFO+priority, идемпотентные ключи этапов (`stage_results`),
  `request_cancel/pause/resume`, прогресс и токены (flush в `on_progress`).
- `app/workers/worker.py` + `run.py`: цикл claim→stages→done; heartbeat-поток с
  **собственным thread-local подключением** (дефект `check_same_thread` найден и исправлен,
  регрессия — в T-E3-1); кооперативная отмена/пауза на границах групп;
  `target.epub → target.epub.partial` при отмене; job-логи `job.log/errors.log/quality.log`.
- `app/workers/pipeline.py`: стадии validating (ридер, статистика, `suspicious` →
  `NEEDS_CONFIRMATION`) → planning → translating (движок, кэш в `jobs/<id>/engine_cache`) →
  analyzing → generating → validating_output; идемпотентный ключ
  `sha256(input+config+prompt_version+target_language)`, совпадение → пропуск этапа
  (повторный перевод не порождается); персист `fill_failures.json`.
  Прямой импорт движка — временный шов (D17, адаптер в E5).
- `app/services/jobs.py`: `create_job` (стадиальный статус по `suspicious`), `confirm_job`.
- Конфиг: `server.heartbeat_seconds`/`lease_seconds` (валидатор `lease ≤ 2×heartbeat`, D18),
  `Settings.jobs_db_path` (единый источник пути БД).
- Измерено (evidence E3): окно осиротения 0.574 с ≤ 2×heartbeat; 152/152 недопустимых
  переходов отклонены (44 допущено, строк в `transitions` = 44); повтор этапа — 0 запросов,
  хэш артефакта идентичен; retranslation share 0.0000% (X=4970, Y=5004, Z=9974); отмена
  за 0.392 с с пометкой `target.epub.partial`; 1 исполнитель из 2 воркеров.
- Тесты: 7 в `tests/test_e3_worker.py` (T-E3-1..4 + идемпотентность, отмена, подтверждение);
  точка kill — по токенам в БД, не по progress (движок пишет 0.4/0.7/1.0 пачкой в конце
  фазы — замеры; см. «Замечания» в `docs/CONTROL/E3.md`).
- Фикстуры `lang_*.epub` получили `nav.xhtml` (движок требует TOC, `read_toc` → ValueError).

## Сделано в E2

- `app/domain/book.py`: неизменяемые модели `Book → Chapter → Segment` + `BookStats`
  (главы, spine-документы, сегменты, символы, слова, изображения, таблицы, сноски,
  has_drm, token_estimate).
- `app/epub/segmenter.py`: блочные элементы из **настраиваемого** множества
  (`settings.segmentation.block_tags`); `segment_id = sha256(путь в архиве + позиционный
  XPath + нормализованный текст NFC/схлопывание пробелов)`; `marked_text` — внутренняя
  разметка без namespace-деклараций lxml (`_plain_copy`).
- `app/epub/reader.py`: валидация до LLM: ZIP → mimetype первой записью →
  **DRM до распаковки** (отказ, work-каталог не создаётся) → container.xml → распаковка
  (защита от zip-slip) → OPF → manifest → spine (порядок глав = spine, не имена файлов) →
  разбор каждого spine-XHTML; нечитаемый документ — `InputValidationError` (exit_code=3).
  `token_estimate` — tiktoken тем же `token_encoding`, что у движка (cl100k_base).
  Флаг `suspicious`: `zero_segments`, `single_spine_document` (подтверждение — в E3).
- Фикстуры (`tests/fixtures/epub/build.py`, D9): `complex.epub` (3 главы, имена файлов
  перемешаны относительно spine, таблица/dl/img/сноска; эталон 8/13/5=26), `drm.epub`,
  `broken.epub` (обрезанный OPF), `lang_de/fr/nl/it.epub` (Rule 4).
- Стробы `stubs/lxml/etree.pyi` для mypy (lxml без py.typed); `tiktoken` — явная зависимость.
- Измерено: `complex.epub` → 26 сегментов, 2270 симв./425 слов, 1 img, 1 table, 1 сноска,
  оценка 516 токенов; движок на этой книге — **2 группы** (2 translate-запроса, 237+237) →
  «фактический расход» для E2-5 считается суммой по всем translate-запросам (отклонение 8.9%).

## Сделано в E1

- Установлен `epub-translator==0.1.10` (ровно версия ТЗ; `uv.lock`).
- Полная source-разведка пакета + прогон `tests/probe/run_probe.py` (P1–P9) на
  детерминированном mock (`tests/probe/mock_llm_server.py`, rot3-«перевод»).
- Ключевые измеренные факты:
  - APPEND_BLOCK: перевод = следующий sibling; тег+атрибуты копируются; `id__translated`
    (дедупликация, маркер E6); blockquote — вставка внутрь (корень сегмента = внутренний
    `<p>`); **таблицы деформируются** (ячейки в тот же `<tr>`, колонки удваиваются).
  - Кэш: ключ `sha512(messages + version:target_language)`; тёплый прогон 0 токенов;
    смена языка 100%; **смена модели — 0 (не инвалидирует)** → D15 (изоляция кэша по модели).
  - Недоступный эндпоинт: `APIConnectionError`, `target_path` остаётся валидным ZIP с
    одной записью `mimetype` (атомарности нет → адаптер E5 пишет `.partial` → `os.replace`).
  - Повторы движка: только 502/503/504 + connection-ошибки; HTTP 500 — немедленный raise;
    встроенные ретраи openai SDK (дефолт 2) не отключаются.
  - Fill-сбой: `range(max_retries)` попыток + финальное `over_maximum_retries=True`;
    перевод группы отбрасывается, книга доводится до конца.
  - Вся книга — один поток (1 translate + 1 fill на фикстуру); `dc:language/identifier/date`
    не переводятся; `h1` в nav в поток не входит; ресериализация ломает побайтовую
    идентичность (QC — по текстовому содержимому).
  - concurrency=4: порядок документа сохранён (FIFO-yield), главы побайтово как при 1.
- Тесты T-E1-1..3 (`tests/test_e1_probe.py`): тёплый кэш ≤2%, смена языка ≥90%,
  concurrency 1 vs 4 побайтово.

## Сделано в E0

- git-репозиторий `main`, коммиты по этапам (ветка `stage/<ЭТАП>` → merge --no-ff).
- venv: `.venv` на **Python 3.11.13** (интерпретатор `/usr/bin/python3.11`), управление — `uv 0.12.5`.
  Системный python3 — 3.9.25, **не используется** (не подходит под epub-translator 3.11–3.13).
- Зависимости E0 (зафиксированы в `uv.lock`; полный verbatim-freeze —
  `docs/CONTROL/evidence/E0/e0-5_versions.txt`): fastapi 0.141.1, httpx 0.28.1, jinja2 3.1.6,
  lxml 6.1.3, pydantic 2.13.5, pydantic-settings 2.15.0, python-multipart 0.0.32, PyYAML 6.0.3,
  uvicorn 0.52.4; dev: mypy 2.3.1, pytest 9.1.1, ruff 0.16.7, types-PyYAML.
- Конфиг-слой `app/core/config.py`: ЕДИНСТВЕННОЕ место чтения `.env`/YAML (Rule 1).
  Обязательные без по-умолчанию (состояние после E4.1): `SERVER_BIND_ADDRESS` (.env),
  `llm.profiles.{translation,fill,analysis}.base_url/.model` (YAML), `storage.root` (YAML).
  `0.0.0.0` — только с `ALLOW_WILDCARD_BIND=true`. Профили LLM: translation/fill/analysis.
- Логирование `app/core/logging.py`: JSON-строки, `job_id` через contextvar, redaction секретов.
- Ошибки `app/core/errors.py`: классы кодов выхода (2 конфиг, 3 вход, 4 LLM, 5 выход).
- Точка входа `python -m app` (E0 — валидация конфига; U1 — сервер).
- Инструменты: ruff (0 ошибок), mypy strict (0 ошибок, 10 файлов), pytest (16 passed).
- `.gitignore`: dot-папки (маска), `.env`, `config/config.yaml`, `cache/ jobs/ books/ logs/
  data/lexicons/ data/models/ data/tools/ *.db working/ output/`, артефакты Python.

## Команды запуска среды

```bash
cd /mdisk/EpubTranslator
.venv/bin/pytest            # тесты
.venv/bin/ruff check .      # линтер
.venv/bin/mypy app tests    # типы
.venv/bin/python -m app --config config/config.yaml --env .env   # bootstrap (E0)
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env  # воркер (E3)
.venv/bin/python tests/e3_evidence.py   # сборка verbatim-evidence CONTROL E3
.venv/bin/python tests/e4_evidence.py   # сборка verbatim-evidence CONTROL E4
.venv/bin/python tests/e5_evidence.py   # сборка verbatim-evidence CONTROL E5
.venv/bin/python tests/e6_evidence.py   # сборка verbatim-evidence CONTROL E6
.venv/bin/python tests/l0_evidence.py   # сборка verbatim-evidence CONTROL L0
.venv/bin/python tests/l1_evidence.py   # сборка verbatim-evidence CONTROL L1
.venv/bin/python tests/l2_evidence.py   # сборка verbatim-evidence CONTROL L2
.venv/bin/python tests/l3_evidence.py   # сборка verbatim-evidence CONTROL L3
.venv/bin/python tests/s1_evidence.py   # сборка verbatim-evidence CONTROL S1
.venv/bin/python tests/s2_evidence.py   # сборка verbatim-evidence CONTROL S2
.venv/bin/python tests/s3_evidence.py   # сборка verbatim-evidence CONTROL S3
.venv/bin/python tests/s4_evidence.py   # сборка verbatim-evidence CONTROL S4
.venv/bin/python tests/s5_evidence.py   # сборка verbatim-evidence CONTROL S5
.venv/bin/python tests/u1_evidence.py   # сборка verbatim-evidence CONTROL U1
.venv/bin/python tests/u2_evidence.py   # сборка verbatim-evidence CONTROL U2
.venv/bin/python tests/v1_evidence.py [v5 v6 v7 v8 v8a v9]   # V1-приёмка (части)
.venv/bin/python tests/v111_tailnet.py  # V1.11: прогон с внешней tailnet-машины
.venv/bin/python tests/v113_runs.py     # V1.13: 5 реальных книг (Learning → ru)
epubtranslator --help                   # установленный CLI (U2; V1.10 — чистая установка)
```

Новые пакеты: `uv add <pkg>` (runtime) / `uv add --dev <pkg>`; lock обновляется автоматически.

## Фактические числа последних измерений

E0-1: ruff 0, mypy 0, pytest 16/16. E0-2: 3/3 отказа, rc=2. E0-3: 5/5 отказов wildcard, rc=2;
с флагом rc=0. E0-4: 0 вхождений секрета в `logs/` (grep rc=1). E0-5: Python 3.11.13, freeze — в evidence.

E1-1: 9/9 verbatim-фрагментов; 176/176 строк вшитых блоков `ENGINE_PROBE.md` совпадают с
evidence побайтово. E1-2: тёплый кэш B/A = 0.00% (порог ≤2%; A: 2 вызова/3857 токенов, B: 0/0).
E1-3: смена языка C/A = 100.00% (порог ≥90%). E1-4: разметка APPEND_BLOCK зафиксирована
(sibling + копируются тег/атрибуты + `id__translated`). E1-5: `APIConnectionError` за 1.279 s,
`target_path` = валидный ZIP с одной записью `['mimetype']`. E1-6: concurrency 1 vs 4 —
главы побайтово одинаковы. Итого по инструментарию после E1: ruff 0, mypy 0 (14 файлов),
pytest 19/19.

E2-1: 26/26 segment_id идентичны между запусками (уникальных 26/26). E2-2: 8/13/5 = 26
(точное совпадение с ручным эталоном). E2-3: 1/1 отклонение DRM до распаковки
(work-каталог не создан). E2-4: 100% совпадение порядка с spine. E2-5: оценка 516 vs
факт 474 (237+237, 2 группы движка) — отклонение 8.9% (порог ≤15%). E2-6: 5/5 языков.
Итого по инструментарию после E2: ruff 0, mypy 0 (21 файл), pytest 27/27.

E3-1: 1/1 kill -9 → INTERRUPTED; окно осиротения 0.574 с ≤ 1.0 с (2×heartbeat);
арена действовала на момент kill (heartbeat продлевал). E3-2: 152/152 недопустимых пар
отклонены + WARNING-лог; допущено 44, строк в transitions = 44. E3-3: хэш `target.epub`
идентичен, 2-й запуск этапа — 0 LLM-запросов (1-й — 4). E3-4: X=4970, Y=5004, Z=9974,
share = 0.0000% (порог ≤5%). E3-5: CANCELLED за 0.392 с (порог ≤30 с),
`target.epub.partial` создан, непомеченного `target.epub` нет. E3-6: 1 исполнитель из 2
воркеров, 1 переход CREATED→VALIDATING, 4 LLM-запроса (2 translate).
Итого по инструментарию после E3: ruff 0, mypy 0 (34 файла), pytest 34/34.

E4-1: 0 LLM-URL/имён моделей/ключей в `app/` (3 XML-неймспейса протокола — зафиксировано
как исключение, D21). E4-2: разрешение промпта записано 3/3 = 100% (generic v1 ×2,
pair en/de v1 ×1). E4-3: pair_required fr→de — `PromptError`, 0 записей разрешения,
0 LLM-запросов (generic-промпт существует — тихий фолбэк исключён). E4-4: `bad_json` ×3
попытки → `AnalysisTaskError`, артефакт 0, сырых ответов 3, токены неудач 1002 учтены.
E4-5: 1896 (translation) + 8078 (fill) + 0 (analysis) = 9974 = `tokens_total`,
расхождение 0; разбивка: 744+1152=1896, 6345+1733=8078. E4-6: 3/3 = 100%
`Cost: unknown` (цены не заданы); с ценами 2.0/8.0 $/Mtok → `Cost: 0.0025 USD`.
Итого по инструментарию после E4: ruff 0, mypy 0 (42 файла), pytest 53/53.

E5-1: пустых переводов 0 на всех 5 фикстурах. E5-2: покрытие 26/26 + 3/3×4 = 1.0
(≥ 99.5%), `shortfall=[]`. E5-3: EPUBCheck v5.3.0 available=True, errors=0 warnings=0
×5 выходов (исходные фикстуры тоже 0/0). E5-4: `mimetype` первой записью Stored ×5
(zipinfo verbatim `stor`). E5-5/T-E5-10: kill -9 ×10 в TRANSLATING (progress=0.100,
exit=-9) → `target.epub` отсутствует, `.partial` 1018 B; повреждённых принятых 0.
E5-6: fill-события 12 (критических 2: TOC + глава 1), строк errors.log 12, расхождение 0,
предупреждение QC выдано. E5-7: 30 терминов, лимит 300 → включено 11, отброшено 19,
prompt_chars=280, truncated=true; топ-11 в system-сообщении translate-запроса,
отброшенных нет. E5-8: 5/5 завершены (complex 5276 B / 5 progress-вызовов, lang 1787–
1836 B / 3), sha256 исходников 10/10. T-E5-9: мёртвый порт → FAILED,
«тракт T недоступен (endpoint http://127.0.0.1:1/v1, model test-model)», выхода нет,
ключ API не в сообщении. T-E5-11: без EPUBCheck → COMPLETED_WITH_WARNINGS +
VALIDATION_UNAVAILABLE, результат выдан.
Итого по инструментарию после E5: ruff 0, mypy 0 (51 файл), pytest 85/85.

E6-1: coverage 1.0 на всех 5 фикстурах (complex 26/26, lang_de/fr/it/nl 3/3),
`min_coverage=0.995`. E6-2: `identity_mismatches=0` ×5. E6-3: невыровненные 1/1
(100%) с причиной `missing_translation` + detail. E6-4: 5/5 вариантов перестановки
обнаружены (a: 2× missing_translation + 2 extra; b: 1× missing_translation + 1 extra;
c: 1× missing_translation; d: 1× xpath_mismatch + 1 extra; e: 4× xpath_mismatch +
3 extra; неверных пар нет). E6-5: повтор (свежее задание) → побитово идентичный
отчёт, 13479 байт, sha256 `8c081ec3…`. E6-6: diff(job−quality)=0.0 и
diff(job−report)=0.0 ×5; ниже порога: 25/26 = 0.961538 < 0.995 → предупреждение
«E6: выравнивание ниже порога: 25/26 (96.2% < 99.5%): OEBPS/z_alpha.xhtml
/htmlbody[1]p[1] (missing_translation)» → COMPLETED_WITH_WARNINGS. E6-7: 38/38 (100%)
пар с разрешимым XPath перевода (тег+текст сверены); T-E6-7: td (deformed table)
sibling в том же `<tr>`, сноска, blockquote inside — вложенные не сломаны.
T-E6-8: REPLACE — 26/26 `how=inplace`, `translation_xpath==source_xpath`,
source_text из исходного файла, исходная фраза в выходе отсутствует.
Итого по инструментарию после E6: ruff 0, mypy 0 (54 файла), pytest 95/95.

L0-1: 5/5 фикстур (en/de/fr/it/nl) — `decision=accepted`, `confidence=1.0`,
top3 одноязычны. L0-2: конфликт (en в OPF, текст de) → NEEDS_CONFIRMATION 3/3
(100%), p=1.000000, причина verbatim; правила `decide()` 7/7 в тесте.
L0-3: 6/6 (100%) degraded/unsupported с перечнем недостающего (5×DEGRADED по 2,
1×UNSUPPORTED 4; 5×SUPPORTED missing=0). L0-4: шов `spacy_available=False` →
11/11 пар unsupported («spaCy-модель не установлена: <модель>»), старт
Pipeline+Worker — 0 падений. L0-5: services.languages == независимый резолвер —
расхождение 0 ×11 пар (+matrix_report). L0-6: supported=5 (en/de/fr/it/nl → ru),
критерий «все исходные → ru» исполнен. T-L0-7: conflict.epub → run_validating True,
`source_language=None` (авто-выбора нет), confirm('de') → PLANNING + plan.json.
T-L0-8: bilingual.epub → подтверждение не требуется, `source_language=en`,
факт: 7 сегментов, share=0.285714 > 0.2, exceeds=true, 2 de-пометки (conf=1.0),
segment_id существуют в книге.
Итого по инструментарию после L0: ruff 0, mypy 0, pytest 116/116 (95 + 21 L0).

L1-1: 5/5 пайплайнов (en_core_web_sm / de_core_news_sm / fr_core_news_sm /
nl_core_news_sm / it_core_news_sm — все 3.8.0; spaCy 3.8.16), one-per-process.
L1-2: лемматизация en/de/nl/fr/it — 47/50 (94%) на каждый; слабые формы verbatim
(en: drank/swam/higher; de: isst/aß/schlief; nl: honden/stoelen/las; fr:
parles→parle/viens→vien/fait; it: mangiamo→mangare/vengo→vengare/sei).
L1-3: de noun без article → 0 выпущенных («'article' is a required property»),
без plural → 0, плейсхолдер «—» → 0 (Rule 5). L1-4: nl noun без de/het → 0,
«der» → 0 (enum), diminutive=null → 1 (легитимно). L1-5: fr/it verb без
auxiliary → 0, без conjugation_group → 0. L1-6: таксономии en 11 / de 12 / nl 9 /
fr 9 / it 9; чужой id (EN-PHRASAL-VERB у de) → принято 0. L1-7: CEFR-аудит —
5/5 строк LIMITATIONS verbatim, все `derived`, 0 лексиконов в поставку.
L1-8: 5× `Estimated CEFR level` (единая точка `cefr.label`). L1-9: границы
A1≤9.23/A2≤10.64/B1≤11.97/B2≤13.29/C1≤15.29/C2 null — из pack.yaml; в коде
app/languages/*.py — 0; примеры: de «der» zipf=7.46→A1, «Häuser» 4.57→A1,
en «the» 7.73→A1, «ubiquitous» 3.42→A1. T-L1-10: golden 20×5, покрытие
таксономии 11/11, 12/12, 9/9, 9/9, 9/9, чужой id отклонён verbatim.
T-L1-11: «Er steht früh auf, bevor die Arbeit beginnt.» → 1 единица
(steht+auf→aufstehen); «Hij staat vroeg op om te joggen.» → 1 (staat+op→opstaan);
FP «Das steht auf der Bank…» → 0, «Hij gaat naar huis…» → 0.
T-L1-12: lexicon hit → `CEFR level (lexicon:testlex): A1`; miss → «не установлен»;
после unlink → derived + `Estimated CEFR level`, 0 исключений, резолвер:
«CEFR-лексикон не найден: xx/cefr/testlex.json».
Итого по инструментарию после L1: ruff 0, mypy 0 (47 файлов), pytest 143/143
(116 + 27 L1).
L2-1…8 (CONTROL L2 8/8 PASS): кандидаты 13/13 идентичны; de — 0 с
заглавной; injected-несогласованность 3/3 (share 0.0); сложный en→ru
46/46 = 100%; truncation 4/13 (лимит 200 символов); парадигмы 9/10;
marlow 20/20; 5 языков → ru 5/5. После L2: ruff 0, mypy 0, pytest 154/154
(143 + 11).
L3-1…10 (CONTROL L3 10/10 PASS): 3/3 и 20/20 правок (editable_share
0→1); 0 правок без записи quality.log; 0 потери разметки; повтор — 0
правок (файл байт-в-байт); превышение 54.5%>50% → стоп ДО LLM (0 вызовов);
пересборка EPUBCheck 0 ERROR; kill ×10 → 0 битых; unverified → 0 правок
+ список; 5/5 языков. После L3: ruff 0, mypy 0, pytest 171/171.
S1-1…7 (CONTROL S1 7/7 PASS): 0 форм вне сегмента (24/24); 0 дублей
лемм; cap 27 / выпущено 24, плотность 13.17 ≤ 15; 0 плейсхолдеров;
воспроизводимость 100%; примеры из книги 0.9583; 5/5 языков. После S1:
ruff 0, mypy 0, pytest 185/185.
S2-1…6 (CONTROL S2 6/6 PASS): 0 id вне таксономии; recall de/nl 1.000
(golden-30), 0 без второй части; 0 частей вне сегмента; precision 1.000
(84 пары, 5 языков); воспроизводимость 100%. После S2: ruff 0, mypy 0,
pytest 202/202.
S3-1…7 (CONTROL S3 7/7 PASS): пояснения только из кандидатов (ghost
отклонён), цитата в сегменте, язык = target, 0 overrun; повтор — ссылка на
первый; 5/5 языков. После S3: ruff 0, mypy 0, pytest 223/223.
S4-1…7 (CONTROL S4 7/7 PASS): 0 gap с чужим словом; 0 mc с 2 correct /
дублями; 0 с ghost-сущностями; 0 answer/correct в теле (ответы в
answers.json 1:1); vocab 100% из выпущенных карт; 5/5 языков ×3
подстадии. После S4: ruff 0, mypy 0, pytest 244/244.
S5-1…9 (CONTROL S5 8/9 PASS + S5-7 BLOCKER): EPUBCheck 0 ERROR ×5;
mimetype STORED первым; 100% сохранение (байт-в-байт / префикс);
«Answer:» 0 в главах / 341 в финале; required-колонки 100% ×4 языка;
coverage = E6 (diff 0); kill ×10 → 0 битых, байт-детерминизм; S5-7:
Anki-импорт не верифицирован (pyanki отсутствует). После S5: ruff 0,
mypy 0, pytest 256/256.
U1-1…9 (CONTROL U1 9/9 PASS): прогресс 40/40 срезов API == БД; 0 «%» в UI
(кроме измеренного E6); LLM off → llm/* error, без «healthy»; 11/11 пар ==
резолвер; 6/6 derived — «Estimated CEFR level»; 16 эндпоинтов (0 без
потребителя); 17/17 роутов 401; 0 секретов в HTML/API/логах; L3 в UI ==
quality.log 3/3; LLM off в TRANSLATING → FAILED с причиной в UI. После U1:
ruff 0, mypy 0, pytest 266/266.
U2-1…3 (CONTROL U2 3/3 PASS): 0 дубликатов (import-allowlist 12, вызовы
9/9, паритет 4/4 + 6/6 + 11/11); 8/8 сценариев с exit-кодами (0/0/1/1/2/3/
3/3); 2/2 взаимная видимость; validate: битый → 3, валидный → 0
(EPUBCheck 0/0). После U2: ruff 0, mypy 0, pytest 270/270.
V1 (в работе, evidence `docs/CONTROL/evidence/V1/`): V1.5/6/7 PASS;
V1.11 PASS 4/4 (внешняя машина `vzu5-claw`: UI 200, upload 200, job
COMPLETED 16/16 сегментов, result 3040 B == локальный); V1.13 PASS 5/5
(de 1818 сегм. / 6 011 243 ток. / 250 s; en 1526 / 3 024 498 / 127;
fr 2571 / 4 600 495 / 411; it 2002 / 3 780 877 / 138, L3-правок 2;
nl 1595 / 3 642 776 / 253, L2 15 → L3 5; E6-покрытие 0.998119–0.999611;
EPUBCheck 0 errors; итог 21 059 889 ток.; статусы — все
COMPLETED_WITH_WARNINGS. Состав warnings verbatim (quality.json в
`v1-13-<lang>.txt`; в CONTROL V1 записывается целиком): E5 п.7
(атрибут `lang` удалён, число дочерних 4→6 / 48→92) — ×5 книг;
E5 п.3 (on_progress ±1: 12≠13, 20≠21, 10≠11, 39≠40, 10≠11);
E5 п.5 (глоссарий усечён: de 358/1257, en 378/953, fr 368/745,
it 393/688, nl 396/846); E5 п.4 (перевод совпадает с оригиналом:
fr 1 блок, it 10 ячеек таблицы, nl 10 блоков); S1-отклонения:
de 1820, en 267, fr 1060, it 1117, nl 974 (преимущественно S1.5
«лемма не соответствует словоформе» на mock-тексте); S2: fr 214 /
it 31 (S2.6 — клише-части вне сегмента), en 2 / nl 4 (дубли);
S3: en 40, nl 27, fr 2; S4 дубли вопросов: en 4, it 16, nl 1;
**nl L3: 12 правок отклонено валидацией («новая версия не содержит
форму парадигмы термина») — 15 расхождений L2 на входе → 5
applied, 12 не устранены**; S5 (все 5 книг): глава «Cover» вне
учебной книги + пометка охвата (99.8–100.0%). Итог стадии (17:10):
V1.8 **16/16 PASS** (третий прогон 17:04; правки инструментария
D73–D75, промежуточный разбор — «Сделано в V1»), V1.8a **PASS**
(14:28; фикстура 32 термина, фильтр по `glossary_fix`), V1.9 **PASS**
(14:32; INTERRUPTED из истории переходов БД), V1.1 **PASS** (17:00),
V1.2–V1.4 **PASS** (аудиты, D79), V1.10 **PASS**, V1.12 **PASS** —
все 13 блоков (CONTROL V1).

## Открытые дефекты / ограничения

**V1 (закрыта 2026-09-14, CONTROL 13/13 PASS, `docs/CONTROL/V1.md`):**
- Промежуточные FAIL V1.8 (8/16) / V1.8a / V1.9 (прогон 08:30) — ВСЕ
  причины в приёмочном инструментарии, не в приложении (разбор и правки
  — «Сделано в V1», решения D73–D75); финальный прогон V1.8 — 16/16
  (17:04). Правок приложения не потребовалось.
- V1.1–V1.4, V1.10, V1.12 — выполнены (см. «Текущее состояние»);
  полная пересборка (ruff 0 / mypy 0 / pytest 270/270) — зафиксирована в
  V1.1.
- Прогон 07:00: `TypeError` в v8-части (одна строка в summary, трейсбэк не
  сохранён). Не воспроизведён повторными прогонами (08:30, 14:28, 17:04);
  конкретная причина того падения не установлена. Рекомендация: `main()`
  evidence-скриптов пишет полный трейсбэк в evidence (сейчас — только
  «Type: message»).

**BLOCKER-1 (снят 2026-09-14, V1R):** реальный OpenAI-совместимый
эндпоинт предоставлен владельцем (vLLM 0.26.0, модель `qwen3.8-27b`,
FP8, max_model_len 262144; в приёмочном окружении — `http://127.0.0.1:8004/v1`).
API-ключ — в `.env` (gitignored), адрес/модель — в `config.yaml`
(gitignored). Модель — thinking: без `chat_template_kwargs:
{enable_thinking: false}` (передаётся через `extra_body`, D80)
reasoning-поток засоряет контент ответа — без D80 реальный прогон
невозможен. Приёмка на реальном LLM — CONTROL V1R (V1R.1–V1R.6b):
полный цикл + S-стадии + реальная книга работают; контроль качества
честно фиксирует модельные несоответствия (таблица не переведена →
E6; L2 < 95% → CWW). Вывод V1, что «соблюдение глоссария моделью —
гарантия только с L3», подтверждён реальным прогоном (L2 76–78% < 95%).
Исторически: ВСЕ прогоны V1 (включая V1.13 — реальные книги) — на
детерминированном mock; «PASS на реальном LLM» по блокам V1 не
заявлялся (Rule) и именно поэтому фазой V1R закрыт. Ограничения
mock, учтённые в приёмке V1: fill-ответ строится из XML блока
независимо от сценария (s3 — сценарий пустит и fill); ground truth
пересланных запросов/токенов — `requests_log` mock (БД не видит
не-flushed группы — V1.9).

**S5:** S5-7 — фактический импорт в Anki не верифицирован (pyanki
отсутствует в окружении; формат экспорта верифицирован структурно).

- L0: `langdetect` — статистический (naive Bayes) детектор; confidence=1.0 на
  фикстурах — не гарантия на реальных книгах: страховка — правила L0.5 (конфликт
  детектора и `dc:language` → подтверждение, авто-выбор запрещён) и порог
  `min_confidence` (YAML); по сегментам — только ≥ `segment_min_chars` (D44).
  Нейро-NLP (spaCy) подключено в L1 (лемматизация, POS, sentencizer; parser/ner —
  в L2).
- L1: `lexical_units` (отделяемые глаголы) детектирует только главную конструкцию
  с приставкой СПРАВА от глагола; финитность головы — эвристика на POS (без
  парсера): правило «ранее в предложении другой VERB без CCONJ/SCONJ/PUNCT — не
  финитный» измерено на nl «vroeg» (модель размечает наречие как VERB «vragen») и
  FP-сценариях; придаточные и начальная позиция («Aufstehen ist…») — не
  детектируются (заявлено в CONTROL L1).
- L1: лемматизация fr/it слаба на ряде форм спряжения (fr: parles→parle,
  viens→vien; it: mangiamo→mangare — verbatim в L1-2): S1 при отборе лексики
  не обязан опираться на лемму глагола как единственный ключ (есть `word_form`).
- L1: CEFR — все 5 языков `derived` (аудит 2026-09-13, D45: лицензионно-чистого
  машиночитаемого лексикона не найдено) — в UI (U1) везде, где показан уровень,
  обязательная метка `Estimated CEFR level` (единая точка `app.languages.cefr.label`).
  CEFR никогда не подаётся как истина (TЗ L1 п.4).
- L1: `wordfreq` 3.1.1 — замороженный снимок ~2021 (wordlist-параметр снят,
  `frequency_of_word` удалён — D48): приемлемо для ранжирования полос частотности,
  нет — для утверждений о «современной употребительности» (LIMITATIONS).

**LLM / общее:**
- Сервер создаёт задания, воркер исполняет их (U1); «сервер запускает
  воркера из UI» — Not implemented (Rule 7).
- Движок `encode_friendly` пишет текст в XML без экранирования (сырой «&»
  ломает fill-промпт — реальная книга «R. Bemporad & Figlio»): обходится на
  стороне mock (`_sanitize_xml_fragment`); для реальной модели не проблема
  (XML не парсит); зафиксировано как артефакт mock (V1.13).

## Среда машины (verbatim-снимок 2026-09-12)

- хост `3p14-aaa` (Tailscale 100.124.155.106, tailnet `3p14-aaa.tail46a65f.ts.net`);
- 24 ядра, 62 GB RAM, 348 GB свободно на /mdisk; OpenJDK 17.0.20; PyPI доступен.
- EPUBCheck 5.3.0 — **fat-jar** в ОБОИХ путях (`data/tools/epubcheck.jar` и
  `data/tools/epubcheck-5.3.0/epubcheck.jar`): дистрибутивный jar
  не самодостаточен (MANIFEST `Class-Path: lib/…`), standalone `java -jar`
  падает с `NoClassDefFoundError: io/mola/galimatias/GalimatiasParseException`;
  рецепт слияния + sha256 — `.a/reports/v1_assets_prep.md`.
- Вторая tailnet-машина для V1.11: `vzu5-claw` (100.95.195.40) —
  BLOCKER-3 снят (2026-09-14, PASS 4/4).
- Реальный LLM-эндпоинт (BLOCKER-1, снят 2026-09-14): vLLM 0.26.0-tp2 на
  этом же хосте, `http://127.0.0.1:8004/v1` (узел 3p14-aaa, порт 8004),
  модель `qwen3.8-27b` (FP8, `root: /mdisk/models/Qwen3.8-27B-FP8`,
  max_model_len 262144). Эндпоинт общий с кодом-агентом (GPU разделяется)
  — длительности вызовов не воспроизводимы; токены/статусы — из БД.

## Правила (напоминание)

Rule 1–8 из `TZ_EpubTranslator_00_OVERVIEW.md` действуют на всех этапах: запрет хардкода,
доказуемость, verbatim-захваты, все пять языков, LLM не арбитр, fail closed, запрет расширения
допуска, запрет дублирующих механизмов. Язык — данные, не код (L0–L2).
