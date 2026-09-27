"""V1 (ТЗ 2026-09-22) V-T10 — контраст тёмной темы (WCAG 2.x).

Критерий: main text ≥ 7, secondary/status ≥ 4.5, borders ≥ 3.
Все цвета — токены §4 (V-T2: colors_off_token пусто), поэтому контраст
полностью определяется палитрой тёмной темы: сверяем реально рендеримые
пары «текст на фоне» из значений :root (dark) в app/web/static/style.css.

Гейт (exit 0): все текстовые пары ≥ порога И видимая линия --lns ≥ 3.
--ln — декоративная групповая граница (карты/строки/панели), в тёмной теме
осознанно < 3:1 (WCAG 1.4.11 не требует 3:1 для границ, не нужных для
идентификации компонента: кнопки/поля читаются по тексту и фону) —
выводится как NOTE, в гейт не входит.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STYLE = Path("app/web/static/style.css")


def _parse_block(css: str, selector: str) -> dict[str, str]:
    m = re.search(selector + r"\{([^}]+)\}", css)
    if not m:
        return {}
    return dict(re.findall(r"(--[a-z0-9-]+):#([0-9A-Fa-f]{6})", m.group(1)))


def _parse_dark(css: str) -> dict[str, str]:
    tok = _parse_block(css, r":root")
    if not tok:
        raise SystemExit("не найден :root (dark) в style.css")
    return tok


def _tokens_for(css: str, theme: str) -> dict[str, str]:
    # base = :root (dark); light = base + [data-theme="light"] override.
    tok = _parse_block(css, r":root")
    if theme == "light":
        ov = _parse_block(css, r':root\[data-theme="light"\]')
        if not ov:
            raise SystemExit('не найден :root[data-theme="light"] в style.css')
        tok.update(ov)
    return tok


def _lum(h: str) -> float:
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))

    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ratio(fg: str, bg: str) -> float:
    l1, l2 = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# (метка, fg-токен, [bg-токены], порог, kind)
# kind: text — в гейт; border — в гейт (видимая линия); border-decor — NOTE.
PAIRS: list[tuple[str, str, list[str], float, str]] = [
    ("main text   --fg",        "--fg",  ["--bg", "--s1", "--s2", "--s3"], 7.0, "text"),
    ("muted        --fgm",      "--fgm", ["--bg", "--s1", "--s2", "--s3"], 4.5, "text"),
    ("muted        --fgs",      "--fgs", ["--bg", "--s1", "--s2", "--s3"], 4.5, "text"),
    ("accent       --acf",      "--acf", ["--bg", "--s1", "--s2", "--s3"], 4.5, "text"),
    ("status ok    --st-o",     "--st-o", ["--st-ob", "--s1", "--bg"], 4.5, "text"),
    ("status warn  --st-a",     "--st-a", ["--st-ab", "--s1", "--bg"], 4.5, "text"),
    ("status err   --st-e",     "--st-e", ["--st-eb", "--s1", "--bg"], 4.5, "text"),
    ("status info  --st-r",     "--st-r", ["--st-rb", "--s1", "--bg"], 4.5, "text"),
    ("status wait  --st-w",     "--st-w", ["--st-wb", "--s1", "--bg"], 4.5, "text"),
    ("btn primary  --oac/−ac",  "--oac", ["--ac"], 4.5, "text"),
    ("btn hover    --os/−acs",  "--os",  ["--acs"], 4.5, "text"),
    ("cta          --oct/−cta", "--oct", ["--cta"], 4.5, "text"),
    ("line         --lns",      "--lns", ["--bg", "--s1", "--s2"], 3.0, "border"),
    ("line (decor) --ln",       "--ln",  ["--bg", "--s1", "--s2"], 3.0, "border-decor"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="dark", choices=("dark", "light"))
    args = ap.parse_args()
    css = STYLE.read_text(encoding="utf-8")
    tok = _tokens_for(css, args.theme)
    theme_ru = "светлой" if args.theme == "light" else "тёмной"
    src = ":root + light-override" if args.theme == "light" else ":root dark"
    hard_fail = 0
    decor_notes = 0
    print(f"V-T10 — контраст {theme_ru} темы (пары «текст на фоне» из {src})")
    print()
    for label, fg, bgs, th, kind in PAIRS:
        if fg not in tok:
            raise SystemExit(f"нет токена {fg}")
        for bg in bgs:
            if bg not in tok:
                raise SystemExit(f"нет токена {bg}")
            r = _ratio(tok[fg], tok[bg])
            ok = r >= th
            tag = "OK " if ok else "LOW"
            if not ok:
                if kind == "border-decor":
                    decor_notes += 1
                    tag = "NOTE"
                else:
                    hard_fail += 1
            print(f"  {label:24} {bg:8} {r:6.2f}  (>={th}) {tag}")
    print()
    verdict = "PASS" if hard_fail == 0 else f"FAIL ({hard_fail})"
    print(f"V-T10: {verdict}" +
          (f" | NOTE: --ln (декоративная граница) < 3:1 — {decor_notes} пары"
           if decor_notes else ""))
    return 0 if hard_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
