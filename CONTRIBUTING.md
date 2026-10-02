# Contributing

MoxMate is **proprietary** — see [LICENSE](LICENSE) (All Rights Reserved).
This repository is published for viewing and reference, not for
unrestricted reuse.

## Before you contribute

Any contribution — code, documentation, configuration, prompts, or assets —
requires the maintainer's **prior written agreement**. If you have something
you would like to contribute, contact the maintainer first at
`alex.olvin@gmail.com` and reach an agreement. Do not open pull requests
unilaterally.

## Development (after agreement)

- Python 3.11–3.13, managed with [uv](https://docs.astral.sh/uv/):
  `uv venv && uv sync && uv sync --group dev`.
- spaCy source-language models (en/de/fr/it/nl, 3.8.0) are installed **after**
  `uv sync` (see [README](README.md), Step 1); a plain re-`uv sync` removes
  them.
- Run the checks before sharing any change:

  ```bash
  .venv/bin/python -m pytest tests -q
  .venv/bin/ruff check app tests
  .venv/bin/python -m mypy app
  ```

## Repository hygiene

- Do not add secrets, personal data, local machine paths, overlay/VPN
  addresses, or agent/operational artifacts to the repository.
- Keep the bilingual READMEs in sync: `README.md` (English) and
  `README.ru.md` (Russian).
