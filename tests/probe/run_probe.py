# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""E1 ENGINE_PROBE: измерение фактического поведения epub-translator (verbatim-захваты).

Запуск: python tests/probe/run_probe.py
Результат: docs/CONTROL/evidence/E1/e1-*.txt (верbatim), резюме в stdout.

Порядок соответствует пунктам 1–9 раздела E1 ТЗ:
  P1  pip show; P2  сигнатуры; P3  APPEND_BLOCK; P4  APPEND_TEXT/REPLACE;
  P5  заголовки/TOC/OPF; P6  кэш; P7  недоступный эндпоинт; P8  on_fill_failed;
  P9  concurrency.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from importlib.metadata import version
from inspect import signature
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

import epub_translator  # noqa: E402
from epub_translator import LLM, FillFailedEvent, SubmitKind, translate  # noqa: E402
from mock_llm_server import MockLLMServer  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "E1"
WORK = ROOT / "cache" / "e1_probe"
BOOK = ROOT / "tests" / "fixtures" / "epub" / "probe.en.epub"


@dataclass
class FillEventInfo:
    error_message: str
    retried_count: int
    over_maximum_retries: bool


@dataclass
class RunResult:
    progress: list[float]
    fill_events: list[FillEventInfo]
    exception: str | None
    elapsed_seconds: float
    total_tokens: int
    input_tokens: int
    input_cache_tokens: int
    output_tokens: int

    def summary(self) -> str:
        return (
            f"exception: {self.exception}\n"
            f"elapsed_seconds: {self.elapsed_seconds}\n"
            f"total_tokens: {self.total_tokens} "
            f"(input={self.input_tokens}, input_cache={self.input_cache_tokens}, "
            f"output={self.output_tokens})\n"
            f"progress: {json.dumps(self.progress)}\n"
            f"fill_events: "
            f"{json.dumps([asdict(event) for event in self.fill_events], ensure_ascii=False)}\n"
        )


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def chapter_text(epub_path: Path, member: str = "OEBPS/chapter1.xhtml") -> str:
    with zipfile.ZipFile(epub_path) as archive:
        return archive.read(member).decode("utf-8")


def zipinfo_dump(epub_path: Path) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(epub_path) as archive:
        for info in archive.infolist():
            lines.append(
                f"{info.filename}\tcompress_type={info.compress_type}"
                f"\tfile_size={info.file_size}\tdate={info.date_time}"
            )
    return "\n".join(lines)


def run_translate(
    name: str,
    *,
    submit: SubmitKind,
    target_language: str = "ru",
    concurrency: int = 1,
    max_retries: int = 1,
    server: MockLLMServer | None = None,
    llm_url: str | None = None,
    model: str = "probe-model",
    cache_name: str | None = None,
    llm_retry_times: int = 1,
    run_timeout: float = 15.0,
) -> RunResult:
    """Один прогон translate() с фиксацией прогресса, событий и токенов."""
    progress: list[float] = []
    fill_events: list[FillEventInfo] = []
    exception: str | None = None

    def on_fill_failed(event: FillFailedEvent) -> None:
        fill_events.append(
            FillEventInfo(event.error_message, event.retried_count, event.over_maximum_retries)
        )

    cache_dir = WORK / f"cache_{cache_name or name}"
    llm = LLM(
        key="mock-key",
        url=llm_url or (server.url if server is not None else ""),
        model=model,
        token_encoding="cl100k_base",
        cache_path=cache_dir,
        timeout=run_timeout,
        temperature=0.2,
        retry_times=llm_retry_times,
        retry_interval_seconds=0.2,
        log_dir_path=WORK / f"engine_log_{name}",
    )
    started = time.time()
    try:
        translate(
            source_path=BOOK,
            target_path=WORK / f"out_{name}.epub",
            target_language=target_language,
            submit=submit,
            max_retries=max_retries,
            max_group_tokens=2600,
            concurrency=concurrency,
            llm=llm,
            on_progress=progress.append,
            on_fill_failed=on_fill_failed,
        )
    except Exception as exc:
        exception = f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}"
    return RunResult(
        progress=progress,
        fill_events=fill_events,
        exception=exception,
        elapsed_seconds=round(time.time() - started, 3),
        total_tokens=llm.total_tokens,
        input_tokens=llm.input_tokens,
        input_cache_tokens=llm.input_cache_tokens,
        output_tokens=llm.output_tokens,
    )


def probe_p1(stamp: str) -> None:
    print("P1: pip show epub-translator")
    proc = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "pip"), "show", "epub-translator"],
        capture_output=True,
        text=True,
        check=False,
    )
    evidence(
        "e1-1_pip_show.txt",
        f"##### E1 P1: версия движка (verbatim)\nдата: {stamp}\n"
        "$ .venv/bin/pip show epub-translator\n" + proc.stdout + proc.stderr,
    )


def probe_p2(stamp: str) -> None:
    print("P2: inspect.signature")
    sig_lines = [
        f"##### E1 P2: фактические сигнатуры (verbatim)\nдата: {stamp}",
        "",
        f"epub-translator version: {version('epub-translator')}",
        f"public __all__: {epub_translator.__all__}",
        "",
        "signature LLM.__init__:",
        str(signature(LLM.__init__)),
        "",
        "signature translate:",
        str(signature(translate)),
        "",
        "LLM-свойства учёта токенов: total_tokens, input_tokens, input_cache_tokens, "
        "output_tokens",
    ]
    evidence("e1-2_signatures.txt", "\n".join(sig_lines))


def probe_p3(stamp: str) -> None:
    print("P3: APPEND_BLOCK")
    server = MockLLMServer()
    server.start()
    try:
        run_block = run_translate("block", submit=SubmitKind.APPEND_BLOCK, server=server)
        requests = server.requests_log()
        translate_req = next(item for item in requests if item["kind"] == "translate")
        fill_req = next(item for item in requests if item["kind"] == "fill")
        out_path = WORK / "out_block.epub"
        chapter1 = chapter_text(out_path)
        chapter2 = chapter_text(out_path, "OEBPS/chapter2.xhtml")
        n_translate = sum(1 for item in requests if item["kind"] == "translate")
        n_fill = sum(1 for item in requests if item["kind"] == "fill")
        content = "\n".join(
            [
                f"##### E1 P3: APPEND_BLOCK (verbatim)\nдата: {stamp}",
                "конфигурация прогона:",
                "  submit=SubmitKind.APPEND_BLOCK, target_language=ru, concurrency=1, "
                "max_retries=1 (fill), max_group_tokens=2600, token_encoding=cl100k_base, "
                "llm: retry_times=1, retry_interval_seconds=0.2, timeout=15.0",
                f"  llm.url={server.url}",
                "",
                run_block.summary(),
                "",
                f"LLM-запросов всего: {len(requests)} (translate={n_translate}, fill={n_fill})",
                "",
                "=== ВХОД: OEBPS/chapter1.xhtml (исходная фикстура) ===",
                chapter_text(BOOK),
                "",
                "=== ВЫХОД: OEBPS/chapter1.xhtml (APPEND_BLOCK) ===",
                chapter1,
                "",
                "=== ВЫХОД: OEBPS/chapter2.xhtml (APPEND_BLOCK) ===",
                chapter2,
                "",
                "=== порядок записей выходного zip (compress_type) ===",
                zipinfo_dump(out_path),
                "",
                "=== ПЕРВЫЙ translate-запрос (полный) ===",
                f"--- system ({len(str(translate_req['system']))} chars) ---",
                str(translate_req["system"]),
                f"--- user ({len(str(translate_req['user']))} chars) ---",
                str(translate_req["user"]),
                f"--- response ({len(str(translate_req['response']))} chars) ---",
                str(translate_req["response"]),
                "",
                "=== ПЕРВЫЙ fill-запрос (полный) ===",
                f"--- system ({len(str(fill_req['system']))} chars) ---",
                str(fill_req["system"]),
                f"--- user ({len(str(fill_req['user']))} chars) ---",
                str(fill_req["user"]),
                f"--- response ({len(str(fill_req['response']))} chars) ---",
                str(fill_req["response"]),
            ]
        )
        evidence("e1-3_append_block.txt", content)
        print(
            f"  tokens={run_block.total_tokens} requests={len(requests)} "
            f"exception={run_block.exception}"
        )
    finally:
        server.stop()


def probe_p4(stamp: str) -> None:
    print("P4: APPEND_TEXT / REPLACE")
    sections: list[str] = [f"##### E1 P4: APPEND_TEXT и REPLACE (verbatim)\nдата: {stamp}"]
    for mode_name, submit_kind in (
        ("APPEND_TEXT", SubmitKind.APPEND_TEXT),
        ("REPLACE", SubmitKind.REPLACE),
    ):
        server_p4 = MockLLMServer()
        server_p4.start()
        try:
            run_p4 = run_translate(mode_name.lower(), submit=submit_kind, server=server_p4)
        finally:
            server_p4.stop()
        out_p4 = WORK / f"out_{mode_name.lower()}.epub"
        chapter_p4 = chapter_text(out_p4) if out_p4.exists() else "(файл не создан)"
        sections.extend(
            [
                "",
                f"=== РЕЖИМ {mode_name} ===",
                f"submit=SubmitKind.{mode_name}, target_language=ru, concurrency=1, max_retries=1",
                run_p4.summary(),
                "",
                f"--- ВЫХОД: OEBPS/chapter1.xhtml ({mode_name}) ---",
                chapter_p4,
            ]
        )
        print(f"  {mode_name}: tokens={run_p4.total_tokens} exception={run_p4.exception}")
    evidence("e1-4_append_text_replace.txt", "\n".join(sections))


def probe_p5(stamp: str) -> None:
    print("P5: заголовки, TOC (nav), OPF-метаданные")
    out_block = WORK / "out_block.epub"
    with zipfile.ZipFile(BOOK) as src_zip, zipfile.ZipFile(out_block) as out_zip:
        opf_in = src_zip.read("OEBPS/content.opf").decode("utf-8")
        opf_out = out_zip.read("OEBPS/content.opf").decode("utf-8")
        nav_in = src_zip.read("OEBPS/nav.xhtml").decode("utf-8")
        nav_out = out_zip.read("OEBPS/nav.xhtml").decode("utf-8")
    h1_in = [line for line in chapter_text(BOOK).splitlines() if "<h1" in line]
    h1_out = [line for line in chapter_text(out_block).splitlines() if "<h1" in line]
    evidence(
        "e1-5_toc_metadata.txt",
        "\n".join(
            [
                f"##### E1 P5: заголовки / TOC / OPF-метаданные (verbatim)\nдата: {stamp}",
                "",
                "=== ВХОД: OEBPS/content.opf ===",
                opf_in,
                "",
                "=== ВЫХОД: OEBPS/content.opf ===",
                opf_out,
                "",
                "=== ВХОД: OEBPS/nav.xhtml ===",
                nav_in,
                "",
                "=== ВЫХОД: OEBPS/nav.xhtml ===",
                nav_out,
                "",
                f"заголовки h1 на входе:  {h1_in}",
                f"заголовки h1 на выходе: {h1_out}",
            ]
        ),
    )


def probe_p6(stamp: str) -> None:
    print("P6: кэш (холодный, тёплый, смена языка, смена модели)")
    server_a = MockLLMServer()
    server_a.start()
    calls_a = 0
    try:
        run_a = run_translate("cacheA", submit=SubmitKind.APPEND_BLOCK, server=server_a)
        calls_a = len(server_a.requests_log())
    finally:
        server_a.stop()
    cache_dir = WORK / "cache_cacheA"
    cache_list_a = sorted(p.name for p in cache_dir.glob("*.txt")) if cache_dir.exists() else []

    server_b = MockLLMServer()
    server_b.start()
    try:
        run_b = run_translate(
            "cacheB", submit=SubmitKind.APPEND_BLOCK, server=server_b, cache_name="cacheA"
        )
        calls_b = len(server_b.requests_log())
    finally:
        server_b.stop()

    server_c = MockLLMServer()
    server_c.start()
    try:
        run_c = run_translate(
            "cacheC",
            submit=SubmitKind.APPEND_BLOCK,
            server=server_c,
            cache_name="cacheA",
            target_language="de",
        )
    finally:
        server_c.stop()
    cache_list_c = sorted(p.name for p in cache_dir.glob("*.txt"))

    server_d = MockLLMServer()
    server_d.start()
    try:
        run_d = run_translate(
            "cacheD",
            submit=SubmitKind.APPEND_BLOCK,
            server=server_d,
            cache_name="cacheA",
            model="other-model",
        )
        calls_d = len(server_d.requests_log())
    finally:
        server_d.stop()

    delta_b = run_b.total_tokens / run_a.total_tokens * 100 if run_a.total_tokens else 0.0
    delta_c = run_c.total_tokens / run_a.total_tokens * 100 if run_a.total_tokens else 0.0
    delta_d = run_d.total_tokens / run_a.total_tokens * 100 if run_a.total_tokens else 0.0
    evidence(
        "e1-6_cache.txt",
        "\n".join(
            [
                f"##### E1 P6: поведение кэша (verbatim)\nдата: {stamp}",
                "конфигурация: общий cache_path во всех прогонах; target_language=ru, "
                "кроме C (de); D — та же папка и язык, но model=other-model.",
                "",
                f"A (холодный кэш, вызовов к mock: {calls_a}):",
                run_a.summary(),
                f"B (тёплый кэш, фактических LLM-вызовов: {calls_b}):",
                run_b.summary(),
                "C (смена target_language на de, та же папка):",
                run_c.summary(),
                f"D (смена model, та же папка, тот же язык; LLM-вызовов: {calls_d}):",
                run_d.summary(),
                "",
                f"прирост токенов B/A: {delta_b:.2f}%   (порог E1-2: <= 2%)",
                f"прирост токенов C/A: {delta_c:.2f}%   (порог E1-3: >= 90%)",
                f"прирост токенов D/A: {delta_d:.2f}%   (смена модели: инвалидация или нет)",
                "",
                "структура кэш-каталога (файлы = ключи):",
                f"после A: файлов={len(cache_list_a)}",
                *[f"  {name}" for name in cache_list_a[:8]],
                f"после C: файлов={len(cache_list_c)}",
                *[f"  {name}" for name in cache_list_c[:8]],
            ]
        ),
    )
    print(
        f"  A={run_a.total_tokens} B={run_b.total_tokens} "
        f"C={run_c.total_tokens} D={run_d.total_tokens}"
    )


def probe_p7(stamp: str) -> None:
    print("P7: недоступный эндпоинт")
    run_u = run_translate(
        "unreachable",
        submit=SubmitKind.APPEND_BLOCK,
        llm_url="http://127.0.0.1:9/v1",
        llm_retry_times=0,
    )
    out_u = WORK / "out_unreachable.epub"
    out_state = "НЕ СУЩЕСТВУЕТ"
    if out_u.exists():
        try:
            with zipfile.ZipFile(out_u) as archive:
                members = archive.namelist()
            out_state = f"существует, валидный ZIP, записей={len(members)}: {members}"
        except Exception as exc:
            out_state = f"существует, НО: {type(exc).__name__}: {exc}"
    evidence(
        "e1-7_unreachable.txt",
        "\n".join(
            [
                f"##### E1 P7: недоступный эндпоинт (verbatim)\nдата: {stamp}",
                "конфигурация: llm.url=http://127.0.0.1:9/v1 (порт закрыт), retry_times=0, "
                "retry_interval_seconds=0.2, submit=APPEND_BLOCK, target_language=ru",
                "",
                run_u.summary(),
                "",
                f"судьба target_path: {out_state}",
            ]
        ),
    )
    print(f"  exception={run_u.exception} target: {out_state}")


def probe_p8(stamp: str) -> None:
    print("P8: on_fill_failed (fill-ответ = мусор)")
    server_f = MockLLMServer()
    server_f.start()
    fill_calls = 0
    try:
        server_f.set_scenario("fill_garbage")
        run_f = run_translate(
            "fillfail", submit=SubmitKind.APPEND_BLOCK, server=server_f, max_retries=2
        )
        fill_calls = sum(1 for item in server_f.requests_log() if item["kind"] == "fill")
    finally:
        server_f.stop()
    out_f = WORK / "out_fillfail.epub"
    ch_in = chapter_text(BOOK)
    ch_f = chapter_text(out_f) if out_f.exists() else "(файл не создан)"
    unchanged = ch_in == ch_f
    evidence(
        "e1-8_fill_failed.txt",
        "\n".join(
            [
                f"##### E1 P8: on_fill_failed на заведомо неверном ответе LLM (verbatim)\n"
                f"дата: {stamp}",
                "конфигурация: сценарий mock=fill_garbage (fill отвечает 'Sorry, I cannot "
                "produce...'),",
                "submit=APPEND_BLOCK, max_retries=2 (fill-повторы), target_language=ru",
                "",
                run_f.summary(),
                f"fill-запросов к mock: {fill_calls}",
                "",
                f"глава 1 выхода побайтово равна входу: {unchanged}",
                "",
                "=== ВЫХОД: OEBPS/chapter1.xhtml ===",
                ch_f,
            ]
        ),
    )
    print(f"  fill_events={len(run_f.fill_events)} fill_calls={fill_calls} unchanged={unchanged}")


def probe_p9(stamp: str) -> None:
    print("P9: concurrency 1 vs 4")
    server_1 = MockLLMServer()
    server_1.start()
    try:
        run_c1 = run_translate(
            "conc1", submit=SubmitKind.APPEND_BLOCK, server=server_1, concurrency=1
        )
    finally:
        server_1.stop()
    server_4 = MockLLMServer()
    server_4.start()
    try:
        run_c4 = run_translate(
            "conc4", submit=SubmitKind.APPEND_BLOCK, server=server_4, concurrency=4
        )
    finally:
        server_4.stop()
    out_c1 = WORK / "out_conc1.epub"
    out_c4 = WORK / "out_conc4.epub"
    ch1_c1 = chapter_text(out_c1)
    ch1_c4 = chapter_text(out_c4)
    ch2_c1 = chapter_text(out_c1, "OEBPS/chapter2.xhtml")
    ch2_c4 = chapter_text(out_c4, "OEBPS/chapter2.xhtml")
    identical = (ch1_c1 == ch1_c4) and (ch2_c1 == ch2_c4)
    evidence(
        "e1-9_concurrency.txt",
        "\n".join(
            [
                f"##### E1 P9: порядок блоков при concurrency (verbatim)\nдата: {stamp}",
                "конфигурация: одинаковые прогоны APPEND_BLOCK, ru; разная только concurrency "
                "(1 против 4), отдельные кэш-папки.",
                "",
                "concurrency=1:",
                run_c1.summary(),
                "concurrency=4:",
                run_c4.summary(),
                "",
                f"chapter1.xhtml: побайтово одинаковы = {ch1_c1 == ch1_c4}",
                f"chapter2.xhtml: побайтово одинаковы = {ch2_c1 == ch2_c4}",
                f"ИТОГО (E1-6, порог 100%): {'ДА' if identical else 'НЕТ'}",
                "",
                "=== chapter1.xhtml concurrency=1 ===",
                ch1_c1,
                "",
                "=== chapter1.xhtml concurrency=4 ===",
                ch1_c4,
            ]
        ),
    )
    print(f"  identical={identical}")


def main() -> int:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    probe_p1(stamp)
    probe_p2(stamp)
    probe_p3(stamp)
    probe_p4(stamp)
    probe_p5(stamp)
    probe_p6(stamp)
    probe_p7(stamp)
    probe_p8(stamp)
    probe_p9(stamp)

    print("ENGINE_PROBE завершён; захваты в docs/CONTROL/evidence/E1/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
