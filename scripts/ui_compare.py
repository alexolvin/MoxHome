"""Compare-листы серии TZ-UI-V (TZ §2.5).

Склеивает в одну картинку одинаковой высоты эталонный фрагмент (слева)
и viewport-снимок (справа). Фрагменты — из эталона (геометрия —
``docs/design/ref/crops.json``), соответствие экранов фрагментам —
таблица TZ §2.8. Несколько фрагментов экрана — столбиком слева.

Запуск:
    .a/tasks/ui-audit/.venv/bin/python scripts/ui_compare.py --stage V0
    .a/tasks/ui-audit/.venv/bin/python scripts/ui_compare.py --stage V2 \
        --shots /mdisk/EpubTranslator/.a/tasks/ui-audit/shots/V2/abc1234
Результат: compare-S<nn>.png в том же каталоге снимков.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = REPO_ROOT / "docs" / "design" / "ref"
SHOTS_ROOT = REPO_ROOT / ".a" / "tasks" / "ui-audit" / "shots"

#: Соответствие экранов TZ §2.8 фрагментам эталона (ключи crops.json).
SCREEN_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "S01": ("login",),
    "S02": ("library", "dashboard-cards"),
    "S03": ("library",),
    "S04": ("export-sheet-cover",),
    "S05": ("export-sheet-cover",),
    "S06": ("dashboard-cards", "status-colors"),
    "S07": ("kit-2",),
    "S08": ("dashboard-cards",),
    "S09": ("create-wizard", "create-wizard-wide"),
    "S10": ("export-sheet",),
    "S11": ("export-sheet", "export-sheet-cover"),
    "S12": ("settings-menu", "settings-toggles"),
    "S13": ("settings-toggles", "kit-2"),
    "S14": ("kit-1", "kit-2", "kit-3", "chips-buttons"),
    "S15": ("settings-menu",),
}

#: Имена экранов (совпадает со SCREENS в ui_shots.py) — для имени снимка.
SCREEN_NAMES: dict[str, str] = {
    "S01": "login", "S02": "books", "S03": "books-empty", "S04": "book-cover",
    "S05": "book-nocover", "S06": "jobs", "S07": "jobs-empty",
    "S08": "job-translating", "S09": "create", "S10": "import",
    "S11": "export", "S12": "settings", "S13": "settings-llm",
    "S14": "dev-kit", "S15": "book-delete",
}

PANEL_BG = (13, 17, 23)  # тёмная подложка за фрагментами (≈ --bg §4)


def build(
    shots_dir: Path, theme: str, crops: dict[str, dict[str, object]]
) -> None:
    src_cache: dict[str, Image.Image] = {}
    for sid, fragments in SCREEN_FRAGMENTS.items():
        name = SCREEN_NAMES[sid]
        vp_path = shots_dir / f"{sid}-{name}-{theme}-viewport.png"
        if not vp_path.exists():
            print(f"{sid}: нет снимка {vp_path.name} — пропуск")
            continue
        vp = Image.open(vp_path).convert("RGB")
        part_h = vp.height // len(fragments)
        parts: list[Image.Image] = []
        for frag in fragments:
            spec = crops.get(frag)
            if spec is None:
                raise SystemExit(f"фрагмент {frag} не найден в crops.json")
            source = str(spec["source"])
            if source not in src_cache:
                src_cache[source] = Image.open(REF_DIR / source).convert("RGB")
            x, y, w, h = (int(v) for v in spec["box"])  # type: ignore[union-attr]
            part = src_cache[source].crop((x, y, x + w, y + h))
            scale = part_h / part.height
            parts.append(part.resize((max(1, int(part.width * scale)), part_h),
                                     Image.LANCZOS))
        left_w = max(p.width for p in parts)
        stack = Image.new("RGB", (left_w, vp.height), PANEL_BG)
        y = 0
        for p in parts:
            stack.paste(p, (0, y))
            y += p.height
        canvas = Image.new("RGB", (left_w + 8 + vp.width, vp.height), PANEL_BG)
        canvas.paste(stack, (0, 0))
        canvas.paste(vp, (left_w + 8, 0))
        out = shots_dir / f"compare-{sid}.png"
        canvas.save(out)
        print(f"{sid}: {out.name} ({len(fragments)} фрагм.)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, help="V0…V4")
    parser.add_argument(
        "--shots",
        default=None,
        help="каталог снимков (дефолт — последний <stage>/<short-sha>)",
    )
    parser.add_argument("--theme", default="dark", choices=("dark", "light"))
    args = parser.parse_args()

    if args.shots:
        shots_dir = Path(args.shots)
    else:
        candidates = [p for p in (SHOTS_ROOT / args.stage).iterdir() if p.is_dir()]
        if not candidates:
            raise SystemExit(f"нет каталогов снимков: {SHOTS_ROOT / args.stage}")
        # Не по имени (short-sha не упорядочен во времени), а по mtime.
        shots_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    if not shots_dir.is_dir():
        raise SystemExit(f"каталог снимков не найден: {shots_dir}")
    crops = json.loads((REF_DIR / "crops.json").read_text(encoding="utf-8"))
    build(shots_dir, args.theme, crops)


if __name__ == "__main__":
    main()
