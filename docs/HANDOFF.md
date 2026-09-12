# HANDOFF — EpubTranslator

Самодостаточный документ для продолжения работы в свежей сессии.
Обновляется **перед** началом каждого этапа и **сразу после** каждого измерения.

---

## Текущее состояние (2026-09-12)

- **Завершено:** E0 (фундамент) — CONTROL PASS 5/5; **E1 (ENGINE_PROBE)** — CONTROL PASS 6/6;
  **E2 (Ридер EPUB и сегментатор)** — CONTROL PASS 6/6; **E3 (Job, воркер, возобновление)** —
  CONTROL PASS 6/6, 34/34 теста. Отчёты: `docs/ENGINE_PROBE.md`, `docs/CONTROL/E2.md`,
  `docs/CONTROL/E3.md`; evidence: `docs/CONTROL/evidence/E1…E3/`.
- **Далее:** **E4 — «Слой LLM»** (профили как единый источник настроек, собственный
  analysis-клиент тракта A с JSON-схемой и `raw/`, разрешение промптов
  `prompts/<task>/<src>/<tgt>/vN.yaml` пар→source→generic + `pair_required`, 6-сценарный
  mock, суммы токенов по профилям, `Cost: unknown` до реального прайса).

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
  Обязательные без по-умолчанию: `SERVER_BIND_ADDRESS`, `LLM_BASE_URL`, `LLM_MODEL`, `storage.root`.
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

## Открытые дефекты / ограничения

- Сервер ещё не запускается (этап U1) — `python -m app` пока только валидирует конфиг.
- Воркер запускается автономно (`python -m app.workers.run --config … --env …`);
  интеграция «сервер создаёт задания + воркер их исполняет» — в U1.
- Конвейер E3 импортирует `epub_translator` напрямую (D17) — временный шов, адаптер
  `TranslationEngine` с атомарным выходом (`.partial` → `os.replace`) появится в E5.
- LLM-эндпоинт — плейсхолдер (`http://127.0.0.1:9999/v1`, модель `probe-model`) до **BLOCKER-1**
  (нужен реальный OpenAI-совместимый эндпоинт от владельца).
- EPUBCheck-джар ещё не скачан (нужен для E5; Java 17 на месте).
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
