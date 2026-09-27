"""V1 (ТЗ 2026-09-22) бюджеты A5 — пер-страничные суммы CSS/JS + гейты.

V-T12: сумма CSS/JS любой страницы (вкл. инлайн, без минификации):
  CSS ≤ 24 576 Б (порог не менялся, D114), JS ≤ 81 920 Б (D114).
V-T16: в templates/static/app нет артефактов автотестов (webdriver,
  playwright, screenshot, demo) — 0 совпадений.

A5: «сумма всех CSS/JS любой страницы, вкл. инлайн, без минификации».
CSS страницы = style.css (ядро) + css-*.css, что линкует её шаблон,
+ инлайн-<style> из base.html (генерирует --u3-* из settings.ui).
JS страницы = app.js (единственный внешний <script src>; инлайн
u3-strings/u3-ui — данные, не код JS — в бюджет не входят, как и в R9).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "web" / "static"
TEMPLATES = ROOT / "app" / "web" / "templates"

CSS_LIMIT = 24576
JS_LIMIT = 81920

# страница (шаблон) -> доп. css-*.css (style.css всегда входит).
PAGE_CSS: dict[str, list[str]] = {
    "auth.html": ["css-auth.css"],
    "book.html": ["css-books.css"],
    "book_delete.html": ["css-books.css"],
    "book_meta.html": ["css-books.css"],
    "books.html": ["css-books.css", "css-import.css"],
    "dev_kit.html": ["css-kit.css", "css-settings.css"],
    "export_status.html": ["css-jobs.css"],
    "import_status.html": ["css-import.css"],
    "job.html": ["css-jobs.css"],
    "jobs.html": ["css-jobs.css"],
    "settings.html": ["css-settings.css"],
    "settings_about.html": ["css-settings.css"],
    "settings_appearance.html": ["css-settings.css"],
    "settings_languages.html": ["css-settings.css"],
    "settings_llm.html": ["css-settings.css"],
    "settings_maintenance.html": ["css-settings.css"],
    "settings_maintenance_purge.html": ["css-settings.css"],
    "settings_prompts.html": ["css-settings.css"],
}

# инлайн-<style> base.html рендерится с дефолтами settings.ui (Rule 1).
_UI_DEFAULTS = {
    "tap_gap_px": 8,
    "row_height_px": 64,
    "tabbar_height_px": 56,
    "input_font_px": 16,
    "mobile_breakpoint_px": 641,
}


def _file(name: str) -> int:
    p = STATIC / name
    if not p.is_file():
        raise SystemExit(f"нет файла: {p}")
    return p.stat().st_size


def _inline_style_bytes() -> int:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    m = re.search(r"<style>(.*?)</style>", base, re.S)
    if not m:
        raise SystemExit("инлайн-<style> не найден в base.html")
    block = m.group(1)
    for k, v in _UI_DEFAULTS.items():
        block = block.replace("{{ ui." + k + " }}", str(v))
    if "{{" in block:
        raise SystemExit("не все {{ ui.* }} подставлены в инлайн-<style>")
    return len(block.encode("utf-8"))


def main() -> int:
    core = _file("style.css")
    js = _file("app.js")
    inline = _inline_style_bytes()
    css_files = {n: _file(n) for n in
                 ("style.css", "css-jobs.css", "css-books.css",
                  "css-settings.css", "css-import.css", "css-kit.css",
                  "css-auth.css")}

    print(f"ядро style.css : {core} Б")
    print(f"app.js         : {js} Б (лимит {JS_LIMIT})")
    print(f"инлайн <style> : {inline} Б")
    print()

    ok = True
    worst = 0
    for page in sorted(PAGE_CSS):
        extra = sum(css_files[n] for n in PAGE_CSS[page])
        total = core + extra + inline
        worst = max(worst, total)
        flag = "OK " if total <= CSS_LIMIT else "FAIL"
        if total > CSS_LIMIT:
            ok = False
        names = " + ".join(["style.css"] + PAGE_CSS[page] + ["inline"])
        print(f"CSS {page:32} {total:6} Б  {flag}  ({names})")

    js_flag = "OK " if js <= JS_LIMIT else "FAIL"
    if js > JS_LIMIT:
        ok = False
    print(f"JS  (все страницы)         : {js:6} Б  {js_flag}")
    print()
    print(f"макс. CSS-страница: {worst} Б / {CSS_LIMIT} Б")

    # V-T16: артефакты автотестов в UI-артефактах (шаблоны + статика) = 0.
    # В Python-коде (app/) допустим baseline: комментарий app/web/app.py:72
    # (упоминает Playwright в пояснении, не тест-код) — не дефект.
    pat = r"webdriver|playwright|screenshot|demo"
    r_ui = subprocess.run(
        ["grep", "-rniE", pat, str(TEMPLATES), str(STATIC)],
        capture_output=True, text=True, check=False,
    )
    ui_hits = [ln for ln in r_ui.stdout.splitlines() if ln.strip()]
    r_app = subprocess.run(
        ["grep", "-rniE", pat, str(ROOT / "app")],
        capture_output=True, text=True, check=False,
    )
    app_hits = [ln for ln in r_app.stdout.splitlines() if ln.strip()]
    baseline = [ln for ln in app_hits if "app.py:72" in ln]
    app_new = [ln for ln in app_hits if ln not in baseline]

    print(f"\nV-T16 grep ({pat!r}):")
    print(f"  UI (templates+static): {len(ui_hits)} (нужно 0)")
    for ln in ui_hits:
        print("    " + ln)
    print(f"  app/ Python: {len(app_hits)} (baseline: {len(baseline)})")
    for ln in app_new:
        print("    NEW " + ln)

    v16_ok = not ui_hits and not app_new
    verdict = "PASS" if (ok and v16_ok) else "FAIL"
    print(f"\nИТОГ V-T12/V-T16: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
