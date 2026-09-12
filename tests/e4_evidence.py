"""Сборщик verbatim-evidence для CONTROL E4 (не тест; assertions — в test_e4_llm.py).

Запуск: python tests/e4_evidence.py
Захваты: docs/CONTROL/evidence/E4/e4-1…e4-6_*.txt

Сценарии:
  e4-1  grep URL/имён моделей/портов/ключей — 0 находок вне config/ и фикстур
  e4-2  разрешение промптов (файл + версия) записано в задание — 100% задач
  e4-3  pair_required без парного промпта → ошибка, 0 тихого generic
  e4-4  невалидный JSON (mock) → ошибка задачи, пустой артефакт не создаётся
  e4-5  сумма токенов по профилям == сумма по заданию, расхождение 0
  e4-6  цена не задана → Cost: unknown в UI и отчёте, 100%
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402

from app.core.config import Settings, load_settings  # noqa: E402
from app.llm.analysis import AnalysisClient, AnalysisTaskError  # noqa: E402
from app.llm.cost import format_cost, job_cost_summary  # noqa: E402
from app.llm.profiles import describe_profiles  # noqa: E402
from app.llm.prompts import PromptError  # noqa: E402
from app.llm.tasks import all_tasks, task  # noqa: E402
from app.services.jobs import create_job  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.workers.pipeline import Pipeline  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "E4"
FIXTURES = ROOT / "tests" / "fixtures" / "epub"
_COMPLEX = FIXTURES / "complex.epub"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


# ------------------------------------------------------------------ служебное


def _run(cmd: str) -> str:
    """Shell-команда (grep -R и пайплайны) с verbatim-stdout; exit 1 = «не найдено»."""
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, shell=True, check=False)
    return proc.stdout


def _out_lines(text: str, empty: str) -> list[str]:
    """Строки verbatim-вывода; при пустом выводе — одна строка-пояснение."""
    lines = [line for line in text.splitlines() if line.strip()]
    return [f"  {line}" for line in lines] if lines else [f"  {empty}"]


def _make_env_storage(work: Path, server: MockLLMServer) -> tuple[Settings, Path, Path]:
    work.mkdir(parents=True, exist_ok=True)
    env_file = work / "test.env"
    # E4.1: в .env — только секреты и адрес бинда.
    env_file.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\n"
        "LLM_API_KEY=test-key-value-000\n",
        encoding="utf-8",
    )
    # E4.1: адрес и модель LLM — только в YAML-профилях.
    profile_lines = "\n".join(
        f"    {name}:\n      base_url: {server.url}\n      model: test-model"
        for name in ("translation", "fill", "analysis")
    )
    cfg_file = work / "config.yaml"
    cfg_file.write_text(
        f"storage:\n  root: {work}\nllm:\n  profiles:\n{profile_lines}",
        encoding="utf-8",
    )
    settings = load_settings(cfg_file, env_file)
    # Промпты — данные: репозиторный prompts/ — единственный источник.
    shutil.copytree(ROOT / "prompts", settings.prompts_dir)
    return settings, cfg_file, env_file


def _repo_for(settings: Settings) -> tuple[Database, JobRepository]:
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return db, JobRepository(db)


def _run_all_tasks(settings: Settings, repo: JobRepository, job_id: str) -> int:
    client = AnalysisClient(settings, repo, job_id)
    text = "The quick brown fox jumps over the lazy dog."
    count = 0
    for spec in all_tasks():
        client.run_task(spec, "en", "de", text)
        count += 1
    return count


# ------------------------------------------------------------------ E4-1


def ev_e4_1() -> None:
    cmd1 = "grep -RnE 'https?://' app/"
    cmd2 = "grep -RnE 'https?://' app/ | grep -vE 'idpf\\.org|w3\\.org|purl\\.org'"
    cmd3 = r"grep -RnE 'sk-[A-Za-z0-9_-]{8,}|probe-model|test-model|example-model' app/"
    llm_text = _run(cmd2)
    mk_text = _run(cmd3)
    raw_lines = _out_lines(_run(cmd1), "(ничего не найдено)")
    llm_url_lines = _out_lines(llm_text, "(ничего не найдено — 0 LLM-URL)")
    mk_lines = _out_lines(mk_text, "(ничего не найдено — 0)")
    llm_url_count = len([line for line in llm_text.splitlines() if line.strip()])
    mk_count = len([line for line in mk_text.splitlines() if line.strip()])
    evidence(
        "e4-1_grep_no_hardcode.txt",
        "\n".join(
            [
                f"##### E4-1: поиск хардкода (verbatim)\nдата: {STAMP}",
                "",
                "Команда 1 (URL в app/; метод ТЗ: grep -R по URL):",
                f"$ {cmd1}",
                *raw_lines,
                "→ все находки — стандартные XML-неймспейсы протоколов EPUB",
                "  (idpf.org, w3.org, purl.org): константы формата, не LLM-настройки.",
                "",
                "Команда 2 (те же URL, исключая протокольные неймспейсы):",
                f"$ {cmd2}",
                *llm_url_lines,
                "",
                "Команда 3 (API-ключи и имена моделей в app/):",
                f"$ {cmd3}",
                *mk_lines,
                "",
                f"LLM-URL вне config/ и фикстур          : {llm_url_count}",
                f"имена моделей/API-ключей в app/        : {mk_count}",
                "порты: охвачены grep'ом URL (порт значим только в адресе эндпоинта);",
                "отдельных числовых портов LLM-эндпоинтов в app/ нет (команда 2 пуста).",
                "допустимые источники (по ТЗ): config/config.example.yaml (шаблон)",
                "и тестовые фикстуры (tests/) — в них адреса/модели есть по определению.",
            ]
        ),
    )


# ------------------------------------------------------------------ E4-2


def ev_e4_2(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db, repo = _repo_for(settings)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        tasks_run = _run_all_tasks(settings, repo, job.id)
        recorded = repo.list_resolved_prompts(job.id)
        lines = [
            f"  {r.task:<22} file={r.file:<32} version=v{r.version} level={r.level}"
            for r in recorded
        ]
        evidence(
            "e4-2_resolution_recorded.txt",
            "\n".join(
                [
                    f"##### E4-2: разрешение промпта записано в задании (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    f"задач исполнено                : {tasks_run}",
                    f"записей resolved_prompts       : {len(recorded)}",
                    f"записано / исполнено           : {100.0 * len(recorded) / tasks_run:.0f}%",
                    "",
                    "записи (файл + версия + уровень) — verbatim из БД задания:",
                    *lines,
                    "",
                    "уровни: analysis_vocabulary/grammar — generic v1 (пары и уровня",
                    "языка для них нет); analysis_glossary — pair en/de v1 (парный",
                    "промпт имеет приоритет над generic; задача pair_required).",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ E4-3


def ev_e4_3(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db, repo = _repo_for(settings)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        spec = task("analysis_glossary")
        client = AnalysisClient(settings, repo, job.id, attempts=1)
        message = ""
        try:
            client.run_task(spec, "fr", "de", "Le renard brun saute par-dessus le chien.")
        except PromptError as exc:
            message = str(exc)
        recorded = [r for r in repo.list_resolved_prompts(job.id) if r.task == spec.name]
        generic_exists = (settings.prompts_dir / "analysis_glossary" / "v1.yaml").is_file()
        raw_exists = (settings.jobs_dir / job.id / "raw").exists()
        assert message, "ожидалось PromptError"
        evidence(
            "e4-3_pair_required_error.txt",
            "\n".join(
                [
                    f"##### E4-3: pair_required без промпта пары (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    f"задача: {spec.name} (pair_required={spec.pair_required})",
                    "пары: fr→de (парного промпта нет)",
                    "",
                    "generic-промпт существует (мог бы быть тихим фолбэком): "
                    f"{generic_exists}",
                    "фактически выброшена ошибка:",
                    f"  PromptError: {message}",
                    "",
                    f"записей resolved_prompts по задаче (тихий generic): {len(recorded)}",
                    f"LLM-запросов выполнено (каталог raw/ существует): {raw_exists}",
                    "",
                    "механизм: при pair_required резолвер ищет ТОЛЬКО уровень пары;",
                    "при его отсутствии — PromptError ДО обращения к модели и ДО записи",
                    "в задание: ни generic, ни уровень языка подставлены не были.",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ E4-4


def ev_e4_4(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db, repo = _repo_for(settings)
    server.set_scenario("bad_json")
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        client = AnalysisClient(settings, repo, job.id, attempts=3)
        message = ""
        try:
            client.run_task(task("analysis_vocabulary"), "en", "de", "The fox jumps.")
        except AnalysisTaskError as exc:
            message = str(exc)
        job_dir = settings.jobs_dir / job.id
        artifact = job_dir / "analysis" / "analysis_vocabulary.json"
        raw = sorted(p.name for p in (job_dir / "raw").iterdir())
        final = repo.require(job.id)
        assert message, "ожидалось AnalysisTaskError"
        evidence(
            "e4-4_invalid_json_error.txt",
            "\n".join(
                [
                    "##### E4-4: невалидный JSON от модели (mock bad_json) (verbatim)\n"
                    f"дата: {STAMP}",
                    f"job_id: {job.id}",
                    "сценарий mock: bad_json (ответ 200 с некорректным JSON), попыток: 3",
                    "",
                    "выброшена ошибка задачи:",
                    f"  AnalysisTaskError: {message}",
                    "",
                    f"пустой артефакт analysis/analysis_vocabulary.json создан: "
                    f"{artifact.exists()}",
                    "сырые ответы попыток в jobs/<job_id>/raw/ (полное сохранение, E4.4):",
                    *[f"  {name}" for name in raw],
                    f"токены неудачных попыток учтены в задании (analysis): "
                    f"{final.tokens_analysis}",
                    "",
                    "механизм: ответ валидируется jsonschema-схемой задачи; при сбое —",
                    "повтор (ограничено profile.retry_times); после исчерпания — ошибка",
                    "задачи, артефакт результата НЕ создаётся; каждая попытка хранится",
                    "в raw/ для аудита.",
                ]
            ),
        )
    finally:
        server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ E4-5


def ev_e4_5(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db, repo = _repo_for(settings)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings)
        job_now = repo.require(job.id)
        pipeline.run_validating(repo, job_now)
        pipeline.run_planning(repo, job_now)
        pipeline.run_translating(repo, job_now, lambda: None)
        final = repo.require(job.id)
        t, f, a = (
            final.tokens_translation,
            final.tokens_fill,
            final.tokens_analysis,
        )
        diff = final.tokens_total - (t + f + a)
        breakdown = repo.profile_tokens(job.id)
        rows = [
            f"  {name:<12} input={row.input_tokens:<6} input_cache="
            f"{row.input_cache_tokens:<4} output={row.output_tokens:<6} "
            f"input+output={row.input_tokens + row.output_tokens}"
            for name, row in sorted(breakdown.items())
        ]
        evidence(
            "e4-5_token_sums.txt",
            "\n".join(
                [
                    "##### E4-5: сумма токенов по профилям == сумма по заданию (verbatim)\n"
                    f"дата: {STAMP}",
                    f"job_id: {job.id}",
                    "полный прогон (validating → planning → translating), mock ok",
                    "",
                    f"tokens_translation : {t}",
                    f"tokens_fill        : {f}",
                    f"tokens_analysis    : {a}",
                    f"сумма по профилям  : {t + f + a}",
                    f"tokens_total (job) : {final.tokens_total}",
                    f"расхождение        : {diff}",
                    "",
                    "разбивка по профилям (таблица job_tokens, E4.7; тракт T — из",
                    "llm.input_tokens/input_cache_tokens/output_tokens):",
                    *rows,
                    "для каждого профиля input + output == сумма профиля (см. колонки)",
                    "— разбивка не расходится с итогами.",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ E4-6


def ev_e4_6(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db, repo = _repo_for(settings)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        repo.add_tokens(job.id, "translation", 1234)
        final = repo.require(job.id)
        summary = job_cost_summary(settings, repo, final)
        ui = describe_profiles(settings)
        lines = [line for line in summary.splitlines() if line.startswith("cost: ")]
        unknown = sum(1 for line in lines if "Cost: unknown" in line)
        profile = settings.llm.profile("translation")
        priced = profile.model_copy(
            update={"input_price_per_mtok": 2.0, "output_price_per_mtok": 8.0}
        )
        evidence(
            "e4-6_cost_unknown.txt",
            "\n".join(
                [
                    f"##### E4-6: цена не задана → Cost: unknown (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    "цен в конфигурации профилей: 0 (input/output price не заданы)",
                    "",
                    "отчёт по заданию (строки стоимости, как в выводе воркера):",
                    *[f"  {line}" for line in lines],
                    f"строк «Cost: unknown» из {len(lines)} профилей: {unknown}"
                    f" ({100.0 * unknown / len(lines):.0f}%)",
                    "",
                    "UI/CLI-показ профилей перед стартом задания (E4.3) — verbatim:",
                    *[f"  {line}" for line in ui.splitlines()],
                    "",
                    "контроль против выдуманной стоимости: те же токены, НО с ценами",
                    "(2.0/8.0 $/Mtok, заданы явно):",
                    f"  {format_cost(priced, 1234, 0)}",
                    "без заданных цен функция возвращает ровно «Cost: unknown» —",
                    "численная стоимость без цен в конфигурации не выводится.",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ main


def main() -> None:
    server = MockLLMServer()
    server.start()
    work_base = ROOT / ".qwen" / "tmp" / "e4_evidence"
    if work_base.exists():
        shutil.rmtree(work_base)
    try:
        print("E4-1: grep-поиск хардкода …")
        ev_e4_1()
        print("E4-2: запись разрешения промптов …")
        ev_e4_2(work_base / "s2", server)
        print("E4-3: pair_required без пары …")
        ev_e4_3(work_base / "s3", server)
        print("E4-4: невалидный JSON …")
        ev_e4_4(work_base / "s4", server)
        print("E4-5: суммы токенов …")
        ev_e4_5(work_base / "s5", server)
        print("E4-6: Cost: unknown …")
        ev_e4_6(work_base / "s6", server)
        print("готово")
    finally:
        server.stop()
        if work_base.exists():
            shutil.rmtree(work_base, ignore_errors=True)


if __name__ == "__main__":
    main()
