"""A3-сверка протокола осмотра (D112, Поправка A к ТЗ V).

Сверяет протокол (inspection.md) с артефактами съёмки:

* секция экрана S<nn> в протоколе должна содержать строку
  ``**A3:** код ``XXXXXX``; цитаты: «строка 1»; «строка 2»; «строка 3»`` —
  код с картинки (правый нижний угол снимка) и ровно три точные цитаты
  видимого текста;
* код хешируется и сверяется с ``stamp_sha256`` в metrics.json (B1: код
  в читаемом виде в metrics.json не существует; несовпадение = чтение с
  другой картинки или выдуманное);
* каждая цитата ищется в ``S<nn>.viewport.txt`` — текст узлов,
  пересекающих 384×715 (B3; нормализация — только схлопывание
  пробельных). Полный дамп ``S<nn>.dom.txt`` — только для полноты.
  Дампы агент НЕ открывает до написания протокола — открытие до
  протокола, нарушение A3.
* расхождение — чтение недействительно, экран FAIL, запись в журнал
  конфабуляций (B6: только дозапись; тесты — ``--journal <tmp>``).

Запуск (из корня репозитория, только stdlib):
    .venv/bin/python scripts/ui_verify.py --shots-dir .a/tasks/ui-audit/shots/V1/<sha>
    .venv/bin/python scripts/ui_verify.py --shots-dir … --screens S15
Выход: 0 — всё сошлось, 1 — есть расхождения (в журнале запись).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL = REPO_ROOT / ".a" / "tasks" / "ui-audit" / "confabulations.md"

A3_RE = re.compile(r"\*\*A3:\*\*\s*код\s*`([A-Z0-9]{6})`\s*;\s*цитаты:\s*(.+)")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def parse_a3_line(section: str) -> tuple[str, list[str]] | None:
    m = A3_RE.search(section)
    if not m:
        return None
    return m.group(1), re.findall(r"«([^»]*)»", m.group(2))


def split_sections(text: str) -> dict[str, str]:
    """Секции '### S<nn> …' до следующего заголовка '### '."""
    out: dict[str, str] = {}
    parts = re.split(r"(?m)^(### S\d+[^\n]*\n)", text)
    for i in range(1, len(parts), 2):
        m = re.match(r"### (S\d+)", parts[i])
        if m:
            out[m.group(1)] = parts[i] + parts[i + 1]
    return out


def check_screen(
    sid: str,
    commit: str,
    screens_meta: dict[str, Any],
    sections: dict[str, str],
    shots_dir: Path,
) -> tuple[str | None, list[str]]:
    """A3/B-сверка одного экрана (B1: hash кода, B3: цитаты по viewport-
    дампу); возвращает (код, список расхождений)."""
    meta = screens_meta.get(sid)
    if meta is None:
        return None, ["нет в metrics.json"]
    section = sections.get(sid)
    parsed = parse_a3_line(section) if section else None
    if parsed is None:
        return None, ["нет строки A3 в протоколе"]
    code, quotes = parsed
    expected = meta.get("stamp_sha256")
    cited = hashlib.sha256(f"{sid}:{commit}:{code}".encode()).hexdigest()
    vp_path = shots_dir / meta.get("viewport_dump", f"{sid}.viewport.txt")
    problems: list[str] = []
    if expected is None:
        problems.append("в metrics.json нет stamp_sha256 (съёмка до B1)")
    elif cited != expected:
        problems.append("hash кода ≠ stamp_sha256 из metrics.json")
    if len(quotes) != 3:
        problems.append(f"цитат {len(quotes)} (нужно ровно 3)")
    if not vp_path.exists():
        problems.append(f"нет viewport-дампа {vp_path.name} (B3)")
    else:
        dom = normalize(vp_path.read_text(encoding="utf-8"))
        problems.extend(
            f"цитата не в viewport-дампе: «{q}»"
            for q in quotes if normalize(q) not in dom
        )
    return code, problems


def journal_header() -> str:
    return (
        "# confabulations.md — журнал случаев конфабуляции (A3, D112)\n\n"
        "Записи вносит scripts/ui_verify.py (расхождение A3-сверки) и осматривающий\n"
        "агент при отвергнутом чтении. Формат: дата, экран/коммит, ожидаемый и\n"
        "процитированный код, отсутствующие цитаты, что сделано с описанием.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots-dir", required=True)
    parser.add_argument("--inspection", default=None,
                        help="дефолт <shots-dir>/inspection.md")
    parser.add_argument("--screens", nargs="*", default=None,
                        help="подмножество S01…S15 (дефолт — все из metrics.json)")
    parser.add_argument("--journal", default=str(JOURNAL),
                        help="B6: путь журнала (только дозапись; тесты — "
                             "временный путь)")
    args = parser.parse_args()

    shots_dir = Path(args.shots_dir)
    metrics_path = shots_dir / "metrics.json"
    if not metrics_path.exists():
        raise SystemExit(f"нет metrics.json: {metrics_path}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    screens_meta = metrics.get("screens", {})
    run_commit = metrics.get("run", {}).get("commit", "")
    run = f"{metrics.get('run', {}).get('stage', '?')}/{run_commit}"

    inspection_path = Path(args.inspection) if args.inspection else shots_dir / "inspection.md"
    if not inspection_path.exists():
        raise SystemExit(
            f"нет inspection.md: {inspection_path} — сначала протокол §2.4 со "
            "строками A3 (код с картинки + три цитаты)"
        )
    sections = split_sections(inspection_path.read_text(encoding="utf-8"))
    journal_path = Path(args.journal)

    failures: list[str] = []
    for sid in args.screens or list(screens_meta):
        code, problems = check_screen(
            sid, run_commit, screens_meta, sections, shots_dir
        )
        if problems:
            print(f"{sid}: FAIL — " + "; ".join(problems))
            failures.append(f"{sid}: " + "; ".join(problems))
        else:
            print(f"{sid}: PASS (код {code}, 3 цитаты в дампе)")

    if failures:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not journal_path.exists()
        with journal_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(journal_header())
            f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M:%S %z')} — {run}\n")
            for line in failures:
                f.write(f"- {line}\n")
        print(f"\nFAIL — экранов с расхождениями: {len(failures)}; "
              f"запись в {journal_path}")
        raise SystemExit(1)
    print(f"\nPASS — все экраны сверены ({run})")


if __name__ == "__main__":
    main()
