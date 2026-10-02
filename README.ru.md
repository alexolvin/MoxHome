# MoxHome

**Языки:** [English](README.md) | Русский

<p align="left">
  <img alt="Лицензия" src="https://img.shields.io/badge/лицензия-All%20Rights%20Reserved-red.svg">
</p>

Self-hosted конвейер: перевод EPUB + генерация учебных материалов. На входе —
EPUB; на выходе — двуязычный (или одноязычный) EPUB и учебный пакет:
карточки лексики (CSV/Anki/JSON), устойчивые словосочетания, грамматические
заметки, CEFR-разметка. LLM — любой OpenAI-совместимый эндпоинт
(vLLM, Ollama, ...); приложение не привязано к провайдеру. GPU и локальная
модель не требуются — MoxHome не ведёт локального инференса, а обращается к
внешнему LLM-эндпоинту; сервер и воркер работают на обычной CPU-машине.

Два процесса: **Web-сервер** (UI + API) и **воркер** (исполнение заданий).
Воркер — отдельный процесс; запуск из UI намеренно не реализован (ограничение
задокументировано, а не молчаливый no-op).

## Возможности

- Перевод EPUB → двуязычный или одноязычный EPUB.
- Учебный пакет: карточки лексики (CSV / Anki TSV / JSON), устойчивые
  словосочетания, грамматические заметки, CEFR-разметка.
- Любой OpenAI-совместимый LLM-эндпоинт (`/v1/chat/completions`); без
  привязки к провайдеру.
- Web UI: Books, Jobs, Languages, Models, Prompts, Settings — с мобильной
  вёрсткой.
- CLI с теми же операциями, что API.
- Версионирование промптов (неизменяемые версии, детерминированный резолвер).
- Возобновление прерванных заданий (lease, единый механизм восстановления).
- Отчёты QC (покрытие выравнивания, согласованность глоссария, правки PE).
- Экспорт результата во многие форматы (EPUB, PDF, DOCX, FB2, AZW3, TXT, MD,
  HTMLZ).

## Требования

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) (менеджер зависимостей проекта; `uv.lock` в репозитории)
- Java 17+ — для EPUBCheck (опционально; см. шаг 1)
- [Podman](https://podman.io/) — для Calibre/импорта форматов (опционально; см. шаг 1)
- LLM-эндпоинт: OpenAI-совместимый (`/v1/chat/completions`)

## Быстрый старт (7 шагов)

Все команды выполняются **из корня проекта**.

### Шаг 1. Установка

```bash
git clone <адрес репозитория> MoxHome
cd MoxHome

uv venv
uv sync
```

spaCy-модели исходных языков (en/de/fr/it/nl — версии 3.8.0):

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
`VALIDATION_UNAVAILABLE` (честное предупреждение, не «успех»).

Calibre для импорта форматов (опционально; нужен [Podman](https://podman.io/)):
Calibre работает в контейнере **без сети** (`tools/ebook-convert/` —
Containerfile debian-slim + обёртка). Сборка одним скриптом:

```bash
tools/ebook-convert/build.sh
```

(образ `localhost/ebook-convert`; затем в `config.yaml` —
`converter.binary` с абсолютным путём к обёртке
`tools/ebook-convert/ebook-convert`). Без Calibre интерфейс принимает
только EPUB и показывает явную строку «Конвертация недоступна: не
установлен calibre» (молчаливой деградации нет).

### Шаг 2. Настройка LLM

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

- `config/config.yaml` — `llm.profiles.<translation|fill|analysis>`:
  `base_url` и `model` (единственный источник адресов и моделей) +
  `storage.root` — корень данных (поставьте свой каталог проекта).
- `extra_body` (шаблон `config.example.yaml`, комментарий к полю) —
  дополнительные ключи тела запроса в LLM. Если модель — thinking-модель
  (Qwen3 на vLLM), во всех трёх профилях задайте
  `extra_body: {chat_template_kwargs: {enable_thinking: false}}` — без него
  reasoning-поток попадает в контент ответа и блоки не переводятся.
- `.env` — только секреты: `LLM_API_KEY` (опционально
  `LLM_ANALYSIS_API_KEY` — отдельный ключ профиля analysis),
  `SERVER_BIND_ADDRESS` (по умолчанию `127.0.0.1`), опционально
  `SERVER_AUTH_SECRET` — секрет доступа к UI/API. Без секрета UI доступен
  только для чтения: изменяющие операции отвечают `403` (fail closed) —
  задайте секрет и перезапустите сервер, либо создавайте задания через CLI.

| Переменная | Назначение | Пример | Обязательно |
|---|---|---|---|
| `LLM_API_KEY` | Ключ для профилей translation/fill | `sk-…` | да (для реального LLM) |
| `LLM_ANALYSIS_API_KEY` | Отдельный ключ профиля analysis | `sk-…` | нет (фолбэк на `LLM_API_KEY`) |
| `SERVER_BIND_ADDRESS` | Адрес бинда сервера | `127.0.0.1` | нет (по умолч. `127.0.0.1`) |
| `SERVER_AUTH_SECRET` | Общий секрет доступа к UI/API | `changeme` | нет (без него read-only) |

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

Страница Jobs показывает статус воркера: «ok (pid …)», пока воркер жив, либо
«error» с пояснением. Список заданий на странице обновляется сам, пока есть
активные задания.

### Шаг 5. Перевод тестовой книги

В репозитории есть тестовая книга: `tests/fixtures/epub/probe.en.epub`
(2 главы, EN, детерминированная фикстура).

**Через UI** (изменяющие операции требуют `SERVER_AUTH_SECRET` — см. шаг 2):
Books → выбрать файл → «Загрузить» → Jobs → форма (target `ru`, режим
`bilingual`, опционально уровень learning) → «Создать задание». Если исходный
язык определён неуверенно, задание ждёт подтверждения
(статус `NEEDS_CONFIRMATION`) — подтвердите его на странице задания.

**Через CLI (эквивалент):**

```bash
.venv/bin/python -m app.cli translate tests/fixtures/epub/probe.en.epub \
    --source en --target ru --mode bilingual --level Learning --wait
```

`--wait` — ждать терминальный статус; код выхода отражает исход. Воркер обязан
быть запущен (шаг 3).

### Шаг 6. Поиск результата

**Через UI:** Jobs → задание → статус `COMPLETED` / `COMPLETED_WITH_WARNINGS`.
На странице задания — ссылки на результат («Скачать результат»: при Learning —
`learning.epub`, иначе `target.epub`) и именованные ссылки на каждый имеющийся
файл: «Перевод (target.epub)», «Учебная книга (learning.epub)», «карточки CSV»
/ «карточки Anki TSV» / «карточки JSON», «отчёт QC (quality.json)». Та же
страница показывает время создания/обновления и таймлайн стадий задания.

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

Прерванное (воркер убит, `kill`) задание восстанавливается **воркером**: арена
(lease) задания истекает, следующий запуск воркера помечает задание `INTERRUPTED`
(единственный механизм) и возобновляет его с последнего подтверждённого
результата стадии. Задание не теряется: завершённые стадии сохранены в БД,
завершённые группы сегментов берутся из кэша. То есть «возобновление
прерванного задания» — это перезапуск воркера:

```bash
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop
```

Если задание было **приостановлено** пользователем (статус `PAUSED`), а не
прервано, — возобновление через UI (страница задания → «Продолжить») или CLI:

```bash
.venv/bin/python -m app.cli job resume <job_id>    # только из PAUSED
```

## CLI-справка

Все команды — из корня проекта, через `.venv/bin/python -m app.cli`
(проект — uv-проект «virtual»: код используется из репозитория, в
site-packages не устанавливается).

| Команда | Назначение |
|---|---|
| `analyze <epub>` | разбор книги: заголовок + язык |
| `translate <epub> --source .. --target .. --mode .. [--level ..] [--cefr ..] [--wait]` | задание перевода |
| `job list` / `job status <id>` | список / состояние задания |
| `job resume <id>` / `job cancel <id>` | возобновить (только из `PAUSED`) / отменить |
| `validate <epub>` | валидация EPUB (структура + EPUBCheck) |
| `languages` | языки и пары (резолвер) |
| `export cards <id> --format {csv,anki,json} [--output ..]` | экспорт карточек (`--format` обязателен) |

## Структура проекта

```
MoxHome/
├── app/               # код приложения
│   ├── web/           # FastAPI-приложение, views, API, шаблоны, статика (UI)
│   ├── services/      # бизнес-логика (jobs, import, analyze, llm config)
│   ├── workers/       # воркер + конвейер перевода
│   ├── core/          # настройки, ошибки, логирование
│   ├── epub/          # чтение/сборка EPUB
│   ├── adapters/      # epub_engine (движок + EPUBCheck), …
│   ├── learning/      # генерация учебных материалов, экспорт карточек
│   ├── languages/     # детекция языка + резолвер пар
│   ├── mtpe/          # тракт «машинный перевод + постредактура»
│   ├── storage/       # персистентность (репозиторий заданий)
│   ├── cli.py         # командный интерфейс
│   └── __main__.py    # точка входа `python -m app`
├── config/            # config.example.yaml + реестр языков (данные)
├── prompts/           # версионируемые шаблоны промптов
├── scripts/           # вспомогательные / аудит-скрипты
├── stubs/             # type-stub'ы
├── tests/             # тесты + детерминированные фикстуры
├── tools/             # ebook-convert (Calibre): сборка + обёртка
├── LICENSE            # All Rights Reserved
├── README.md          # английский
├── README.ru.md       # этот файл (русский)
├── .env.example       # шаблон секретов
├── pyproject.toml     # метаданные проекта (uv)
└── uv.lock            # зафиксированные зависимости
```

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

## Безопасность

В репозитории нет секретов (см. [SECURITY.md](SECURITY.md)). Доступ — HTTP
Basic с общим секретом (`SERVER_AUTH_SECRET`); изменяющие операции защищены
кросс-запросным CSRF-токеном. Без секрета интерфейс read-only, изменяющие
операции отвечают `403` (fail closed).

## Вклад

MoxHome — проприетарный проект (All Rights Reserved, см. [LICENSE](LICENSE)).
Внесение любых изменений требует предварительного письменного согласования —
см. [CONTRIBUTING.md](CONTRIBUTING.md).

## Лицензия

Проект является **проприетарным** и публикуется для просмотра и
ознакомления. **Все права защищены (All Rights Reserved).** Никакая лицензия
на копирование, использование, изменение, публикацию, распространение или
создание производных материалов не предоставляется без предварительного
письменного разрешения. Текст — [LICENSE](LICENSE); запрос разрешения —
`alex.olvin@gmail.com`.
