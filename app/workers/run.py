"""Входная точка воркера (E3).

    python -m app.workers.run --config config/config.yaml --env .env [--loop]

По умолчанию воркер берёт одно занятое задание и выходит (--once);
--loop — непрерывный цикл (производительный режим).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.core.config import load_settings
from app.core.errors import ConfigurationError
from app.core.logging import setup_logging
from app.llm.cost import job_cost_summary
from app.llm.profiles import describe_profiles
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.worker import Worker


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.workers.run")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument(
        "--loop", action="store_true", help="непрерывный цикл (иначе — одно задание)"
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config, args.env)
    except ConfigurationError as exc:
        print(f"ошибка конфигурации: {exc.message}", file=sys.stderr)
        return exc.exit_code

    setup_logging(settings.logs_dir, secrets=settings.secrets())
    # E4.3: перед стартом задания CLI показывает провайдер/эндпоинт/модель по профилям.
    print(describe_profiles(settings))
    with Database(settings.jobs_db_path, settings.storage.busy_timeout_ms) as db:
        repo = JobRepository(db)
        worker = Worker(settings, repo)
        if args.loop:
            worker.run_forever()
            return 0
        job = worker.run_once()
        if job is None:
            print("worker: нет доступных заданий (очередь пуста)")
            return 0
        print(
            f"worker: job={job.id} status={job.status.value} "
            f"progress={job.progress:.2f} tokens_total={job.tokens_total}"
        )
        # E4.6: стоимость — только при заданных ценах, иначе Cost: unknown.
        print(job_cost_summary(settings, repo, job))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
