# EpubTranslator

Self-hosted конвейер: перевод EPUB + генерация учебных материалов. На входе —
EPUB; на выходе — двуязычный (или одноязычный) EPUB и учебный пакет:
карточки лексики (CSV/Anki/JSON), устойчивые словосочетания, грамматические
заметки, CEFR-разметка. LLM — любой OpenAI-совместимый эндпоинт
(vLLM, Ollama, ...); приложение не привязано к провайдеру.

Два процесса: **Web-сервер** (UI + API) и **воркер** (исполнение заданий).
Воркер отдельный процесс — запуск из UI не реализован (ограничение, не
притворяемся, что он запускается).

## Требования

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) (менеджер зависимостей проекта; `uv.lock` в репозитории)
- Java 17+ — для EPUBCheck (опционально; см. шаг 1)
- LLM-эндпоинт: OpenAI-совместимый (`/v1/chat/completions`)

## Быстрый старт (7 шагов)

Все команды выполняются **из корня проекта**.

### Шаг 1. Установка

```bash
git clone <адрес репозитория> EpubTranslator
cd EpubTranslator

uv venv
uv sync
```

spaCy-модели исходных языков (en/de/fr/it/nl — Rule 4; версии 3.8.0):

```bash
uv pip install --python .venv/bin/python \
  https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.8.0/fr_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/it_core_news_sm-3.8.0/it_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/nl_core_news_sm-3.8.0/nl_core_news_sm-3.8.0-py3-none-any.whl
```

Важно: модели ставятся ПОСЛЕ `uv sync` — повторный `uv sync` (без `--inexact`)
уберёт пакеты, которых нет в `uv.lock`.

EPUBCheck (опционально; нужен Java 17+): скачать дистрибутив W3C 5.3.0
([epubcheck.org](https://www.epubcheck.org/download/)) и распаковать в
`data/tools/` (получится каталог `data/tools/epubcheck-5.3.0/` с
`epubcheck.jar` и `lib/`), затем в `config.yaml` указать
`epubcheck.jar_path: data/tools/epubcheck-5.3.0/epubcheck.jar`
(`lib/` рядом с jar — classpath собирается автоматически). Без EPUBCheck
задания завершаются `COMPLETED_WITH_WARNINGS` с причиной
`VALIDATION_UNAVAILABLE` (Rule 6: честное предупреждение, не «успех»).

### Шаг 2. Настройка LLM

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

- `config/config.yaml` — `llm.profiles.<translation|fill|analysis>`:
  `base_url` и `model` (единственный источник адресов и моделей, E4.1) +
  `storage.root` — корень данных (поставьте свой каталог проекта).
- `extra_body` (шаблон `config.example.yaml`, комментарий к полю) —
  дополнительные ключи тела запроса в LLM. Если модель — thinking-модель
  (Qwen3 на vLLM), во всех трёх профилях задайте
  `extra_body: {chat_template_kwargs: {enable_thinking: false}}` — без него
  reasoning-поток попадает в контент ответа и блоки не переводятся
  (измерено при чистой установке: 0/15 блоков,
  `COMPLETED_WITH_WARNINGS`).
- `.env` — только секреты: `LLM_API_KEY` (опционально
  `LLM_ANALYSIS_API_KEY` — отдельный ключ профиля analysis),
  `SERVER_BIND_ADDRESS` (по умолчанию `127.0.0.1`), опционально
  `SERVER_AUTH_SECRET` — секрет доступа к UI/API. Без секрета UI доступен
  только для чтения: изменяющие операции в UI (загрузка книги, создание
  задания, удаление, очистка логов, версии промптов) отвечают `403`
  (fail closed, U3) — задайте секрет и перезапустите сервер, либо
  создавайте задания через CLI (шаг 5).

### Шаг 3. Запуск сервера

Два процесса (в двух терминалах):

```bash
.venv/bin/python -m app                                   # Web UI + API (порт: server.port, по умолчанию 8321)
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop   # воркер
```

### Шаг 4. Открытие UI

В браузере: `http://127.0.0.1:8321/` (порт — `server.port` в `config.yaml`).
Если задан `SERVER_AUTH_SECRET` — HTTP Basic: любое имя, пароль = секрет.
Разделы: Books (книги), Jobs (задания), Languages, Models, Prompts, Settings.

Страница Jobs показывает статус воркера: «ok (pid …)», пока воркер жив,
либо «error» с пояснением (heartbeat не найден / оффлайн). Статус читается
из файла `.worker-heartbeat` в корне `storage.root`, который воркер в
режиме `--loop` периодически перезаписывает (в режиме без `--loop` он не
ведётся). Список заданий на странице обновляется сам, пока есть активные
задания.

### Шаг 5. Перевод тестовой книги

В репозитории есть тестовая книга: `tests/fixtures/epub/probe.en.epub`
(2 главы, EN, детерминированная фикстура).

**Через UI** (изменяющие операции требуют `SERVER_AUTH_SECRET` — см.
шаг 2; без него загрузка книги и создание задания отвечают `403`):
Books → выбрать файл → «Загрузить» → Jobs → форма
(target `ru`, режим `bilingual`, опционально уровень learning) →
«Создать задание». Если исходный язык определён неуверенно, задание ждёт
подтверждения (статус `NEEDS_CONFIRMATION`) — подтвердите его на странице
задания.

**Через CLI (эквивалент):**

```bash
.venv/bin/python -m app.cli translate tests/fixtures/epub/probe.en.epub \
    --source en --target ru --mode bilingual --level Learning --wait
```

`--wait` — ждать терминальный статус; код выхода отражает исход. Воркер
обязан быть запущен (шаг 3).

### Шаг 6. Поиск результата

**Через UI:** Jobs → задание → статус `COMPLETED` / `COMPLETED_WITH_WARNINGS`.
На странице задания — ссылки на результат: «Скачать результат» (артефакт
`main`: при Learning — `learning.epub`, иначе `target.epub`) и именованные
ссылки на каждый имеющийся файл: «Перевод (target.epub)», «Учебная книга
(learning.epub)», «карточки CSV» / «карточки Anki TSV» / «карточки JSON»,
«отчёт QC (quality.json)». Та же страница показывает время создания/
обновления (человекочитаемое) и таймлайн стадий задания (переходы из БД).

**Через CLI:**

```bash
.venv/bin/python -m app.cli job list
.venv/bin/python -m app.cli job status <job_id>
.venv/bin/python -m app.cli export cards <job_id> --format csv    # карточки (csv/anki/json)
.venv/bin/python -m app.cli validate <storage.root>/jobs/<job_id>/target.epub   # структура + EPUBCheck
```

Файлы результата: `<storage.root>/jobs/<job_id>/` — `target.epub`,
`learning.epub`, `cards.csv` / `cards.anki.tsv` / `cards.json`,
`quality.json`, `quality.log`.

### Шаг 7. Возобновление прерванного задания

Прерванное (воркер убит, `kill`) задание восстанавливается **воркером**:
арена (lease) задания истекает, следующий запуск воркера помечает задание
`INTERRUPTED` (единственный механизм, E3-1) и возобновляет его с
последнего подтверждённого результата стадии. Задание не теряется:
завершённые стадии сохранены в БД, завершённые группы сегментов берутся из
кэша (E3-4: повторный перевод ≤ 5%).

То есть «возобновление прерванного задания» — это перезапуск воркера:

```bash
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop
```

Если задание было **приостановлено** пользователем (статус `PAUSED`), а не
прервано, — возобновление через UI: страница задания → «Продолжить»;
или через CLI:

```bash
.venv/bin/python -m app.cli job resume <job_id>    # только из PAUSED
```

## Управление заданиями и промптами в интерфейсе

Все изменяющие операции (загрузка книги, создание задания, удаление,
очистка логов, создание версий промптов)
требуют `SERVER_AUTH_SECRET`: при пустом секрете они отвечают `403`
(fail closed), чтение интерфейса остаётся открытым.

**Удаление задания.** Страница задания → «Удалить задание» (подтверждение
показывает, что именно будет удалено). Удаляется только задание в
терминальном статусе (`COMPLETED`, `COMPLETED_WITH_WARNINGS`, `FAILED`,
`CANCELLED`); на активное — `409` с причиной verbatim. Удаляются строки
`jobs`, `transitions`, `job_tokens`, `stage_results` (одна транзакция) и
каталог `<storage.root>/jobs/<job_id>/` целиком.

**Массовая очистка (purge).** Страница Jobs → «Массовая очистка (purge)».
Два шага: «Показать перечень (dry-run)» — перечень `job_id`, их число и
суммарный объём, ничего не удаляется; «Удалить перечисленные»
(`confirm_token` из dry-run) — удаляет ровно перечисленные. Фильтры:
статус (только терминальные), книга, пара языков; `updated_at` старше
`ui.purge_min_age_days` (YAML).

**Очистка логов.** Страница задания → «Очистить логи»: `job.log` и
`errors.log` усекаются до нуля с записью в `job.log` строки факта (время,
пользователь, объём). `quality.log` отдельной очистке не подлежит
(доказательная база L3) — он исчезает только вместе с удалением задания.

**Версионирование промптов.** Страница Prompts — все версии всех промптов
с уровнем разрешения (пара / язык / generic) и меткой версии, которую
выберет резолвер (max N). «Изменить (новая версия)» создаёт `v(N+1).yaml`;
перезапись существующей версии запрещена (версия входит в идемпотентный
ключ этапа и в `resolved_prompts` — правка задним числом сделала бы
артефакты завершённых заданий неверифицируемыми). Новая версия меняет
идемпотентный ключ этапа: новые задания будут переведены заново,
завершённые — не затрагиваются. Валидация до записи: YAML разбирается,
обязательные поля на месте, согласованность с `pair_required` задачи;
ошибка — `400`, файл не создаётся, LLM-запросов нет.

**Мобильная вёрстка.** Под 640 px таблицы рендерятся карточным списком с
тем же набором полей — ни одно поле не скрывается.

## CLI-справка

Все команды — из корня проекта, через `.venv/bin/python -m app.cli`
(проект — uv-проект «virtual»: код используется из репозитория, в
site-packages не устанавливается).

| Команда | Назначение |
|---|---|
| `analyze <epub>` | разбор книги: заголовок + язык (L0) |
| `translate <epub> --source .. --target .. --mode .. [--level ..] [--cefr ..] [--wait]` | задание перевода |
| `job list` / `job status <id>` | список / состояние задания |
| `job resume <id>` / `job cancel <id>` | возобновить (только из `PAUSED`) / отменить |
| `validate <epub>` | валидация EPUB (структура + EPUBCheck) |
| `languages` | языки и пары (резолвер L0) |
| `export cards <id> --format {csv,anki,json} [--output ..]` | экспорт карточек (`--format` обязателен) |

## Конфигурация

- `config/config.yaml` (шаблон — `config/config.example.yaml`, боевой файл
  не коммитится): `server` (порт, heartbeat/lease), `storage.root`,
  `llm.profiles.*` (base_url, model, temperature, повторы, параллелизм),
  `limits`, `glossary` (L2/L3, по умолчанию выключены — D60/D63),
  `alignment`, `epubcheck.jar_path`, `languages.root`, `translation`.
- `.env` (не коммитится): секреты — API-ключи LLM, адрес бинда, секрет UI.
- Языки — данные: реестр `config/languages/<code>/pack.yaml` (+ cefr, mwe),
  матрица пар `config/language_matrix.yaml`.

## Разработка

```bash
uv sync --group dev        # ruff, mypy, pytest
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check app tests
.venv/bin/python -m mypy app
```

Детерминированные тестовые книги (в репозитории): `tests/fixtures/epub/` —
`probe.en.epub` (2 главы), `complex.epub` (3 главы), `lang_de/fr/nl/it.epub`,
`drm.epub`, `broken.epub`, `conflict.epub`, `bilingual.epub` (генератор —
`tests/fixtures/epub/build.py`). Детерминированный mock LLM-сервера —
`tests/probe/mock_llm_server.py`.
