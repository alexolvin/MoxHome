# MoxMate

**Languages:** English | [Русский](README.ru.md)

<p align="left">
  <img alt="License" src="https://img.shields.io/badge/license-All%20Rights%20Reserved-red.svg">
</p>

Self-hosted pipeline that translates EPUB books and generates language-learning
materials. On the way in: an EPUB. On the way out: a bilingual (or
monolingual) EPUB plus a learning package — vocabulary cards (CSV / Anki /
JSON), multiword expressions, grammar notes, and CEFR tagging. It works with
any OpenAI-compatible LLM endpoint (vLLM, Ollama, …); the application is not
tied to any provider.

Two processes: a **web server** (UI + API) and a **worker** (job execution).
The worker is a separate process — starting it from the UI is intentionally
not implemented (a documented limitation, not a silent no-op).

## Features

- EPUB → bilingual or monolingual EPUB translation.
- Learning package: vocabulary cards (CSV / Anki TSV / JSON), multiword
  expressions, grammar notes, CEFR level tagging.
- Any OpenAI-compatible LLM endpoint (`/v1/chat/completions`); no provider
  lock-in.
- Web UI: Books, Jobs, Languages, Models, Prompts, Settings — with
  responsive (mobile) layout.
- A CLI that exposes the same operations as the API.
- Versioned prompts (immutable versions, resolved deterministically).
- Resume of interrupted jobs (lease-based, single recovery mechanism).
- QC reports (alignment coverage, glossary consistency, post-edit fixes).
- Result export to many formats (EPUB, PDF, DOCX, FB2, AZW3, TXT, MD, HTMLZ).

## Requirements

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) — the project dependency manager
  (`uv.lock` is committed)
- Java 17+ — **optional**, required only for EPUBCheck (Step 1)
- [Podman](https://podman.io/) — **optional**, required only for the Calibre
  non-EPUB import (Step 1)
- An OpenAI-compatible LLM endpoint (`/v1/chat/completions`)

**No local GPU or local model is required.** MoxMate performs no local
inference; it calls an external LLM endpoint. The web server and worker run
on an ordinary CPU machine.

## Quick start

All commands run **from the project root**.

### Step 1 — Install

```bash
git clone <repository-url> MoxMate
cd MoxMate

uv venv
uv sync
```

spaCy source-language models (en/de/fr/it/nl — v3.8.0). Install these **after**
`uv sync`; a later plain `uv sync` (without `--inexact`) removes packages that
are not in `uv.lock`.

```bash
uv pip install --python .venv/bin/python \
  https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.8.0/fr_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/it_core_news_sm-3.8.0/it_core_news_sm-3.8.0-py3-none-any.whl \
  https://github.com/explosion/spacy-models/releases/download/nl_core_news_sm-3.8.0/nl_core_news_sm-3.8.0-py3-none-any.whl
```

EPUBCheck (**optional**; needs Java 17+): download the W3C 5.3.0
distribution from [epubcheck.org](https://www.epubcheck.org/download/) and
unpack it into `data/tools/` (producing `data/tools/epubcheck-5.3.0/` with
`epubcheck.jar` and `lib/`), then set
`epubcheck.jar_path: data/tools/epubcheck-5.3.0/epubcheck.jar` in
`config.yaml` (`lib/` next to the jar — the classpath is built automatically).
Without EPUBCheck, jobs finish `COMPLETED_WITH_WARNINGS` with the reason
`VALIDATION_UNAVAILABLE` (an honest warning, not a silent success).

Calibre for non-EPUB import (**optional**; needs Podman): Calibre runs in a
**network-less** container (`tools/ebook-convert/` — a Debian slim
Containerfile plus a wrapper). Build it with:

```bash
tools/ebook-convert/build.sh
```

(image `localhost/ebook-convert`; then set `converter.binary` in `config.yaml`
to the absolute path of the wrapper `tools/ebook-convert/ebook-convert`).
Without Calibre the UI accepts only EPUB and shows an explicit
"Conversion unavailable: calibre is not installed" line (no silent
degradation).

### Step 2 — Configure the LLM

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

- `config/config.yaml` — `llm.profiles.<translation|fill|analysis>`: `base_url`
  and `model` (the single source of endpoint addresses and models) plus
  `storage.root` — the data root (set it to your project data directory).
- `extra_body` (see `config.example.yaml`, the field comment) — extra request
  body keys for the LLM. For thinking models (e.g. Qwen3 on vLLM), set
  `extra_body: {chat_template_kwargs: {enable_thinking: false}}` in all three
  profiles — otherwise the reasoning stream leaks into the answer content and
  blocks are not translated.
- `.env` — secrets only: `LLM_API_KEY` (optionally `LLM_ANALYSIS_API_KEY` — a
  separate key for the analysis profile), `SERVER_BIND_ADDRESS`
  (default `127.0.0.1`), and optionally `SERVER_AUTH_SECRET` — the UI/API
  access secret. Without a secret the UI is read-only: mutating operations
  (upload, create job, delete, clear logs, prompt versions) return `403`
  (fail closed). Set a secret and restart, or create jobs via the CLI (Step 5).

| Variable | Purpose | Example | Required |
|---|---|---|---|
| `LLM_API_KEY` | API key for the translation/fill profiles | `sk-…` | yes (for real LLM) |
| `LLM_ANALYSIS_API_KEY` | Separate key for the analysis profile | `sk-…` | no (falls back to `LLM_API_KEY`) |
| `SERVER_BIND_ADDRESS` | Address the server binds to | `127.0.0.1` | no (default `127.0.0.1`) |
| `SERVER_AUTH_SECRET` | Shared UI/API access secret | `changeme` | no (read-only if empty) |

### Step 3 — Start the server

Two processes (in two terminals):

```bash
.venv/bin/python -m app                                   # Web UI + API (port: server.port, default 8321)
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop   # worker
```

### Step 4 — Open the UI

In the browser: `http://127.0.0.1:8321/` (port = `server.port` in
`config.yaml`). If `SERVER_AUTH_SECRET` is set — HTTP Basic: any username,
password = the secret. Sections: Books, Jobs, Languages, Models, Prompts,
Settings. The Jobs page shows the worker status ("ok (pid …)" while alive, or
"error" with an explanation). The job list refreshes by itself while there are
active jobs.

### Step 5 — Translate a test book

The repository includes a deterministic test book:
`tests/fixtures/epub/probe.en.epub` (2 chapters, EN).

**Via the UI** (mutating operations need `SERVER_AUTH_SECRET` — Step 2):
Books → pick a file → "Upload" → Jobs → form (target `ru`, mode `bilingual`,
optional learning level) → "Create job". If the source language was detected
with low confidence, the job waits in `NEEDS_CONFIRMATION` — confirm it on the
job page.

**Via the CLI (equivalent):**

```bash
.venv/bin/python -m app.cli translate tests/fixtures/epub/probe.en.epub \
    --source en --target ru --mode bilingual --level Learning --wait
```

`--wait` blocks until a terminal status; the exit code reflects the outcome.
The worker must be running (Step 3).

### Step 6 — Find the result

**Via the UI:** Jobs → job → status `COMPLETED` / `COMPLETED_WITH_WARNINGS`.
The job page links to the result ("Download result" — `learning.epub` for
Learning, else `target.epub`) plus named links for each available file
(translation, learning book, cards CSV/Anki/JSON, QC report) and shows the
stage timeline.

**Via the CLI:**

```bash
.venv/bin/python -m app.cli job list
.venv/bin/python -m app.cli job status <job_id>
.venv/bin/python -m app.cli export cards <job_id> --format csv    # csv/anki/json
.venv/bin/python -m app.cli validate <storage.root>/jobs/<job_id>/target.epub
```

Result files live in `<storage.root>/jobs/<job_id>/` — `target.epub`,
`learning.epub`, `cards.csv` / `cards.anki.tsv` / `cards.json`, `quality.json`,
`quality.log`.

### Step 7 — Resume an interrupted job

An interrupted job (worker killed) is recovered **by the worker**: the job's
lease expires, the next worker run marks it `INTERRUPTED` (the single
recovery mechanism) and resumes it from the last confirmed stage result. The
job is not lost — completed stages are in the database and completed segment
groups come from cache. Resuming an interrupted job means restarting the
worker:

```bash
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop
```

If a job was **paused** by the user (status `PAUSED`), resume it from the UI
(job page → "Resume") or via CLI:

```bash
.venv/bin/python -m app.cli job resume <job_id>    # only from PAUSED
```

## CLI reference

All commands run from the project root, through `.venv/bin/python -m app.cli`
(this is a uv "virtual" project — the code is used from the repository and is
not installed into site-packages).

| Command | Purpose |
|---|---|
| `analyze <epub>` | Parse a book: title + language |
| `translate <epub> --source .. --target .. --mode .. [--level ..] [--cefr ..] [--wait]` | Create a translation job |
| `job list` / `job status <id>` | List jobs / job state |
| `job resume <id>` / `job cancel <id>` | Resume (from `PAUSED`) / cancel |
| `validate <epub>` | Validate an EPUB (structure + EPUBCheck) |
| `languages` | Languages and pairs (resolver) |
| `export cards <id> --format {csv,anki,json} [--output ..]` | Export cards |

## Project structure

```
MoxMate/
├── app/               # application code
│   ├── web/           # FastAPI app, views, API, templates, static (UI)
│   ├── services/      # business logic (jobs, import, analyze, llm config)
│   ├── workers/       # job worker + translation pipeline
│   ├── core/          # settings, errors, logging
│   ├── epub/          # EPUB reader / rebuild
│   ├── adapters/      # epub_engine (translation engine + EPUBCheck), …
│   ├── learning/      # learning-materials generation, card export
│   ├── languages/     # language detection + pair resolver
│   ├── mtpe/          # machine-translation + post-editing track
│   ├── storage/       # persistence (job repository)
│   ├── cli.py         # command-line interface
│   └── __main__.py    # `python -m app` entry point
├── config/            # config.example.yaml + language registry (data)
├── prompts/           # versioned LLM prompt templates
├── scripts/           # helper / audit scripts
├── stubs/             # type stubs
├── tests/             # test suite + deterministic fixtures
├── tools/             # ebook-convert (Calibre) build + wrapper
├── LICENSE            # All Rights Reserved
├── README.md          # this file (English)
├── README.ru.md       # Russian
├── .env.example       # secrets template
├── pyproject.toml     # project metadata (uv)
└── uv.lock            # locked dependencies
```

## Testing

```bash
uv sync --group dev          # ruff, mypy, pytest
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check app tests
.venv/bin/python -m mypy app
```

Deterministic test books are in the repository: `tests/fixtures/epub/` —
`probe.en.epub` (2 chapters), `complex.epub` (3 chapters), `lang_de/fr/nl/it.epub`,
`drm.epub`, `broken.epub`, `conflict.epub`, `bilingual.epub` (generator —
`tests/fixtures/epub/build.py`). A deterministic mock LLM server is at
`tests/probe/mock_llm_server.py`.

## Security

No secrets live in the repository (see [SECURITY.md](SECURITY.md)). Access is
gated by HTTP Basic with a single shared secret (`SERVER_AUTH_SECRET`);
mutating operations are additionally protected by a per-request CSRF token.
With no secret configured, the interface is read-only and mutating operations
fail closed with `403`.

## Contributing

MoxMate is proprietary (All Rights Reserved — see [LICENSE](LICENSE)).
Contributions require the maintainer's prior written agreement — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is **proprietary** and is published for viewing and reference
only. **All rights are reserved.** No license is granted to copy, use, modify,
merge, publish, distribute, or create derivative works of any part of the
project without prior written permission. See [LICENSE](LICENSE) and contact
`alex.olvin@gmail.com` to request permission.
