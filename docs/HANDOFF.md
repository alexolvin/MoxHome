# HANDOFF — EpubTranslator

Самодостаточный документ для продолжения работы в свежей сессии.
Обновляется **перед** началом каждого этапа и **сразу после** каждого измерения.

---

## Текущее состояние (2026-09-13)

- **Завершено:** E0 (фундамент) — CONTROL PASS 5/5; **E1 (ENGINE_PROBE)** — CONTROL PASS 6/6;
  **E2 (Ридер EPUB и сегментатор)** — CONTROL PASS 6/6; **E3 (Job, воркер, возобновление)** —
  CONTROL PASS 6/6; **E4 (Слой LLM)** — CONTROL PASS 6/6; **E5 (Тракт T: перевод)** —
  CONTROL PASS 8/8; **E6 (Выравнивание оригинал ↔ перевод)** — CONTROL PASS 7/7
  (+T-E6-8), 95/95 теста (на mock, BLOCKER-1 — реальные прогоны не заявляются).
  Отчёты: `docs/ENGINE_PROBE.md`, `docs/CONTROL/E2.md`, `docs/CONTROL/E3.md`,
  `docs/CONTROL/E4.md`, `docs/CONTROL/E5.md`, `docs/CONTROL/E6.md`;
  evidence: `docs/CONTROL/evidence/E1…E6/`.
- **Далее:** **L0 — «Реестр языков, матрица пар, определение языка»** (TZ_02:
  `langdetect` с фиксированным seed, сверка с `dc:language`, резолвер ресурсов пары
  supported/degraded/unsupported; CONTROL L0-1…L0-6, тесты T-L0-1…T-L0-8).

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

## Открытые дефекты / ограничения

- Сервер ещё не запускается (этап U1) — `python -m app` пока только валидирует конфиг.
- Воркер запускается автономно (`python -m app.workers.run --config … --env …`);
  интеграция «сервер создаёт задания + воркер их исполняет» — в U1.
- Показ доли выравнивания в UI (E6-6, «показан в UI») — в U1: данные готовы
  (единая колонка `jobs.alignment_coverage`, diff 0 с `quality.json` и
  `alignment_report.json`; ниже порога → `COMPLETED_WITH_WARNINGS`).
- Стадия `analyzing` в pipeline — заглушка (E4): клиент анализа, реестр задач и промпты
  v1 готовы, исполнение задач в конвейере — этапы L1/L2; профиль `analysis` не входит
  в `config_hash` (D21) — смена analysis-настроек не инвалидирует готовый перевод.
- LLM-эндпоинт — плейсхолдер (`http://127.0.0.1:9999/v1`, модель `probe-model`) до **BLOCKER-1**
  (нужен реальный OpenAI-совместимый эндпоинт от владельца). Весь тракт T (E5) проверен
  на mock — PASS реальных прогонов не заявлен; соблюдение глоссария моделью — гарантия
  только с L3 (жёсткая проверка/правка).
- На E3 найден и исправлен реальный дефект: heartbeat-поток использовал
  `sqlite3.Connection` главной нити → `ProgrammingError`, аренда не продлевалась.
  Регрессия зафиксирована в T-E3-1 (kill позже истечения исходной аренды +
  проверка `lease_expires_at ≥ t_kill`).

## Среда машины (verbatim-снимок 2026-09-12)

- хост `3p14-aaa` (Tailscale 100.124.155.106, tailnet `3p14-aaa.tail46a65f.ts.net`);
- 24 ядра, 62 GB RAM, 348 GB свободно на /mdisk; OpenJDK 17.0.20; PyPI доступен.

## Правила (напоминание)

Rule 1–8 из `TZ_EpubTranslator_00_OVERVIEW.md` действуют на всех этапах: запрет хардкода,
доказуемость, verbatim-захваты, все пять языков, LLM не арбитр, fail closed, запрет расширения
допуска, запрет дублирующих механизмов. Язык — данные, не код (L0–L2).
