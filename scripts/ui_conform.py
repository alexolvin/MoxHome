"""Сверка компонентов с эталоном (TZ-UI-AUDIT §8 step 4, R-K / R-T4 / R-L4).

Замер контура 2 (машина) поверх декларации `ui-components.yaml` + `ui-roles.yaml`:

* R-K11 — кит актуален: `/dev/kit` содержит все компоненты реестра (якорь
  `kit-anchor` на месте). 0 компонентов без якоря.
* R-K12 — кит на стенде доступен (`dev_kit: true`); 404-ветку в проде
  проверяет unit-тест маршрута.
* R-L4 — размер компонента ∈ `size` из ui-components.yaml (мин И макс).
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
    (".u3-seg-pill.is-current", "segmented", "pill"),
    (".u3-seg-pill:not(.is-current)", "segmented", "pill"),
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

_TOKEN_MAP: dict[str, str] = {}


@dataclass
class Size:
    w: tuple[float, float] | None = None
    h: tuple[float, float] | None = None


@dataclass
class Registry:
    base: str
    comps: dict
    roles: dict


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


def check_screen(page, sid: str, path: str, reg: Registry, out: dict[str, Any]) -> int:
    """R-T1 / R-L4 / R-T4 на одном экране; возвращает число экземпляров."""
    page.goto(reg.base + path, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_load_state("networkidle", timeout=15_000)
    inst = page.evaluate(INSTANCES_JS, [list(t) for t in SIGNATURES])

    n_interactive = page.evaluate(f"() => document.querySelectorAll('{INTERACTIVE_JS}').length")
    n_with_role = page.evaluate(
        f"() => document.querySelectorAll('{INTERACTIVE_JS}[data-role]').length")
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
        out["stats"]["total_instances"] = total
        browser.close()

    write_report(out, args.out)


if __name__ == "__main__":
    main()
