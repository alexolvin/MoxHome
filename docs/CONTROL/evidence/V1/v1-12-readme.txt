V1.12 — README: 7 шагов «быстрого старта», исполненные буквально
(2026-09-14 16:56:49)

Окружение: ЧИСТОЙ ИНСТАЛЛЯЦИИ — git-клон репозитория в /tmp/v1-clean/et
(stage/V1 @ ad16dd9), ничего из основной рабочей копии не переносилось,
кроме артефактов, которых README указывает скачать/установить (spaCy-модели,
EPUBCheck). Порт 8399 (README-дефолт 8321 занят основной инсталляцией —
единственное отклонение, зафиксировано в v1-10). LLM — детерминированный
mock (tests/probe/mock_llm_server.py) на 127.0.0.1:33365, delay 2.0 c —
ограничение BLOCKER-1: реального LLM-эндпоинта в приёмочном окружении нет.
python=3.12.14 (README: 3.11–3.13) spacy=3.8.16

Все команды выполнены из корня чистого клона (/tmp/v1-clean/et), если не
указано иное. Выводы verbatim.

=== Шаг 1. Установка ===
Команды README (исполнены):
  git clone <адрес репозитория> EpubTranslator   # у нас: git clone /mdisk/EpubTranslator
  cd EpubTranslator
  uv venv
  uv sync
  uv pip install --python .venv/bin/python \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl \
    ... (de/fr/it/nl core news sm 3.8.0 — см. README)
  # EPUBCheck 5.3.0: дистрибутив W3C распакован в data/tools/

Состояние (верификация verbatim):
  $ git rev-parse HEAD
  ad16dd95f95ea9355acf3cb452c1d44a33eadbf8
  $ git log --oneline -1
  ad16dd9 V1: программа приёмки (work in progress; финальный amend — при завершении стадии)
  $ .venv/bin/python --version
  Python 3.12.14
  $ .venv/bin/python -c "from spacy.util import get_installed_models; print(sorted(get_installed_models()))"
  ['de_core_news_sm', 'en_core_web_sm', 'fr_core_news_sm', 'it_core_news_sm', 'nl_core_news_sm']
  $ ls data/tools/epubcheck-5.3.0/epubcheck.jar
  data/tools/epubcheck-5.3.0/epubcheck.jar
  $ ls data/tools/epubcheck-5.3.0/lib/ | head -3
  Saxon-HE-11.4.jar
  accessors-smart-2.4.9.jar
  asm-9.3.jar
  $ java -version
  openjdk version "17.0.20" 2026-07-21 LTS
  # config.yaml: epubcheck.jar_path: data/tools/epubcheck-5.3.0/epubcheck.jar (см. Шаг 2)

=== Шаг 2. Настройка LLM ===
Команды README (исполнены):
  cp config/config.example.yaml config/config.yaml
  cp .env.example .env
Изменения по README: llm.profiles.*.base_url / model, storage.root; .env —
LLM_API_KEY. (Адрес — локальный mock LLM, BLOCKER-1.)

Содержимое config.yaml (выжимка verbatim, секреты не содержатся):
  server:
    port: 8399
  storage:
    root: /tmp/v1-clean/et
  llm:
    profiles:
      translation:
        base_url: http://127.0.0.1:33365/v1
        model: probe-model
      fill:
        base_url: http://127.0.0.1:33365/v1
        model: probe-model
      analysis:
        base_url: http://127.0.0.1:33365/v1
        model: probe-model
  epubcheck:
    jar_path: data/tools/epubcheck-5.3.0/epubcheck.jar

.env (имена переменных, значения заменены):
  LLM_API_KEY=<заменено>
  SERVER_BIND_ADDRESS=<заменено>
  # LLM_ANALYSIS_API_KEY, ALLOW_WILDCARD_BIND, SERVER_AUTH_SECRET — закомментированы

=== Шаг 3. Запуск сервера ===
Команды README (исполнены, два процесса):
  .venv/bin/python -m app
  .venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop

Процессы (верификация verbatim; отобраны по cwd=/tmp/v1-clean/et —
остальные процессы машины не относятся к инсталляции):
  pid=2228124 cwd=/tmp/v1-clean/et cmd=.venv/bin/python -m app
  pid=2270920 cwd=/tmp/v1-clean/et cmd=.venv/bin/python -m app.workers.run --config config/config.yaml --env .env --loop

=== Шаг 4. Открытие UI ===
  $ curl -s -o /dev/null -w "HTTP %{http_code} -> %{redirect_url}\n" http://127.0.0.1:8399/
  HTTP 302 -> http://127.0.0.1:8399/jobs
  $ curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8399/jobs
  HTTP 200
  $ curl -s http://127.0.0.1:8399/jobs | grep -o "<title>[^<]*</title>"
  <title>Jobs · EpubTranslator</title>

=== Шаг 5. Перевод тестовой книги ===
Тестовая книга — из репозитория: tests/fixtures/epub/probe.en.epub.

Через UI (HTTP API, те же вызовы, что браузер):
  $ curl -s -F "file=@tests/fixtures/epub/probe.en.epub" http://127.0.0.1:8399/api/books
  {"name":"u1-0407fc76-probe.en.epub","title":"Probe Book","dc_language":"en","detected":"en","confidence":1.0,"probabilities":{"en":1.0},"decision":"accepted","accepted_language":"en","reasons":[],"stats":{"chapter_count":2,"segment_count":15,"word_count":77}}
  # исходный язык определён уверенно (confidence 1.0) — NEEDS_CONFIRMATION нет
  $ curl -s -X POST http://127.0.0.1:8399/api/jobs -H "Content-Type: application/json" \
      -d '{"book":"u1-0407fc76-probe.en.epub","target_language":"ru","mode":"bilingual","learning_level":"Learning"}'
  {"id":"j-20260914T135551Z-fc4e5d","status":"CREATED"}
  # ожидание терминального статуса:
  статус: COMPLETED_WITH_WARNINGS (progress=1.00)
  $ .venv/bin/python -m app.cli job status j-20260914T135551Z-fc4e5d
  задание: j-20260914T135551Z-fc4e5d
  статус: COMPLETED_WITH_WARNINGS
  пара: en → ru  режим: bilingual
  learning, уровень: Learning
  прогресс: все фазы перевода завершены (сегментов 15 / 15)
  сегменты: 15 / 15
  охват выравнивания (E6): 15 / 15 (100.0%)
  токенов всего: 20692
  прошло: 15 c
  остаток (оценка): 0 c
  предупреждение: S1: 1 карточек отклонено: d: словоформа отсутствует в сегменте 59db5d48b7553ff4a9093dfd07b3b62ca841039b76a11307379fced5e6bf7e8d (S1.1)
  предупреждение: S2: MWE-кандидаты не отобраны (0) — записей нет
  предупреждение: S3: грамматические кандидаты не отобраны (0) — заметок нет
  предупреждение: S4 вопросы: 3 отклонено: ch1: дублирующийся вопрос (S4); ch2: дублирующийся вопрос (S4); ch2: дублирующийся вопрос (S4)
  результат готов: target.epub, learning.epub, cards.csv, cards.anki.tsv, cards.json
  # предупреждения S1–S4 — честные (Rule 6): на детерминированной фикстуре
  # часть кандидатов детерминированно отклоняется.

Через CLI (эквивалент из README):
  $ .venv/bin/python -m app.cli translate tests/fixtures/epub/probe.en.epub \
      --source en --target ru --mode bilingual --level Learning --wait 300
  задание: j-20260914T120316Z-16c278
  статус: CREATED
  исход: COMPLETED_WITH_WARNINGS
  (код выхода: 0)

Примечание: в БД есть ещё одно задание j-20260914T135105Z-24f090 (FAILED,
причина verbatim: «тракт T недоступен (endpoint http://127.0.0.1:33365/v1,
model probe-model): Connection error.») — mock LLM (вспомогательный
процесс приёмки, не часть приложения) завершился по собственному таймеру;
задание корректно упало в FAILED с причиной as-is (fail closed, Rule 6) —
это поведение приложения, а не дефект.

=== Шаг 6. Поиск результата ===
Через UI (HTTP API):
  $ curl -s -D - -o /tmp/v1-clean/res_main.epub \
      "http://127.0.0.1:8399/api/jobs/j-20260914T120316Z-16c278/result?artifact=main"
  HTTP/1.1 200 OK
  content-type: application/octet-stream
  content-disposition: attachment; filename="learning.epub"
  $ file /tmp/v1-clean/res_main.epub
  /tmp/v1-clean/res_main.epub: EPUB document EPUB document
  # Learning-задание: артефакт main = learning.epub (README-соглашение)
  $ curl -s -o /tmp/v1-clean/cards.csv -w "HTTP %{http_code}\n" \
      "http://127.0.0.1:8399/api/jobs/j-20260914T120316Z-16c278/result?artifact=cards.csv"
  HTTP 200
  # cards.csv (первые строки):
  lemma,word_form,pos,translation,meaning,example,frequency_band,cefr_level,chapter_id,segment_id,cefr_source,past_tense,past_participle
  visit,visited,verb,ylvlw,ylvlw,She visited the old mill on a cold morning.,A1,A1,ch1,b5162d42fe60abe6396010926f2f257ab8ce35309ba7f9668a77aea467ccb678,derived,ylvlwd,ylvlwed

Через CLI:
  $ .venv/bin/python -m app.cli job list
  j-20260914T120316Z-16c278  COMPLETED_WITH_WARNINGS   en→ru  bilingual, Learning  все фазы перевода завершены (сегментов 15 / 15)
  j-20260914T115701Z-2a818e  COMPLETED_WITH_WARNINGS   en→ru  bilingual, Learning  все фазы перевода завершены (сегментов 15 / 15)
  j-20260914T115609Z-e4c90c  COMPLETED_WITH_WARNINGS   en→ru  bilingual, Learning  все фазы перевода завершены (сегментов 15 / 15)
  j-20260914T115258Z-fb6dd9  COMPLETED_WITH_WARNINGS   en→ru  bilingual, Learning  все фазы перевода завершены (сегментов 15 / 15)
  j-20260914T114905Z-d52a02  COMPLETED_WITH_WARNINGS   en→ru  bilingual, Learning  все фазы перевода завершены (сегментов 15 / 15)
  $ .venv/bin/python -m app.cli job status j-20260914T114905Z-d52a02 | grep -E "^(задание|статус|сегменты|результат)"
  задание: j-20260914T114905Z-d52a02
  статус: COMPLETED_WITH_WARNINGS
  сегменты: 15 / 15
  результат готов: target.epub, learning.epub, cards.csv, cards.anki.tsv, cards.json
  $ .venv/bin/python -m app.cli validate jobs/j-20260914T120316Z-16c278/target.epub
  EPUBCheck v5.3.0: 0 FATAL, 0 ERROR, WARNING 0
  $ .venv/bin/python -m app.cli export cards j-20260914T120316Z-16c278 --format csv | head -2
  lemma,word_form,pos,translation,meaning,example,frequency_band,cefr_level,chapter_id,segment_id,cefr_source,past_tense,past_participle
  visit,visited,verb,ylvlw,ylvlw,She visited the old mill on a cold morning.,A1,A1,ch1,b5162d42fe60abe6396010926f2f257ab8ce35309ba7f9668a77aea467ccb678,derived,ylvlwd,ylvlwed

Файлы результата на диске (jobs/j-20260914T120316Z-16c278/):
  alignment_report.json, answers.json, cards.anki.tsv, cards.csv, cards.json,
  engine_cache, engine_log, fill_failures.json, grammar_notes.json,
  l3_fix_report.json, learning.epub, mwe_records.json, plan.json,
  quality.json, questions.json, s5_report.json, target.epub,
  translation_result.json, validation.json, vocabulary_cards.json (+
  каталоги analysis/, raw/, unpack/, out_unpack/)

=== Шаг 7. Возобновление прерванного задания ===
Сценарий: задание j-20260914T115701Z-2a818e создаётся (UI/API), воркер
pid 2228127 убит SIGKILL во время TRANSLATING; затем — ПЕРЕЗАПУСК ВОРКЕРА
(команда README шага 7). Механизм (README): арена истекает, воркер при
старте помечает задание INTERRUPTED и возобновляет с последнего
подтверждённого результата стадии.

Переходы задания из БД (jobs/jobs.db, таблица transitions; времена UTC):
  2026-09-14 08:57:01Z  CREATED -> VALIDATING  (worker): начало этапа
  2026-09-14 08:57:01Z  VALIDATING -> PLANNING  (worker): начало этапа
  2026-09-14 08:57:01Z  PLANNING -> TRANSLATING  (worker): начало этапа
  2026-09-14 09:02:26Z  TRANSLATING -> INTERRUPTED  (recovery): арена истекла (worker_pid=2228127); восстановление при старте воркера
  2026-09-14 09:02:26Z  INTERRUPTED -> TRANSLATING  (worker): resume: перезапуск воркера, арена истекла
  2026-09-14 09:02:31Z  TRANSLATING -> ANALYZING  (worker): начало этапа
  2026-09-14 09:02:31Z  ANALYZING -> GENERATING  (worker): начало этапа
  2026-09-14 09:02:31Z  GENERATING -> VALIDATING_OUTPUT  (worker): начало этапа
  2026-09-14 09:02:31Z  VALIDATING_OUTPUT -> GLOSSARY_FIX  (worker): начало этапа
  2026-09-14 09:02:31Z  GLOSSARY_FIX -> LEARNING  (worker): начало этапа
  2026-09-14 09:02:42Z  LEARNING -> COMPLETED_WITH_WARNINGS  (worker): результат валидирован (вкл. L3, S)
  финальный статус: COMPLETED_WITH_WARNINGS | error_message: None

Задание не потеряно: завершённые до kill стадии не переделаны заново
(результаты стадий в БД, кэш групп — E3-4; на mock с delay 2.0 c повтор
составил бы заметную долю — см. V1.9, где доля повторного перевода
измерена = 0.0000% ≤ 5%).

--- ИТОГ ---
Шагов: 7/7. Каждый шаг README исполнён буквально на чистой инсталляции;
все выводы выше — verbatim. Отклонения от README (зафиксированы):
(1) порт 8399 вместо 8321 (занят основной инсталляцией);
(2) LLM-адрес = детерминированный mock (BLOCKER-1);
(3) EPUBCheck — локальная копия дистрибутива W3C 5.3.0 (data/tools —
gitignored, в репозиторий не входит).

ВЕРДИКТ: V1-12 (README 7 шагов) — PASS
