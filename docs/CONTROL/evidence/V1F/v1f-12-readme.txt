V1F.12 — README 7/7, дословно на чистой установке, код включает U3
(2026-09-18, /tmp/v1f-clean/et; установка — та же, что V1F.10;
LLM — реальный 127.0.0.1:8004, qwen3.8-27b, решение владельца)

Критерий (ТЗ 04 §V1.12): 7/7 шагов по тексту без догадок; неработающая
инструкция исправляется в документации.

Два дефекта README найдены ДОСЛОВНЫМ исполнением и исправлены в
документации (код приложения не тронут):
1. D107 — шаги 2/5: мутирующие операции UI без SERVER_AUTH_SECRET
   отвечают 403 «мутирующие операции требуют SERVER_AUTH_SECRET»
   (fail closed, D91) — README описывал их как доступные из коробки.
   Исправлено в 3 местах (шаг 2 .env, шаг 5 «Через UI», раздел
   «Управление заданиями и промптами в интерфейсе»).
2. D108 — шаг 2: thinking-моделям (Qwen3 на vLLM) нужен
   extra_body: {chat_template_kwargs: {enable_thinking: false}} во всех
   трёх профилях — README на шаблон не направлял (config.example.yaml
   имеет extra_body: {}). Измерено: без него 0/15 блоков (задача A),
   с ним 15/15 (задача B).

=== Шаг 1. Установка === (verbatim — v1f-10, /tmp/v1f_clean_install.log)
  git clone + uv venv + uv sync + 5 spaCy-моделей + EPUBCheck —
  все RC=0. EPUBCheck — локальная копия W3C 5.3.0 (зафиксированное
  отклонение, как V1.10/V2.1).

=== Шаг 2. Настройка LLM ===
  cp config/config.example.yaml config/config.yaml — команда verbatim;
  cp .env.example .env — команда verbatim.
  config.yaml: base_url/model × 3 профиля (единственный источник, E4.1),
  storage.root = корень клона (D62: relative prompts_root от storage.root).
  .env: LЛM_API_KEY, SERVER_BIND_ADDRESS=127.0.0.1, SERVER_AUTH_SECRET
  (дефект 1: без секрета UI read-only для мутирующих операций).
  extra_body enable_thinking: false × 3 профиля (дефект 2).

=== Шаг 3. Запуск сервера ===
  Два процесса, команды README verbatim (зафиксированное отклонение:
  env -u <app-ключи> — D71, окружение сессии маскирует .env):
    .venv/bin/python -m app
    .venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop
  health (GET /api/health, Basic) verbatim: app/database/storage ok,
  worker "ok (pid 313704)", llm translation/fill/analysis ok,
  epubcheck ok, nlp de/en/fr/it/nl ok, ru "n/a (целевой язык)".

=== Шаг 4. Открытие UI ===
  http://127.0.0.1:8321/ → 302 → /jobs 200 (Basic — секрет задан).
  Verbatim: <title>EpubTranslator — Задания</title>; u3-strings = 1
  (вёрстка U3); статус воркера на странице: «ok (pid 4165726)» —
  пока воркер жив (D87, heartbeat).

=== Шаг 5. Перевод тестовой книги ===
  ПЕРВАЯ попытка (README до фикса, без секрета) — 403 verbatim (дефект 1):
    {"error":"forbidden","message":"мутирующие операции требуют SERVER_AUTH_SECRET"}
    HTTP 403 — POST /api/books и POST /api/jobs.
  После фикса README + секрета:
  UI: POST /api/books 200 → name "u1-ab1f5a92-probe.en.epub"
      (приложение добавляет префикс с хэшем, U1 — штатно);
      POST /api/jobs 200 (JSON csrf из meta страницы) → задача B.
  Задача A (j-20260918T152023Z-fe9aac; до фикса extra_body) —
  CWW, 0/15, verbatim warnings (дефект 2): «E5-2: переведено блоков
  0/15 (0.0% < 99.5%)…», «E5 п.4: fill не удался (over_maximum_retries)
  в 1 группе(ах)», «E6: выравнивание ниже порога: 0/15»; 41197 токенов.
  Задача B (j-20260918T160359Z-98045c; после фикса) — CWW, 15/15,
  coverage 1.0, 23651 токен, 194 s. Warnings verbatim (честное поведение
  РЕАЛЬНОЙ модели, fail closed): S1 26 карточек отклонено
  (доп. поля вне card_schema.json), S2/S3 0 кандидатов, S4 21 вопрос
  отклонено (S4.4). Пороги не ослаблялись.
  CLI (эквивалент, команда README verbatim):
    .venv/bin/python -m app.cli translate tests/fixtures/epub/probe.en.epub \
        --source en --target ru --mode bilingual --level Learning --wait
    → «задание: j-20260918T162232Z-1899b5 / статус: CREATED /
       исход: COMPLETED_WITH_WARNINGS», RC=0, 113 s;
    факты: 15/15, coverage 1.0, 19943 токена, все файлы результата.

=== Шаг 6. Поиск результата ===
  UI-ссылка: GET /api/jobs/j-20260918T160359Z-98045c/result →
  HTTP 200 SIZE 3997; zip-магия: 50 4b 03 04; размер == learning.epub
  в каталоге задания (3997) — основной артефакт для Learning (README).
  CLI verbatim (все RC=0):
    job list        → обе задачи (B/A) со статусами и прогрессом
    job status $JID → статус CWW; сегменты 15/15; охват E6 15/15 (100.0%);
                      токенов 23651; прошло 194 c; warnings verbatim;
                      «результат готов: target.epub, learning.epub,
                      cards.csv, cards.anki.tsv, cards.json»
    export cards $JID --format csv → header + 1 карточка (morning/A1)
    validate jobs/$JID/target.epub → «EPUBCheck v5.3.0: 0 FATAL,
                      0 ERROR, WARNING 0»

=== Шаг 7. Возобновление прерванного задания ===
  Задача C (j-20260918T161332Z-5d6da2, создана через UI с Basic+CSRF).
  kill -9 воркера (pid 168975) в TRANSLATING: 16:13:52Z.
  Перезапуск воркера командой README (перезапуск — механизм D78).
  Переходы БД verbatim (таблица transitions):
    CREATED→VALIDATING→PLANNING→TRANSLATING  2026-09-18 16:13:32Z
    TRANSLATING→INTERRUPTED                  16:14:17Z
        «арена истекла (worker_pid=168975); восстановление при старте
         воркера»  (actor=recovery; PID убитого воркера записан)
    INTERRUPTED→TRANSLATING                  16:14:17Z
        «resume: перезапуск воркера, арена истекла» (actor=worker)
    TRANSLATING→ANALYZING→GENERATING→VALIDATING_OUTPUT→GLOSSARY_FIX
    →LEARNING                                16:14:33Z
    LEARNING→COMPLETED_WITH_WARNINGS         16:17:29Z
        «результат валидирован (вкл. L3, S)»
  Итог: 15/15, coverage 1.0. Задание не потеряно: завершённые стадии
  сохранены, повторного перевода нет (E3-4; resume 19 мс после
  INTERRUPTED, переводные группы взяты из кэша).
  PAUSED-ветка («Продолжить» / job resume) не исполнялась: в этом
  прогоне пользовательской паузы не было — механизм прерывания
  (единственный по TЗ, E3-1) верифицирован полностью.

--- ИТОГ ---
7/7 шагов README дословно на чистой установке (один и тот же клон,
что V1F.10). 2 дефекта инструкции найдены дословным исполнением и
исправлены в документации (D107/D108), код приложения не менялся.
Зафиксированные отклонения: EPUBCheck — локальная копия W3C; LLM —
реальный (решение владельца); env -u <app-ключи> (D71).

ВЕРДИКТ: V1F.12 (README 7/7) — PASS
