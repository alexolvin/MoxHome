# MTPE BASELINE — сверка стартового состояния (M0)

**Дата:** 2026-09-28 · **Коммит базы:** `8c61a08` (main HEAD) · **Ветка:** `stage/mtpe-M0`

Метод: read-only сверка TZ-MTPE §2 (состояние проекта, зафиксированное ТЗ на 2026-09-21) с
фактическим кодом на `8c61a08`. Rule 4: каждая строка — статус + доказательство
(`path:line` + дословная выдержка). Ничего в `app/`, `config/`, `templates/` не изменялось (M0-5).

**Проверки на базе `8c61a08`:** `ruff check app/` — 0; `mypy app/` — 0 (85 source files);
`pytest` — 433 passed, 2 warnings, RC=0 (1289 с). Журналы: `.a/tasks/mtpe/m0_lint.log`,
`.a/tasks/mtpe/m0_pytest.log` (шапка `baseline_commit=8c61a08…`).

Легенда статуса: **совпадает** — строка ТЗ точна; **совпадает (уточнение)** — ядро строки
точное, ТЗ неточно в детали (зафиксировано, решение не требуется); **отличается** — строка
ТЗ неверна → запись в `docs/DECISIONS.md` (D115–D119).

---

## §2.1 Стек и рамка

| Область | Статус | Доказательство (path:line + выдержка) |
|---|---|---|
| Хост | совпадает (уточнение: стенд-порт) | `hostname`=3p14-aaa; `/etc/os-release` AlmaLinux 9; tailnet `100.124.155.106`. Уточнение: ТЗ «стенд `:8321`» — исторический боевой порт; текущий пост-V стенд — `:8422` (`docs/UI_HANDOFF.md:3168`) |
| Веб | совпадает | `app/web/pages.py:1441` `@router.get("/settings/llm")` → `TemplateResponse(request, "settings_llm.html", …)` (FastAPI+Jinja, серверный HTML, `app/web/templates/`); прогрессивное улучшение: `_sheet_create.html:2-6` «no-JS — радио книг + радио-чипы (нативно, POST /jobs/new → 303)» |
| Бюджеты | **отличается** | `scripts/v1_measure.py:4,25-26`: «CSS ≤ 24 576 Б … JS ≤ 81 920 Б»; `CSS_LIMIT = 24576`, `JS_LIMIT = 81920`. ТЗ: JS 65 536 — устарело (D114 поднял JS 65 536→81 920; CSS 24 576 не менялся). → **D117** |
| Строки и токены | совпадает | `app/web/strings.py:1-7` «Все видимые строки UI — только здесь: плоский словарь «ключ → строка»»; `docs/design-system.md:30,40` токены `:root` / `:root[data-theme="light"]` |
| Числа UI | совпадает | `config/config.yaml:104-116` `ui:` — page_size:20, tap_target_px:44, row_height_px:64, mobile_breakpoint_px:641, … (только в YAML) |
| Аутентификация | совпадает | `app/web/auth.py:14-33` «каждый мутирующий запрос обязан нести CSRF-токен … HMAC(secret, "u3-csrf|<username>") … 403»; `WWW_AUTHENTICATE = 'Basic realm="epubtranslator"'` (`auth.py:48`); cookie `u3-token` (`auth.py:15-19`); один секрет `SERVER_AUTH_SECRET` (`auth.py:21-24`, fail-closed) |
| CLI | совпадает | `app/cli.py:16-19` «Коды выхода: 0 — успех; 1 — неуспешное задание (FAILED/CANCELLED/INTERRUPTED); 2 — конфигурация; 3 — валидация входа; 4 — недоступность LLM; 5 — отказ валидации выхода» |
| Правило | совпадает | TZ-UI-R:75 «6. Одна операция — одно место. Дублирование запуска, удаления или создания на двух экранах — критический дефект» |

## §2.2 Перевод и анализ

| Компонент | Статус | Доказательство |
|---|---|---|
| Движок | совпадает | `pyproject.toml:7` `epub-translator>=0.1.10`; `docs/ENGINE_PROBE.md` §2 P2 signature `translate(source_path, target_path, target_language, submit, user_prompt=None, …)` — file→file, один `user_prompt` на книгу, без посегментного входа и без параметра исходного языка; вызов `engine.py:134-142` |
| Тракт T | совпадает | `engine.py:110-114` `_on_progress(progress)` (callback); возобновление `cache_path=` `engine.py:198,210` (model-isolated кэш) |
| Тракт A | совпадает | `app/epub/reader.py:1-8` «детерминированное чтение в Book → Chapter → Segment»; `app/domain/book.py:18-26` `Segment` с `segment_id` (sha256 от пути+XPath+норм. текста, стабилен, E2.3) |
| E5 | совпадает | `app/adapters/epub_engine/atomic.py:54-58` `fsync_file(partial); repair_epub2_opf_namespaces(partial); validate_output_epub(…)`; `:77-78` `partial.replace(target); fsync_dir(…)` (ос.replace); EPUBCheck `atomic.py:62-69`; «mimetype первой записью (STORED)» — `ENGINE_PROBE.md` §3 P3 п.9; детерм. QC `pipeline.py:624-625` |
| E6 | совпадает | `app/epub/aligner.py:2-12` (механизм из измеренной разметки движка, `id__translated`); `:58` `TRANSLATED_MARKER="__translated"`; `:79-90` `AlignedPair` с `translation_xpath` (патч-адрес, E6.7); «после правки ... пересчитывается — TZ E6.7» |
| L2 | совпадает | `config/languages/ru/pack.yaml:16-24` `paradigm_forms` (6 падежей × оба числа = 12 форм); `glossary.py:17,20` `build_glossary_prompt` → `user_prompt`; детектор `pipeline.py:262-267` `_glossary_consistency` (L2.6); стилевой профиль `pipeline.py:2877` `store.load_style(…)` |
| L3 | совпадает | `job.py:34` `GLOSSARY_FIX`; гейт `pipeline.py:796-803` `if not glossary_cfg.enabled …`; 5-пунктная валидация `fix.py:323-378` (П.1 форма парадигмы, П.3 разметка, П.4 язык, П.5 не пуста…); лимит доли `config.py:232` `fix_max_segment_share` (`pipeline.py:837-841` ДО LLM); аудит `pipeline.py:2951-2955` quality.log; D60 `enabled: bool = False` `config.py:205-207` |
| S1–S5 | совпадает | `app/learning/__init__.py:1-6` «Материалы строятся ТОЛЬКО по тексту ПОСЛЕ L3 (стадия LEARNING следует за GLOSSARY_FIX)»; порядок — `TRANSITIONS` `GLOSSARY_FIX→LEARNING` (`job.py:82`) |
| Языки | совпадает | `config/languages/{en,de,fr,nl,it,ru}/pack.yaml` (6 пакетов); резолвер `app/languages/resolver.py:1-4` + `PairStatus` supported/degraded/unsupported (`resolver.py:39-42`) |

## §2.3 Конвейер и задания

| Компонент | Статус | Доказательство |
|---|---|---|
| Конвейер | совпадает (уточнение: место) | 16 статусов `JobStatus` — `app/domain/job.py:24-43`; `TRANSITIONS` — `job.py:95` (Mapping[frozenset]); уточнение: ТЗ указывает `app/workers/pipeline.py` — фактически статусы/TRANSITIONS в `domain/job.py` (pipeline.py — исполнитель стадий, `NEXT_STAGE` `job.py:77-85`); проверка перехода `jobs_repo.py:364-367` |
| Воркер | совпадает | `app/workers/run.py:48-50` `Worker(…)` + `run_forever`; `worker.py:75` «одно активное задание»; lease `worker.py:112-113` `settings.server.lease_seconds` (default 30.0, `config.py:123`); очередь `jobs_repo.py:553` `ORDER BY queue_rank DESC, created_at ASC, id ASC` |
| Прогресс | **отличается** | `jobs.progress` = дробь движка: `engine.py:15-18` «движок вызывает on_progress дробью 0..1 (свои веса: TOC 5%, metadata 5%, главы 90%)»; `epub_translator …/translator.py` `toc_weight=0.05, metadata_weight=0.05, chapters_weight=…` (по главам). НЕ «подтверждённые сегменты/всего»: сегменты X/Y — только UI-метка (`pipeline.py:567-570`). Время по `transitions` — совпадает (`jobs_repo.py:398-403`, `views.py:316` elapsed_and_eta). → **D118** |
| Артефакты | совпадает | каталог `config.py:479-484` `job_dir`; сырые ответы `jobs/<id>/raw/` — `analysis.py:156,280-292` `_save_raw`; `quality.log` — `app/core/logging.py:108`; `quality.json` — `pipeline.py:640` (+ стадии L3/S) |

## §2.4 LLM

| Компонент | Статус | Доказательство |
|---|---|---|
| Профили | совпадает | `config/config.yaml:12-57` `llm.profiles:` — translation/fill/analysis, все `base_url: http://127.0.0.1:8004/v1`, `model: qwen3.8-27b`, `extra_body.chat_template_kwargs.enable_thinking: false` |
| Ключи | совпадает (уточнение: маркер) | `config.py:36-42` `ALL_ENV_KEYS=(…, LLM_API_KEY, LLM_ANALYSIS_API_KEY)`; `:102-106` `api_key_for` (analysis→analysis_api_key or api_key); redaction `config.py:547-550` `secrets()`. Уточнение: маркер — `[REDACTED]` (`app/core/logging.py:59-63` `line.replace(secret, "[REDACTED]")`), не `***`, как в ТЗ; в API ключи не отдаются (`profiles.py` «api_key: задан/не задан») |
| Режим рассуждения | совпадает (уточнение) | `config.yaml:29-31,42-44,54-56` `enable_thinking: false` в 3 профилях (D80); применяется `analysis.py:193` `request_body.update(profile.extra_body)`. Уточнение: не code-enforced — `extra_body: dict = Field(default_factory=dict)` (`config.py:76`), обязательность только по значению конфига |
| Клиент тракта A | совпадает | `analysis.py:177` `total_attempts = … profile.retry_times` (default 3, `config.py:66`); `:198` `httpx.Client(timeout=…)` (raw, ручной retry); raw в `jobs/<id>/raw/` (`:156`); `AnalysisTaskError` (`:35,221-224`), пустой артефакт не создаётся (пишет только `_finish` `:294`). Структурированный ответ — БЕЗ OpenAI `response_format`: JSON-схема в system-prompt + `jsonschema.validate` (`analysis.py:229-235,258-`) |
| Промпты | совпадает | `config.py:511-515` `prompts_dir` = `storage.root/prompts_root`; правка только новой версией `api.py:1267-1276` `_next_prompt_version` (перезапись запрещена), `:1279-1285` атомарно (409 если `final.exists()`) |
| Идемпотентность | **отличается** | Базовый ключ `pipeline.py:179-183` `input_hash` = sha256(input_sha256+config_sha256+prompt_version+target_language); `config_hash` `jobs.py:43-56` (translation/fill, analysis НЕ входит — D21). Отличие: S-stage key `pipeline.py:921-932` = `sha256(input_hash \| glossary_hash \| learning_level \| learning_cefr)` — БЕЗ отпечатка analysis (provider+model), хотя ТЗ утверждает «ключи S-этапов расширены отпечатком профиля analysis». Модель analysis — только в артефактах (`pipeline.py:1093-1094`). → **D119** |
| Mock | совпадает | `mock_llm_server.py:89-100` rot3-эхо; `:1082` `_fill_response(user, scenario)`; dispatch `:1094-1104`; инъекция `set_scenario` (`:146-148`) / `POST /_control` (`:1126-1132`); 23 сценария |
| Сервер | совпадает | `config.yaml:13-15` «vLLM, узел 3p14-aaa, порт 8004, локально 127.0.0.1:8004; модель qwen3.8-27b (FP8)»; все 3 профиля — на тот же endpoint/model |

## §2.5 Интерфейс

| Компонент | Статус | Доказательство |
|---|---|---|
| Серия R | **отличается** | ТЗ (2026-09-21): «R0–R4 смержены, шёл `stage/ui-R5`; R6–R9 не начаты». Факт: **R0–R9 И V0–V4 полностью смержены** (merge-коммиты; HEAD `8c61a08` «серия R завершена», `2a13dfe` «серия V завершена»). M0 стартует с полностью завершённого UI. → **D115** |
| Мастер, шаг 2 | совпадает (уточнение) | V3 §7.6 переделал создание в **одну страницу** с 4 строками-чипами: `_sheet_create.html:55` Языки, `:64` Режим, `:72` Уровень · Estimated CEFR level, `:82` Материалы. Уточнение: «Уровень» объединён с CEFR; объём (learning_level) — в блоке «Ещё» (`:90-100`). Пятой строки «Способ перевода» нет (это M6) |
| `/settings/llm` | **отличается** | ТЗ: «ещё не реализован». Факт: **реализован в R8** — `pages.py:1441` (GET), `:1447` (POST no-JS 303), `:1480` (POST /probe), шаблон `settings_llm.html`; строка-вход `settings.html:21`. Механизм записи конфига тоже уже есть. → **D116** |

---

## Пункт 2 — дословная фиксация (2.1–2.10)

### 2.1 Механизм фиксации конфига задания
Снимок — **хэш**, не полный JSON настроек. В `create_job()` (`app/services/jobs.py:197-199`), до INSERT:
`config_sha256 = config_hash(settings, target_language, mode)`, `prompt_version = PROMPT_VERSION`,
`input_sha256 = sha256(source_path)`. `config_hash` (`jobs.py:49-61`) = `sha256(json.dumps({
"profiles": {translation, fill}, "segmentation": block_tags, "target_language", "mode",
"submit_mode_bilingual", "prompt_version"}, sort_keys=True))`. Профиль `analysis` в хэш НЕ входит (D21).
Пользовательские поля (`target_language, mode, learning_level, learning_cefr, priority, param_chips`)
тоже фиксируются в строке `jobs` при создании (`jobs.py:188-205`). Полные LLM-настройки при исполнении
**перечитываются с диска перед каждым заданием** (`_reload_config`, `worker.py:95-110`); «снимок на
задание» = объект `Pipeline` (Settings), захваченный в момент claim (`worker.py:116-118` — «подмена
конфигурации между стадиями недопустима»). Справочный артефакт `plan.json` пишется на PLANNING.

### 2.2 Имена переменных `.env`
`config.py:36-42` `ALL_ENV_KEYS = (SERVER_BIND_ADDRESS, SERVER_AUTH_SECRET, ALLOW_WILDCARD_BIND,
LLM_API_KEY, LLM_ANALYSIS_API_KEY)`. `config.py:46` `PROFILE_NAMES = (translation, fill, analysis)`.
`LLM_API_KEY` → translation + fill; `LLM_ANALYSIS_API_KEY` → analysis (пусто → LLM_API_KEY,
`api_key_for` `config.py:102-106`). Новые для mt/postedit по TZ §5.2: `LLM_MT_API_KEY`,
`LLM_POSTEDIT_API_KEY` — добавит M1.

### 2.3 Загрузчик конфига, неизвестные поля
`config.py:59` `LLMProfile` — `model_config = ConfigDict(frozen=True)` (без `extra`). Профили читаются
`LLMProfile.model_validate(_sub(profiles_sub, name))` (`config.py:636-638`). Pydantic v2 по умолчанию
`extra='ignore'` → неизвестное поле **тихо отбрасывается** (без ошибки; файл на диске не меняется).
Контраст: language packs явно `extra="forbid"` (`app/languages/registry.py:37`).

### 2.4 Глоссарий L2: статус сборки и гейт
Дефолт `enabled: bool = False` (`config.py:207`, D60). Сборка — внутри стадии `run_translating`
(`pipeline.py:438-443`): `if self._glossary_provider is not None: … elif settings.glossary.enabled:
bundle = self._book_glossary_bundle(repo, job)`. При `enabled: false` глоссарий = пустой кортеж,
сборка не вызывается. Страховка — `app/glossary/service.py:60-63` `GlossaryDisabledError`. Проверка
согласованности L2.6 тоже гейтится (`pipeline.py:268-269`).

### 2.5 `SubmitKind` + `ENGINE_PROBE.md`
`SubmitKind(Enum)` — в движке (`epub_translator/xml_translator/submitter.py:11-14`): `REPLACE`,
`APPEND_BLOCK`, `APPEND_TEXT`. Все три использует проект (`engine.py:48-53` `_SUBMIT_BY_MODE`).
`docs/ENGINE_PROBE.md` покрывает каждый: §3 P3 — APPEND_BLOCK (`:46`), §4 P4 — APPEND_TEXT/REPLACE (`:164`).

### 2.6 Клиент `app/llm/`
Транспорт — raw `httpx.Client` (`analysis.py:198`), ручной цикл повторов `profile.retry_times`
(`analysis.py:177`; default 3, `config.py:66`). Сырые ответы каждой попытки — в `jobs/<id>/raw/`
(`analysis.py:156,280-292`). Структурированный ответ — БЕЗ `response_format`: JSON-схема в system-prompt
+ `jsonschema.validate` (`analysis.py:229-235,258-`). Отказ — `AnalysisTaskError` (`analysis.py:35,221-224`);
пустой артефакт не создаётся (артефакт пишет только `_finish`, `:294`).

### 2.7 Механизм инъекции сценариев mock E4
Состояние — строка `state.scenario`. Установка: программно `set_scenario(mode)` (`mock_llm_server.py:146-148`)
/ HTTP `POST /_control` (`:1126-1132`). Диспетчер `_build_response(kind, scenario, user, system)`
(`:1094-1104`); fill — `_fill_response` (`:1082`). 23 сценария: ok, fill_garbage, translate_empty,
http500, timeout, empty, bad_json, refuse, glossary_inconsistent(_triple), glossary_fix_no_term,
vocab_ghost, vocab_generated_example, vocab_schema_bad, mwe_bad_id, grammar_bad_id, grammar_bad_quote,
grammar_note_too_long, s4_entity_ghost, s4_mc_two_correct, s4_mc_dup_option, s4_gap_missing_word,
s4_vocab_ghost.

### 2.8 Механизм миграций `jobs`
Собственный, без alembic — `app/storage/database.py`: `SCHEMA` DDL (`CREATE TABLE IF NOT EXISTS`, jobs
`:24-56`) + `migrate()` (`:155`, вызов из `__init__` `:128`): `PRAGMA table_info(jobs)` +
`ALTER TABLE jobs ADD COLUMN` для колонок из `_MIGRATED_JOB_COLUMNS` (`:101`; 8 колонок: tokens_total,
mode, alignment_coverage, source_language, learning_level, learning_cefr, queue_rank, param_chips) +
бекап-индекс `idx_jobs_claim`. Чтобы добавить колонку: DDL в `SCHEMA` + запись в `_MIGRATED_JOB_COLUMNS`
+ поле в dataclass `Job` (`domain/job.py:134`) + колонка в INSERT (`jobs_repo.py:89-115`).
**`jobs.pipeline` и `jobs.phase` НЕ СУЩЕСТВУЮТ** (ни в `SCHEMA`, ни в `_MIGRATED_JOB_COLUMNS`, ни в ORM) — добавит M1.

### 2.9 Фактическое состояние серии R
R0–R9 и V0–V4 полностью смержены в main (HEAD `8c61a08`). `/settings/llm` существует
(`pages.py:1441/1447/1480` + `settings_llm.html`, R8). Стенд сейчас `100.124.155.106:8422`.

### 2.10 Библиотека записи YAML с сохранением комментариев
`ruamel.yaml` **НЕТ** (только `pyyaml>=6.0.3`, `pyproject.toml:18`; 0 совпадений в `uv.lock`).
Атомарная запись конфига с сохранением комментариев **уже реализована в R8** — строковый line-editor
`app/core/config_edit.py:1-7` («правка — на уровне строк», «ruamel.yaml не вводится») + атомарный
`app/services/llm_config.update_llm_config`. M1 переиспользует, не вводит новую lib.

---

## Расхождения (каждое «отличается» → запись в `docs/DECISIONS.md`)

| # | Строка ТЗ | Суть | DECISIONS |
|---|---|---|---|
| 1 | §2.5 Серия R | R0–R9 + V0–V4 все в main (ТЗ: R6–R9 не начаты); M-серия стартует после полного UI, а не «между R7 и R8» | **D115** |
| 2 | §2.5 /settings/llm + §3.3 | Экран и механизм записи конфига уже сделаны в R8; M1 переиспользует + расширяет, M2 переделывает существующий экран под §6 | **D116** |
| 3 | §2.1 Бюджеты | JS ≤ 81 920 Б (не 65 536); CSS 24 576 | **D117** |
| 4 | §2.3 Прогресс | single: дробь движка (TOC 5% + metadata 5% + главы 90%), не «сегменты/всего»; mt_pe — отдельная формула §4.4 | **D118** |
| 5 | §2.4 Идемпотентность | S-stage key без отпечатка analysis (provider+model) | **D119** |

**Уточнения** (решение не требуется, зафиксированы в таблицах выше): стенд-порт 8422 (§2.1);
место статусов/TRANSITIONS — `app/domain/job.py` (§2.3); маркер redaction `[REDACTED]` (§2.4);
`enable_thinking: false` — config-value, не code-enforced (§2.4); мастер — одна страница, «Уровень»=CEFR (§2.5).
