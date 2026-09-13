# HANDOFF — EpubTranslator

Самодостаточный документ для продолжения работы в свежей сессии.
Обновляется **перед** началом каждого этапа и **сразу после** каждого измерения.

---

## Текущее состояние (2026-09-13)

- **Завершено:** E0 (фундамент) — CONTROL PASS 5/5; **E1 (ENGINE_PROBE)** — CONTROL PASS 6/6;
  **E2 (Ридер EPUB и сегментатор)** — CONTROL PASS 6/6; **E3 (Job, воркер, возобновление)** —
  CONTROL PASS 6/6; **E4 (Слой LLM)** — CONTROL PASS 6/6; **E5 (Тракт T: перевод)** —
  CONTROL PASS 8/8; **E6 (Выравнивание оригинал ↔ перевод)** — CONTROL PASS 7/7
  (+T-E6-8); **L0 (Реестр языков, матрица пар, определение языка)** — CONTROL PASS 8/8
  (L0-1…L0-6 + T-L0-7/8); **L1 (Языковые пакеты: NLP, таксономия, карточки, CEFR,
  частотность)** — CONTROL PASS 9/9 (L1-1…L1-9 + T-L1-10/11/12);
  **L2 (Глоссарий и терминология)** — CONTROL PASS 8/8 (L2-1…L2-8 + T-L2-1…11);
  **L3 (Правка перевода по глоссарию)** — CONTROL PASS 10/10 (L3-1…L3-10 +
  T-L3-1…15);
  **S1 (Отбор лексики, ТЗ_03)** — CONTROL PASS 7/7 (S1-1…S1-7 + T-S1-1…10);
  **S2 (Устойчивые выражения и MWE, ТЗ_03)** — CONTROL PASS 6/6 (S2-1…S2-6 +
  T-S2-1…7), 202/202 теста (на mock, BLOCKER-1 — реальные прогоны не
  заявляются);
  **S3 (Грамматические заметки, ТЗ_03)** — CONTROL PASS 7/7 (S3-1…S3-7 +
  T-S3-1…10), 223/223 теста (на mock, BLOCKER-1 — реальные прогоны не
  заявляются).
  Отчёты: `docs/ENGINE_PROBE.md`, `docs/CONTROL/E2.md`, `docs/CONTROL/E3.md`,
  `docs/CONTROL/E4.md`, `docs/CONTROL/E5.md`, `docs/CONTROL/E6.md`, `docs/CONTROL/L0.md`,
  `docs/CONTROL/L1.md`, `docs/CONTROL/L2.md`, `docs/CONTROL/L3.md`,
  `docs/CONTROL/S1.md`, `docs/CONTROL/S2.md`, `docs/CONTROL/S3.md`; evidence:
  `docs/CONTROL/evidence/E1…E6/`, `docs/CONTROL/evidence/L0/`,
  `docs/CONTROL/evidence/L1/`, `docs/CONTROL/evidence/L2/`,
  `docs/CONTROL/evidence/L3/`, `docs/CONTROL/evidence/S1/`,
  `docs/CONTROL/evidence/S2/`, `docs/CONTROL/evidence/S3/`.
- **Далее:** **S4 — summary, вопросы, упражнения (ТЗ_03 §S4)**, затем S5
  (учебный EPUB-аугмент + экспорты + kill ×10) и TZ_04
  (U1, U2, V1). S4 (ТЗ_03 §S4): summary только по фактическому тексту главы
  (по названию — запрещено; пустая/нечитаемая глава — не создаётся,
  пропуск зафиксирован); вопросы на понимание трёх уровней (Easy — 5
  фактических / Standard — 5–10 на содержание / Advanced — вывод, намерение
  автора, аргументация); упражнения: множественный выбор, верно/неверно,
  заполнение пропуска, перевод, лексика, грамматика, свободный ответ
  (состав типов — конфигурация); ответы — отдельная сущность (не в теле
  главы); детерминированная валидация: пропуск — удалённое слово в
  исходном предложении; выбор — ровно один правильный, без дублей,
  правильный не выделяется длиной (распределение длин); верно/неверно —
  сопоставимо с текстом главы по сущностям; вопросы по отсутствующим
  сущностям — отклоняются; лексические/грамматические упражнения — из
  выпущенных S1-карточек и S3-заметок (не порождают новый материал). CONTROL
  S4-1…S4-7 (S4-1: пустая глава — 100% без summary, пропуск зафиксирован;
  S4-2: пропусков вне предложения — 0; S4-3: выбор с числом правильных ≠ 1 —
  0; S4-4: вопросы по отсутствующим сущностям — 0; S4-5: ответы в теле
  главы — 0 вхождений; S4-6: лексические упражнения из выпущенных карточек —
  100%; S4-7: 5 языков), T-S4-8 — глава-изображение без текста, T-S4-9 —
  распределение длин вариантов (100 вопросов). Гейт уровней (таблица
  ТЗ_03): вопросы — `Learning`, упражнения — `Intensive`; подстадия/подстадии
  S4 в `run_learning` (гейт `SUBSTAGE_LEVELS` расширить),
  ключ идемпотентности `sha256(input_hash | glossary_hash | level | cefr)`,
  LLM-задача по главам батчами, `quality.json`-секция, версионные поля.
  Общие требования TZ_03 (неизменны для S4–S5):
  только по выровненному подмножеству E6 и тексту ПОСЛЕ L3; привязка к
  `chapter_id`/`segment_id`/`source_text_hash` + версии (промпт, профиль,
  модель, пара, уровень, timestamp, хэш глоссария); уровни
  Minimal/Balanced/Learning/Intensive; CEFR — отбор, не фильтр-абсолют;
  генераторы поглавные (никогда не вся книга в запрос); ответы хранятся
  отдельно от заданий. Протокол этапов как в E/L-стадиях (ветка stage/<S>,
  явные пути в коммите, merge --no-ff, гейты, CONTROL + evidence, DECISIONS).

## Сделано в S3

- **`app/learning/grammar.py` (новое):** `load_grammar_golden`
  (`grammar_golden.yaml` пакета: text + expected ids; отсутствует/пуст —
  пустой набор, как mwe_golden S2; `unit` — ошибка), `reference_explanation`
  (детерминированная ссылка S3.4, hex-id segment в текст не встраивается —
  langdetect сдвигает ru-фразу на bg), `check_grammar_notes` — 5-пунктная
  детерминированная валидация (id ∈ таксономии языка S3-1; поверхность
  среди детерминированных кандидатов — S1.5/S2.6; surface в сегменте
  `contains_form` S3-2; пояснение непустое и ≤ `grammar_note_max_chars` —
  превышение отклоняется, а не усекается S3.1/T-S3-10; язык = целевой
  (langdetect, seed из конфига) S3-5).
- **`app/languages/phenomena.py` (расширение, D66):** поле `stages`
  таксономии (обязательно ВМЕСТЕ с `patterns`; `for_stage(phenomena,
  "s3")` — скоп подстадии, движок агностичен к стадиям, Rule 8); новый тип
  шаблона `token` (`TokenCriterion`: И полей `pos`/`text`/`mood`/`tense`/
  `aspect`/`position` — морфология spaCy, TЗ_03 S3.5); wrap-маркеры
  расширены mood/tense/aspect; значения морфологии — данные (Rule 1).
- **`app/learning/mwe.py` (расширение):** движок отбора стал общим S2/S3
  (Rule 8): `_morph_value`/`_morph_ok` (универсальные фичи spaCy),
  `_criterion_ok` (шаблон `token`), wrap-маркеры с морфологией; `MweCandidate`
  переиспользуется S3 (unit не ожидается).
- **`app/workers/pipeline.py`:** подстадия `s3_grammar` в `run_learning`
  (`SUBSTAGE_LEVELS["s3"] = {Learning, Intensive}`): `select_mwe_candidates`
  со скопом `for_stage(…, "s3")` → `_s3_split_candidates` (лимит ≤K на главу,
  топ-K по (−вхождения, id, поверхность), K = `limits.grammar_max_notes_per_chapter`)
  → `_s3_explain_once` (первый кандидат явления — развёрнуто, остальные —
  ссылки) → LLM `grammar_note` (батчи по главам `grammar_llm_batch=25`) →
  `check_grammar_notes` → дедупликация по surface → `_s3_build_notes`
  (развёрнутая отклонена — ссылающиеся отклоняются) →
  `grammar_notes.json` + секция `s3` в `quality.json` (per_chapter,
  detailed/reference, not_selected, rejected с причинами) +
  `record_stage_result("s3_grammar", …)`.
- **`app/llm/tasks.py`:** задача `grammar_note` (`pair_required=false`,
  JSON-схема surface/phenomenon_id/explanation; лимит длины — payload
  `max_explanation_chars`).
- **Данные:** `config/languages/*/grammar_golden.yaml` (en 20, de 21,
  nl 31, fr 20, it 21 предложений; 95 пар «предложение → id», 100% покрытие
  s3-таксономий); `phenomena.yaml` — `stages: [s3]` у грамматических явлений
  + шаблоны (`token`/`wrap` с морфологией; DE-NEBENSATZ-VERBLETZT —
  dass/weil/wenn/als/ob/obwohl → VERB/AUX `end_final`); конфиг
  `limits.grammar_max_notes_per_chapter` (10), `limits.grammar_note_max_chars`
  (300), `limits.grammar_llm_batch` (25) + `config.example.yaml`.
- **Промпты:** `prompts/grammar_note/v1.yaml` (generic).
- **Mock:** сценарии `grammar_bad_id` (ZZ-S3-FAKE вне таксономии),
  `grammar_bad_quote` (surface 'Qwzxw' вне кандидатов).
- **Измерено (evidence S3, `docs/CONTROL/evidence/S3/`, 7 файлов):** 0
  принятых с id вне таксономии (инъекция отклонена, verbatim); 0 цитат вне
  сегмента (инъекция + unit-проверка); 16 кандидатов → 10 выпущенных (ch1: 10
  ≤ K=10, 6 not_selected verbatim); 6 detailed + 4 reference (ссылки
  `model=''`), повторных развёрнутых 0; 10/10 пояснений ru conf=1.00;
  recall golden 1.000 ×5 (de 21/21, nl 31/31, en 22/22, fr 20/20, it 21/21)
  ≥ 0.75 (precision справочно 1.000/1.000/0.786/1.000/0.913); 5/5 языков до
  LEARNING (en 303/16/10, de 155/3/3, fr 43/1/1, it 35/1/1, nl 67/3/3) +
  гейт уровней (Minimal/Balanced — без s3).
- Тесты: 21 запуск в `tests/test_s3_grammar.py` (T-S3-1…T-S3-10; T-S3-8 —
  de придаточное DE-NEBENSATZ-VERBLETZT; T-S3-9 — it IT-CONGIUNTIVO + fr
  FR-SUBJONCTIF; T-S3-10 — превышение лимита отклоняется). Evidence-скрипт
  `tests/s3_evidence.py` (7 verbatim-файлов). Полный прогон 223/223 (202 +
  21), ruff 0, mypy 0 (63 файла).
- Дефекты этапа: не обнаружено. Честные пределы механизма (в CONTROL S3):
  DE-NEBENSATZ-VERBLETZT — только придаточное в конце предложения
  (`end_final`); приоритет по уровню пользователя не реализован (CEFR-уровень
  грамматических явлений в таксономии не заведён — приоритет =
  повторяемость); EN-PERFECT ⊂ modal-perfect/cond-3 — дополнительные
  кандидаты (precision справочно); it CONGIUNTIVO — по Mood=Subj, fr
  SUBJONCTIF — по словоформам (fr-теггер без морфологии); LLM — mock
  (BLOCKER-1).

## Сделано в L2

- **`app/domain/glossary.py` (новое):** `GlossaryEntry` (термин, перевод, часть
  речи, `segment_id` первого вхождения, число вхождений, примечание,
  `approved=False`, `source_forms`, `paradigm`/`paradigm_status`/`paradigm_reason`),
  `Paradigm` (inflected, forms, coincidences), `Glossary` (terms + `by_term`),
  `BookStyle` (style/region/note), статусы `PARADIGM_{VERIFIED,UNVERIFIED,NOT_APPLICABLE}`,
  `check_paradigm` (D56: число форм = числу имён, нет пустых, каждое совпадение
  ИМЁН форм объяснено, общая основа ≥1), `GlossaryError` (fail-closed, D55).
- **`app/glossary/candidates.py` (новое, D54):** `extract_candidates(book, pack,
  nlp, settings)` — 5 правил, `RULE_PRIORITY = (ner, multiword, abbreviation,
  unit, capitalized)`: NER — токены с `ent_type` (NER-токены пропускаются
  capitalized-правилом — вхождение считается один раз); многословные — n-граммы
  2..`ngram_max`, `occurrences ≥ min_occurrences`; аббревиатуры/единицы — данные
  `pack.yaml` (`units`); заглавные вне начала предложения — только если
  `pack.capitalized_candidates` (de — false); дедуп NFC+casefold, каноническая
  поверхность — самая частотная (тайбрейк — первая встреча). Детерминированно
  (L2-1: 13/13, побайтово).
- **`app/glossary/annotation.py` (новое, D55):** `annotate_candidates` — задача
  `glossary_annotation` (батчи ≤25, `pair_required=false`), JSON-массив на вход,
  отсутствующий/некорректный элемент → `GlossaryError`; `_annotation_paradigm` —
  парадигма вместе с переводом (треб. 4): `inflected=false` → `not_applicable`,
  `forms=()` (T-L2-11); `_indeclinable_paradigm` — выделенный путь.
- **`app/glossary/consistency.py` (новое, D57):** `check_consistency(glossary,
  alignment)` — presence-based по спаренным сегментам E6: термин «в сегменте» =
  любой `source_form` (NFC+casefold, граница слова); согласовано = в переводе
  есть канонический перевод или форма ПРОВЕРЕННОЙ парадигмы; `ConsistencyReport`
  (terms: варианты casefold, mismatch_segments; mismatches: term/segment_id/
  тексты; `share`; `to_dict`); L2 текст НЕ правит.
- **`app/glossary/store.py` (новое, D53):** `BookStore(books_dir)` —
  `books/<book_key>/<source_hash>/` (glossary.yaml, style.yaml, meta.yaml
  current/superseded, mismatches/); `book_key_of(path) = Path(path).name`;
  атомарная запись (врем. файл + `Path.replace`); чтение глоссария — строго по
  хэшу версии; `save_mismatches`.
- **`app/glossary/service.py` (новое):** `ensure_book_glossary(settings, repo,
  job, *, book, source_pack, target_pack)` — загрузить из store (по хэшу) ИЛИ
  построить: кандидаты → аннотация (LLM) → сохранение (глоссарий + стиль);
  `_determine_style` — задача `book_style` один раз на версию (D58); глоссарий
  отключён (`glossary.enabled=false`, D60) → None без артефактов.
- **`app/workers/pipeline.py`:** `_book_glossary_bundle` (лениво, по версии
  книги) в `run_translating` → `glossary_provider` (E5-механизм, без изменений
  E5) — `build_glossary_prompt(prefix=<стилевой профиль>)`; `run_validating_output`
  — `check_consistency` по отчёту E6 → `glossary_consistency.json` (report:
  terms + mismatches с текстами сегментов) + секция `quality.json`
  (`glossary_consistency`) + `save_mismatches` + предупреждение «L2:
  согласованность терминов ниже порога: …» при share < `consistency_min_share`
  (D57); выделенный `_glossary_consistency` (PLR0915).
- **Данные:** `config/languages/ru/pack.yaml` — `inflected: true` +
  `paradigm_forms.{noun,proper_noun}` = 12 имён форм (6 падежей × ед./мн. ч.);
  de — `capitalized_candidates: false` (явно), en/fr/it/nl — true;
  `config/languages/ru/inflection_golden.yaml` — 5 имён × 12 форм + 6
  падежных предложений (nom_sg==acc_sg, nom_pl==acc_pl — с объяснениями);
  промпты: `prompts/glossary_annotation/v1.yaml`, `prompts/book_style/v1.yaml`
  (generic, `pair_required=false`); `GlossarySection` в `app/core/config.py`
  (enabled=false по умолчанию — D60; min_occurrences=2, ngram 2..4,
  max_term_chars=80, units, style_sample_chars=5000, annotation_batch_size=25,
  consistency_min_share=0.95); `config.example.yaml` — `glossary.enabled: false`
  (локальный `config.yaml` — true, в git не попадает).
- **LLM-задачи/адаптер (расширение E4):** `glossary_annotation` и `book_style`
  в `app/llm/tasks.py` (pair_required=false); mock: ответы аннотации = эхо
  кандидатов (translation = rot3(термин); парадигма — rot3 + суффиксы
  `_MOCK_PARADIGM_SUFFIXES`, 12 различимых форм → verified; sentinel
  `badparadigm` — 12 идентичных форм без объяснений → unverified; sentinel
  `indeclinable` — inflected=false → not_applicable), `book_style` →
  `{"style": "neutral", "region": null, ...}`; НОВЫЙ сценарий
  `glossary_inconsistent` — в переводе rot3(Wexley) заменён на «Xrongy» (в
  translate-ответе И в fill-ответе — финальный текст mock-мира идёт через
  fill-путь, см. дефекты ниже).
- **NER включён (D59):** `app/languages/nlp.py` — `disable=["parser"]` (ner
  смодели есть, загрузка без exclude).
- **Измерено (evidence L2, `docs/CONTROL/evidence/L2/`, 8 файлов):** кандидаты
  13/13 идентичны; de: 0 capitalized (контрфакт — 2: 'Fluss'×4, 'Mühle'×3);
  внедрённая несогласованность 3/3 (share 0.0, «Xrongy»×3 / «Zhaohb»×0 в
  target.epub, хэши источника и target.epub на границах этапов — verbatim);
  complex en→ru 46/46 = 100.0% (13 терминов); усечение 4/13 (лимит 200,
  prompt 186), предупреждение verbatim; парадигмы 9/10 = 90.0% (Badparadigm —
  unverified с причиной verbatim, Indeclinable — not_applicable); golden-30:
  0/30 с парадигмой / 20/30 без (контроль); marlow 20× → 20/20, 1 вариант;
  5 языков → ru: 5/5.
- Тесты: 11 запусков в `tests/test_l2_glossary.py` (T-L2-1…11); evidence-скрипт
  `tests/l2_evidence.py` (8 verbatim-файлов). Полный прогон 154/154 (143 + 11),
  ruff 0, mypy 0 (54 файла).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/L2.md`): **финальный текст
  mock-мира — FILL-путь** (code() сдвигает и буквы XML-тегов → translate-ответ
  структурно невалиден, движок переключается на fill) — инъекция сценария
  добавлена и в `_fill_response`; **stale job-объект**: `run_validating` пишет
  `source_language` в БД, объект в памяти устаревает → без подтверждения
  `AppError «подтверждённый исходный язык не задан»` (фикс: `repo.require`
  после блока подтверждения — в тесте; в воркере require на каждом этапе уже
  есть, D40); **`load_settings` игнорировал `llm.prompts_root` из YAML** (поле было
  в `LLMSection`, но загрузчик не передавал его — ключ молча не действовал, Rule 1/6)
  — **исправлено 2026-09-13 (D62)**: ключ читается (относительный путь — от
  `storage.root`, абсолютный — как есть), явно заданный несуществующий корень →
  `ConfigurationError` на старте; тестовый хелпер L2 — `prompts_root` во временный
  каталог (copytree в storage убран); **NER sm-шум**: `fox`→ORG, `first`,
  `Second` в complex.epub — ложные
  сущности (детерминированность не страдает; отсев — за LLM-аннотацией);
  **NER пропускает имена в начале предложения** (sentencizer + «; ») и редкие
  выдуманные имена — в тестах имена стоят в середине предложения и частые
  (каждое вхождение гарантировано capitalized-правилом); **variant-списки
  casefold** (сравнение golden — по casefold-ожидаемым).

## Сделано в L1

- **`app/languages/nlp.py` (D46, D47):** `_NlpHolder` — один пайплайн на модель на
  процесс (класс-держатель, без `global`); `load_nlp` (загрузка `spacy.load(model,
  disable=["parser","ner"])` + добавление `sentencizer` без него `doc.sents` = E030);
  `nlp_report` — секция `nlp` отчёта задания (модель, версии через
  `importlib.metadata` — без загрузки пайплайна, D50; disabled-компоненты,
  sentence_split); `lemma_of` (один токен); `lexical_units` — отделяемые глаголы
  de/nl: голова = VERB + правило финитности (ранее в предложении другой VERB без
  CCONJ/SCONJ/PUNCT — не финитный; nl-модель размечает «vroeg» как VERB «vragen» —
  без правила ложная «opvragen»), приставка из `separable_prefixes` (данные:
  de 21, nl 18), запрет именных constituents между глаголом и приставкой,
  приставка-предлог с именным дополнением — не единица; окно
  `sentence_split` (min_chars/max_chars из pack.yaml) — D46.
- **`app/languages/phenomena.py` (L1.5/L1-6):** `load_phenomena` (`phenomena.yaml`
  каталога пакета; отсутствует/пуст — отсутствующий ресурс, резолвер перечислит;
  невалидный id/dup/пустые name/description → `ConfigurationError`),
  `PhenomenaTaxonomy` (`.validate` → (валидные, невалидные)), `validate_golden`
  (чужой id — ошибка; пустая таксономия — ошибка, а не молчание, Rule 6).
- **`app/languages/cards.py` (L1.6, D51):** `load_card_schema` (JSON draft 2020-12,
  `if/then` по части речи; невалидная схема → `ConfigurationError`), `validate_card`
  (jsonschema + отдельная проверка плейсхолдеров Rule 5: `—`/`n/a`/пустая строка —
  отклонение; JSON `null` — легитимно для nullable-полей), `issue_cards` →
  (выпущенные, отклонённые с причинами) — счётчик QC.
- **`app/languages/cefr.py` (L1.4, D45):** `CefrSource` (`lexicon:<имя>` —
  `cefr/<имя>.json` маппинг слово→уровень; файл отсутствует → derived без исключения
  (T-L1-12); невалидный JSON/уровень → `ConfigurationError`), `level_for`
  (lexicon-hit → (уровень, источник, estimated=False); miss → уровень не
  устанавливается; derived → (None, derived, True)), `label` — ЕДИНАЯ подпись
  (L1-8): derived → `Estimated CEFR level`; lexicon → `CEFR level (<источник>):
  <уровень|не установлен>`. UI (U1) читает только её.
- **`app/languages/frequency.py` (L1.3, D48, D52):** `normalize_text` (NFC/NFD/NFKC/
  NFKD + casefold + схлопывание — данные `pack.yaml: normalization`), `zipf_for`
  (wordfreq 3.1.1: `zipf_frequency(word.casefold(), lang)`; wordlist-параметр снят,
  `frequency_of_word` удалён — измерено), `band_for`/`frequency_band` — полосы
  только из данных (`BandSpec`), 0 числовых порогов в коде (L1-9, проверено тестом),
  `band_report` — справочное представление.
- **`app/languages/registry.py` (расширение):** `NormalizationSpec`,
  `SentenceSplitSpec` (max ≥ min), `BandSpec` (≥2 полосы, null-max — не более одной
  и только последней, границы строго растут; YAML-маппинг {A1: 9.23, …} —
  before-валидатор); `PackSpec` + `card_schema`, `normalization`, `sentence_split`,
  `prompt_tasks` (валидация по реестру задач, D49), `pos_select` (universal tags),
  `frequency_bands`, `separable_prefixes` (isalpha — ü в «zurück»); `GoldenEntry` +
  `phenomena`; `LemmaEntry` + `load_lemma_reference` (отсутствует/пуст — ресурс,
  не ошибка).
- **`app/languages/resolver.py` (расширение, D49):** `_l1_resource_deficits` —
  мягкие: нет таксономии/схемы карточки/эталонов лемматизации/частотных полос,
  CEFR-лексикон не найден (только при `cefr_source: lexicon:` — переход в derived
  зафиксирован в перечне, T-L1-12); golden без размеченных явлений — отдельное
  сообщение (L1/T-L1-10); `prompt_tasks` пакета источника — пространство имён
  промптов (нет пакета/список пуст — все задачи, поведение L0 без изменения).
- **`app/workers/pipeline.py`:** `_nlp_section` — секция `nlp` в `validation.json`
  (по accepted language; конфликт — None) и `plan.json` (по подтверждённому
  источнику) — `nlp_report`, без загрузки пайплайна (D50).
- **Данные (новые/перезаписанные, `config/languages/{en,de,fr,it,nl}/`):**
  `phenomena.yaml` (en 11, de 12, nl 9, fr 9, it 9 id — id вида XX-NAME,
  kind grammar/mwe/morphology, name/description — данные для S1-промптов);
  `card_schema.json` (draft 2020-12, additionalProperties false, `if/then`:
  de noun → article der/die/das + plural; de verb → auxiliary + main_forms +
  separable; nl noun → article de/het + plural + diminutive (nullable); nl verb →
  auxiliary + participle + separable; fr noun → gender; fr verb →
  conjugation_group 1-4/irregular + auxiliary avoir/etre + irregular_forms
  (nullable); it noun → gender + plural; it verb → group 1-3/irregular + auxiliary
  avere/essere + irregular_forms; en verb → past_tense/past_participle (nullable —
  регулярный глагол)); `pack.yaml` (per-языковые `card_schema`, `normalization`,
  `sentence_split` 10–400, `prompt_tasks` ×3, `pos_select` [NOUN,VERB,ADJ],
  `frequency_bands` A1≤9.23/A2≤10.64/B1≤11.97/B2≤13.29/C1≤15.29/C2 null —
  Zipf-шкала `1+log2(rank)`, de `separable_prefixes` 21, nl 18); `golden.yaml`
  (перезаписаны: по 20 предложений с размеченными явлениями, 100% покрытие
  таксономии, de-1-е = «Er steht früh auf, bevor die Arbeit beginnt.» (DE-TRENNBAR),
  nl-1-е = «Hij staat vroeg op om te joggen.» (NL-SCHEIDBAAR)); `lemma_reference.yaml`
  (по 50 словоформ: 47 проходимых + 3 известных слабых формы — эталон = стандартная
  грамматика, не вывод модели). `ru/pack.yaml` без изменений (дефолты новых полей).
- **`docs/LIMITATIONS.md`:** CEFR-аудит 2026-09-13 — таблица 5/5 (en/de/fr/nl/it —
  ресурс/лицензия/«нет»/`derived`), все 5 языков `derived` (D45); wordfreq 3.1.1 API
  (wordlist снят, `frequency_of_word` удалён); NLP: 5/5 моделей, 94%×5 лемматизация,
  слабые формы verbatim, parser/ner отключены.
- **Измерено (evidence L1, `docs/CONTROL/evidence/L1/`, 12 файлов):** 5/5 пайплайнов
  (5×3.8.0, spaCy 3.8.16, one-per-process); 47/50 (94%) ×5; de без article/plural → 0
  выпущенных (причины verbatim); nl без de/het → 0, «der» → 0, diminutive=null → 1;
  fr/it без auxiliary/group → 0; таксономии 11/12/9/9/9, чужой id → 0 принятых;
  CEFR-таблица 5/5 verbatim; 5× `Estimated CEFR level`; границы из данных, в коде 0;
  golden 20×5, покрытие 100%, чужой id отклонён verbatim; «Er steht früh auf» →
  aufstehen (1), «Hij staat vroeg op» → opstaan (1), FP-предлоги → 0; удаление
  lexicon → derived, метка `Estimated CEFR level`, 0 падений, резолвер перечисляет
  «CEFR-лексикон не найден: xx/cefr/testlex.json».
- Тесты: 27 запусков в `tests/test_l1_packages.py` (T-L1-1…12, параметризация
  5 языков); evidence-скрипт `tests/l1_evidence.py` (12 verbatim-файлов).
  Полный прогон 143/143 (116 + 27), ruff 0, mypy 0 (47 файлов).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/L1.md`): `doc.sents` без
  сегментатора → E030 (добавлен sentencizer, D46); sentencizer не принимает
  `max_len` (confection — D46: окно обработки вместо конфига); nl-модель
  размечает «vroeg» как VERB «vragen» → ложная единица «opvragen» (правило
  финитности головы, D47; первая попытка — «последний глагол» — неверна, исключала
  истинную голову); `_PREFIX_RE [a-z]+` не совпадал с «zurück» (ü) → isalpha;
  wordfreq 3.1.1 API (D48); CEFR-check резолвера срабатывал бы на `derived` (защита
  `startswith("lexicon:")`); en-эталон 49 записей (добавлен rivers→river).

## Сделано в L0

- **`app/languages/registry.py`:** `PackSpec` (спаци-модель, источник частотности,
  CEFR-диапазон, фикстура, алиасы), `load_registry`/`load_pack`/`load_golden`/
  `aliases_map` — данные `config/languages/<code>/pack.yaml` + `golden.yaml` (Rule 1).
- **`app/languages/detect.py` (D7/D41):** `langdetect 1.0.9` с фикс. сидом (seed=42):
  `_FactoryHolder` — единый фабрик процесса (профили один раз, `set_seed` на вызов),
  `detect_text` (пустой/неопределяемый → None, Rule 6), `decide()` — правила L0.5
  (совпадение → ACCEPTED; конфликт или неуверенность → NEEDS_CONFIRMATION; без dc —
  решение детектора при уверенности), `detect_book`, `normalize_code` (алиасы,
  под-теги), многоязычность L0.6 (D44: доля = помечено/ВСЕ сегменты, сегменты
  < `segment_min_chars` не детектируются, пометки — только при превышении порога).
- **`app/languages/resolver.py` (D38):** `PairResolver` — статус пары по фактическим
  ресурсам; unsupported (задание не создаётся) = нет пакета источника ∨ нет
  spaCy-модели источника ∨ нет пакета целевого языка (расширение); degraded
  (задание разрешено) = нет частотности/фикстуры/golden/промптов пары (ТЗ буквально);
  `missing` — точный перечень (L0.3), у pair_required-задач — пометка E4.3;
  шов `spacy_available` (L0-4 — симуляция удаления модели без деструктивных действий);
  `load_matrix` — неверная матрица → `ConfigurationError` (Rule 6).
- **`app/services/languages.py` (L0-5):** `pair_status`/`matrix_report` — единая точка
  для UI/API: статусы только из резолвера.
- **Данные:** `config/languages/` — 6 пакетов (en/de/fr/it/nl/ru) + 5 golden-наборов;
  `config/language_matrix.yaml` — 11 пар (5×→ru supported; 5 кросс-пар degraded —
  нет pair-промптов vocabulary/grammar; en→ja unsupported — демонстрация);
  промпты: +6 glossary-пар (`{de,fr,it,nl}/en`, `de/it`, `en/de` — со E4).
- **Интеграция пайплайна:** `run_validating` — детект (до LLM, E2.5) +
  `jobs.source_language` (колонка, миграция) + перепроверка пары (unsupported →
  AppError → FAILED с перечнем); `run_planning` — без подтверждённого языка AppError
  (fail closed) и перечитывание свежей строки (D40); `create_job` — проверка пары по
  `dc:language` ДО создания (L0.7); `confirm_job(source_language=…)` — выбор
  пользователя (D43); `read_dc_language` (reader, без распаковки); причина
  NEEDS_CONFIRMATION воркера — из `validation.json` (D42, Rule 8).
- **Фикстуры:** `conflict.epub` (немецкий текст, `dc:language=en`, 2 главы — без
  подозрительности), `bilingual.epub` (en h1+3p + de h1+2p, `dc:language=en`).
- **Конфиг:** `LanguagesSection` (`languages.root`/`matrix`, дефолты — корень
  репозитория: production-конфиг не менялся), `LanguageDetectionSection` расширен
  (seed, multilingual_share=0.2, segment_min_chars=40); `config.example.yaml`
  обновлён. Зависимости: `langdetect>=1.0.9`, `spacy>=3.8.16`, `wordfreq>=3.1.1`
  (+стансы `stubs/langdetect/` — D41; per-file-ignores ruff для `stubs/**`).
- **Тестовые среды (D39):** `make_yaml` по умолчанию кладёт реестр/матрицу
  репозитория; T-E5-10 — `server.lease_seconds=120` (heartbeat=60 по D18): с 30-с
  арендой one-shot-воркер брал «зомби» вместо текущего задания (измерено до релиза).
- **Измерено (evidence L0, `docs/CONTROL/evidence/L0/`):** 5/5 фикстур conf=1.0;
  конфликт 3/3 (100%) с причиной verbatim; 6/6 degraded/unsupported с перечнем;
  шов без spaCy → 11/11 unsupported, 0 падений; UI==резолвер diff 0 ×11;
  supported=5 (все →ru); T-L0-7: confirm('de') → PLANNING + план; T-L0-8:
  share=0.285714 > 0.2, 2 de-пометки, segment_id существуют.
- Тесты: 21 запуск в `tests/test_l0_languages.py` (T-L0-1…8 + правила `decide()` ×7,
  детерминированность, unsupported `create_job`, деформации `load_matrix` ×6);
  evidence-скрипт `tests/l0_evidence.py` (8 verbatim-файлов).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/L0.md`): `decide()` — кортеж причины
  был строкой (хвостовая запятая; смоук); «нет пакета целевого языка» не попадал в
  `missing` (поймано новым тестом L0.7); E5-10 лиза (D39); API langdetect 1.x
  (`.lang`/`.prob`, нет `.name`) и `--no-implicit-reexport` стансов (D41).

## Сделано в E6

- **`app/epub/aligner.py`** (новое, ядро E6 + общее с QC, Rule 8 / D32): пары
  `(segment_id источника, текст перевода)`. Механизм — по разметке E1 §3: оригинал в
  выходе — по `id`, иначе по документному порядку `(тег, оригинальный текст)` с пропуском
  `__translated` (порядковый номер — не единственный критерий, TZ E6.2); перевод —
  sibling / inside (контейнеры) / inplace (REPLACE). Идентичность = (doc, позиционный
  XPath сегментации, нормализованный текст); `segment_id` пересчитывается из выходного
  элемента (текст — БЕЗ вставленных поддеревьев, `original_text`, D33) и сверяется —
  несовпадение → `xpath_mismatch`, пары нет.
- **`text_root` (D34):** корень сегмента движка (E1 §3 п.5 `block_parent`) — для
  blockquote это внутренний `<p>`: тег перевода, маркер `id__translated` и ожидаемая
  позиция (`sibling`/`inside`) берутся с него. Позиция `inside` исключает «уверенные
  неверные пары» со stray-sibling'ом после контейнера.
- **`resolve_positional_xpath`:** парсер реального формата позиционного XPath
  (`/htmlbody[1]p[3]` — без разделителей, корень без индекса) + разрешение на элемент;
  карта хранит ИЗМЕРЕННЫЙ XPath перевода — адресация для L3 (E6-7: 38/38 пар).
- **Причины (TZ E6.4, фиксированный набор):** `missing_translation`, `xpath_mismatch`,
  `empty`, `identical`, `extra_block`; невыровненные — поимённо в
  `alignment_report.json` с `detail`, отбрасывания нет. Честность extra (D37):
  смещённый оригинал — `xpath_mismatch`, не «вставка».
- **Статус и охват (TZ E6.5, D36):** `jobs.alignment_coverage` (колонка, миграция для
  E5-баз) — единый источник (S-стадии/UI читают его, diff 0 с `quality.json` и отчётом);
  ниже `alignment.min_coverage` (YAML, дефолт 0.995) — предупреждение
  «E6: выравнивание ниже порога: …» → `COMPLETED_WITH_WARNINGS` (готовой логикой
  воркера E5). UI-показ — U1.
- **Белый список (TZ E6.3, D35):** `alignment.identical_whitelist_pattern` — regex
  fullmatch по нормализованному переводу (дефолт — цифры/пунктуация); неверный regex →
  `ConfigurationError` при загрузке (fail closed).
- **QC (E5) переиспользует ядро спаривания** (`blocks`/`match_originals`/
  `find_translation` из aligner; `find_translation` по умолчанию `expect="any"` —
  поведение QC не изменилось).
- **Измерено (evidence E6, `docs/CONTROL/evidence/E6/`):** coverage 1.0 ×5 (26/26 +
  3/3×4), identity 0; невыровненные 1/1 с причиной; перестановка 5/5 вариантов
  обнаружена (неверных пар нет; честно: межамена двух no-id переводов одного тега —
  не обнаруживается, E1 «без id — только позиция»); повтор → побитово идентичный
  отчёт (sha256); ниже порога 25/26 = 0.961538 + предупреждение verbatim; 38/38 (100%)
  XPath разрешимы (включая deformed table — td sibling в том же `<tr>`, сноска,
  blockquote inside); REPLACE: 26/26 inplace, источник — исходный файл (T-E6-8).
- **Измеренный факт:** позиционные XPath оригиналов в выходе СДВИГАЮТСЯ вставленными
  sibling'ами — «тот же XPath в выходе» не существует; справка = (doc, src-xpath,
  текст из выхода) (D31).
- Тесты: 10 функций в `tests/test_e6_alignment.py`; evidence-скрипт
  `tests/e6_evidence.py` (воспроизводит 7 verbatim-файлов); полный прогон 95 passed,
  ruff 0, mypy 0 (54 файла).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/E6.md`): контейнерные пары —
  `original_text` (D33); тег/маркер — `text_root` (D34); формат позиционного XPath
  (первая `resolve_positional_xpath` — 0/38); extra/`_is_original` (D37); латентный E5:
  `quality.json["warnings"]` не совпадал со статусом (исправлен: полный набор).

## Сделано в E5

- **Адаптер `app/adapters/epub_engine/`** (D2/D17 снят): `TranslationEngine` (Protocol) +
  `EpubTranslatorEngine` — единственный шов над `epub-translator 0.1.10`; прикладной код
  движок не импортирует. `_make_llms` — кэш LLM-клиентов по модели (каталоги
  `engine_cache/<модель>/`, D15); адаптер НЕ переинтерпретирует исключения движка —
  семантику «тракт T недоступен» (`LLMUnavailableError` с endpoint/model) назначает
  конвейер `run_translating`, кооперативные `WorkerCancelled/WorkerPaused/LeaseLost`
  проходят без поглощения (D23).
- **Режимы (E5.2):** `translation` → `SubmitKind.REPLACE`, `bilingual` →
  `APPEND_BLOCK` (по умолчанию; `APPEND_TEXT` — только явной настройкой
  `translation.submit_mode_bilingual`, дефолт APPEND_BLOCK).
- **Атомарный выход (E5.6):** движок пишет `target.epub.partial` (финальное имя не
  получает); финализация `_finalize`: fsync → внутренняя валидация (`validate_output_epub`
  + `find_broken_links`) → EPUBCheck (реальный jar 5.3.0, classpath-режим, версия в
  отчёте) → бэкап `target.epub.bak-<ts>` перед перезаписью → `os.replace` → fsync каталога.
  EPUBCheck недоступен → `COMPLETED_WITH_WARNINGS` + `VALIDATION_UNAVAILABLE`, результат
  всё же выдан (T-E5-11).
- **QC (E5 п.8, `app/workers/qc.py`, 10 пунктов):** пустые переводы, покрытие
  (поимённая недостача), дублирование пп.5–6 (ожидание = оригиналы+переводы, D25),
  сломанные ссылки (по имени), TOC, метаданные, прогресс (progress_mapping), структура
  (теги/атрибуты Clark + текст; ns-декларации и whitespace-tail не сравниваются, D24;
  известная ресериализация = корневой `lang`), fill-события (все, критические отдельно,
  D26), глоссарий (усечение записано, E5-7).
- **Прогресс в реальных единицах (E5 п.3, D27):** «глава k / N, сегментов A / B (doc)» —
  из последовательности `on_progress` ([TOC][meta]+главы) и кумулятива
  `per_chapter_segments` из `validation.json`; в job.log.
- **Глоссарий (E5 п.5):** `glossary_provider` → `build_glossary_prompt` (лимит
  `limits.glossary_max_user_prompt_chars`, приоритет = число вхождений, детерминизм по
  термину); `user_prompt` движок кладёт в `<rules>` системного сообщения translate (D30);
  усечение → QC-предупреждение с числами.
- **Отмена (D28):** `_finalize_cancelled` не переименовывает — прежний `target.epub`
  (валидный результат) сохраняется, `.partial` = пометка незавершённости.
- **Фикстуры:** `dcterms:modified` добавлено в OPF (EPUBCheck RSC-005 на исходниках);
  комплексная + 4 языковых, эталоны 26/3 сегментов.
- Измерено (evidence E5, `docs/CONTROL/evidence/E5/`): пустые 0×5; покрытие 26/26 +
  3/3×4 (1.0); EPUBCheck v5.3.0 0 ERROR ×5 выходов (исходники 0/0); mimetype первой
  Stored ×5; kill -9 ×10 → 0 повреждённых (target отсутствует, .partial 1018 B);
  fill_garbage: 12 событий (2 критических: TOC + глава 1) = 12 строк errors.log,
  расхождение 0; глоссарий 30→11 (лимит 300, prompt 280) + топ-11 в запросе;
  5/5 книг завершены, sha256 исходников 10/10; сбой LLM → FAILED без выхода;
  без EPUBCheck → COMPLETED_WITH_WARNINGS.
- Тесты: 13 функций (32 запуска) в `tests/test_e5_translation.py`; evidence-скрипт
  `tests/e5_evidence.py` (воспроизводит 8 верbatim-файлов); полный прогон 85 passed,
  ruff 0, mypy 0 (51 файл).
- Дефекты этапа (Rule 3, подробно в `docs/CONTROL/E5.md`): QC п.6 ложные 24 (D25);
  QC п.7 ложные 65 = 49 nsmap + 16 tail (D24); адаптер поглощал `WorkerCancelled`
  (CANCELLED→FAILED) (D23); RSC-005 в фикстурах (build.py).

## Сделано в E4

- **E4.1 — единый источник LLM-настроек:** `llm.profiles.{translation,fill,analysis}` в
  YAML (`base_url`, `model`, `token_encoding`, `temperature`, `timeout_seconds`,
  `retry_times`, `concurrency`, опц. `input/output_price_per_mtok`); из `.env` убраны
  `LLM_BASE_URL`/`LLM_MODEL`/`LLM_ANALYSIS_*` — остались только секреты
  (`LLM_API_KEY`, `LLM_ANALYSIS_API_KEY`) и адрес бинда. `LLMSection.endpoint(name)` /
  `profile(name)` / `api_key_for(name)` — единственный путь к настройкам (D21).
- **E4.3 — показ профилей:** `app/llm/profiles.py::describe_profiles` (провайдер,
  эндпоинт, модель, профиль, факт ключа) — выводится CLI воркера перед стартом задания.
- **E4.4 — клиент анализа (тракт A):** `app/llm/analysis.py` — httpx, не-streaming
  (`stream:false`), валидация `jsonschema` по схеме задачи (`app/llm/tasks.py`:
  `analysis_vocabulary`, `analysis_grammar`, `analysis_glossary` — pair_required=true),
  повторы ≤ `profile.retry_times`, сырые ответы каждой попытки →
  `jobs/<job_id>/raw/`; невалидно после исчерпания → `AnalysisTaskError`,
  пустой артефакт не создаётся (D19). Зависимости: `jsonschema` + `types-jsonschema` (dev).
- **E4.5 — внешние промпты:** `prompts/<task>/<source>/<target>/vN.yaml` в storage.root
  (корень — `llm.prompts_root`, D20); разрешение пара → язык → generic, версия = max N;
  `pair_required` — без парного промпта `PromptError` до обращения к модели; применённый
  файл+версия+уровень → таблица `resolved_prompts` (E4-2: 100% задач).
  В репозитории: generic v1 ×3 + pair `analysis_glossary/en/de/v1.yaml`.
- **E4.6 — mock:** вид запросов `analysis` (системный промпт с маркером
  `You are an analysis engine`) + сценарии `empty`, `bad_json`, `refuse`;
  не-streaming JSON-ответ при `stream:false` (SSE — для движка, без изменений).
- **E4.7 — токены:** `jobs.tokens_total` (инвариант = сумма профилей; отдельный UPDATE —
  семантика SQLite SET, D22; миграция E3→E4 `ALTER`+бекфилл), разбивка
  `job_tokens(job_id, profile, input, input_cache, output)`; тракт T — из
  `llm.total_tokens/input_tokens/input_cache_tokens/output_tokens` (flush в pipeline);
  тракт A — собственные счётчики из `usage` (учитываются и неудачные попытки).
- **E4.6/E4.7 — стоимость:** `app/llm/cost.py::format_cost` — без цен `Cost: unknown`
  (выдумка запрещена), `job_cost_summary` в отчёте воркера.
- Измерено (evidence E4): 0 LLM-URL/моделей/ключей в `app/` (3 XML-неймспейса
  протокола — исключение, D21); разрешение 3/3=100% записано; pair_required fr→de —
  `PromptError`, 0 записей, 0 LLM-запросов; `bad_json` ×3 → `AnalysisTaskError`,
  0 артефактов, 3 сырых ответа, 1002 токена; 1896+8078+0=9974=`tokens_total`,
  расхождение 0 (разбивка 744+1152 / 6345+1733); 3/3 `Cost: unknown`.
- Тесты: 10 функций (15 запусков) в `tests/test_e4_llm.py` (T-E4-1..8 + резолвер +
  показ профилей); полный прогон 53 passed, ruff 0, mypy 0 (42 файла).

## Сделано в E3

- `app/domain/job.py`: машина состояний (14 статусов), явная таблица `TRANSITIONS`,
  `NEXT_STAGE`, `WORKING_STATES`, `CLAIMABLE_STATES`; resume только в сохранённый `*_from`.
- `app/storage/database.py` + `app/storage/jobs_repo.py`: SQLite WAL (единственный
  писатель — воркер), `PRAGMA busy_timeout`/`foreign_keys`, миграции; атомарные переходы
  (недопустимый → `JobStateError` + запись WARNING), `transitions`-журнал,
  lease/heartbeat, восстановление истёкших арен **только при старте воркера**,
  очередь FIFO+priority, идемпотентные ключи этапов (`stage_results`),
  `request_cancel/pause/resume`, прогресс и токены (flush в `on_progress`).
- `app/workers/worker.py` + `run.py`: цикл claim→stages→done; heartbeat-поток с
  **собственным thread-local подключением** (дефект `check_same_thread` найден и исправлен,
  регрессия — в T-E3-1); кооперативная отмена/пауза на границах групп;
  `target.epub → target.epub.partial` при отмене; job-логи `job.log/errors.log/quality.log`.
- `app/workers/pipeline.py`: стадии validating (ридер, статистика, `suspicious` →
  `NEEDS_CONFIRMATION`) → planning → translating (движок, кэш в `jobs/<id>/engine_cache`) →
  analyzing → generating → validating_output; идемпотентный ключ
  `sha256(input+config+prompt_version+target_language)`, совпадение → пропуск этапа
  (повторный перевод не порождается); персист `fill_failures.json`.
  Прямой импорт движка — временный шов (D17, адаптер в E5).
- `app/services/jobs.py`: `create_job` (стадиальный статус по `suspicious`), `confirm_job`.
- Конфиг: `server.heartbeat_seconds`/`lease_seconds` (валидатор `lease ≤ 2×heartbeat`, D18),
  `Settings.jobs_db_path` (единый источник пути БД).
- Измерено (evidence E3): окно осиротения 0.574 с ≤ 2×heartbeat; 152/152 недопустимых
  переходов отклонены (44 допущено, строк в `transitions` = 44); повтор этапа — 0 запросов,
  хэш артефакта идентичен; retranslation share 0.0000% (X=4970, Y=5004, Z=9974); отмена
  за 0.392 с с пометкой `target.epub.partial`; 1 исполнитель из 2 воркеров.
- Тесты: 7 в `tests/test_e3_worker.py` (T-E3-1..4 + идемпотентность, отмена, подтверждение);
  точка kill — по токенам в БД, не по progress (движок пишет 0.4/0.7/1.0 пачкой в конце
  фазы — замеры; см. «Замечания» в `docs/CONTROL/E3.md`).
- Фикстуры `lang_*.epub` получили `nav.xhtml` (движок требует TOC, `read_toc` → ValueError).

## Сделано в E2

- `app/domain/book.py`: неизменяемые модели `Book → Chapter → Segment` + `BookStats`
  (главы, spine-документы, сегменты, символы, слова, изображения, таблицы, сноски,
  has_drm, token_estimate).
- `app/epub/segmenter.py`: блочные элементы из **настраиваемого** множества
  (`settings.segmentation.block_tags`); `segment_id = sha256(путь в архиве + позиционный
  XPath + нормализованный текст NFC/схлопывание пробелов)`; `marked_text` — внутренняя
  разметка без namespace-деклараций lxml (`_plain_copy`).
- `app/epub/reader.py`: валидация до LLM: ZIP → mimetype первой записью →
  **DRM до распаковки** (отказ, work-каталог не создаётся) → container.xml → распаковка
  (защита от zip-slip) → OPF → manifest → spine (порядок глав = spine, не имена файлов) →
  разбор каждого spine-XHTML; нечитаемый документ — `InputValidationError` (exit_code=3).
  `token_estimate` — tiktoken тем же `token_encoding`, что у движка (cl100k_base).
  Флаг `suspicious`: `zero_segments`, `single_spine_document` (подтверждение — в E3).
- Фикстуры (`tests/fixtures/epub/build.py`, D9): `complex.epub` (3 главы, имена файлов
  перемешаны относительно spine, таблица/dl/img/сноска; эталон 8/13/5=26), `drm.epub`,
  `broken.epub` (обрезанный OPF), `lang_de/fr/nl/it.epub` (Rule 4).
- Стробы `stubs/lxml/etree.pyi` для mypy (lxml без py.typed); `tiktoken` — явная зависимость.
- Измерено: `complex.epub` → 26 сегментов, 2270 симв./425 слов, 1 img, 1 table, 1 сноска,
  оценка 516 токенов; движок на этой книге — **2 группы** (2 translate-запроса, 237+237) →
  «фактический расход» для E2-5 считается суммой по всем translate-запросам (отклонение 8.9%).

## Сделано в E1

- Установлен `epub-translator==0.1.10` (ровно версия ТЗ; `uv.lock`).
- Полная source-разведка пакета + прогон `tests/probe/run_probe.py` (P1–P9) на
  детерминированном mock (`tests/probe/mock_llm_server.py`, rot3-«перевод»).
- Ключевые измеренные факты:
  - APPEND_BLOCK: перевод = следующий sibling; тег+атрибуты копируются; `id__translated`
    (дедупликация, маркер E6); blockquote — вставка внутрь (корень сегмента = внутренний
    `<p>`); **таблицы деформируются** (ячейки в тот же `<tr>`, колонки удваиваются).
  - Кэш: ключ `sha512(messages + version:target_language)`; тёплый прогон 0 токенов;
    смена языка 100%; **смена модели — 0 (не инвалидирует)** → D15 (изоляция кэша по модели).
  - Недоступный эндпоинт: `APIConnectionError`, `target_path` остаётся валидным ZIP с
    одной записью `mimetype` (атомарности нет → адаптер E5 пишет `.partial` → `os.replace`).
  - Повторы движка: только 502/503/504 + connection-ошибки; HTTP 500 — немедленный raise;
    встроенные ретраи openai SDK (дефолт 2) не отключаются.
  - Fill-сбой: `range(max_retries)` попыток + финальное `over_maximum_retries=True`;
    перевод группы отбрасывается, книга доводится до конца.
  - Вся книга — один поток (1 translate + 1 fill на фикстуру); `dc:language/identifier/date`
    не переводятся; `h1` в nav в поток не входит; ресериализация ломает побайтовую
    идентичность (QC — по текстовому содержимому).
  - concurrency=4: порядок документа сохранён (FIFO-yield), главы побайтово как при 1.
- Тесты T-E1-1..3 (`tests/test_e1_probe.py`): тёплый кэш ≤2%, смена языка ≥90%,
  concurrency 1 vs 4 побайтово.

## Сделано в E0

- git-репозиторий `main`, коммиты по этапам (ветка `stage/<ЭТАП>` → merge --no-ff).
- venv: `.venv` на **Python 3.11.13** (интерпретатор `/usr/bin/python3.11`), управление — `uv 0.12.5`.
  Системный python3 — 3.9.25, **не используется** (не подходит под epub-translator 3.11–3.13).
- Зависимости E0 (зафиксированы в `uv.lock`; полный verbatim-freeze —
  `docs/CONTROL/evidence/E0/e0-5_versions.txt`): fastapi 0.141.1, httpx 0.28.1, jinja2 3.1.6,
  lxml 6.1.3, pydantic 2.13.5, pydantic-settings 2.15.0, python-multipart 0.0.32, PyYAML 6.0.3,
  uvicorn 0.52.4; dev: mypy 2.3.1, pytest 9.1.1, ruff 0.16.7, types-PyYAML.
- Конфиг-слой `app/core/config.py`: ЕДИНСТВЕННОЕ место чтения `.env`/YAML (Rule 1).
  Обязательные без по-умолчанию (состояние после E4.1): `SERVER_BIND_ADDRESS` (.env),
  `llm.profiles.{translation,fill,analysis}.base_url/.model` (YAML), `storage.root` (YAML).
  `0.0.0.0` — только с `ALLOW_WILDCARD_BIND=true`. Профили LLM: translation/fill/analysis.
- Логирование `app/core/logging.py`: JSON-строки, `job_id` через contextvar, redaction секретов.
- Ошибки `app/core/errors.py`: классы кодов выхода (2 конфиг, 3 вход, 4 LLM, 5 выход).
- Точка входа `python -m app` (E0 — валидация конфига; U1 — сервер).
- Инструменты: ruff (0 ошибок), mypy strict (0 ошибок, 10 файлов), pytest (16 passed).
- `.gitignore`: dot-папки (маска), `.env`, `config/config.yaml`, `cache/ jobs/ books/ logs/
  data/lexicons/ data/models/ data/tools/ *.db working/ output/`, артефакты Python.

## Команды запуска среды

```bash
cd /mdisk/EpubTranslator
.venv/bin/pytest            # тесты
.venv/bin/ruff check .      # линтер
.venv/bin/mypy app tests    # типы
.venv/bin/python -m app --config config/config.yaml --env .env   # bootstrap (E0)
.venv/bin/python -m app.workers.run --config config/config.yaml --env .env  # воркер (E3)
.venv/bin/python tests/e3_evidence.py   # сборка verbatim-evidence CONTROL E3
.venv/bin/python tests/e4_evidence.py   # сборка verbatim-evidence CONTROL E4
.venv/bin/python tests/e5_evidence.py   # сборка verbatim-evidence CONTROL E5
.venv/bin/python tests/e6_evidence.py   # сборка verbatim-evidence CONTROL E6
.venv/bin/python tests/l0_evidence.py   # сборка verbatim-evidence CONTROL L0
.venv/bin/python tests/l1_evidence.py   # сборка verbatim-evidence CONTROL L1
.venv/bin/python tests/l2_evidence.py   # сборка verbatim-evidence CONTROL L2
```

Новые пакеты: `uv add <pkg>` (runtime) / `uv add --dev <pkg>`; lock обновляется автоматически.

## Фактические числа последних измерений

E0-1: ruff 0, mypy 0, pytest 16/16. E0-2: 3/3 отказа, rc=2. E0-3: 5/5 отказов wildcard, rc=2;
с флагом rc=0. E0-4: 0 вхождений секрета в `logs/` (grep rc=1). E0-5: Python 3.11.13, freeze — в evidence.

E1-1: 9/9 verbatim-фрагментов; 176/176 строк вшитых блоков `ENGINE_PROBE.md` совпадают с
evidence побайтово. E1-2: тёплый кэш B/A = 0.00% (порог ≤2%; A: 2 вызова/3857 токенов, B: 0/0).
E1-3: смена языка C/A = 100.00% (порог ≥90%). E1-4: разметка APPEND_BLOCK зафиксирована
(sibling + копируются тег/атрибуты + `id__translated`). E1-5: `APIConnectionError` за 1.279 s,
`target_path` = валидный ZIP с одной записью `['mimetype']`. E1-6: concurrency 1 vs 4 —
главы побайтово одинаковы. Итого по инструментарию после E1: ruff 0, mypy 0 (14 файлов),
pytest 19/19.

E2-1: 26/26 segment_id идентичны между запусками (уникальных 26/26). E2-2: 8/13/5 = 26
(точное совпадение с ручным эталоном). E2-3: 1/1 отклонение DRM до распаковки
(work-каталог не создан). E2-4: 100% совпадение порядка с spine. E2-5: оценка 516 vs
факт 474 (237+237, 2 группы движка) — отклонение 8.9% (порог ≤15%). E2-6: 5/5 языков.
Итого по инструментарию после E2: ruff 0, mypy 0 (21 файл), pytest 27/27.

E3-1: 1/1 kill -9 → INTERRUPTED; окно осиротения 0.574 с ≤ 1.0 с (2×heartbeat);
арена действовала на момент kill (heartbeat продлевал). E3-2: 152/152 недопустимых пар
отклонены + WARNING-лог; допущено 44, строк в transitions = 44. E3-3: хэш `target.epub`
идентичен, 2-й запуск этапа — 0 LLM-запросов (1-й — 4). E3-4: X=4970, Y=5004, Z=9974,
share = 0.0000% (порог ≤5%). E3-5: CANCELLED за 0.392 с (порог ≤30 с),
`target.epub.partial` создан, непомеченного `target.epub` нет. E3-6: 1 исполнитель из 2
воркеров, 1 переход CREATED→VALIDATING, 4 LLM-запроса (2 translate).
Итого по инструментарию после E3: ruff 0, mypy 0 (34 файла), pytest 34/34.

E4-1: 0 LLM-URL/имён моделей/ключей в `app/` (3 XML-неймспейса протокола — зафиксировано
как исключение, D21). E4-2: разрешение промпта записано 3/3 = 100% (generic v1 ×2,
pair en/de v1 ×1). E4-3: pair_required fr→de — `PromptError`, 0 записей разрешения,
0 LLM-запросов (generic-промпт существует — тихий фолбэк исключён). E4-4: `bad_json` ×3
попытки → `AnalysisTaskError`, артефакт 0, сырых ответов 3, токены неудач 1002 учтены.
E4-5: 1896 (translation) + 8078 (fill) + 0 (analysis) = 9974 = `tokens_total`,
расхождение 0; разбивка: 744+1152=1896, 6345+1733=8078. E4-6: 3/3 = 100%
`Cost: unknown` (цены не заданы); с ценами 2.0/8.0 $/Mtok → `Cost: 0.0025 USD`.
Итого по инструментарию после E4: ruff 0, mypy 0 (42 файла), pytest 53/53.

E5-1: пустых переводов 0 на всех 5 фикстурах. E5-2: покрытие 26/26 + 3/3×4 = 1.0
(≥ 99.5%), `shortfall=[]`. E5-3: EPUBCheck v5.3.0 available=True, errors=0 warnings=0
×5 выходов (исходные фикстуры тоже 0/0). E5-4: `mimetype` первой записью Stored ×5
(zipinfo verbatim `stor`). E5-5/T-E5-10: kill -9 ×10 в TRANSLATING (progress=0.100,
exit=-9) → `target.epub` отсутствует, `.partial` 1018 B; повреждённых принятых 0.
E5-6: fill-события 12 (критических 2: TOC + глава 1), строк errors.log 12, расхождение 0,
предупреждение QC выдано. E5-7: 30 терминов, лимит 300 → включено 11, отброшено 19,
prompt_chars=280, truncated=true; топ-11 в system-сообщении translate-запроса,
отброшенных нет. E5-8: 5/5 завершены (complex 5276 B / 5 progress-вызовов, lang 1787–
1836 B / 3), sha256 исходников 10/10. T-E5-9: мёртвый порт → FAILED,
«тракт T недоступен (endpoint http://127.0.0.1:1/v1, model test-model)», выхода нет,
ключ API не в сообщении. T-E5-11: без EPUBCheck → COMPLETED_WITH_WARNINGS +
VALIDATION_UNAVAILABLE, результат выдан.
Итого по инструментарию после E5: ruff 0, mypy 0 (51 файл), pytest 85/85.

E6-1: coverage 1.0 на всех 5 фикстурах (complex 26/26, lang_de/fr/it/nl 3/3),
`min_coverage=0.995`. E6-2: `identity_mismatches=0` ×5. E6-3: невыровненные 1/1
(100%) с причиной `missing_translation` + detail. E6-4: 5/5 вариантов перестановки
обнаружены (a: 2× missing_translation + 2 extra; b: 1× missing_translation + 1 extra;
c: 1× missing_translation; d: 1× xpath_mismatch + 1 extra; e: 4× xpath_mismatch +
3 extra; неверных пар нет). E6-5: повтор (свежее задание) → побитово идентичный
отчёт, 13479 байт, sha256 `8c081ec3…`. E6-6: diff(job−quality)=0.0 и
diff(job−report)=0.0 ×5; ниже порога: 25/26 = 0.961538 < 0.995 → предупреждение
«E6: выравнивание ниже порога: 25/26 (96.2% < 99.5%): OEBPS/z_alpha.xhtml
/htmlbody[1]p[1] (missing_translation)» → COMPLETED_WITH_WARNINGS. E6-7: 38/38 (100%)
пар с разрешимым XPath перевода (тег+текст сверены); T-E6-7: td (deformed table)
sibling в том же `<tr>`, сноска, blockquote inside — вложенные не сломаны.
T-E6-8: REPLACE — 26/26 `how=inplace`, `translation_xpath==source_xpath`,
source_text из исходного файла, исходная фраза в выходе отсутствует.
Итого по инструментарию после E6: ruff 0, mypy 0 (54 файла), pytest 95/95.

L0-1: 5/5 фикстур (en/de/fr/it/nl) — `decision=accepted`, `confidence=1.0`,
top3 одноязычны. L0-2: конфликт (en в OPF, текст de) → NEEDS_CONFIRMATION 3/3
(100%), p=1.000000, причина verbatim; правила `decide()` 7/7 в тесте.
L0-3: 6/6 (100%) degraded/unsupported с перечнем недостающего (5×DEGRADED по 2,
1×UNSUPPORTED 4; 5×SUPPORTED missing=0). L0-4: шов `spacy_available=False` →
11/11 пар unsupported («spaCy-модель не установлена: <модель>»), старт
Pipeline+Worker — 0 падений. L0-5: services.languages == независимый резолвер —
расхождение 0 ×11 пар (+matrix_report). L0-6: supported=5 (en/de/fr/it/nl → ru),
критерий «все исходные → ru» исполнен. T-L0-7: conflict.epub → run_validating True,
`source_language=None` (авто-выбора нет), confirm('de') → PLANNING + plan.json.
T-L0-8: bilingual.epub → подтверждение не требуется, `source_language=en`,
факт: 7 сегментов, share=0.285714 > 0.2, exceeds=true, 2 de-пометки (conf=1.0),
segment_id существуют в книге.
Итого по инструментарию после L0: ruff 0, mypy 0, pytest 116/116 (95 + 21 L0).

L1-1: 5/5 пайплайнов (en_core_web_sm / de_core_news_sm / fr_core_news_sm /
nl_core_news_sm / it_core_news_sm — все 3.8.0; spaCy 3.8.16), one-per-process.
L1-2: лемматизация en/de/nl/fr/it — 47/50 (94%) на каждый; слабые формы verbatim
(en: drank/swam/higher; de: isst/aß/schlief; nl: honden/stoelen/las; fr:
parles→parle/viens→vien/fait; it: mangiamo→mangare/vengo→vengare/sei).
L1-3: de noun без article → 0 выпущенных («'article' is a required property»),
без plural → 0, плейсхолдер «—» → 0 (Rule 5). L1-4: nl noun без de/het → 0,
«der» → 0 (enum), diminutive=null → 1 (легитимно). L1-5: fr/it verb без
auxiliary → 0, без conjugation_group → 0. L1-6: таксономии en 11 / de 12 / nl 9 /
fr 9 / it 9; чужой id (EN-PHRASAL-VERB у de) → принято 0. L1-7: CEFR-аудит —
5/5 строк LIMITATIONS verbatim, все `derived`, 0 лексиконов в поставку.
L1-8: 5× `Estimated CEFR level` (единая точка `cefr.label`). L1-9: границы
A1≤9.23/A2≤10.64/B1≤11.97/B2≤13.29/C1≤15.29/C2 null — из pack.yaml; в коде
app/languages/*.py — 0; примеры: de «der» zipf=7.46→A1, «Häuser» 4.57→A1,
en «the» 7.73→A1, «ubiquitous» 3.42→A1. T-L1-10: golden 20×5, покрытие
таксономии 11/11, 12/12, 9/9, 9/9, 9/9, чужой id отклонён verbatim.
T-L1-11: «Er steht früh auf, bevor die Arbeit beginnt.» → 1 единица
(steht+auf→aufstehen); «Hij staat vroeg op om te joggen.» → 1 (staat+op→opstaan);
FP «Das steht auf der Bank…» → 0, «Hij gaat naar huis…» → 0.
T-L1-12: lexicon hit → `CEFR level (lexicon:testlex): A1`; miss → «не установлен»;
после unlink → derived + `Estimated CEFR level`, 0 исключений, резолвер:
«CEFR-лексикон не найден: xx/cefr/testlex.json».
Итого по инструментарию после L1: ruff 0, mypy 0 (47 файлов), pytest 143/143
(116 + 27 L1).

## Открытые дефекты / ограничения

- Сервер ещё не запускается (этап U1) — `python -m app` пока только валидирует конфиг.
  UI-показ статусов пар (L0-5) и экран подтверждения языка (L0.5) — в U1: данные
  готовы (`services.languages` — единая точка, `validation.json` — решение и причины).
- L0: `langdetect` — статистический (naive Bayes) детектор; confidence=1.0 на
  фикстурах — не гарантия на реальных книгах: страховка — правила L0.5 (конфликт
  детектора и `dc:language` → подтверждение, авто-выбор запрещён) и порог
  `min_confidence` (YAML); по сегментам — только ≥ `segment_min_chars` (D44).
  Нейро-NLP (spaCy) подключено в L1 (лемматизация, POS, sentencizer; parser/ner —
  в L2).
- L1: `lexical_units` (отделяемые глаголы) детектирует только главную конструкцию
  с приставкой СПРАВА от глагола; финитность головы — эвристика на POS (без
  парсера): правило «ранее в предложении другой VERB без CCONJ/SCONJ/PUNCT — не
  финитный» измерено на nl «vroeg» (модель размечает наречие как VERB «vragen») и
  FP-сценариях; придаточные и начальная позиция («Aufstehen ist…») — не
  детектируются (заявлено в CONTROL L1).
- L1: лемматизация fr/it слаба на ряде форм спряжения (fr: parles→parle,
  viens→vien; it: mangiamo→mangare — verbatim в L1-2): S1 при отборе лексики
  не обязан опираться на лемму глагола как единственный ключ (есть `word_form`).
- L1: CEFR — все 5 языков `derived` (аудит 2026-09-13, D45: лицензионно-чистого
  машиночитаемого лексикона не найдено) — в UI (U1) везде, где показан уровень,
  обязательная метка `Estimated CEFR level` (единая точка `app.languages.cefr.label`).
  CEFR никогда не подаётся как истина (TЗ L1 п.4).
- L1: `wordfreq` 3.1.1 — замороженный снимок ~2021 (wordlist-параметр снят,
  `frequency_of_word` удалён — D48): приемлемо для ранжирования полос частотности,
  нет — для утверждений о «современной употребительности» (LIMITATIONS).
- Воркер запускается автономно (`python -m app.workers.run --config … --env …`);
  интеграция «сервер создаёт задания + воркер их исполняет» — в U1.
- Показ доли выравнивания в UI (E6-6, «показан в UI») — в U1: данные готовы
  (единая колонка `jobs.alignment_coverage`, diff 0 с `quality.json` и
  `alignment_report.json`; ниже порога → `COMPLETED_WITH_WARNINGS`).
- Стадия `analyzing` в pipeline — заглушка (E4): клиент анализа, реестр задач и промпты
  v1 готовы, исполнение задач в конвейере — этап L2 и далее; профиль `analysis` не
  входит в `config_hash` (D21) — смена analysis-настроек не инвалидирует готовый
  перевод.
- LLM-эндпоинт — плейсхолдер (`http://127.0.0.1:9999/v1`, модель `probe-model`) до **BLOCKER-1**
  (нужен реальный OpenAI-совместимый эндпоинт от владельца). Весь тракт T (E5) проверен
  на mock — PASS реальных прогонов не заявлен; соблюдение глоссария моделью — гарантия
  только с L3 (жёсткая проверка/правка).
- На E3 найден и исправлен реальный дефект: heartbeat-поток использовал
  `sqlite3.Connection` главной нити → `ProgrammingError`, аренда не продлевалась.
  Регрессия зафиксирована в T-E3-1 (kill позже истечения исходной аренды +
  проверка `lease_expires_at ≥ t_kill`).

## Среда машины (verbatim-снимок 2026-09-12)

- хост `3p14-aaa` (Tailscale 100.124.155.106, tailnet `3p14-aaa.tail46a65f.ts.net`);
- 24 ядра, 62 GB RAM, 348 GB свободно на /mdisk; OpenJDK 17.0.20; PyPI доступен.

## Правила (напоминание)

Rule 1–8 из `TZ_EpubTranslator_00_OVERVIEW.md` действуют на всех этапах: запрет хардкода,
доказуемость, verbatim-захваты, все пять языков, LLM не арбитр, fail closed, запрет расширения
допуска, запрет дублирующих механизмов. Язык — данные, не код (L0–L2).
