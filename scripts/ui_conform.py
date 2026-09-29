"""Сверка компонентов с эталоном (TZ-UI-AUDIT §8 step 4, R-K / R-T4 / R-L4).

Замер контура 2 (машина) поверх декларации `ui-components.yaml` + `ui-roles.yaml`:

* R-K11 — кит актуален: `/dev/kit` содержит все компоненты реестра (якорь
  `kit-anchor` на месте). 0 компонентов без якоря.
* R-K12 — кит на стенде доступен (`dev_kit: true`); 404-ветку в проде
  проверяет unit-тест маршрута.
* R-L4 — размер компонента ∈ `size` из ui-components.yaml (мин И макс).
* R-Y1 — число различных кеглей за прогон ≤ `type_scale_max_steps` (ui-rules).
* R-Y2 — никаких кеглей < 11 px; R-Y3 — на мобильном ≤ `max_font_app_screen_px`.
* R-Y5/R-Y6 — число строк ≤ `max_lines` (ui-text.yaml) у однострочных ролей.
* R-Y10 — длина подписи ≤ `max_chars` (ui-text.yaml).
* R-T1 — каждый видимый интерактивный элемент несёт `data-role` (0 без роли).
* R-T4 — вычисленный цвет/фон совпадает с токеном роли (data-role → ui-roles).
  Без `data-role` элемент не проверяется (это R-T1).
* R-K6 — пиксельное соответствие киту — NOT IMPLEMENTED в этом прогоне (нужны
  эталонные вырезки + нормализация размера). Честно помечено, не «проходится».

Все замеры — на чистом дереве (git status --porcelain пуст), на запущенном
стенде. Артефакт: `conform.json` + печатный список нарушений с ID правила.

Запуск (из audit-окружения, из корня репозитория):
    .a/tasks/ui-audit/.venv/bin/python scripts/ui_conform.py --base http://127.0.0.1:8421
    .a/tasks/ui-audit/.venv/bin/python scripts/ui_conform.py --base … --screens S02 S06
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / ".a" / "ui-audit"
ENV_DEMO = REPO_ROOT / ".env.demo"
WIDTH, HEIGHT, DSF = 384, 715, 2.25
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
PATH_BY_ID = {
    "S02": "/books", "S06": "/jobs", "S12": "/settings",
    "S13": "/settings/llm", "S14": "/dev/kit", "S18": "/settings/appearance",
}

# --- сигнатура компонента: CSS-селектор → (component, variant) ------------------
# Рядом с ui-components.yaml; определяет, как классы маппятся на реестр.
SIGNATURES: list[tuple[str, str, str]] = [
    # (css selector, component, variant)
    (".u3-btn.u3-primary", "button", "primary"),
    (".u3-btn.u3-danger", "button", "destructive"),
    (".u3-btn.u3-text", "button", "text"),
    (".u3-btn.u3-tertiary", "button", "tertiary"),
    (
        ".u3-btn:not(.u3-primary):not(.u3-danger):not(.u3-text):not(.u3-tertiary)",
        "button",
        "secondary",
    ),
    (".u3-row-btn", "button", "icon"),
    (".u3-fab", "fab", "default"),
    (".u3-rchip", "chip", "radio"),
    (".u3-chip-select.is-selected", "chip", "selected"),
    (".u3-chip-select:not(.is-selected):not(.is-current)", "chip", "default"),
    # segmented — меряем визуальный бар .u3-seg-vis (36 px), а не tap-обёртку
    # .u3-seg-pill (min-height:44 → 46 px): размер компонента = визуал (R-L4).
    (".u3-seg-pill .u3-seg-vis", "segmented", "pill"),
    (".u3-switch", "switch", "default"),
    (".u3-status-chip.u3-st-wait", "status-chip", "wait"),
    (".u3-status-chip.u3-st-run", "status-chip", "run"),
    (".u3-status-chip.u3-st-ok", "status-chip", "ok"),
    (".u3-status-chip.u3-st-warn", "status-chip", "warn"),
    (".u3-status-chip.u3-st-err", "status-chip", "error"),
    (".u3-row-job", "list-row", "job"),
    (".u3-book-card", "book-card", "default"),
    (".u3-grp-row", "settings-row", "default"),
    (".u3-sheet", "sheet", "default"),
    (".u3-appbar", "appbar", "default"),
    (".u3-tabbar", "tabbar", "mobile"),
    (".u3-banner.u3-banner-ok", "banner", "ok"),
    (".u3-banner.u3-banner-warn", "banner", "warn"),
    (".u3-banner.u3-banner-err", "banner", "error"),
]

# Интерактивные элементы (для R-T1: должны нести data-role).
INTERACTIVE_JS = "button, a[href], input, select, textarea, [role=button]"
# ВАЖНО: data-role через :is() — иначе [data-role] в конце списка селекторов
# применяется только к последнему ([role=button]) и R-T1 занизит нарушения.
INTERACTIVE_WITH_ROLE_JS = (
    ":is(button, a[href], input, select, textarea, [role=button])[data-role]"
)

_TOKEN_MAP: dict[str, str] = {}

# --- R-Y (текст) -----------------------------------------------------------------
# Однострочные текстовые роли (ui-text.yaml max_lines: 1) → селектор. Для R-Y5/6
# меряем число строк у этих элементов (getClientRects()). Роли без уверенного
# селектора (chip/status/book-card) НЕ маплены честно — не гадаем (TZ §1.4).
TEXT_SINGLE_LINE: list[tuple[str, str]] = [
    # (селектор, max_lines)
    (".u3-appbar-title", 1),   # appbar.title
    (".u3-row-line1", 1),      # list.title
    (".u3-row-line2", 1),      # list.meta
    (".u3-row-line3", 1),      # list line3
    (".u3-btn", 1),            # button.label
    (".u3-tab", 1),            # tabbar.label
    (".u3-toast", 2),          # toast.label
]

# R-Y10 — потолок длины (ui-text.yaml max_chars) → (селектор, роль, max_chars)
TEXT_MAX_CHARS: list[tuple[str, str, int]] = [
    (".u3-btn", "button.label", 20),
    (".u3-chip-select, .u3-rchip", "chip.label", 16),
    (".u3-tab", "tabbar.label", 12),
]

# (selector, role, case) — роли с объявленным регистром (ui-text.yaml: case).
# sentence: первая буква заглавная; не ALL CAPS; не начинается со строчной.
TEXT_CASE: list[tuple[str, str, str]] = [(".u3-btn", "button.label", "sentence")]

# R-Y10: длина подписи ≤ max_chars (по ui-text.yaml).
CHARS_JS = r"""
(sels) => {
  const out = {};
  for (const [sel, role, maxc] of sels) {
    const arr = [];
    for (const e of document.querySelectorAll(sel)) {
      if (getComputedStyle(e).display === "none") continue;
      const t = (e.textContent || "").trim();
      if (!t) continue;
      arr.push({ len: t.length, txt: t.slice(0, 24) });
    }
    out[sel] = { maxc, role, items: arr };
  }
  return out;
}
"""

# R-Y7: текст, обрезанный по ширине (scrollWidth > clientWidth), обязан иметь
# text-overflow: ellipsis ИЛИ полное значение в title/aria-label (TZ §2.5).
TRUNC_JS = r"""
() => {
  const out = [];
  document.querySelectorAll("body *").forEach(e => {
    const hasText = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!hasText) return;
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    if (cs.display === "none" || cs.visibility === "hidden" || r.width < 1) return;
    if (e.scrollWidth <= e.clientWidth + 1) return;
    const ell = cs.textOverflow && cs.textOverflow.split(/\s+/).includes("ellipsis");
    const hasAlt = !!(e.getAttribute("title") || e.getAttribute("aria-label"));
    if (!ell && !hasAlt) out.push(
      { tag: e.tagName, cls: String(e.className || "").slice(0, 30),
        sw: e.scrollWidth, cw: e.clientWidth });
  });
  return out;
}
"""

TEXT_JS = r"""
({ sels, lo, hi, lhBody, lhHead, headFs }) => {
  const out = { sizes: [], flag: [], lhLow: [], lines: {} };
  document.querySelectorAll("body *").forEach(e => {
    const hasText = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!hasText) return;
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    if (cs.display === "none" || cs.visibility === "hidden" || r.width < 1 || r.height < 1) return;
    const fs = Math.round(parseFloat(cs.fontSize) * 100) / 100;
    out.sizes.push(fs);
    if (fs < lo || fs > hi) out.flag.push(
      { fs, tag: e.tagName, cls: String(e.className || "").slice(0, 40),
        text: (e.textContent || "").trim().slice(0, 24) });
    // R-Y4: line-height ≥ 1.4 (body) / ≥ 1.15 (heading, кегль ≥ headFs).
    // Дизайн — компактный (lh ~1.25-1.38), ниже порога → не поэлементно, а
    // единым сводным finding (список kеглей ниже порога). «normal» (NaN) —
    // не меряем (зависит от браузера), честно пропускаем.
    const lh = parseFloat(cs.lineHeight);
    if (!isNaN(lh) && fs > 0) {
      const ratio = Math.round((lh / fs) * 100) / 100;
      const minLh = (fs >= headFs) ? lhHead : lhBody;
      if (ratio < minLh - 1e-9 && !out.lhLow.includes(ratio)) out.lhLow.push(ratio);
    }
  });
  // TZ §2.5: число строк — getClientRects() для ТЕКСТОВОГО УЗЛА (не элемента):
  // иначе инлайновые иконки/SVG дают ложные «линии».
  const firstText = (el) => {
    const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (tw.nextNode()) { if (tw.currentNode.textContent.trim()) return tw.currentNode; }
    return null;
  };
  for (const [sel, maxl] of sels) {
    const arr = [];
    for (const e of document.querySelectorAll(sel)) {
      const cs = getComputedStyle(e);
      if (cs.display === "none" || e.getBoundingClientRect().width < 1) continue;
      const tn = firstText(e);
      if (!tn) continue;
      // nowrap/pre — перенос невозможен → 1 строка (getClientRects для
      // overflow:hidden+ellipsis возвращает лишний фрагмент — артефакт, не строка).
      const nowrap = cs.whiteSpace === "nowrap" || cs.whiteSpace === "pre";
      let lines = 1;
      if (!nowrap) {
        const range = document.createRange(); range.selectNodeContents(tn);
        lines = range.getClientRects().length;
      }
      arr.push({ lines, ws: cs.whiteSpace });
    }
    out.lines[sel] = { maxl, items: arr };
  }
  return out;
}
"""


@dataclass
class Size:
    w: tuple[float, float] | None = None
    h: tuple[float, float] | None = None


@dataclass
class Registry:
    base: str
    comps: dict
    roles: dict


@dataclass
class TextRules:
    """Пороги R-Y из ui-rules.yaml / ui-text.yaml (пакет для check_text)."""
    lo: float
    hi: float
    lh_body: float
    lh_head: float
    head_fs: float


def git_dirty() -> bool:
    porcelain = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    return bool(porcelain.strip())


def read_secret() -> str:
    """SERVER_AUTH_SECRET из .env.demo (тот же секрет, что у запущенного стенда)."""
    for line in ENV_DEMO.read_text(encoding="utf-8").splitlines():
        if line.startswith("SERVER_AUTH_SECRET="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SERVER_AUTH_SECRET не найден в {ENV_DEMO} (запустите seed_demo.py)")


def login(context, base: str, secret: str) -> None:
    """POST /login (form, не data= — иначе app не парсит → 401). Ожидаем 303."""
    resp = context.request.post(
        base + "/login", form={"password": secret, "next": "/"}, max_redirects=0,
    )
    if resp.status != 303:
        raise SystemExit(f"ОТКАЗ: login → HTTP {resp.status} (ожидался 303)")


def load_declarations() -> dict[str, Any]:
    def _load(name: str) -> dict[str, Any]:
        return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
    return {
        "components": _load("ui-components.yaml")["components"],
        "roles": _load("ui-roles.yaml")["roles"],
        "rules": _load("ui-rules.yaml"),
    }


def parse_size(spec: dict) -> Size:
    """ui-components size → Size (mobile h/w ranges). `w`/`h` — [min,max]."""
    def rng(v) -> tuple[float, float] | None:
        if isinstance(v, list) and len(v) == 2:
            return (float(v[0]), float(v[1]))
        if isinstance(v, list) and len(v) == 1:
            return (float(v[0]), float(v[0]))
        return None
    s = Size()
    if isinstance(spec, dict):
        mob = spec.get("mobile") or spec
        if "h" in mob:
            s.h = rng(mob["h"])
        if "w" in mob:
            s.w = rng(mob["w"])
        # w_max_pct — ширина в %, не абсолютный пиксель → R-L4 пропускает
    return s


def token_hex(token: str) -> str | None:
    """--token → hex (парсинг :root из style.css). Ленивый кэш без global."""
    if not _TOKEN_MAP:
        css = (REPO_ROOT / "app/web/static/style.css").read_text(encoding="utf-8")
        _TOKEN_MAP.update({
            name: val.lstrip("#").lower()
            for name, val in re.findall(r"([A-Za-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", css)
        })
    return _TOKEN_MAP.get(token.lstrip("-"))


def hex_of(color: str) -> str | None:
    c = color.strip()
    if c.startswith("#"):
        return c.lstrip("#").lower()
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", c)
    if not m:
        return None
    r, g, b = (int(x) for x in m.groups())
    if c.startswith("rgba") and re.search(r",\s*0(\.0)?\)", c):
        return None
    return f"{r:02x}{g:02x}{b:02x}"


def add_violation(out: dict[str, Any], rule: str, sev: str, sid: str, msg: str) -> None:
    out["violations"].append({"rule": rule, "sev": sev, "screen": sid, "msg": msg})


# --- Playwright-замеры -----------------------------------------------------------

INSTANCES_JS = r"""
(sigs) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = getComputedStyle(el);
    return st.visibility !== "hidden" && st.display !== "none" && st.opacity !== "0";
  };
  const out = [];
  for (const [sel, comp, variant] of sigs) {
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      out.push({
        selector: sel, component: comp, variant,
        x: r.left, y: r.top, w: r.width, h: r.height,
        data_role: el.getAttribute("data-role"),
        color: st.color, background: st.backgroundColor,
      });
    }
  }
  return out;
}
"""


def check_kit(page, reg: Registry, out: dict[str, Any]) -> None:
    """R-K12 (кит на стенде) + R-K11 (все якоря реестра на месте)."""
    page.goto(reg.base + "/dev/kit", wait_until="domcontentloaded", timeout=30_000)
    out["stats"]["r_k12_kit_reachable"] = page.evaluate("() => !!document.body")
    anchors = [v["kit_anchor"] for v in reg.comps.values()]
    missing = page.evaluate(
        "(as) => as.filter(a => !document.getElementById(a)).length", anchors
    )
    out["stats"]["r_k11_missing_anchors"] = missing
    if missing:
        add_violation(out, "R-K11", "P1", "S14", f"якорей кита нет: {missing}")
    else:
        print(f"R-K11: все {len(reg.comps)} якорей кита на месте")
    out["stats"]["r_k6"] = "NOT IMPLEMENTED (нужны эталонные вырезки кита)"


def check_size(it: dict, reg: Registry, sid: str, out: dict[str, Any]) -> None:
    spec = reg.comps.get(it["component"], {}).get("size")
    sz = parse_size(spec) if spec else Size()
    ident = f"{it['component']}/{it['variant']}"
    if sz.h and not (sz.h[0] - 1 <= it["h"] <= sz.h[1] + 1):
        add_violation(out, "R-L4", "P0", sid,
                      f"{ident} h={it['h']:.0f} ∉ {list(sz.h)} — {it['selector'][:44]}")
    if sz.w and not (sz.w[0] - 1 <= it["w"] <= sz.w[1] + 1):
        add_violation(out, "R-L4", "P0", sid,
                      f"{ident} w={it['w']:.0f} ∉ {list(sz.w)} — {it['selector'][:44]}")


def check_role_color(it: dict, reg: Registry, sid: str, out: dict[str, Any]) -> None:
    if not it["data_role"]:
        return
    role = reg.roles.get(it["data_role"])
    if role is None:
        add_violation(out, "R-T2", "P1", sid, f"неизвестная роль {it['data_role']}")
        return
    for attr, got in (("bg", it["background"]), ("fg", it["color"])):
        if attr not in role:
            continue
        want = token_hex(role[attr])
        gotx = hex_of(got)
        if want and gotx and want != gotx:
            add_violation(out, "R-T4", "P0", sid,
                          f"{it['component']}: {attr} {gotx} ≠ роль {it['data_role']} ({want})")


def _check_line_counts(lines: dict, sid: str, out: dict[str, Any]) -> None:
    """R-Y5/6 — число строк ≤ max_lines (роли с max_lines)."""
    for sel, spec in lines.items():
        maxl = spec["maxl"]
        for it in spec["items"]:
            if it["lines"] > maxl:
                sev = "P0" if sel in (".u3-btn", ".u3-tab") else "P1"
                add_violation(out, "R-Y5", sev, sid,
                              f"{sel}: {it['lines']} строки > {maxl} (white-space: {it['ws']})")


def _check_max_chars(page, sid: str, out: dict[str, Any]) -> None:
    """R-Y10 — длина ≤ max_chars (ui-text.yaml)."""
    for spec in page.evaluate(CHARS_JS, [list(t) for t in TEXT_MAX_CHARS]).values():
        for it in spec["items"]:
            if it["len"] > spec["maxc"]:
                add_violation(out, "R-Y10", "P1", sid,
                              f"{spec['role']}: {it['len']} симв. > {spec['maxc']} — «{it['txt']}»")


def _check_truncate(page, sid: str, out: dict[str, Any]) -> None:
    """R-Y7 (P1) — обрезанный по ширине текст обязан иметь ellipsis или title/aria-label."""
    for f in page.evaluate(TRUNC_JS, None):
        add_violation(
            out, "R-Y7", "P1", sid,
            f"<{f['tag']} .{f['cls']}>: scrollWidth {f['sw']} > clientWidth {f['cw']}"
            " без text-overflow:ellipsis и без title/aria-label",
        )


def _case_issue(text: str, cas: str) -> str | None:
    """Нарушение объявленного регистра; None — ок."""
    al = [c for c in text if c.isalpha()]
    if not al:
        return None
    if cas == "sentence":
        if al[0].islower():
            return "начинается со строчной (не sentence case)"
        if len(al) > 2 and all(c.isupper() for c in al):
            return "ALL CAPS (не sentence case)"
    return None


def _check_case(page, sid: str, out: dict[str, Any]) -> None:
    """R-Y11 (P2) — регистр подписи соответствует case роли (ui-text.yaml)."""
    for sel, role, cas in TEXT_CASE:
        texts = page.evaluate(
            "(s) => [...document.querySelectorAll(s)]"
            ".map(e => (e.textContent || '').trim()).filter(Boolean)",
            sel,
        )
        for t in texts:
            issue = _case_issue(t, cas)
            if issue:
                add_violation(out, "R-Y11", "P2", sid, f"{role} «{t}»: {issue} (case: {cas})")


def check_text(page, sid: str, out: dict[str, Any], tr: TextRules) -> None:
    """R-Y1 (кегли в шкалу) / R-Y2 (<lo) / R-Y3 (>hi моб.) / R-Y4 (line-height) /
    R-Y5+6 (строки ≤ max_lines) / R-Y10 (симв. ≤ max_chars)."""
    res = page.evaluate(TEXT_JS, {"sels": [list(t) for t in TEXT_SINGLE_LINE],
                                  "lo": tr.lo, "hi": tr.hi, "lhBody": tr.lh_body,
                                  "lhHead": tr.lh_head, "headFs": tr.head_fs})
    fonts = out["stats"].setdefault("fonts_seen", [])
    for fs in res["sizes"]:
        if fs not in fonts:
            fonts.append(fs)
    for f in res["flag"]:
        if f["fs"] < tr.lo:
            add_violation(out, "R-Y2", "P1", sid,
                          f"кегль {f['fs']}px < {tr.lo:g} — <{f['tag']} {f['cls']}> «{f['text']}»")
        else:  # > hi на мобильном (кроме объявленных исключений — R-Y3, P2)
            add_violation(
                out, "R-Y3", "P2", sid,
                f"кегль {f['fs']}px > {tr.hi:g} (моб.) — <{f['tag']} {f['cls']}>",
            )
    # R-Y4: сводный — собрать кегли ниже порога (глобально, в stats).
    lh_low = out["stats"].setdefault("r_y4_low_ratios", [])
    for r in res["lhLow"]:
        if r not in lh_low:
            lh_low.append(r)
    _check_line_counts(res["lines"], sid, out)
    _check_max_chars(page, sid, out)
    _check_truncate(page, sid, out)
    _check_case(page, sid, out)


def check_screen(page, sid: str, path: str, reg: Registry, out: dict[str, Any]) -> int:
    """R-T1 / R-L4 / R-T4 на одном экране; возвращает число экземпляров."""
    page.goto(reg.base + path, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_load_state("networkidle", timeout=15_000)
    inst = page.evaluate(INSTANCES_JS, [list(t) for t in SIGNATURES])

    # R-T1 — только ВИДИМЫЕ интерактивные элементы (TZ: «каждый видимый
    # элемент несёт роль»); скрытые (display:none) не учитываются.
    t1 = page.evaluate(
        """(inter) => {
          const vis = (e) => { const cs = getComputedStyle(e);
            const r = e.getBoundingClientRect();
            return cs.visibility !== 'hidden' && cs.display !== 'none'
              && r.width > 0 && r.height > 0; };
          const v = [...document.querySelectorAll(inter)].filter(vis);
          return { n: v.length, withRole: v.filter(e => e.hasAttribute('data-role')).length };
        }""",
        INTERACTIVE_JS,
    )
    n_interactive, n_with_role = t1["n"], t1["withRole"]
    n_no_role = n_interactive - n_with_role
    out["stats"].setdefault("r_t1", {})[sid] = {
        "interactive": n_interactive, "with_role": n_with_role, "without_role": n_no_role,
    }
    if n_no_role:
        add_violation(out, "R-T1", "P1", sid,
                      f"интерактивных без data-role: {n_no_role}/{n_interactive}")

    for it in inst:
        check_size(it, reg, sid, out)
        check_role_color(it, reg, sid, out)
    return len(inst)


def write_report(out: dict[str, Any], out_path: str) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    p0 = sum(1 for v in out["violations"] if v["sev"] == "P0")
    p1 = sum(1 for v in out["violations"] if v["sev"] == "P1")
    print(f"\nconform: {len(out['violations'])} нарушений (P0 {p0}, P1 {p1}) → {out_path}")
    for v in out["violations"][:60]:
        print(f"  [{v['sev']}] {v['rule']} {v['screen']}: {v['msg']}")
    if len(out["violations"]) > 60:
        print(f"  … и ещё {len(out['violations']) - 60}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8421")
    ap.add_argument("--screens", nargs="*", default=None)
    ap.add_argument("--theme", default="dark", choices=("dark", "light"))
    ap.add_argument("--out", default=str(ARTIFACTS / "conform.json"))
    args = ap.parse_args()

    if git_dirty():
        print("ОТКАЗ: дерево git грязно — замеры только на чистом дереве (TZ §3)")
        sys.exit(2)

    dec = load_declarations()
    reg = Registry(args.base, dec["components"], dec["roles"])
    rules = dec.get("rules", {}) or {}
    max_steps = int(rules.get("type_scale_max_steps", 8))
    tr = TextRules(
        lo=11.0,  # R-Y2: абсолютный нижний порог («ничего < 11 px»)
        hi=float((rules.get("max_font_app_screen_px") or {}).get("mobile", 20)),
        lh_body=float(rules.get("line_height_body_min", 1.4)),
        lh_head=float(rules.get("line_height_heading_min", 1.15)),
        head_fs=18.0,  # кегль ≥ 18 — заголовок (иначе body); из шкалы (flg=18)
    )
    out: dict[str, Any] = {
        "violations": [], "stats": {}, "run": {"theme": args.theme, "base": args.base},
    }
    screens = args.screens or ["S02", "S06"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=DSF, is_mobile=True, has_touch=True,
            color_scheme=args.theme, locale="ru-RU", user_agent=ANDROID_UA,
        )
        if args.theme == "light":
            ctx.add_cookies([{"name": "u3-theme", "value": "light", "url": args.base}])
        login(ctx, args.base, read_secret())
        page = ctx.new_page()
        check_kit(page, reg, out)
        total = 0
        for sid in screens:
            path = PATH_BY_ID.get(sid)
            if path is None:
                print(f"{sid}: нет пути в базовом наборе — пропуск")
                continue
            total += check_screen(page, sid, path, reg, out)
            check_text(page, sid, out, tr)
        out["stats"]["total_instances"] = total
        browser.close()

    # R-Y1 — число различных кеглей за прогон ≤ шкале (type_scale_max_steps)
    fonts = out["stats"].get("fonts_seen", [])
    out["stats"]["r_y1_distinct_sizes"] = sorted(fonts)
    if len(fonts) > max_steps:
        add_violation(out, "R-Y1", "P1", "ALL",
                      f"различных кеглей {len(fonts)} > {max_steps}: {sorted(fonts)}")
    # R-Y4 — сводный: kегли ниже порога (компактный дизайн vs body≥1.4)
    lh_low = sorted(out["stats"].get("r_y4_low_ratios", []))
    if lh_low:
        add_violation(out, "R-Y4", "P2", "ALL",
                      f"line-height {lh_low} ниже body {tr.lh_body} "
                      f"/ heading {tr.lh_head} (компактный дизайн; вопрос владельца)")
    write_report(out, args.out)


if __name__ == "__main__":
    main()
