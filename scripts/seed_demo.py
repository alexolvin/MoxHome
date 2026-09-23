"""Посев демо-стенда серии TZ-UI-V (TZ §11).

Демо-стенд — отдельная веб-инстанс (config/demo.yaml, данные
data/demo/), на которой снимаются 15 экранов. Скрипт сидит:

* 12 книг: 8 Gutenberg (epub3.images — с обложками) + 4 EPUB-фикстуры
  репозитория (без обложек); импорт — теми же сервисами, что боевой
  POST /api/books (classify → EPUBCheck → scan_book → extract_cover
  → .meta.json);
* 6 заданий во всех 5 группах статусов (wait/run/ok/warn/err),
  прогресс — через слой хранения проекта (services.jobs.create_job +
  JobRepository.transition/set_progress), без прямого SQL.

Маркировка демо-фикстуры: actor ``seed-demo``, reason
``demo-fixture: …`` в transitions; маркер ``.demo-stand`` в корне
каталога данных.

Отказ (fail closed): каталог данных демо совпадает с боевым (строко)
или боевой корень лежит внутри демо-корня. ``--reset`` дополнительно
требует маркер (сбрасывается только демо-стенд).

Запуск (из корня репозитория):
    .venv/bin/python scripts/seed_demo.py
    .venv/bin/python scripts/seed_demo.py --reset   # сначала очистить
    .venv/bin/python scripts/seed_demo.py --wipe    # только очистить (без посева)

Пустые состояния (S03 /books пусто, S07 /jobs пусто): ``--wipe`` → съёмка
S03/S07 → повторный посев без флагов.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from app.adapters.convert.checks import classify  # noqa: E402
from app.adapters.epub_engine.epubcheck import facts as epubcheck_facts  # noqa: E402
from app.adapters.epub_engine.epubcheck import run_epubcheck  # noqa: E402
from app.core.config import ALL_ENV_KEYS, Settings, load_settings  # noqa: E402
from app.domain.job import Job, JobStatus  # noqa: E402
from app.services import jobs as jobs_svc  # noqa: E402
from app.services.analyze import scan_book  # noqa: E402
from app.services.covers import extract_cover  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.web import views  # noqa: E402

DEMO_MARKER = ".demo-stand"
MANIFEST = ".seed-manifest.json"
GUTENBERG_IDS: tuple[int, ...] = (11, 74, 84, 120, 345, 1342, 1661, 2701)
FIXTURES: tuple[str, ...] = ("probe.en.epub", "bilingual.epub", "lang_de.epub", "complex.epub")
PIPELINE: tuple[JobStatus, ...] = (
    JobStatus.VALIDATING,
    JobStatus.PLANNING,
    JobStatus.TRANSLATING,
    JobStatus.ANALYZING,
    JobStatus.GENERATING,
    JobStatus.VALIDATING_OUTPUT,
    JobStatus.GLOSSARY_FIX,
    JobStatus.LEARNING,
)
#: 6 заданий — по каждому статусу группы (TZ §11: 1–2 на группу, 5 групп).
JOB_PLAN: tuple[dict[str, object], ...] = (
    {"gid": 11, "status": JobStatus.TRANSLATING, "progress": 0.47},
    {"gid": 74, "status": JobStatus.CREATED, "progress": 0.0},
    {"gid": 84, "status": JobStatus.COMPLETED, "progress": 1.0},
    {"gid": 120, "status": JobStatus.COMPLETED_WITH_WARNINGS, "progress": 1.0},
    {"gid": 345, "status": JobStatus.PAUSED, "progress": 0.20},
    {
        "gid": 1342,
        "status": JobStatus.FAILED,
        "progress": 0.35,
        "error": "demo-fixture: LLM-эндпоинт недоступен (демо-стенд, TZ-UI-V)",
    },
)


def load_pair(demo_config: Path, prod_config: Path) -> tuple[Settings, Settings]:
    demo = load_settings(demo_config, REPO_ROOT / ".env.demo")
    prod = load_settings(prod_config, REPO_ROOT / ".env")
    return demo, prod


def guard(demo_root: Path, prod_root: Path) -> None:
    if demo_root == prod_root:
        raise SystemExit(
            f"ОТКАЗ: каталог данных демо {demo_root} совпадает с боевым {prod_root}"
        )
    if prod_root.is_relative_to(demo_root):
        raise SystemExit(
            f"ОТКАЗ: боевой корень {prod_root} лежит внутри демо-каталога {demo_root}"
        )


def ensure_env_demo() -> None:
    env_path = REPO_ROOT / ".env.demo"
    if env_path.exists():
        return
    env_path.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\n"
        f"SERVER_AUTH_SECRET={secrets.token_hex(16)}\n",
        encoding="utf-8",
    )
    print(f".env.demo создан (секрет случайный; файл в .gitignore): {env_path}")


def reset_data(settings: Settings) -> None:
    marker = settings.storage.root / DEMO_MARKER
    if not marker.exists():
        raise SystemExit(
            f"ОТКАЗ: нет маркера {marker} — это не демо-стенд, данные не трогаю"
        )
    for directory in settings.all_data_dirs():
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    # Манифест живёт в корне каталога данных (не в data-подкаталогах) —
    # без его удаления после сброса скрипт скажет «уже посеяно» на пустом
    # стенде.
    manifest = settings.storage.root / MANIFEST
    if manifest.exists():
        manifest.unlink()
    print(f"сброшено: {settings.storage.root}")


def _ipv4_only() -> None:
    """IPv6 на этом хосте нестабилен: gutenberg.org отдаёт AAAA, и
    handshake иногда уходит в blackhole на весь timeout (замер 2026-09-23:
    ConnectTimeout 180 c, повтор по IPv4 — 2,9 c). Форсим IPv4 в этом
    процессе (операторский скрипт, не приложение)."""
    orig = socket.getaddrinfo

    def ipv4(host, port, family=0, type=0, proto=0, flags=0):  # noqa: PLR0917 — сигнатура getaddrinfo
        if family in (0, socket.AF_INET6, getattr(socket, "AF_UNSPEC", -1)):
            family = socket.AF_INET
        return orig(host, port, family, type, proto, flags)

    socket.getaddrinfo = ipv4


def _download(client: httpx.Client, url: str, cached: Path) -> None:
    last: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            resp = client.get(url, timeout=120)
            resp.raise_for_status()
            cached.write_bytes(resp.content)
            return
        except httpx.HTTPError as e:
            last = e
            print(f"  повтор {attempt}/3: {type(e).__name__} {str(e)[:80]}")
    raise SystemExit(f"не удалось скачать {url}: {last}")


def new_name(original: str) -> str:
    """То же имя, что боевой импорт (api.upload_book): u1-<hex8>-<файл>."""
    return f"u1-{uuid.uuid4().hex[:8]}-{original}"


def import_book(settings: Settings, src: Path, name: str) -> Path:
    """Тот же конвейер, что POST /api/books для .epub (api._import_epub_sync)."""
    dest = settings.input_dir / name
    shutil.copyfile(src, dest)
    try:
        verdict = classify(settings, dest, "epub", dest.stat().st_size)
        if not verdict["ok"]:
            raise RuntimeError(f"classify: {verdict['detail']}")
        outcome = run_epubcheck(
            settings.epubcheck_jar_path, dest, settings.epubcheck.timeout_seconds
        )
        if outcome.available and (outcome.fatals or outcome.errors):
            raise RuntimeError(
                "epubcheck: "
                + " ".join((*outcome.fatals, *outcome.errors)[:3])
            )
        meta = scan_book(settings, dest)
        cover_path = extract_cover(dest)
        meta = {
            **views.book_meta_defaults(),
            **meta,
            "imported_at": time.time(),
            "source_format": "epub",
            "epubcheck": epubcheck_facts(outcome),
            "cover_path": cover_path,
        }
        views.write_book_meta(dest, meta)
    except Exception:
        views.delete_book_files(settings, dest)
        raise
    return dest


def path_to(target: JobStatus) -> tuple[JobStatus, ...]:
    if target is JobStatus.PAUSED:
        return (JobStatus.VALIDATING, JobStatus.PAUSED)
    if target is JobStatus.FAILED:
        return (JobStatus.VALIDATING, JobStatus.FAILED)
    if target in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS):
        return (*PIPELINE, target)
    return PIPELINE[: PIPELINE.index(target) + 1]


def seed_job(  # noqa: PLR0917 — служебная фабрика демо-фикстуры
    repo: JobRepository,
    settings: Settings,
    source_path: Path,
    target: JobStatus,
    progress: float,
    error: str | None = None,
) -> Job:
    job = jobs_svc.create_job(
        repo, settings, source_path=source_path, target_language="ru"
    )
    if target is not JobStatus.CREATED:
        for step in path_to(target):
            if step is JobStatus.FAILED:
                repo.transition(
                    job.id,
                    step,
                    "demo-fixture: fail",
                    actor="seed-demo",
                    error_message=error or "demo-fixture: failure",
                )
            else:
                repo.transition(
                    job.id, step, "demo-fixture: seed", actor="seed-demo"
                )
    repo.set_progress(job.id, progress)
    # Перечитываем из БД: transition/set_progress меняют строку в БД, а
    # не в-памяти доменный объект (иначе вывод «CREATED 0.00» не совпадёт
    # со стендом).
    fresh = repo.get(job.id)
    return fresh if fresh is not None else job


def clean_env() -> None:
    """Защита от утечки: shell сессии может держать секреты/бинд боевого
    стенда, а os.environ приоритетнее файла .env (Rule 1) — демо-стенд
    читает строго свой .env.demo."""
    for key in ALL_ENV_KEYS:
        os.environ.pop(key, None)


def main() -> None:
    clean_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo-config",
        default=str(REPO_ROOT / "config" / "demo.yaml"),
        help="YAML демо-стенда (дефолт config/demo.yaml)",
    )
    parser.add_argument(
        "--prod-config",
        default=str(REPO_ROOT / "config" / "config.yaml"),
        help="YAML боевого стенда для гварда (дефолт config/config.yaml)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="сначала очистить каталоги данных демо-стенда",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help=(
            "только очистить каталоги данных (без посева) — для пустых "
            "состояний S03/S07"
        ),
    )
    args = parser.parse_args()

    if args.reset and args.wipe:
        raise SystemExit("ОТКАЗ: --reset и --wipe несовместимы")

    demo, prod = load_pair(Path(args.demo_config), Path(args.prod_config))
    guard(demo.storage.root, prod.storage.root)
    if args.wipe:
        reset_data(demo)
        print("стёрто (без посева): S03/S07; для посева — без флагов")
        return
    if args.reset:
        reset_data(demo)

    manifest_path = demo.storage.root / MANIFEST
    if manifest_path.exists() and not args.reset:
        print(f"уже посеяно (манифест {manifest_path}); --reset для повторного посева")
        return

    ensure_env_demo()
    (demo.storage.root / DEMO_MARKER).write_text(
        "DEMO STAND — TZ-UI-V fixture (scripts/seed_demo.py)\n", encoding="utf-8"
    )

    repo = JobRepository(
        Database(demo.jobs_db_path, demo.storage.busy_timeout_ms)
    )
    _ipv4_only()
    cache_dir = demo.storage.root / ".gutenberg-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = REPO_ROOT / "tests" / "fixtures" / "epub"

    books: dict[str, str] = {}
    # 8 Gutenberg: epub3.images — с обложками (TZ §11).
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for gid in GUTENBERG_IDS:
            name = new_name(f"gutenberg-{gid}.epub")
            books[str(gid)] = name
            cached = cache_dir / f"gutenberg-{gid}.epub"
            if not cached.exists():
                url = f"https://www.gutenberg.org/ebooks/{gid}.epub3.images"
                print(f"загрузка {url} …")
                _download(client, url, cached)
            import_book(demo, cached, name)
            print(f"  книга: {name}")
    # 4 фикстуры репозитория: без обложек (TZ §11: ≥ 2 без обложки).
    for fixture in FIXTURES:
        name = new_name(fixture)
        books[f"fixture:{fixture}"] = name
        import_book(demo, fixture_dir / fixture, name)
        print(f"  книга: {name}")

    jobs_out: dict[str, str] = {}
    for plan in JOB_PLAN:
        gid = str(plan["gid"])
        job = seed_job(
            repo,
            demo,
            demo.input_dir / books[gid],
            JobStatus(str(plan["status"])),
            float(plan["progress"]),
            str(plan.get("error") or ""),
        )
        jobs_out[gid] = job.id
        print(f"  задание {job.id} → {job.status.value} (progress {job.progress:.2f})")

    manifest_path.write_text(
        json.dumps(
            {"books": books, "jobs": jobs_out, "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"готово: книг {len(books)}, заданий {len(jobs_out)}; манифест {manifest_path}")


if __name__ == "__main__":
    main()
