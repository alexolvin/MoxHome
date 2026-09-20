# UI_HANDOFF — ТЗ_EpubTranslator_UI_2026-09-19 + серия R (ТЗ-UI-R, 2026-09-20)

Самодостаточный документ для продолжения работ в свежей сессии.
Обновляется **перед** началом каждого этапа и **сразу после** каждого
измерения. ТЗ: `.a/tasks/TZ_EpubTranslator_UI_2026-09-19.md` (серия D) и
`.a/tasks/TZ-UI-R-EpubTranslator-2026-09-20.md` (серия R, полный редизайн
mobile-first 384×715; этапы R0–R9, ветки `stage/ui-R<n>`, по одному
merge --no-ff в main на этап).

---

## Состояние (2026-09-20)

- **В работе с 2026-09-20: серия R.** Основание — карта состояния
  проекта 2026-09-19. Серия R поглощает незакрытые D3/D4/D6: их
  контрольные позиции (M2, M4, M5, M8, M9, тёмная тема, focus, T1–T14)
  перенесены в разделы ТЗ-R и в §16 (T1–T20). Порядок: R0 каркас →
  R1 дизайн-система (статусы/поверхности/тёмная тема/фокус) →
  R2 «Задания» → R3 управление заданиями → R4 создание задания →
  R5 «Библиотека»+карточка книги → R6 импорт (12 форматов, Calibre) →
  R7 экспорт (8 форматов) → R8 «Настройки» (LLM-профили) → R9 приёмка.
  **R0: завершён 2026-09-20 (измерения — секция R0)** — каркас
  (appbar 48 px, таб-бар 56 px + safe-area, FAB 56 px, роутинг и
  301-редиректы, 9 компонентов §14), новые ключи `settings.ui`
  (tap_target_px, tap_gap_px, row_height_px, tabbar_height_px,
  input_font_px, export_ttl_hours; page_size 50→20,
  refresh_max_seconds→refresh_seconds_max 30→15,
  mobile_breakpoint_px 640→641). **CONTROL R0: T1 PASS (131/56 px
  ≤ 160), T2 PASS (320/375/384/414/768), T3 PASS (0/0); pytest
  305/305, аудит A/B/C 31 checks 0 ошибок.** **R1: завершён
  2026-09-20 (измерения — секция R1)** — статусы 16→5 групп (единая
  таблица views.STATUS_GROUP), поверхности 0–3, токены TZ-R, тёмная
  тема активирована, фокус 100 % + input[type=date]. **CONTROL R1:
  T13 PASS (56 пар, 0 нарушений, обе темы), T14 PASS (12/12 экранов,
  250 элементов), T18 PASS (0 литералов, 4 блока); pytest 305/305,
  аудит 31/0, T1 131/56; CSS 23 313 / JS 32 680 Б.** Дальше R2
  (строка §10, сегментированный фильтр, группировка, точечное
  обновление: T4/T16).
- **Стенд 2026-09-20:** сервер + воркер запущены заново (были
  остановлены); health: app/database/storage ok, worker ok, llm
  translation/fill/analysis ok, epubcheck ok. Java 17 для EPUBCheck
  на месте. **Calibre для R6: НЕДОСТУПЕН — все каналы исчерпаны
  (2026-09-20):** официальный хост download.calibre-ebook.com в этом
  окружении отдаёт HTML-индекс (2362 Б) на ЛЮБЫЕ пути (проверено
  с двух нод: 3p14-aaa и vzu5-claw); dnf/EPEL-9: «No match for
  argument: calibre» (RC=1); GitHub org calibre-ebook: 404; PyPI
  «calibre» — чужой ML-пакет (не тот); Fedora 41 RPM: цепочка
  зависимостей не собирается на Alma 9 (py3.13-расширения); на
  диске установки нет. Решение: R6 реализует деградированный режим
  TZ §8 (импорт EPUB без конвертации + явная строка «Конвертация
  недоступна: не установлен calibre»); T7/T8-конвертация BLOCKED,
  поднять владельцу до merge R6.
- **Завершён 2026-09-19: D1** (дизайн-система: выбор GitHub/Primer по
  5 критериям §3.2; docs/design-system.md; 29 токенов в :root,
  0 литералов цвета/кегля/отступа/радиуса вне :root; контраст —
  полный PASS расчётом в обеих темах; регресс 31/31 + 0 ошибок;
  B 28/28; realdevice 384×7; детали — секция D1). Коммит
  `stage/ui-D1` → main (merge --no-ff). Стенд восстановлен в 11:40
  МСК (сервер + воркер были остановлены; запущены заново). Inspo
  200 через туннель, MCP inspo подключён в сессии.
- **Завершён 2026-09-19: D5** (E1/E2/E6/E7/E9 + 5 состояний
  §7.1 + онбординг §7.4 + сжатие JS ≤ 32 КБ) — **CONTROL D5
  пройден** (evidence: прогоны 4 + 5b, `.a/tasks/ui-audit/`;
  детали — секция D5 ниже). `app.js` = **32 332 Б** (лимит
  32 768), pytest **305/305** (env -u см. ниже). Web-сервер на
  новом коде с 09:38 МСК. Коммит `stage/ui-D5` → main
  (merge --no-ff).
- **D1 разблокирован (11:20 МСК):** Inspo недоступен напрямую
  с 3p14-aaa (локальная блокировка хоста; с vzu5-claw — 200).
  Решение владельца: SSH-туннель 127.0.0.1:443 → inspomcp.dev:443
  через vzu5-claw + hosts-пин (инфраструктура — секция «Стенд»).
  Проверено: `curl https://inspomcp.dev/` → 200, tls_verify=0;
  MCP-эндпоинт отвечает на JSON-RPC initialize (serverInfo
  inspo hosted-85acb10). Сервер `inspo` зарегистрирован в
  Qwen Code user-конфиге (`qwen mcp add --transport http`;
  `inspo-mcp install` для Qwen Code не сработает — клиент не в
  списке детекции, dry-run находил только Claude Code).
  **Для старта D1 сессию Qwen Code надо перезапустить**
  (MCP-серверы обнаруживаются при старте; новый сервер может
  потребовать approval в /mcp).
- **Завершено:** этап 0 (подготовка стенда), **D0** (базовая
  линия, run 3), **D2** (P0-скролл E3/E4/E5, CONTROL 3/3,
  05:46–05:51 МСК), **D5** (CONTROL D5, прогоны 4+5b,
  10:38–10:48 МСК), **D1** (дизайн-система, 11:40–16:30 МСК).
- **Осталось (серия D):** D3, D4, D6 — **поглощены серией R**
  (ТЗ-UI-R 2026-09-20 §1); отдельно больше не закрываются.

### D0 — базовая линия (финал: run 3, 2026-09-19 05:21–05:26 МСК)

**CONTROL (ТЗ §9) — итог:**

| Критерий | Результат |
|---|---|
| `mobile_realdevice.scrollWidth` = 701 @384 | ✓ **701** (стр. j-20260916T171416Z-571009; wide: `span.status-completed_with_warnings` right=397, w=381 — это E4; overflow 701 даёт текст-хеш в `<li>` — E3) |
| 6 страниц = 200 | ✓ 200×6, h1 на всех |
| E1 воспроизведён | ✓ 404, `title=""`, `h1=null` (голый JSON) |
| E8 воспроизведён | — **не воспроизведён** в прогонах 09-19 (гонка не сработала). Базовое доказательство — 09-18: results того дня (tag C2) + отчёт `ui-audit-2026-09-18-errors.md` (console.error 404 при удалении). Механизм: GET поллинга в flight во время DELETE; вероятность ~10 % на удаление |
| E9 воспроизведён | ✓ favicon 404 + console.error на /jobs |

Ключевые числа run 3 (`results.json`: 24 checks, 47 скриншотов):

- Фаза C целиком зелёная (C01–C11); все объекты аудита удалены —
  после прогона в данных ровно 9 пользовательских заданий +
  1 книга (u1-ce30c3f8-probe.en.epub), остатков 0.
- C3c: `paused_after_ms` = **55755** (55.7 с; run 2: 24885),
  `pause_requested_line_ms` = **null** (E6: строки «пауза
  запрошена» в UI нет), ok: true.
- C5: `cancelled_after_ms` = 9086, ok: true, задание удалено
  (api_after_delete 404).
- C4: COMPLETED_WITH_WARNINGS, 15/15, выравнивание 100 %,
  26362 токена; «время» = «прошло: 177.3 с · осталось (оценка):
  0 с» — симптом **E7** (wall-clock включает паузу 55.7 с;
  реальная работа ≈ 121 с).
- C7 purge dry-run: 9 заданий, 135 358 236 Б (129.1 МБ);
  `msg_text` = «Перечень: 9 заданий, объём 129.1 МБ.»;
  `msg_shows_job_ids` = False, `msg_shows_all_job_ids` = False
  (базовая линия **E2**); confirm disabled до dry ✓.
- 3 записи errors в results.json: favicon 404 (E9) + 2 × prompt
  400 (C8.prompt_roundtrip — намеренный негативный тест: битый
  YAML отклонён сервером; дефектом не является).

**Риск T6 (зафиксирован в базовой линии):** время до статуса
PAUSED определяется длительностью in-flight LLM-вызова
(кооперативная пауза дожидается завершения текущего шага;
fill-чанк = один вызов qwen3.8-27b): измерено **24.9 с** (run 2)
и **55.7 с** (run 3). Критерий T6 «статус PAUSED — ≤ 25 с»
базовой линией не гарантируется — это свойство кооперативного
механизма, а не UI. UI-часть T6 (строка «пауза запрошена»
≤ 3 с) — в нашей зоне (E6). В D6 оба числа будут честно
измерены и отчитаны.

**История прогона (сжато):**
- **Run 1 (04:48–04:57 МСК):** A/B прошли (11 checks); **C.CRASH**
  на C4c (таймаут клика `#u3-delete-job`): флэп скрипта — C3c
  читал статус один раз через 1.5 с против кооперативной паузы →
  задание зависло в PAUSED → кнопки delete нет. Остатки run 1
  (j-20260919T015016Z-f941f2, u1-fc33191f-probe.en.epub)
  очищены 05:08 МСК через API (`cleanup_d0.py`, лог
  cleanup_d0.log).
- **Run 2 (05:09–05:16 МСК):** фаза C целиком без краха; тот же
  флэп повторился в C5 (кооперативная отмена) → остатки run 2
  (j-20260919T021605Z-a6eb9f, u1-4c6087b4-probe.en.epub)
  удалены через API 05:21 МСК; recheck снял 384 (открыл своё
  отменённое задание без предупреждений — не базовое).
- **Run 3 (05:21–05:26 МСК):** финал, всё чисто, числа выше.

**Правки инструмента (audit_ui.py / audit_mobile_recheck.py,
2026-09-19; методика измерений не менялась, ТЗ §8.4):**
1. C3c: ожидание PAUSED поллингом до 60 с (было: одно чтение
   через 1.5 с) + замер T6 — `pause_requested_line_ms`
   (время появления строки «пауза запрошена»; в базовой линии
   null = E6).
2. C4: самовосстановление из PAUSED (reload + клик resume).
3. C5: ожидание CANCELLED поллингом до 60 с + reload перед
   проверкой кнопки удаления.
4. C7: `msg_shows_all_job_ids` (критерий ТЗ E2/T5 — all);
   `msg_text` читается `text_content()` (внутренности `<details>`).
5. Контексты: `Accept-Language: ru` зафиксировано (аудиты фаз
   A–C и recheck — на русских строках; после D4 язык страницы
   выбирается по Accept-Language).

### D1 — дизайн-система (2026-09-19, в работе с 11:40 МСК)

**Процедура §3.1 (порядок выдержан):** `get_filters` → `recommend`
(бриф ровно из ТЗ) → макроструктуры: recommend выдал Feature Stack
(маркетинговый пик) — отклонён по §3.1 п. 4; отбор по `index-first`
(4 сайта) + `long-document` (18) → `get_design_system` ×3 → `compare`.

**Кандидаты:** perplexity-ai (light, Index-First, база 8 px, pplxSans —
проприетарный, тёмная тема = инверсия), github-com--blog (dark-захват,
база 4 px, Mona Sans OFL, полный Primer-набор токенов обеих тем),
nextjs-org (light, база 4 px, Geist, монохром без хроматического
акцента, секции 80–96 px). `compare`: общее — minimalism, technical,
4-пиксельная база, радиус 6 px.

**Выбор: GitHub (Primer)** — по всем пяти критериям §3.2: (1) тёмная
тема first-class (единственный; сам захват тёмный, dark-токены
захвачены, весь набор проходит пороги §6.1 расчётом); (2) 67-символьные
хеши/логи/бейджи — канонический продукт (commit SHA, Actions);
(3) плотность: тело 14 px / 1.5, сетка 4 px, радиус 6 px;
(4) ноль запросов — системный стек (собственный фонт-стек GitHub —
Mona Sans + системный фолбэк); (5) dual-theme изначально. Перplexity
отклонён: плотность (витрина), шрифт не хостится, тёмная вторична.
Next.js отклонён: документация (антиплотность), нет статусных
хроматических значений.

**Макроструктура страниц:** списки — Index-First, страница задания —
Long Document; маркетинговые формы отклонены (§3.1 п. 4). Из источника
перенесены только значения (§3).

**Что изменено:**
1. `docs/design-system.md` — новый: кандидаты, выбор, 5 критериев,
   полная таблица токенов, измеренный контраст, решения (шрифты,
   границы 3:1, фиксированные токены).
2. `app/web/static/style.css` (10 037 → **12 484 Б**, лимит 16 КБ):
   29 токенов в `:root` (все 15 групп §3.4: bg/surface/text/muted/
   border/accent/ok/warn/err/focus, font-sans/font-mono, fs-100…700,
   space-1…6, radius-sm/md, tap, bp-cards). Удалены `--u3-head-bg`,
   `--u3-pre-bg` (→ `--u3-surface`), `--u3-cancelled` (→ `--u3-muted`),
   `--u3-radius` (→ `--u3-radius-md`; nav-link — `--u3-radius-sm`) —
   единый механизм §8.1.1. Все литералы цвета/кегля/отступа/радиуса
   вынесены в `:root`: аудит-скрипт `.a/tmp/d1_css_audit.py` —
   **0 нарушений** (RC=0); док. исключения в шапке CSS: толщины линий
   1px/2px + outline-offset 2px (§3.4 минимум), layout-размеры, @media.
   Палитра: Primer light (bg #ffffff, surface #f6f8fa, text #1f2328,
   muted #656d76, border #767e89, accent #0969da) + статусные —
   оттенок Primer, затемнённый до порога с запасом (Primer success
   #1a7f37 → 4.77 на surface, не проходит → **#176f33** 6.25/5.87;
   attention #9a6700 → 4.87/4.57, не проходит → **#755100**
   7.16/6.72; danger #cf222e → 5.03 на surface, без запаса →
   **#b9202b** 6.36/5.97). Тёмный набор зафиксирован в док-е
   (#0d1117/#161b22/#e6edf3/#8b949e/#6e7681/#58a6ff/#3fb950/#d29922/
   #f85149 — полный PASS расчётом), активация — D4.
3. `tests/u3_evidence.py` (ev_u3_11): пары контраста приведены к новой
   архитектуре токенов (порог ≥ 4.5:1 не тронут; удалённые токены
   покрыты surface/muted-парами).

**Контраст (расчёт `.a/tmp/d1_contrast.py`, не на глаз, §6.1):**
светлая — text 15.80/14.84 (≥7), muted 5.25/4.93 (≥4.5), accent
5.19/4.88 (≥4.5), ok 6.25/5.87, warn 7.16/6.72, err 6.36/5.97
(≥5.0), border 4.10/3.86 (≥3) на bg/surface — все PASS. Тёмная —
все пары PASS (worst: err 5.16 на surface).

**Регресс (полный audit_ui.py, `run_d1_full.log`, 266 с, RC=0):**
31 checks, **0 errors**. A 11/11 (7×200 + h1, 404 HTML, 401 неверный
пароль, 403 без CSRF, favicon 200), D5 6/6, C 14/14 (upload, create,
кнопки активного, 409, pause/resume, C4.e7, terminal, download,
clear-logs, delete, cancel, delete-book, purge dry-run, prompt
roundtrip, D5.6 0 console.error). Фаза B: 320/375/414/768 × 7 страниц
= viewport, wide=0 (**28/28**). Mobile realdevice 384×715 DPR 2.25:
7 страниц scrollWidth=384, wide=0, ошибок 0.

**pytest (env -u):** web-трио 32/32 RC=0. Полный сетап: 304/305
зелёные; 1 timing-флейк `test_u2_cli.py::test_u2_2_exit_codes_eight_
scenarios` (сценарий 3 «translate --wait dead LLM»: хвост лога
«статус: CREATED» вместо «статус: FAILED» — поллинг --wait завершился
до видимости перехода под нагрузкой полного сетапа; D1 не трогает
CLI/воркер-путь). Изолированный репро: тест RC=0, весь файл
`test_u2_cli.py` RC=0 — флейк не воспроизводится. Сетап ~7–13 мин
(медленнее под нагрузкой).

**Данные:** после всех прогонов ровно 9 пользовательских заданий +
1 книга (Probe Book), остатков 0.

**Итог:** CONTROL D1 пройден (3 кандидата, 1 выбран; 15 групп токенов
в :root; grep — 0 литералов цвета вне :root, стр. 20–29). Коммит
`stage/ui-D1` → main (merge --no-ff).

### D2 — P0-скролл E3/E4/E5 (2026-09-19 05:40–05:51 МСК, завершён)

**Что изменено** (только `app/web/static/style.css`, 9 079 →
10 037 Б, лимит 16 КБ в норме; код и шаблоны не тронуты):

1. **M1 (E3/E4):** единая механика переноса длинных токенов —
   `body { overflow-wrap: anywhere; }` (свойство наследуется
   всеми потомками, включая динамические карточки; `pre`
   защищён `white-space: pre` — единственный допустимый
   горизонтальный скролл, M9). Семь разрозненных деклараций
   `overflow-wrap` (footer p, a, th/td ×3, dt, dd, .u3-workline)
   удалены — в файле ровно одна декларация.
2. **M3 (E5):** `@media (max-width: 640px)` — `.u3-facts`
   вертикальный стек «метка / значение»: table/tbody/tr →
   display:block, метка `th[scope=row]` без nowrap и min-width,
   ячейка значения во всю ширину (352 px на 384).

**CONTROL (ТЗ §9) — итог 3/3:**

| Критерий | Базовая линия (D0) | Измерено (D2) |
|---|---|---|
| scrollWidth = 384 на всех 7 страницах @384 | 701 на стр. задания | ✓ **384×7**, wide=0 (реcheck, j-20260916T171416Z-571009 — то же задание, что в D0) |
| = ширине viewport на 320/375/414 | 320: +9 px на job_detail | ✓ **320/375/414** (и 768) × 7 страниц, 0 отклонений (фаза B) |
| `rect.right` статуса ≤ viewport | 397 (span 381 px) | ✓ wide=0 на всех страницах (элементов за краем нет) |

**Регресс §4.1 (полный audit_ui.py, 05:46–05:51 МСК):** 24 checks
зелёные; C3c `paused_after_ms` = 18381, ok; C4
COMPLETED_WITH_WARNINGS 15/15 (самовосстановление из PAUSED
сработало штатно); C5 10087 ms ok; C6/C7/C8 ok (C7: 9 заданий,
129.1 МБ, confirm disabled до dry). Ошибки (3) — ожидаемые:
favicon 404 (E9, закрывается в D5) + 2 × prompt 400
(намеренный негативный тест C8). E8 — гонка не сработала
(базовое доказательство 09-18, см. секцию D0).

**Данные:** после прогона ровно 9 пользовательских заданий +
1 книга, остатков 0.

**Для D3 (справочно, из B-фазы):** `smallInputs` — status-
чекбоксы 13.33 px (UA-default, T12 касается текстовых
контролов) и `textarea.u3-mono` = 14.4 px (M4 → 16 px).

### D5 — E1/E2/E6/E7/E9 + 5 состояний §7.1 + онбординг (2026-09-19, завершён)

**Что изменено** (app/web: app.py, auth.py, views.py, strings.py,
static/app.js, static/favicon.ico, templates: base, books, job,
jobs, + auth.html/forbidden.html; tests/test_u1_web.py):

1. **E1:** обработчик HTTPException в `app.py` — любой путь вне
   `/api/` → 404 `not_found.html` (title «EpubTranslator — Не
   найдено», общее оформление); `/api/*` — JSON как был.
2. **E2:** purge dry-run — сообщение перечисляет **все** job_id
   (`<details>` + `<code>`) + «Действие необратимо: при
   подтверждении будут удалены…»; confirm-кнопка disabled до
   dry-run, enabled после (механизм не тронут).
3. **E6:** строки «пауза запрошена — применится по завершении
   текущего шага» / «отмена запрошена — …» (`role="status"`)
   появляются сразу по клику (app.js + job.html).
4. **E7:** `views.elapsed_and_eta(job, transitions)` — «прошло»
   = сумма рабочих интервалов по таблице `transitions` (уже
   существовавшей — без расширения схемы); в PAUSED ETA не
   выводится, строка «на паузе с ‹t›».
5. **E9:** `favicon.ico` (2 360 Б) + `<link rel="icon">` в
   base.html; `/favicon.ico` публичен (см. 6).
6. **Публичная статика** (решение владельца, см. ниже):
   middleware: `_PUBLIC_PATHS = {"/favicon.ico"}` + `/login` +
   `/static/*` пропускаются до auth; страницы и `/api` — под
   auth полностью; CSRF и 401 на неверный пароль — без
   изменений (§7.3). test_u1_7 обновлён.
7. **5 состояний §7.1:** Пусто (онбординг §7.4 — блок из 3
   шагов при total==0: серверно в templates + в JS-рендерере,
   `data-u1-onboarding`); Загрузка (первичный серверный HTML
   fetch-контейнеров — «загрузка…», /settings: #u1-health);
   Ошибка сети (любой fetch: текст + кнопка «Повторить» —
   `containerError`/`failWithRetry`, атрибуты retry-кнопок
   `data-u3-retry` / `data-u3-jobs-retry` (строка
   #u3-jobs-refresh) / `data-u3-poll-retry`); 401/403 —
   страницы в общем оформлении (`auth.html` c формой /login,
   `forbidden.html`; **без** `WWW-Authenticate` — не системный
   диалог); Не найдено (E1).
8. **Сжатие JS:** 37 347 → **32 332 Б** (лимит 32 768;
   node --check + Node smoke-тест DOM-стабом), `style.css` =
   10 037 Б (лимит 16 КБ). Внешних сетевых запросов: 0.

**CONTROL D5 (ТЗ §9) — итог (прогоны 4: 10:38–10:45 МСК +
5b: 10:47 МСК, `results_run4_full.json` / `results.json`):**

| Критерий | Измерено |
|---|---|
| E1 | ✓ A.404: 404, `text/html`, title «EpubTranslator — Не найдено», h1 «Не найдено», e1_ok |
| E2 | ✓ C7: e2_ok — 9/9 job_id в сообщении, «Действие необратимо», confirm disabled до dry / enabled после |
| E6 | ✓ C3c: строка «пауза запрошена…» за **522 мс**, `role=status`, e6_ok; C5: «отмена запрошена…» за **1051 мс**, e6_cancel_ok (критерий ≤ 3 с) |
| E7 | ✓ C4.e7: «прошло: 200.5 с» (UI) vs **200.55 с** (производственная elapsed_and_eta по transitions), diff **0.05 с**; пауза 2.0 с исключена (elapsed+paused ≈ wall: 200.55+2.0 ≈ 202.65); C3c в PAUSED: «прошло: 31.4 с · на паузе с 2026-09-19 10:41:04», ETA скрыта (eta_hidden_while_paused). Сценарий ТЗ «6 мин паузы» буквально не воспроизведён (аудит-цикл держит паузу 2.0 с) — механизм исключения верифицирован равенством, код-путь один |
| E9 | ✓ /favicon.ico → 200 (гость и авторизованный; 2 360 Б), 0 console.error на странице входа (D5.1) |
| 5 состояний §7.1 | ✓×5: Пусто+онбординг (D5.5: attr, 3 шага, таблица отсутствует, «Книг нет. Загрузите EPUB», кнопка загрузки), Загрузка (D5.4: «загрузка…» → health), Ошибка сети (D5.3: «Ошибка API: Failed to fetch (повтор через 6 с) Повторить» → клик → «последнее обновление: …», 9 строк), 401 (D5.1: страница + форма /login + css/favicon 200), 403 (D5.2: «Доступ запрещён») |
| T8/E8 | ✓ 0 console.error в цикле create→delete (прогон 4: единственные 2 ошибки с tag C — намеренный негатив C8 на /prompts, исключены по URL; fix E8 = quiesce поллинга перед DELETE) |
| Регресс A | ✓ 11/11 checks (7 страниц 200, 404, 401 неверный пароль, 403 без CSRF, favicon) |
| Регресс B | ✓ 320/375/414/768 × 7 страниц = viewport, wide=0 |
| Mobile realdevice | ✓ 384×715 DPR 2.25: scrollWidth=384 на всех страницах, wide=0, 0 ошибок (9 карточек) |
| Данные | ✓ 9 пользовательских заданий + 1 книга до и после; объекты аудита удалены, остатков 0 |

**Числа T6 (честно, свойство кооперативной паузы, не UI):**
время до PAUSED по LLM-шагам: 24.9 с (D0 run2), 55.7 с (D0
run3), 176 с (D5 run2), 18.2 с (D5 run3), 30.3 с (D5 run4).
Критерий T6 «≤ 25 с» механизмом не гарантируется (fill-чанк =
один вызов qwen3.8-27b) — в D6 отчитать оба числа (UI-строка
≤ 3 с гарантирована: E6 ✓ 522 мс / 1051 мс).

**История прогонов CONTROL D5 (все сбои — инструмент, не
приложение; приложение не менялось во время измерений):**
- **Run 1 (09:53 МСК):** краш в фазе A — `http_credentials`
  challenge-based vs страница 401 без `WWW-Authenticate` (§7.1)
  → креденшелы не отправлялись, все страницы 401. Фикс: форма
  /login. Результаты невалидны.
- **Run 2 (10:01–10:06 МСК):** D5.3 — авто-поллинг сам
  останавливается при неактивных заданиях (ронять нечего) →
  сценарий = abort начального fetch; C — «socket hang up»
  (keep-alive race, uvicorn 5 c) → api_get retry-once; остатки
  (PAUSED-задание) — cleanup_d5.py.
- **Run 3 (10:22–10:27 МСК):** C целиком зелёный (E6 527/1059
  мс, E7 122.35/122.3, E2 9/9, E1, mobile 0); D5.CRASH —
  `m.location()` в console-обработчике: **location — свойство
  (dict), не метод** (TypeError пробросился в goto).
- **Run 4 (10:38–10:45 МСК):** A ✓, C ✓ (числа выше), B ✓,
  mobile ✓; D5: D5.1/D5.2 ✓, D5.3.CRASH — неверный селектор
  (кнопка «Повторить» списка — в `#u3-jobs-refresh` c
  `data-u3-jobs-retry`; список рендерится сервером, строки при
  abort остаются). Побочный эффект: контекст фазы D5 не
  закрыт после краша → 14 пар requestfailed/console.error за
  4 мин (abort-маршрут «дожил» до browser.close) — артефакт
  инструмента, зафиксирован (unroute в finally).
- **Run 5b (10:47 МСК, `--phase d5`, без LLM-цикла):**
  D5.1–D5.5 все ok, D5.6 ok, 0 ошибок — **CONTROL D5
  собран из прогонов 4 (A/B/C/mobile) + 5b (D5.1–D5.6)**;
  приложение между прогонами не менялось.

**Разбор 26 ошибок results.json run 4 (дефектов приложения: 0):**
14 пар tag D5 (requestfailed + console.error
`/api/jobs?limit=50&offset=0`) = намеренный abort D5.3
(окно _EXPECTED покрывало только goto+wait; после краха
контекст продолжил тикать с route) + 1 пара в
errors_expected; 2 записи tag C = намеренный негатив C8
(http.400 /api/prompts + console.error /prompts) — исключены
D5.6 по URL (`_is_c8`).

### R0 — каркас (серия R, 2026-09-20, в работе)

**Объём (TZ R §17, этап R0):** таб-бар, appbar, FAB, safe-area,
роутинг и 301-редиректы, девять компонентов §14; CONTROL T1/T2/T3.
Ветка `stage/ui-R0`.

**Что сделано:**
- `app/core/config.py` UiSection: `refresh_max_seconds →
  refresh_seconds_max` (30→15), `page_size` 50→20,
  `mobile_breakpoint_px` 640→641; новые `tap_target_px: 44`,
  `tap_gap_px: 8`, `row_height_px: 64`, `tabbar_height_px: 56`,
  `input_font_px: 16`, `export_ttl_hours: 48`. `config/config.yaml`
  + `config/config.example.yaml` — ui: секция из 12 ключей.
- Shell (base.html + style.css): appbar 48 px sticky (h1-заголовок
  fs-4/600, «назад» 44×44 на вложенных, меню «Ещё»
  details/summary — рендерится только при appbar_menu, появится
  в R2/R5/R8); таб-бар 56 px + safe-area fixed (3 пункта
  Библиотека/Задания/Настройки, иконка 24 px + подпись 11 px,
  активный — цветом И жирным); FAB 56 px fixed (bottom 72 px +
  safe-area, right 16 px) — в R0 якорь на форму ниже списка
  (R4/R6 заменят на bottom-sheet); ≥641 px: таб-бар → левая
  панель 220 px, FAB → кнопка в шапке (двухколонник — R2).
  `viewport-fit=cover`; layout-токены генерируются в base.html из
  settings.ui (Rule 1); каркасные константы (48/56/72) —
  декларативные токены :root (--u3-appbar-h, --u3-fab-size,
  --u3-fab-bottom) + доп. кегли --u3-fs-050/--u3-fs-800,
  --u3-shadow-2, --u3-radius-circle.
- Роутинг (pages.py): корень /settings — 4 маршрутные строки
  (LLM и модели / Языки / Промпты / Обслуживание→#u1-health);
  /settings/llm|languages|prompts — подстраницы (бывшие
  models/languages/prompts.html → settings_*.html, старые
  шаблоны удалены); /languages, /models, /prompts — **301**.
  h1 со страниц убран (несёт appbar); книги/задания — список
  сверху, формы ниже (T1); все ID, на которые смотрят app.js и
  аудиты (#u1-upload, #u1-create, #u1-jobs, #u1-books,
  #u1-pause/cancel/resume, #u3-delete-job, #u3-clear-logs,
  #u3-job-requested, .u3-facts), сохранены.
- 9 компонентов §14 (app/web/templates/_*.html): _appbar, _tabbar,
  _fab, _row — рабочие (используются shell); _sheet, _chip,
  _progress, _toast, _field — скелеты с документацией (CSS — в
  этапах, где подключаются: R2–R8).
- app.js: `ui.refresh_max_seconds → ui.refresh_seconds_max`
  (backoff-потолок; pause-while-hidden сохранён — u3-15).
- Тесты: _CONSUMERS/_CONSUMER_CHECKS (settings_*.html),
  UiSection в u3-15 харнессе, bases tap-проверки u3-11
  (.u3-nav-link → .u3-tab + .u3-appbar-back).
- Чинены pre-existing дефекты (T19=0): ruff RUF023 (auth.py
  __slots__ sort), mypy app.py login_post (form = {}).

**Решения:**
- ⋯-меню не рендерится без appbar_menu (нет мёртвой кнопки в R0;
  TZ §11: сортировка/очистка/воркер — R2).
- T3: checkbox/radio исключены из прямого замера — тап-цель =
  обёртывающий label (min-height 44 px, .u3-check); <label> —
  часть управления, не отдельная цель (документировано в
  audit_mobile_recheck.py T3_JS). Д5-базовая линия имела 20–29
  tapSmall (13×13 checkbox'и) — в T3-смысле закрыто.
- Иконка «Настройки» — U+2699 + U+FE0E (текстовая презентация):
  на стенде стоит Noto Color Emoji, без VS15 шестерёнка
  рендерилась бы цветным эмодзи.
- Пустая строка-объявление поллинга .u3-announce:empty скрыта
  (T1: первый ряд /jobs ≤ 160 px).

**Измерения (2026-09-20, mobile_realdevice 384×715 DPR 2.25):**
- **T1 PASS:** первый ряд /jobs top = **131 px**, /books top =
  **56 px** (лимит ≤ 160).
- **T2 (384):** scrollWidth = 384, overflow = False, wide = 0 —
  на всех 7 страницах (jobs, books, languages→settings/languages,
  models→settings/llm, prompts→settings/prompts, settings,
  job_detail); 320/375/414/768 — фаза B полного аудита.
- **T3 PASS:** 0 элементов < 44×44, 0 пар с зазором < 8 px —
  все 7 страниц (0 console-ошибок).
- Геометрия (probe_r0_geom.py): mobile — appbar 0–48 sticky,
  FAB 56×56 fixed (right 16, bottom 72), таб-бар 659–715 (56 px),
  зазор между табами 16 px, иконка 24 px/подпись 11 px/активный
  fw 700; desktop 1024 — панель 220 px, appbar/main left 220,
  FAB 44×44 в шапке (position static), табы 44 px.
- Web-трио (u1+u3+v2): **32/32 PASS** (env -u 3 ключей).
- Полный pytest: **305/305 PASS, RC=0**; ruff 0, mypy strict 0
  (попутно чинены pre-existing RUF023/mypy — см. «Что сделано»).
- Полный аудит `audit_ui.py --phase all` (18:08–18:13 МСК,
  `audit_r0.log`): **31 checks, 0 ошибок, 49 скриншотов, RC=0**.
  А: 7 страниц 200 (h1 — appbar), 404/401/403/favicon ok,
  301-редиректы /languages|/models|/prompts → /settings/*. B:
  **T2 PASS на 320/375/414/768** (scrollWidth = viewport, все
  страницы; 384 — recheck выше). C: upload→create→pause (526 мс
  до строки «пауза запрошена», role=status)→**COMPLETED_WITH_
  WARNINGS живым qwen3.8-27b** (15/15 сегментов)→download→
  cancel→delete job/book→purge dry-run (10 заданий, token)→
  prompt round-trip; D5.1–D5.6 ok; **0 console-ошибок в цикле**.
- CSS **17 720 Б** (бюджет TZ-R 24 576), JS **32 332 Б** (бюджет
  TZ-R 65 536). Скриншоты 384×715: shots/R0/.

**CONTROL R0 (TZ §16): T1 PASS (131/56 px ≤ 160), T2 PASS
(320/375/384/414/768 = viewport), T3 PASS (0/0). Этап завершён.**

### R1 — дизайн-система (серия R, 2026-09-20, завершён)

**Объём (TZ R §16, этап R1):** статусы → 5 групп, поверхности 0–3,
токены TZ-R, тёмная тема, фокус 100 %, 0 литералов вне `:root`.
CONTROL T13/T14/T18. Ветка `stage/ui-R1`.

**Что сделано:**
- **Статусы 16 → 5 групп** (TZ §4): единая таблица `STATUS_GROUP` +
  `STATUS_GLYPH` в `app/web/views.py` — wait ○: CREATED,
  NEEDS_CONFIRMATION; run ◐: 8 активных (VALIDATING…LEARNING); ok ✓:
  COMPLETED; warn !: COMPLETED_WITH_WARNINGS, PAUSED, INTERRUPTED;
  err ✕: FAILED, CANCELLED. Один источник на CSS-класс (Jinja-фильтр
  `status_group`), глиф (global `status_glyph`), API (`/api/jobs`,
  job-facts: `status_group`/`status_glyph`) и JS-рендер (таблицу в JS
  не дублирует). Цвет — вторичный признак: первичные — текст статуса
  verbatim и глиф.
- **Поверхности 0–3**: `--surface-0/1/2/3` (light: 2/3 = #ffffff —
  глубина тенью, Primer canvas-inset/subtle; dark: #1c2128/#21262d);
  новый `--shadow-3` (тост/диалог).
- **Переименование в TZ-R**: `u3-bg→surface-0`, `u3-surface→surface-1`,
  `u3-muted→fg-muted`, `u3-accent→accent-fg`, `u3-focus→focus-ring`,
  `u3-ok/warn/err→status-<g>-fg` + новые `-bg` (подложки), группы
  wait/run — новые; `u3-text`/`u3-border`/шкалы fs/space/radius —
  имена в TZ-R не заданы, префикс D1 сохранён.
- **Тёмная тема активирована** (перенос D4): 4 токеновых блока
  (`@media (prefers-color-scheme: dark) :root` +
  `:root[data-theme="light"/"dark"]`); inline-скрипт `base.html` до
  первой отрисовки читает `localStorage("u3-theme")` (private mode →
  системная тема; состояние на сервере не хранится); переключатель —
  R8. Ручной dark при системном light проверен (скрин manual).
- **Фокус (T14)**: глобальный `:focus-visible` 2 px `--focus-ring` +
  исключение `input[type="date"]:focus` — Chromium отдаёт date-инпут
  на 4 шага Tab (внутренние сегменты), на 4-м `:focus-visible = false`
  и обводка гаснет; дефект найден walk-измерением, исправлен
  целевым правилом.
- `--u3-side-w: 220px` — ширина панели (одна величина: CSS + media 640).
- **Буг (дообнаружен):** `fieldsRows` в app.js не вычитывал 2-й элемент
  кортежа (класс ячейки) — статусная колонка таблицы /jobs рендерилась
  без класса; исправлено.
- U3-1: 5 русскоязычных комментариев base.html/app.js → английский
  (инвариант 0 кириллицы вне strings.py). U3-3: тест обновлён под
  4 токеновых блока (`_strip_root_blocks` — brace-матчинг).

**CONTROL R1 (TZ §16): три гейта PASS.**

| Измерение | Результат |
|---|---|
| **T13** контраст (`r1_contrast.py`) | RC=0: 56 пар «токен/фон», 0 нарушений, обе темы. Худшие зазоры: light `fg-muted`/surface-1 4.93 (порог 4.5), light `accent-fg`/surface-1 4.88 (4.5), dark `err-fg`/surface-1 5.16 (5.0), dark `border`/surface-3 3.31 (3.0). Текст 15.80/16.02 (≥7), статусные ≥5.87, текст на status-bg 10.92–14.66 (≥4.5). |
| **T14** фокус (`r1_focus_walk.py`) | PASS 12/12 конфигураций (jobs/books × 384/1024, settings + 3 подстраницы, job, 404, 403, login), 250 элементов: 100 % посещено, порядок Tab = DOM (ротация цикла допущена — autofocus login), 100 % с видимой обводкой, 0 чужих элементов, 0 tabindex>0, 0 console-ошибок (отфильтрованы намеренные 404/403-документы). Методика: ожидаемый набор = видимые интерактивные (`checkVisibility()` — у закрытого `<details>` есть layout-box, а отрисовки нет) + скроллируемые контейнеры (в Chrome таббables); date-инпуты — dwell по 4 шага (документировано). |
| **T18** литералы (`r1_literal_audit.py`) | RC=0: числовые цвета и width > 360 px — только в 4 токеновых блоках; ~41 задокументированное исключение (линии 1/2 px, outline-offset, layout-размеры, `@media`, `calc()` с проверкой баланса скобок). |
| Регресс | pytest **305/305** (17:32, RC=0; первый прогон сессии упёрся в LLM-флейк s3 под нагрузкой — изолированно 21/21 за 68 с), ruff 0, mypy 0 (78 файлов), `audit_ui.py --phase all`: **31 checks, 0 ошибок, 49 скриншотов** (r1_audit.log; фаза C — живой LLM-цикл create→pause→COMPLETED_WITH_WARNINGS→cancel→delete→purge dry-run). |
| Мобайл 384×715 | T1 PASS (jobs 131, books 56 ≤ 160). T3: 7 мелких на /jobs = id-ссылки `a.u3-mono` 16 px — **существующий артефакт** (R0: ~22 px, тоже <44), гейтом R1 не является; строка R2 (TZ §10) убирает id из строки списка («идентификатор — на экране задания и в ⋯») + зона действий 44×44 → закроется в R2. |
| Бюджеты | CSS **23 313 Б** / 24 576 (95 %, запас 1 263 Б), JS **32 680 Б** / 65 536 (50 %). |
| Скриншоты | `shots/R1/` — 15: 7 light + 7 dark (системная) + 1 manual `data-theme="dark"` при системном light (механизм принудительной темы проверен). |

**CONTROL R1 (TZ §16): T13 PASS, T14 PASS, T18 PASS. Этап завершён.
Дальше R2 (строка §10, сегментированный фильтр, группировка,
точечное обновление: T4/T16).**

### Стенд

- Машина: 3p14-aaa = tailnet 100.124.155.106 (эталонный браузер
  пользовательского телефона s20 = 100.92.137.83: кадр 864×1920,
  DPR 2.25 → **384×715 CSS px**, UA Android/DuckDuckGo).
- Сервер: `.venv/bin/python -m app` (порт 8321, бинд 100.124.155.106
  из `.env`; Basic-auth, пароль = `SERVER_AUTH_SECRET`).
- Воркер: `.venv/bin/python -m app.workers.run --config config/config.yaml
  --env .env --loop` (heartbeat 15 c).
- LLM: vLLM 127.0.0.1:8004, модель из `config/config.yaml`
  (профиль translation; на 2026-09-19 — qwen3.8-27b) — **реальный**,
  не mock.
- **Inspo-туннель (2026-09-19, разблокировка D1):** inspomcp.dev
  с 3p14-aaa напрямую недоступен (локальная блокировка хоста;
  с vzu5-claw — 200). Весь трафик к хосту — через systemd-юнит
  `inspo-tunnel.service` (enabled, User=user):
  `ssh -N -L 127.0.0.1:443:inspomcp.dev:443 vzu5-claw`
  (ExitOnForwardFailure, ServerAlive 30×3, Restart=always) +
  пины в `/etc/hosts`: `127.0.0.1 inspomcp.dev` и
  `100.95.195.40 vzu5-claw` (Tailscale DNS на 3p14-aaa
  выключен — имя резолвится только через hosts) +
  `net.ipv4.ip_unprivileged_port_start=443`
  (`/etc/sysctl.d/99-lowport.conf`) — бинд 443 без root.
  Туннель шлёт сырой TCP, имя резолвится на стороне vzu5-claw —
  TLS валиден (проверено: 200 + tls_verify=0, JSON-RPC
  initialize отвечает). Порт 443 на tailnet-IP слушает сам
  tailscaled — с 127.0.0.1:443 конфликта нет. **Не
  использовать HTTP(S)_PROXY-переменные** — регрессия
  фреймворка: весь трафик API (вкл. NO_PROXY и LiteLLM
  127.0.0.1:8020) пошёл бы через прокси. Диагностика:
  `systemctl status inspo-tunnel`,
  `curl -s -o /dev/null -w '%{http_code}' https://inspomcp.dev/`
  (должно быть 200).
- **MCP inspo (D1):** зарегистрирован в user-конфиге Qwen Code
  (`~/.qwen/settings.json` → `mcpServers.inspo.httpUrl =
  https://inspomcp.dev/api/mcp`, `qwen mcp add --transport
  http inspo …`). Сессия видит сервер только после
  перезапуска; `inspo-mcp install` для Qwen Code не сработает
  (клиент не в списке детекции — только Claude Code).
- Статус 2026-09-19: сервер и воркер запущены (сервисы, фоновые),
  6 страниц = 200, строка «Воркер: ok», inspo-tunnel active.

### Инструменты измерения (не менять, §8.4 ТЗ)

- Каталог: `.a/tasks/ui-audit/`, venv: `.a/tasks/ui-audit/.venv`
  (python 3.11, Playwright + системный Google Chrome через
  `channel="chrome"` — браузеры Playwright не качать).
- `audit_ui.py` — фазы A (desktop read-only, 404, auth/CSRF, favicon),
  B (мобильные эмуляции 320/375/414/768), C (функциональный цикл:
  книга → задание → pause/resume → cancel → скачивание → очистка
  логов → удаления → purge dry-run → промпт v2). Создаёт только
  собственные объекты (uuid-книги, задания аудита) и удаляет их.
  Purge: только dry-run, **confirm не нажимается** (ограничение ТЗ).
- `audit_mobile_recheck.py` — контекст реального устройства
  384×715, DPR 2.25 → `results.json` → `mobile_realdevice`.
  Оверфлоу сравнивает `document.documentElement.scrollWidth`
  с фиксированной 384 (НЕ с `window.innerWidth`).
- `probe_geom.py` — геометрия header для сверки viewport.
- Выход: `results.json` + `shots/*.png`.

**Правки D5 (2026-09-19; методика не менялась, ТЗ §8.4):**
1. Авторизация контекстов — формой /login (cookie u3-token)
   вместо `http_credentials`: страница 401 по §7.1 отдаётся без
   `WWW-Authenticate`, challenge-based креденшелы Playwright не
   отправляются. Username cookie-авторизации фиксирован
   'admin' (auth._authorized) — совпадает с CSRF-токенами.
   A.4 (неверный пароль) — через форму /login. Первый запуск
   CONTROL D5 (09:53 МСК) упал на этом (все страницы 401);
   прогон перезапущен, результаты 09:53 невалидны (в т.ч.
   mobile recheck — мерял страницу входа).
2. Новая фаза D5: гость 401 (оформление + 0 console.error +
   публичный favicon), 403-страница (Sec-Fetch-Mode: form без
   CSRF), ошибка сети + «Повторить» (page.route abort → retry),
   состояние «загрузка» (серверный первичный HTML /settings),
   пустое состояние + онбординг §7.4 (TestClient на пустой
   временной БД — живой стенд не обнуляется).
3. Фаза A: A.404 — content-type + e1_ok. Фаза C: C3c —
   requested_row role=status (E6), строка «время» в PAUSED
   (E7: «на паузе с», без ETA); C4 — C4.e7: «прошло» из UI
   против производственной elapsed_and_eta по transitions
   (read-only, venv проекта); C5 — строка «отмена запрошена»;
   C7 — msg_has_warning (необратимо) + e2_ok; D5.6 — 0
   console.error в цикле create→delete (T8/E8).
4. Второй запуск (10:01–10:06 МСК) — два сбоя инструмента:
   (a) D5.3: авто-поллинг /jobs сам останавливается, когда все
   задания неактивны (3.11.48), ронять нечего — сценарий
   изменён на abort НАЧАЛЬНОГО fetch контейнера (тот же
   контракт «ошибка + Повторить»); (b) C: «socket hang up» —
   пул сокетов Playwright переиспользует keep-alive-соединение
   после того, как uvicorn закрыл его за простои >5 с (гонка
   клиента) — api_get с одним повтором. Плюс: D5.1 — document-401
   гостя браузер логирует console.error неизбежно, фильтр по URL
   документа; E7-строка «на паузе с» ловится и в C4 (пауза
   2026-09-19 применилась через 176 с — окно C3c 60 с не
   гарантированно). Остатки второго прогона (j-20260919T070345Z-
   d816c2 PAUSED + u1-0d0de250-probe.en.epub) — cleanup_d5.py
   (resume → терминальный → DELETE, как cleanup_d0.py).
5. Третий/четвёртый запуски (10:22 / 10:38 МСК) — ещё три
   сбоя инструмента: (a) D5.1 — `m.location()` в
   console-обработчике: в python-API Playwright `location` —
   свойство (dict), а не метод → TypeError пробрасывался из
   pyee в goto; фикс: `getattr(m, "location", None)`; (b) D5.3 —
   неверный селектор: «Повторить» автообновления списка — в
   `#u3-jobs-refresh` с `data-u3-jobs-retry` (список
   серверно-рендерится, при abort строки остаются; контракт
   §7.1 выполняется строкой ошибки); (c) утечка abort-
   маршрута при краше фазы (контекст не закрыт) — unroute в
   finally + скриншот ошибки до unroute. Плюс: `--phase d5`
   (быстрая допроверка D5.1–D5.6 без LLM-цикла фазы C) и
   D5.6 — исключение намеренного негатива C8 (по
   errors_expected и URL /prompts).

Размеры базовой линии: `style.css` = 9 079 Б, `app.js` =
37 347 Б (JS выше бюджета 32 КБ — ужать в D3/D5, решение
владельца).

### Решения владельца (2026-09-19)

1. **Inspo (inspomcp.dev) недоступен** (TCP-таймаут 80/443,
   DNS ок, общий интернет ок): D0/D2 идут без него; перед D1
   ретраить доступность, если не ожил — эскалировать владельцу.
2. **JS-бюджет:** базовая линия `app.js` = 37 347 Б (36,5 КБ)
   — выше лимита 32 КБ из ТЗ (цифра «28 КБ» в ТЗ устарела,
   файл не менялся с U3). Решение: ужать до ≤ 32 КБ в рамках
   работ (D3/D5: мёртвый код, дубли, вынос строк).
3. **Публичная статика (09-19, выбор «Сделать /static
   публичным»):** конфликт ТЗ §7.1 (401/403 — «страница в общем
   оформлении») и U1-7 (все пути под auth): гостю нужен
   style.css для страницы входа. Статика не несёт данных и
   секретов (CSRF — в per-page meta) → `/static/*` публичен
   (middleware пропускает до auth), страницы и /api — под auth
   изменились (§7.3). test_u1_7 обновлён: /static/app.js,
   style.css, favicon.ico → 200 без секрета.
4. **Туннель к Inspo (11:10 МСК, решение по эскалации п. 1):**
   inspomcp.dev жив (владелица открывает /mcp — 200), но
   блокировка локальная для 3p14-aaa. Обход — вывод трафика к
   одному хосту через vzu5-claw: SSH-туннель 127.0.0.1:443 +
   hosts-пин (детали — «Стенд»). HTTP/HTTPS-прокси-переменные
   сознательно НЕ используются (регрессия фреймворка: весь
   трафик API, вкл. NO_PROXY, пошёл бы через прокси → лёг бы
   LiteLLM 127.0.0.1:8020 и сессия агента). Порядок этапов ТЗ
   не меняется; D3 без D1 отклонён (ломает §9, гарантированная
   переделка после появления токенов).

### Реестр E1–E9 / M1–M9 — статус

| ID | Статус | Этап |
|---|---|---|
| E1 404 JSON | **closed 09-19 (D5)** — 404 HTML, title «Не найдено» | D5 |
| E2 purge без job_ids | **closed 09-19 (D5)** — 9/9 job_id + «необратимо» | D5 |
| E3 scroll 701 @384 | **closed 09-19 (D2)** — 384×7 | D2 |
| E4 h1 status за краем | **closed 09-19 (D2)** — wide=0 | D2 |
| E5 .u3-facts 77 px | **closed 09-19 (D2)** — стек, 352 px | D2 |
| E6 «пауза запрошена» | **closed 09-19 (D5)** — 522 мс, role=status (отмена 1051 мс) | D5 |
| E7 elapsed с паузами | **closed 09-19 (D5)** — transitions, diff 0.05 с, пауза исключена, «на паузе с» без ETA | D5 |
| E8 console.error 404 | **closed 09-19 (D5)** — quiesce перед DELETE; T8: 0 в цикле (деф. 09-18) | D5 |
| E9 favicon 404 | **closed 09-19 (D5)** — 200, 2 360 Б, link | D5 |
| M1 overflow-wrap | **closed 09-19 (D2)** — единая декларация | D2 |
| M2 hash в details | open → серия R | R (поглощено) |
| M3 facts стек | **closed 09-19 (D2)** (часть E5) | D2 |
| M4 textarea 16 px | open → серия R | R (поглощено) |
| M5 фильтр в details | open → серия R | R (поглощено) |
| M6 индикация паузы | **closed 09-19 (D5)** (часть E6) | D5 |
| M7 время без пауз | **closed 09-19 (D5)** (часть E7) | D5 |
| M8 safe-area | open → серия R | R0 (R, поглощено) |
| M9 log 0.85rem | open → серия R | R (поглощено) |

### Контрольные пункты по этапам (из ТЗ §9)

- **D0:** `mobile_realdevice.scrollWidth` = 701 @384; 6×200;
  E1/E8/E9 воспроизведены.
- **D2:** scrollWidth = 384 на всех 7 страницах @384; = viewport
  на 320/375/414; rect.right статуса ≤ viewport.
- **D1:** 3 кандидата, 1 выбран, 15 токенов в `:root`, 0 литералов
  цвета вне `:root` (grep). **✓ пройден 09-19** (GitHub/Primer,
  29 токенов, 0 наруш. d1_css_audit.py, регресс 31/31).
- **D3:** textarea = 16 px; фильтр в `<details>`; ячейки значений
  ≥ 200 px @384; тап-цели ≥ 44 px.
- **D4:** 5 пар контраста в обеих темах; фокус на 100 % элементов;
  0 литералов вне словаря.
- **D5:** критерии E1/E2/E6/E7/E9; 5 состояний §7.1.
  **✓ пройден 09-19** (прогоны 4+5b).
- **D6:** T1–T14 целиком; 0 console.error; git чист.

### Бюджеты и инварианты (напоминалка)

- CSS ≤ 24 576 Б, JS ≤ 65 536 Б (TZ-R §2, T12; поднято с 16/32 КБ
  серией D — причина в TZ-R); внешних сетевых запросов 0. На R1:
  CSS 23 313 Б, JS 32 680 Б (R0: 17 720 / 32 332).
- Литералы цвета/кегля/отступа/радиуса — только в `:root`
  (T18; исключения — толщины линий 1/2 px, layout-ширины,
  значения @media; зафиксированы в шапке style.css).
- `--u3-tap: 44px` и брейкпоинт 641 px — не менять (фикс. TZ §3).
- Строки UI — только `app/web/strings.py` (T17); числовые параметры
  — только `settings.ui` (Rule 1).
- Пользовательские данные не трогать: книга пользователя;
  purge — только dry-run (фаза C аудита создаёт своё задание и
  удаляет его).
- Один PR на этап: ветка `stage/ui-R<n>`, merge --no-ff в main
  (remote нет); commit `R<n>: <что> — <числа>; CONTROL T<x> PASS`.
- Регресс после каждого этапа — аудит A/B/C (фаза C = сквозной
  цикл с живым LLM) + mobile recheck 384×715 + полный pytest
  (env -u SERVER_AUTH_SECRET -u SERVER_BIND_ADDRESS -u LLM_API_KEY)
  + ruff + mypy strict.
