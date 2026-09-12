# ENGINE_PROBE — epub-translator 0.1.10

**Дата:** 2026-09-12 · **Ветка:** `stage/E1` · **Метод:** детерминированный OpenAI-совместимый
mock-сервер (`tests/probe/mock_llm_server.py`, D3) + EPUB3-фикстура
`tests/fixtures/epub/probe.en.epub` (2 главы, D9). Запустивший: `python tests/probe/run_probe.py`.

Полные verbatim-захваты (канонические, без сокращений) — `docs/CONTROL/evidence/E1/e1-1…e1-9*.txt`.
Перевод mock-а = rot3 (по-символьный сдвиг латиницы): структура и длина сохраняются; ответ fill
(шаблон с code-преобразованием всех текстовых узлов) — консистентный «перевод», поэтому все
прогоны завершаются без fill-сбоев, кроме P8 (сценарий заведомо неверного ответа).

---

## 1. P1 — версия движка (verbatim)

```
$ .venv/bin/pip show epub-translator
Name: epub-translator
Version: 0.1.10
Summary: Translate the epub book using LLM. The translated book will retain the original text and list the translated text side by side with the original text.
Home-page: https://hub.oomol.com/package/books-translator
Author: Tao Zeyu
Author-email: i@taozeyu.com
License: MIT
Location: /mdisk/EpubTranslator/.venv/lib64/python3.11/site-packages
Requires: jinja2, mathml2latex, openai, resource-segmentation, tiktoken
Required-by:
```

## 2. P2 — фактический API (verbatim)

```
epub-translator version: 0.1.10
public __all__: ['LLM', 'translate', 'language', 'FillFailedEvent', 'SubmitKind']

signature LLM.__init__:
(self, key: str, url: str, model: str, token_encoding: str, timeout: float | None = None, top_p: float | tuple[float, float] | None = None, temperature: float | tuple[float, float] | None = None, retry_times: int = 5, retry_interval_seconds: float = 6.0, cache_path: os.PathLike | str | None = None, log_dir_path: os.PathLike | str | None = None, extra_body: dict[str, object] | None = None) -> None

signature translate:
(source_path: os.PathLike | str, target_path: os.PathLike | str, target_language: str, submit: epub_translator.xml_translator.submitter.SubmitKind, user_prompt: str | None = None, max_retries: int = 5, max_group_tokens: int = 2600, concurrency: int = 1, llm: epub_translator.llm.core.LLM | None = None, translation_llm: epub_translator.llm.core.LLM | None = None, fill_llm: epub_translator.llm.core.LLM | None = None, on_progress: collections.abc.Callable[[float], None] | None = None, on_fill_failed: collections.abc.Callable[[epub_translator.xml_translator.callbacks.FillFailedEvent], None] | None = None) -> None
```

Учёт токенов — свойства `LLM`: `total_tokens`, `input_tokens`, `input_cache_tokens`, `output_tokens`
(считаются из `usage` стрим-чанков OpenAI SDK; кэшированные ответы токены не добавляют).

## 3. P3 — APPEND_BLOCK: точная фактическая разметка

Измерено (полный захват — `e1-3_append_block.txt`):

1. **Позиция:** переведённый блок вставляется **сразу после исходного** — как следующий sibling
   (внутрь того же родителя, на индекс исходного + 1).
2. **Тег и атрибуты:** тег и **все атрибуты** исходного блока копируются в перевод
   (`Element(raw.tag, raw.attrib)`); повторяющийся `id` дедуплицируется: 2-я встреча →
   `id__translated`, 3-я → `id__translated_2`.
3. **Inline-якоря:** `<a>` внутри переведённого блока получает `id="a1__translated"`
   (атрибут скопирован, дедупликация применена).
4. **Блок без id** → переведённый блок без id.
5. **blockquote:** корнем text-сегмента является **внутренний** `<p>`, а не `<blockquote>` →
   переведённый `<p>` вставляется **внутрь** blockquote (sibling внутреннего `<p>`),
   а не после blockquote.
6. **Таблица — структурная деформация:** переведённые ячейки вставляются в **тот же `<tr>`**
   как siblings исходных `<td>` → число колонок удваивается (2 → 4):
   `alpha | doskd | beta | ehwd`. Семантика исходной таблицы нарушена.
7. **Вся книга = один поток:** ОДИН translate-запрос содержит TOC + метаданные + обе главы;
   при `max_group_tokens=2600` вся фикстура — одна группа → всего **2 LLM-вызова**
   (1 translate + 1 fill).
8. **Ресериализация:** порядок атрибутов меняется; неиспользуемые namespace-декларации
   удаляются (`xmlns:epub` пропадает из глав, где нет `epub:`-атрибутов); `<link/>` —
   self-closing; `lang` + `xml:lang` схлопываются в `xml:lang`. **Побайтовая идентичность
   с исходником не сохраняется никогда** — QC обязан сравнивать текстовое содержимое,
   а не байты.
9. **Выходной zip:** `mimetype` — первая запись (STORED, сохраняет дату исходника);
   переписанные XHTML/OPF — дата `1980-01-01`; порядок записей не совпадает с исходным
   (nav.xhtml раньше content.opf; META-INF/container.xml — у конца).

### Verbatim: вход → выход `OEBPS/chapter1.xhtml` (APPEND_BLOCK)

```
=== ВХОД: OEBPS/chapter1.xhtml (исходная фикстура) ===
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Chapter One</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1 id="h1-1">Chapter One</h1>
  <p id="p1">The quick <b>brown fox</b> jumps over the <i>lazy dog</i> near the river.</p>
  <p id="p2">She visited the <a href="https://example.com/old-mill" id="a1">old mill</a> on a cold morning.</p>
  <p>The third paragraph has no id and contains a <strong>strong phrase</strong> and a <code>code span</code>.</p>
  <ul>
    <li id="li1">First list item with <b>emphasis</b>.</li>
    <li>Second list item, plain.</li>
  </ul>
  <blockquote id="bq1"><p>The fox said: patience is a virtue.</p></blockquote>
  <p id="p3">End of chapter one.</p>
</body>
</html>

=== ВЫХОД: OEBPS/chapter1.xhtml (APPEND_BLOCK) ===
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xml:lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter One</title>
  <link rel="stylesheet" type="text/css" href="style.css" />
</head>
<body>
  <h1 id="h1-1">Chapter One</h1><h1 id="h1-1__translated">Fkdswhu Rqh</h1>
  <p id="p1">The quick <b>brown fox</b> jumps over the <i>lazy dog</i> near the river.</p><p id="p1__translated"> Wkh txlfn <b>eurzq ira</b> mxpsv ryhu wkh <i>odcb grj</i> qhdu wkh ulyhu. </p>
  <p id="p2">She visited the <a href="https://example.com/old-mill" id="a1">old mill</a> on a cold morning.</p><p id="p2__translated"> Vkh ylvlwhg wkh <a href="https://example.com/old-mill" id="a1__translated">rog ploo</a> rq d frog pruqlqj. </p>
  <p>The third paragraph has no id and contains a <strong>strong phrase</strong> and a <code>code span</code>.</p><p> Wkh wklug sdudjudsk kdv qr lg dqg frqwdlqv d <strong>vwurqj skudvh</strong> dqg d <code>frgh vsdq</code> . </p>
  <ul>
    <li id="li1">First list item with <b>emphasis</b>.</li><li id="li1__translated"> Iluvw olvw lwhp zlwk <b>hpskdvlv</b> . </li>
    <li>Second list item, plain.</li><li>Vhfrqg olvw lwhp, sodlq.</li>
  </ul>
  <blockquote id="bq1"><p>The fox said: patience is a virtue.</p><p>Wkh ira vdlg: sdwlhqfh lv d yluwxh.</p></blockquote>
  <p id="p3">End of chapter one.</p><p id="p3__translated">Hqg ri fkdswhu rqh.</p>
</body>
</html>
```

(Пункт 6 наглядно виден в `OEBPS/chapter2.xhtml` того же вывода:
`<tr><td>alpha</td><td>doskd</td><td>beta</td><td>ehwd</td></tr>` — полный текст — в `e1-3`.)

### Verbatim: порядок записей выходного zip

```
mimetype	compress_type=0	file_size=20	date=(2026, 1, 1, 0, 0, 0)
OEBPS/nav.xhtml	compress_type=8	file_size=437	date=(1980, 1, 1, 0, 0, 0)
OEBPS/content.opf	compress_type=8	file_size=1120	date=(1980, 1, 1, 0, 0, 0)
OEBPS/chapter1.xhtml	compress_type=8	file_size=1405	date=(1980, 1, 1, 0, 0, 0)
OEBPS/chapter2.xhtml	compress_type=8	file_size=611	date=(1980, 1, 1, 0, 0, 0)
META-INF/container.xml	compress_type=8	file_size=235	date=(2026, 1, 1, 0, 0, 0)
OEBPS/style.css	compress_type=8	file_size=53	date=(2026, 1, 1, 0, 0, 0)
```

### Verbatim: единственный translate-запрос (system)

```
You are a translator. Translate the following text into ru.

Translation rules:
- **Translate completely; do not omit any part of the source text**
- Maintain fidelity to the source; do not modify, add, or remove content
- Translate sentence by sentence, preserving paragraph structure
- Preserve all details; do not summarize or abbreviate
- Do not correct errors or inconsistencies in the source
- Do not censor sensitive content; translate as-is
- Do not add commentary or explanations
- Output plain text only; do not use markdown, HTML tags, or any formatting syntax

Output only the translated text, nothing else.
```

user-часть (497 chars) — весь текст книги одним сообщением: заголовки nav, dc:title/creator/
description/subject, затем главы; response (497 chars) — тот же текст с rot3.
fill-запрос: system 6704 chars («You are an XML structure validator…»), user содержит
`Source text:` + `XML template:` (```` ```XML … ``` ````) + `Translated text:`; полный текст
обоих запросов — в `e1-3_append_block.txt`.

## 4. P4 — APPEND_TEXT / REPLACE (verbatim — вывод `OEBPS/chapter1.xhtml`)

**APPEND_TEXT** — перевод (с inline-разметкой) дописывается **внутрь** исходного блока;
дублирующиеся id получают суффикс (`a1__translated` у вставленного якоря):

```
  <h1 id="h1-1">Chapter One Fkdswhu Rqh</h1>
  <p id="p1">The quick <b>brown fox</b> jumps over the <i>lazy dog</i> near the river. Wkh txlfn <b>eurzq ira</b> mxpsv ryhu wkh <i>odcb grj</i> qhdu wkh ulyhu. </p>
  <p id="p2">She visited the <a href="https://example.com/old-mill" id="a1">old mill</a> on a cold morning. Vkh ylvlwhg wkh <a href="https://example.com/old-mill" id="a1__translated">rog ploo</a> rq d frog pruqlqj. </p>
  <p>The third paragraph has no id and contains a <strong>strong phrase</strong> and a <code>code span</code>. Wkh wklug sdudjudsk kdv qr lg dqg frqwdlqv d <strong>vwurqj skudvh</strong> dqg d <code>frgh vsdq</code> . </p>
  <ul>
    <li id="li1">First list item with <b>emphasis</b>. Iluvw olvw lwhp zlwk <b>hpskdvlv</b> . </li>
    <li>Second list item, plain. Vhfrqg olvw lwhp, sodlq.</li>
  </ul>
  <blockquote id="bq1"><p>The fox said: patience is a virtue. Wkh ira vdlg: sdwlhqfh lv d yluwxh.</p></blockquote>
  <p id="p3">End of chapter one. Hqg ri fkdswhu rqh.</p>
```

**REPLACE** — содержимое блока заменяется переводом; id сохраняются **без** суффиксов
(дублирования нет → дедупликация не срабатывает):

```
  <h1 id="h1-1">Fkdswhu Rqh</h1>
  <p id="p1"> Wkh txlfn <b>eurzq ira</b> mxpsv ryhu wkh <i>odcb grj</i> qhdu wkh ulyhu. </p>
  <p id="p2"> Vkh ylvlwhg wkh <a href="https://example.com/old-mill" id="a1">rog ploo</a> rq d frog pruqlqj. </p>
  <p> Wkh wklug sdudjudsk kdv qr lg dqg frqwdlqv d <strong>vwurqj skudvh</strong> dqg d <code>frgh vsdq</code> . </p>
  <ul>
    <li id="li1"> Iluvw olvw lwhp zlwk <b>hpskdvlv</b> . </li>
    <li>Vhfrqg olvw lwhp, sodlq.</li>
  </ul>
  <blockquote id="bq1"><p>Wkh ira vdlg: sdwlhqfh lv d yluwxh.</p></blockquote>
  <p id="p3">Hqg ri fkdswhu rqh.</p>
```

Полный вывод (обе главы, оба режима) — `e1-4_append_text_replace.txt`.

## 5. P5 — TOC / OPF-метаданные (verbatim)

- OPF: `dc:title`, `dc:creator`, `dc:description`, `dc:subject` — **переводятся** (APPEND_TEXT:
  перевод дописан внутрь элемента); `dc:identifier`, `dc:language`, `dc:date`, `<meta>` —
  **не изменяются** (SKIP_FIELDS движка).
- nav.xhtml: текст ссылок TOC **переводится**; `h1` внутри nav («Contents») **не входит
  в поток перевода** (отсутствует в user-сообщении translate-запроса).

OPF, фрагмент `<metadata>` выхода:

```
    <dc:identifier id="book-id">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>Probe Book Sureh Errn</dc:title>
    <dc:creator>Probe Author Sureh Dxwkru</dc:creator>
    <dc:description>A small probe book for engine verification. D vpdoo sureh errn iru hqjlqh yhulilfdwlrq. </dc:description>
    <dc:subject>probe sureh</dc:subject>
    <dc:language>en</dc:language>
    <dc:date>2026-01-01</dc:date>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
```

nav.xhtml, фрагмент `<nav>` выхода:

```
  <nav id="toc" epub:type="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="chapter1.xhtml">Chapter One Fkdswhu Rqh</a></li>
      <li><a href="chapter2.xhtml">Chapter Two Fkdswhu Wzr</a></li>
    </ol>
  </nav>

заголовки h1 на входе:  ['  <h1 id="h1-1">Chapter One</h1>']
заголовки h1 на выходе: ['  <h1 id="h1-1">Chapter One</h1><h1 id="h1-1__translated">Fkdswhu Rqh</h1>']
```

Полный вход/выход OPF и nav — `e1-5_toc_metadata.txt`.

## 6. P6 — кэш (verbatim)

Ключ = `sha512(JSON{"messages": [...], "cache_seed": seed})`, `cache_seed = f"{версия}:
{target_language}"`; файлы кэша — плоские `{sha512}.txt` с двухфазной коммит-сделкой.

```
A (холодный кэш, вызовов к mock: 2):
exception: None
elapsed_seconds: 0.024
total_tokens: 3857 (input=2950, input_cache=0, output=907)
progress: [0.05, 0.1, 0.5499999999999999, 0.9999999999999999]
fill_events: []

B (тёплый кэш, фактических LLM-вызовов: 0):
exception: None
elapsed_seconds: 0.009
total_tokens: 0 (input=0, input_cache=0, output=0)
progress: [0.05, 0.1, 0.5499999999999999, 0.9999999999999999]
fill_events: []

C (смена target_language на de, та же папка):
exception: None
elapsed_seconds: 0.026
total_tokens: 3857 (input=2950, input_cache=0, output=907)
progress: [0.05, 0.1, 0.5499999999999999, 0.9999999999999999]
fill_events: []

D (смена model, та же папка, тот же язык; LLM-вызовов: 0):
exception: None
elapsed_seconds: 0.009
total_tokens: 0 (input=0, input_cache=0, output=0)
progress: [0.05, 0.1, 0.5499999999999999, 0.9999999999999999]
fill_events: []


прирост токенов B/A: 0.00%   (порог E1-2: <= 2%)
прирост токенов C/A: 100.00%   (порог E1-3: >= 90%)
прирост токенов D/A: 0.00%   (смена модели: инвалидация или нет)

структура кэш-каталога (файлы = ключи):
после A: файлов=2
  9cdc40d6800e53d28a9cb3ce721d4e774c40f95cc97218b30b0dfe6091aa7584ceca42207f3292128475fff44d0255bcb7f6c672ca3f6ebda3fd11f20ea70b31.txt
  a56c8a6758651bfaab638af681a6d1ec66a0c34789baeda83659e2ea345d243656fa535826bd0c67b74e1dbac9511861d253282bc2de041781800cebed49a6c6.txt
после C: файлов=4
  8199e6d5c73b352b0adc9e93b1d7a24076c6069f266482c6d69e7b1320e196bd6de1ae1f56f7703e83f990e91d43ba57c30ebcfdfce81cc963f1cb2a619e2010.txt
  9cdc40d6800e53d28a9cb3ce721d4e774c40f95cc97218b30b0dfe6091aa7584ceca42207f3292128475fff44d0255bcb7f6c672ca3f6ebda3fd11f20ea70b31.txt
  a284ead9dd8ffdbb35dbff22b589b602cc4d1b28d3ec620154c6e87dad6e98c28a30632d1a747cb632390a793c44d402b999dc260e68ce4c4bc3f92bf066040c.txt
  a56c8a6758651bfaab638af681a6d1ec66a0c34789baeda83659e2ea345d243656fa535826bd0c67b74e1dbac9511861d253282bc2de041781800cebed49a6c6.txt
```

**Ключевой факт D: модель НЕ входит в ключ кэша** — смена `model` кэш **не инвалидирует**
(0 токенов, 0 вызовов). Ключи `ru` после прогона C сохраняются (4 файла). Полный список
ключей — `e1-6_cache.txt`.

## 7. P7 — недоступный эндпоинт (verbatim)

```
конфигурация: llm.url=http://127.0.0.1:9/v1 (порт закрыт), retry_times=0, retry_interval_seconds=0.2, submit=APPEND_BLOCK, target_language=ru

exception: openai.APIConnectionError: Connection error.
elapsed_seconds: 1.279
total_tokens: 0 (input=0, input_cache=0, output=0)
progress: []
fill_events: []

судьба target_path: существует, валидный ZIP, записей=1: ['mimetype']
```

Факты:
- цикл повторов движка при `retry_times=0` — одно прохождение, НО клиент `OpenAI(...)`
  создаётся **без** `max_retries` → действуют встроенные ретраи openai SDK (по умолчанию 2)
  с экспоненциальным бэкоффом: отсюда `elapsed=1.279s` на ECONNREFUSED;
- прогресс-событий не было (сбой на первом же LLM-вызове);
- **атомарности нет**: `target_path` создаётся до перевода, `mimetype` пишется первым →
  после сбоя остаётся валидный ZIP с одной записью `mimetype` (невалидный EPUB).

## 8. P8 — on_fill_failed (verbatim)

Цикл fill (измерено + исходники): `for retry_count in range(max_retries)` — на каждой
ошибке событие `FillFailedEvent(error_message, retried_count=retry_count+1,
over_maximum_retries=False)`, история разговора пополняется (ответ ассистента + текст ошибки)
и участвует в следующем вызове; после исчерпания — финальное событие
`FillFailedEvent(error_message, retried_count=max_retries, over_maximum_retries=True)`.
При `max_retries=2` на сбойную группу: **2 fill-вызова + 3 события**.

```
fill_events: [{"error_message": "No complete <xml>...</xml> block found. Please ensure you have properly closed the XML with </xml> tag.", "retried_count": 1, "over_maximum_retries": false}, {"error_message": "No complete <xml>...</xml> block found. Please ensure you have properly closed the XML with </xml> tag.", "retried_count": 2, "over_maximum_retries": false}, {"error_message": "No complete <xml>...</xml> block found. Please ensure you have properly closed the XML with </xml> tag.", "retried_count": 2, "over_maximum_retries": true}]

fill-запросов к mock: 2
глава 1 выхода побайтово равна входу: False
```

Поведение при сбое fill: перевод группы **отбрасывается**, исходный контент остаётся
(в ресериализованном виде — отсюда `False` по побайтовому сравнению; текстовое содержимое
не изменилось), обработка книги продолжается до конца (`progress` доходит до 1.0).
Всё в одном потоке — сбой единственной группы затронул все элементы фикстуры
(ни одна строка не переведена, включая nav и OPF). Полный вывод — `e1-8_fill_failed.txt`.

## 9. P9 — порядок при concurrency (verbatim)

```
concurrency=1:
exception: None
elapsed_seconds: 0.023
total_tokens: 3857 (input=2950, input_cache=0, output=907)
progress: [0.05, 0.1, 0.5499999999999999, 0.9999999999999999]
fill_events: []

concurrency=4:
exception: None
elapsed_seconds: 0.023
total_tokens: 3857 (input=2950, input_cache=0, output=907)
progress: [0.05, 0.1, 0.5499999999999999, 0.9999999999999999]
fill_events: []


chapter1.xhtml: побайтово одинаковы = True
chapter2.xhtml: побайтово одинаковы = True
ИТОГО (E1-6, порог 100%): ДА
```

Результаты yield'ятся FIFO — порядок документа при `concurrency=4` идентичен
`concurrency=1` побайтово (полные выходы — `e1-9_concurrency.txt`).

## 10. Повторные попытки и ошибки (измерено + исходники 0.1.10)

- Повторяемые **циклом движка** (`retry_times`): `openai.Timeout`, `openai.APIConnectionError`,
  `httpx` Stream/Network/Protocol-ошибки, HTTP **502/503/504**.
- Простой HTTP **500 — НЕ повторяется**: немедленный `raise` (в `is_retry_error` — только
  502/503/504).
- Встроенные ретраи openai SDK (дефолт `max_retries=2`) **не отключаются** движком —
  складываются с его циклом (см. P7).
- `retry_interval_seconds` применяется между попытками цикла движка.

## Критические следствия для E5/E6

1. **E6 (выравнивание):** основной маркер — id `…__translated` (дедупликация); для блоков
   без id — только позиция (перевод = следующий sibling исходного). Вложенные случаи
   (blockquote, таблица) — позиция считается от **внутреннего** элемента-корня сегмента.
2. **QC (E5):** APPEND_BLOCK деформирует таблицы (колонки удваиваются) — QC обязан
   проверять не только присутствие переводов, но и сохранность структуры.
3. **Атомарность:** адаптер обязан писать в `.partial` → валидация → `os.replace`
   (сам движок при сбое оставляет `target_path` с единственным `mimetype`).
4. **Кэш:** модель не входит в ключ → адаптер обязан изолировать кэш-папку по модели
   (и языку); иначе смена модели в конфиге даст «прозрачные» старые ответы (D15).
5. **Политика повторов** настраивается профилем LLM (E4): HTTP 500 ретраится только
   встроенным механизмом SDK, а не циклом движка.
