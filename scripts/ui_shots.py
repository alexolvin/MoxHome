"""Съёмка 15 экранов демо-стенда (TZ-UI-V §2).

Запуск — из audit-окружения (Playwright + Pillow), из корня репозитория:
    .a/tasks/ui-audit/.venv/bin/python scripts/ui_shots.py --stage V0
    .a/tasks/ui-audit/.venv/bin/python scripts/ui_shots.py \
        --stage V2 --screens S02 S03 S04 --theme dark

Правила (TZ §1–§2):
* при грязном дереве (ненулевой ``git status --porcelain``) — ОТКАЗ;
* 384×715, device_scale_factor 2.25, is_mobile + has_touch,
  color_scheme dark (light — V4), locale ru-RU;
* готовность: networkidle + ``document.fonts.ready`` + все видимые
  ``img`` complete;
* два снимка на экран: ``-viewport.png`` (ровно 384×715 CSS px) и
  ``-full.png`` (вся страница);
* размещение: ``.a/tasks/ui-audit/shots/<stage>/<short-sha>/`` +
  ``metrics.json`` (поля TZ §2.6);
* контекст подстановки ``{book:...}`` / ``{job:...}`` — из манифеста
  демо-стенда ``data/demo/.seed-manifest.json`` (seed_demo.py).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOTS_ROOT = REPO_ROOT / ".a" / "tasks" / "ui-audit" / "shots"
DEMO_MANIFEST = REPO_ROOT / "data" / "demo" / ".seed-manifest.json"
ENV_DEMO = REPO_ROOT / ".env.demo"

WIDTH, HEIGHT, DSF = 384, 715, 2.25
TABBAR = 56  # px: нижняя граница первого экрана (full_items, TZ §2.6)

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


@dataclass(frozen=True)
class Screen:
    sid: str
    name: str
    url: str  # относительный; плейсхолдеры {book:...} / {job:...}
    item_selector: str | None = None
    needs_auth: bool = True
    needs_manifest: bool = False


# 15 экранов TZ §2.8. Плейсхолдеры (манифест demo-стенда):
#   {book:11} — гутенберг-книга id 11 (с обложкой); {job:11} — её задание.
SCREENS: tuple[Screen, ...] = (
    Screen("S01", "login", "/login", needs_auth=False),
    Screen("S02", "books", "/books", item_selector=".u3-row-book"),
    Screen("S03", "books-empty", "/books", item_selector=".u3-row-book"),
    Screen("S04", "book-cover", "/books/{book:11}", needs_manifest=True),
    Screen("S05", "book-nocover", "/books/{book:fixture:probe.en.epub}", needs_manifest=True),
    Screen("S06", "jobs", "/jobs", item_selector=".u3-row-job"),
    Screen("S07", "jobs-empty", "/jobs", item_selector=".u3-row-job"),
    Screen("S08", "job-translating", "/jobs/{job:11}", needs_manifest=True),
    Screen("S09", "create", "/jobs?sheet=create&book={book:11}", needs_manifest=True),
    Screen("S10", "import", "/books/upload"),
    Screen("S11", "export", "/jobs?sheet=export&job={job:84}", needs_manifest=True),
    Screen("S12", "settings", "/settings"),
    Screen("S13", "settings-llm", "/settings/llm"),
    Screen("S14", "dev-kit", "/dev/kit"),
    Screen("S15", "book-delete", "/books/{book:11}/delete", needs_manifest=True),
)


def git_state() -> tuple[str, str]:
    short_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    porcelain = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return short_sha, porcelain


def read_secret() -> str:
    for line in ENV_DEMO.read_text(encoding="utf-8").splitlines():
        if line.startswith("SERVER_AUTH_SECRET="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SERVER_AUTH_SECRET не найден в {ENV_DEMO} (запустите seed_demo.py)")


def manifest_context() -> dict[str, str]:
    if not DEMO_MANIFEST.exists():
        raise SystemExit(
            f"нет манифеста демо-стенда {DEMO_MANIFEST} — нужен для экранов с "
            "плейсхолдерами {book:/job:} (seed_demo.py)"
        )
    data = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    return {**data["books"], **{f"job:{k}": v for k, v in data["jobs"].items()}}


def fill(url: str, ctx: dict[str, str]) -> str:
    """{book:11} → books["11"]; {book:fixture:probe.en.epub} → books["fixture:…"];
    {job:11} → jobs["11"] (манифест seed_demo.py)."""
    while "{" in url:
        start = url.index("{")
        end = url.index("}", start)
        kind, _, rest = url[start + 1 : end].partition(":")
        key = rest if kind == "book" else f"{kind}:{rest}"
        if key not in ctx:
            raise SystemExit(f"плейсхолдер {{{kind}:{rest}}} не резолвится из манифеста")
        url = url[:start] + ctx[key] + url[end + 1 :]
    return url


def wait_ready(page: Any) -> None:
    page.wait_for_load_state("networkidle", timeout=30_000)
    page.evaluate("() => document.fonts.ready.then(() => true)")
    page.wait_for_function(
        "() => Array.from(document.images).every("
        "img => !img.isConnected || getComputedStyle(img).display === 'none' "
        "|| img.complete)",
        timeout=15_000,
    )
    page.wait_for_timeout(500)  # оседание дельта-обновлений тика


#: Метрики (TZ §2.6). Свои текстовые узлы; цвета — bg/color/border;
#: тап-цели группируются по классам/тегам.
METRICS_JS = r"""
(itemSelector) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = getComputedStyle(el);
    return st.visibility !== "hidden" && st.display !== "none" && st.opacity !== "0";
  };
  const out = {max_font_px: 0, colors: {}, long_text_blocks: [], items: [], targets: {}};
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    const st = getComputedStyle(el);
    const fs = parseFloat(st.fontSize);
    if (fs > out.max_font_px) out.max_font_px = fs;
    for (const c of [st.backgroundColor, st.color, st.borderColor]) {
      if (c && c !== "rgba(0, 0, 0, 0)") out.colors[c] = (out.colors[c] || 0) + 1;
    }
    let own = "";
    for (const node of el.childNodes) if (node.nodeType === 3) own += node.textContent;
    own = own.trim();
    if (own.length > 80) out.long_text_blocks.push(own.slice(0, 80) + "…");
  }
  if (itemSelector) {
    for (const el of document.querySelectorAll(itemSelector)) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      out.items.push({top: r.top, bottom: r.bottom});
    }
  }
  const groupOf = (el) => {
    const cls = (el.getAttribute("class") || "").split(/\s+/);
    if (el.tagName === "BUTTON" || el.getAttribute("role") === "button"
        || cls.some((c) => /^u3-(btn|fab)/.test(c))) return "buttons";
    if (cls.some((c) => /chip|pill|tag/.test(c))) return "chips";
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA")
      return "inputs";
    return "rows";
  };
  const acc = {};
  for (const el of document.querySelectorAll(
      "button, a[href], input, select, textarea, summary, [role=button]")) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const g = groupOf(el);
    acc[g] = acc[g] || {min_w: Infinity, min_h: Infinity, n: 0};
    acc[g].min_w = Math.min(acc[g].min_w, r.width);
    acc[g].min_h = Math.min(acc[g].min_h, r.height);
    acc[g].n += 1;
  }
  out.targets = acc;
  return out;
}
"""


def _rgb_to_hex(color: str) -> str | None:
    c = color.strip().lower()
    if c.startswith("rgba"):
        parts = [p.strip() for p in c[4:-1].split(",")]
        if len(parts) == 4 and parts[3] in ("0", "0.0"):
            return None
    elif c.startswith("rgb"):
        parts = [p.strip() for p in c[3:-1].split(",")]
    else:
        return c.lstrip("#")
    if len(parts) != 3:
        return None
    return "".join(f"{int(float(p)):02x}" for p in parts)


def saturated_share(viewport_path: Path) -> float:
    img = Image.open(viewport_path).convert("HSV")
    _, sat, val = img.split()
    # logical_and работает с режимом "1" (бинарным), point() даёт "L".
    s_cut = sat.point(lambda x: 255 if x >= int(0.40 * 255) else 0).convert("1")
    v_cut = val.point(lambda x: 255 if x >= int(0.35 * 255) else 0).convert("1")
    combined = ImageChops.logical_and(s_cut, v_cut)
    total = img.size[0] * img.size[1]
    # Pillow 12: пиксели режима "1" имеют значения 0/255 → «включённые»
    # в histogram()[255].
    return combined.histogram()[255] / total


def off_tokens(colors: list[str], token_file: Path) -> list[str]:
    if not token_file.exists():
        return []
    allowed = {line.strip().lower() for line in token_file.read_text().splitlines()
               if line.startswith("#") and len(line) in (7, 9)}
    out: list[str] = []
    for color in colors:
        hex6 = _rgb_to_hex(color)
        if hex6 is None or hex6 not in allowed:
            out.append(color)
    return out


def shoot(
    stage: str,
    base: str,
    theme: str,
    screen_ids: list[str],
    token_file: Path,
) -> Path:
    short_sha, porcelain = git_state()
    if porcelain.strip():
        print("ОТКАЗ: дерево git грязно (git status --porcelain):")
        print(porcelain)
        print("Коммитьте изменения перед съёмкой (TZ §2.2).")
        raise SystemExit(2)

    secret = read_secret()
    ctx = manifest_context() if any(
        s.needs_manifest for s in SCREENS if s.sid in screen_ids
    ) else {}
    out_dir = SHOTS_ROOT / stage / short_sha
    out_dir.mkdir(parents=True, exist_ok=True)

    # Двухпроходная съёмка («с данными» → --reset → «пустые состояния»):
    # S03/S07 снимаются после сброса, когда /books и /jobs пусты. Мержим
    # с metrics.json того же коммита, чтобы итог = все 15 экранов.
    metrics_path = out_dir / "metrics.json"
    merged: dict[str, Any] = {}
    if metrics_path.exists():
        previous = json.loads(metrics_path.read_text(encoding="utf-8"))
        merged = dict(previous.get("screens", {}))

    screens = [s for s in SCREENS if s.sid in screen_ids]
    run_screens = list(dict.fromkeys(list(merged) + [s.sid for s in screens]))
    metrics: dict[str, Any] = {
        "run": {
            "stage": stage,
            "commit": short_sha,
            "theme": theme,
            "base": base,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "screens": run_screens,
        },
        "screens": merged,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=DSF,
            is_mobile=True,
            has_touch=True,
            color_scheme=theme,
            locale="ru-RU",
            user_agent=ANDROID_UA,
        )
        page = context.new_page()

        # Порядок: сначала неаутентифицированные экраны (S01 — страница
        # логина), затем логин, затем остальные (TZ §2.8).
        public = [s for s in screens if not s.needs_auth]
        private = [s for s in screens if s.needs_auth]

        def capture(screen: Screen) -> None:
            url = base + fill(screen.url, ctx)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            wait_ready(page)

            vp = out_dir / f"{screen.sid}-{screen.name}-{theme}-viewport.png"
            fp = out_dir / f"{screen.sid}-{screen.name}-{theme}-full.png"
            page.screenshot(path=str(vp), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
            page.screenshot(path=str(fp), full_page=True)

            data = page.evaluate(METRICS_JS, screen.item_selector)
            colors = [c for c, _ in sorted(data["colors"].items(), key=lambda kv: -kv[1])]
            items = data["items"]
            targets = {
                g: {
                    "min_w": (None if v["min_w"] == float("inf") else round(v["min_w"], 1)),
                    "min_h": (None if v["min_h"] == float("inf") else round(v["min_h"], 1)),
                    "n": v["n"],
                }
                for g, v in data["targets"].items()
            }
            metrics["screens"][screen.sid] = {
                "name": screen.name,
                "url": url,
                "max_font_px": round(data["max_font_px"], 1),
                "first_item_y": (None if not items else round(items[0]["top"], 1)),
                "full_items": (
                    None if not items
                    else sum(1 for it in items if it["bottom"] <= HEIGHT - TABBAR)
                ),
                "items_total": len(items),
                "colors": colors,
                "colors_off_token": off_tokens(colors, token_file),
                "long_text_blocks": list(dict.fromkeys(data["long_text_blocks"]))[:20],
                "min_target": targets,
                "saturated_share": round(saturated_share(vp), 4),
                "files": {
                    "viewport": vp.name,
                    "full": fp.name,
                },
            }
            print(
                f"{screen.sid} {screen.name}: {url} "
                f"(font {metrics['screens'][screen.sid]['max_font_px']} px, "
                f"items {len(items)}, saturated "
                f"{metrics['screens'][screen.sid]['saturated_share']:.1%})"
            )

        for screen in public:
            capture(screen)
        if private:
            # form=, а не data=: Playwright сериализует dict в data= как
            # JSON (content-type application/json), а app.request.form()
            # JSON не парсит → пустой password → 401.
            resp = context.request.post(
                base + "/login",
                form={"password": secret, "next": "/"},
                max_redirects=0,
            )
            if resp.status != 303:
                raise SystemExit(f"ОТКАЗ: login → HTTP {resp.status} (ожидался 303)")
            for screen in private:
                capture(screen)

        browser.close()

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"metrics: {out_dir / 'metrics.json'}")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, help="каталог снимков (V0…V4)")
    parser.add_argument("--base", default="http://127.0.0.1:8421")
    parser.add_argument("--theme", default="dark", choices=("dark", "light"))
    parser.add_argument(
        "--screens", nargs="*", default=None,
        help="подмножество S01…S15 (дефолт — все 15)",
    )
    parser.add_argument(
        "--tokens",
        default=str(REPO_ROOT / "docs" / "design" / "tokens.json"),
        help="файл токенов (строки #rrggbb) для colors_off_token; V0 — без токенов",
    )
    args = parser.parse_args()

    ids = args.screens or [s.sid for s in SCREENS]
    unknown = [i for i in ids if i not in {s.sid for s in SCREENS}]
    if unknown:
        raise SystemExit(f"неизвестные экраны: {unknown}")
    shoot(args.stage, args.base, args.theme, ids, Path(args.tokens))


if __name__ == "__main__":
    main()
