"""Единый словарь строк интерфейса (U3 3.1, критерий U3-1).

Все видимые строки UI — только здесь: плоский словарь «ключ → строка».
Шаблоны, ``app/web/*.py`` и ``app.js`` текстовых литералов интерфейса
не содержат: шаблоны обращаются к ``s["ключ"]`` (контекст страниц),
``app.js`` — к `U3_STRINGS` (встроен в HTML как JSON), Python-код —
к `UI_STRINGS` (импорт из этого модуля).

НЕ переводятся и не переформулируются (3.1.3) — выводятся verbatim
как в БД и артефактах, подставляются в строки этого словаря:
статусы заданий (``COMPLETED_WITH_WARNINGS``, ``FAILED``, ...), коды
языков, имена задач промптов, имена файлов, причины отказов
из БД/артефактов. Иначе ломается сверка UI с логами (U1-11, V1.4).

Значения с плейсхолдерами ``{имя}`` оформляются через `str.format`.
Числовых значений (кроме единиц/аббревиатур) в строках нет — все
числовые параметры интерфейса — в ``settings.ui`` (Rule 1).
"""

from __future__ import annotations

UI_STRINGS: dict[str, str] = {
    # ------------------------------------------------------------- навигация
    # (D2: шапка с горизонтальной навигацией; серия R: таб-бар + appbar)
    "nav_books": "Книги",
    "nav_jobs": "Задания",
    "nav_languages": "Языки",
    "nav_models": "Модели",
    "nav_prompts": "Промпты",
    "nav_settings": "Настройки",
    # ------------------------------------------------------------- каркас серии R
    "tabbar_label": "Разделы",
    "tab_library": "Библиотека",
    "tab_jobs": "Задания",
    "tab_settings": "Настройки",
    "tab_add": "Добавить",
    "appbar_back": "Назад",
    "appbar_more": "Ещё",
    "fab_add_book": "Добавить книгу",
    "fab_new_job": "Новое задание",
    # ------------------------------------------------------------- каркас
    "brand": "EpubTranslator",
    "title_jobs": "Задания",
    "title_job": "Задание {id}",
    "title_books": "Книги",
    "title_languages": "Языки",
    "title_models": "Модели",
    "title_prompts": "Промпты",
    "title_settings": "Настройки",
    "title_settings_llm": "LLM и модели",
    "title_settings_appearance": "Внешний вид",
    "title_settings_maintenance": "Обслуживание",
    "title_settings_about": "О системе",
    "title_readme": "README",
    "title_not_found": "Не найдено",
    "footer_auth_on": "доступ: привязка к Tailscale + общий секрет (HTTP Basic)",
    "footer_auth_off": (
        "доступ: привязка к Tailscale, общий секрет не задан "
        "(SERVER_AUTH_SECRET) — чтение открыто, изменение запрещено"
    ),
    "footer_worker_pre": "воркер — отдельный процесс:",
    "footer_worker_cmd": "python -m app.workers.run --loop",
    "footer_worker_ui": "(запуск из UI: Not implemented)",
    # ------------------------------------------------------------- общие
    "common_loading": "загрузка…",
    "common_err_load": "Ошибка загрузки: {msg}",
    "common_err": "Ошибка: {msg}",
    "common_cmd_failed": "Команда не выполнена: {msg}",
    "common_select_file": "Выберите файл",
    "common_uploading": "Загрузка…",
    "common_uploaded": "Загружено.",
    "common_creating": "Создание…",
    "common_sending": "Отправка…",
    "common_poll_stopped": "Опрос остановлен: {msg}",
    "common_retry": "Повторить",
    "common_yes": "да",
    "common_no": "нет",
    "common_not_set": "—",
    "common_close": "Закрыть",
    "common_size_b": "Б",
    "common_size_kb": "КБ",
    "common_size_mb": "МБ",
    "common_size_gb": "ГБ",
    # ------------------------------------------------------------- Books
    "books_upload_label": "Файл книги",
    "books_upload_btn": "Загрузить",
    "books_upload_hint": (
        "Всё, что не EPUB, конвертируется в EPUB при загрузке (calibre); "
        "оригинал сохраняется рядом. Определение языка — тот же механизм "
        "L0, что и стадия validating."
    ),
    "books_empty": "Книг нет. Импортируйте книгу — это первый шаг к переводу.",
    # ------------------------------------------------------------- Импорт (R6, TZ §8)
    "import_sheet_title": "Импорт книги",
    "import_drop_line": "Выбрать файлы",
    "import_files_label": "Файлы для импорта",
    "import_formats_line": (
        "Поддерживаются EPUB, FB2, MOBI, AZW3, DOCX, PDF, TXT и ещё 6 форматов"
    ),
    "import_formats_link": "полный список",
    "import_formats_note": "Сканерные PDF, комиксы и DRM отклоняются.",
    "import_converter_unavailable": (
        "Конвертация недоступна: не установлен calibre — принимается только EPUB."
    ),
    "import_step_check": "Проверка",
    "import_step_convert": "Конвертация",
    "import_step_validate": "Валидация",
    "import_step_language": "Язык",
    "import_progress_of": "{done} из {total}",
    "import_summary_line": "{ok} добавлено, {err} с ошибкой",
    "import_toast_added": "Книга добавлена",
    "import_toast_translate": "Перевести",
    "import_error_line": "Ошибка: {reason}",
    "import_status_title": "Импорт книги",
    "import_status_active": "Обработка…",
    "import_status_done_line": "Книга добавлена: {name}",
    "import_status_error_line": "Импорт не выполнен: {reason}",
    "import_status_to_book": "К карточке книги",
    "import_status_to_books": "К списку книг",
    "book_source_format": "Исходный формат",
    "books_col_file": "файл",
    "books_col_title": "название",
    "books_col_dc_lang": "dc:language",
    "books_col_detected": "детекция",
    "books_col_confidence": "уверенность",
    "books_col_size": "размер",
    "books_col_chapters": "главы",
    "books_col_segments": "сегменты",
    "books_col_uploaded": "загружено",
    "books_delete_btn": "Удалить",
    "books_delete_confirm": (
        "Удалить книгу {name}? Файл EPUB и результат детекции будут удалены "
        "из input/. Действие необратимо."
    ),
    "books_deleted": "Книга удалена.",
    "books_search_ph": "Поиск книги",
    "books_search_btn": "Найти",
    # ------------------------------------------------------------- R5: карточка книги
    "book_translate_btn": "Перевести",
    "book_export_btn": "Экспорт",
    "book_author": "Автор",
    "book_block_languages": "Языки",
    "book_src_lang": "Язык оригинала",
    "book_pill_src": "Язык",
    "book_pill_tgt": "Цель",
    "book_pill_cefr": "CEFR",
    "book_src_lang_note": "Машинная детекция: {value}",
    "book_detection_confidence": "Уверенность детекции",
    "book_target_default": "Цель по умолчанию",
    "book_edit": "Изменить",
    "book_decision_accepted": "принят",
    "book_decision_accepted_by_detector": "принят детектором",
    "book_decision_needs_confirmation": "требует подтверждения",
    "book_block_jobs": "Задания по этой книге",
    "book_jobs_all": "Все ({n})",
    "book_jobs_none": "Заданий пока нет",
    "book_st_wait": "В очереди",
    "book_st_run": "Переводится",
    "book_st_ok": "Готово",
    "book_st_warn": "С оговорками",
    "book_st_err": "Отказ",
    "book_st_none": "Без задания",
    "book_block_tech": "Технические сведения",
    "book_tech_file": "Файл",
    "book_tech_path": "Путь",
    "book_tech_size": "Размер",
    "book_tech_sha256": "SHA-256",
    "book_tech_added": "Добавлена",
    "book_delete_btn": "Удалить книгу",
    "book_delete_title": "Удаление книги",
    "book_delete_simple_q": "Удалить файл {name}? Действие необратимо.",
    "book_delete_yes": "Удалить",
    "book_delete_cancel": "Отмена",
    "book_delete_referenced": "На книгу ссылаются {n} заданий",
    "book_delete_file_only": "Удалить только файл",
    "book_delete_file_only_hint": (
        "Файл книги будет удалён; {n} заданий останутся в списке со ссылкой "
        "на отсутствующий файл."
    ),
    "book_delete_cascade": "Удалить книгу и {n} заданий",
    "book_delete_cascade_hint": (
        "Книга и {n} заданий будут удалены окончательно вместе с артефактами. "
        "Действие необратимо."
    ),
    "book_delete_cascade_confirm": "Да, удалить",
    "book_delete_nonterminal": (
        "Среди {n} заданий есть не в терминальном статусе: {ids}. Каскад "
        "удаляет только завершённые задания — сначала отмените эти."
    ),
    "book_busy_toast": "Книга занята заданием, попробуйте ещё раз",
    "book_file_deleted": "Файл книги удалён, задания сохранены",
    "book_meta_saved": "Метаданные сохранены",
    "book_field_title": "Название",
    "book_field_author": "Автор",
    "book_field_source": "Язык оригинала",
    "book_field_target": "Язык перевода по умолчанию",
    "book_field_cefr": "Уровень CEFR по умолчанию",
    "book_field_note": "Заметка",
    "book_meta_save": "Сохранить",
    "book_meta_title": "Метаданные книги",
    "jobs_book_strip": "Книга: {name}",
    # ------------------------------------------------------------- Jobs (список)
    "jobs_worker": "Воркер:",
    "jobs_autorefresh": "страница обновляется сама, пока есть активные задания",
    "jobs_refresh_last": "последнее обновление: {time}",
    "jobs_refresh_error": "Ошибка API: {msg} (повтор через {wait} с)",
    "jobs_f_book": "Книга",
    "jobs_f_target": "Целевой язык",
    "jobs_f_mode": "Режим",
    "jobs_f_level": "Уровень обучения",
    "jobs_f_cefr": "CEFR пользователя",
    "jobs_opt_none": "— не выбран —",
    "jobs_filter_title": "Фильтры",
    "jobs_f_status": "Статус",
    "jobs_f_book_filter": "Книга",
    "jobs_f_pair": "Пара языков",
    "jobs_f_created_from": "Создано с",
    "jobs_f_created_to": "по",
    "jobs_filter_apply": "Применить",
    "jobs_filter_reset": "Сбросить",
    "jobs_sort_label": "Сортировка",
    "jobs_sort_direction": "Направление",
    "jobs_sort_created": "создано",
    "jobs_sort_updated": "обновлено",
    "jobs_sort_asc": "по возрастанию",
    "jobs_sort_desc": "по убыванию",
    "jobs_sort_current": "Сортировка: {field} {dir}",
    "jobs_pager_label": "Навигация по страницам",
    "jobs_announce_status": "Статус задания {id} изменился: {status} ({time})",
    "jobs_page": "Страница {page} из {pages}",
    "jobs_page_total": "заданий по фильтру: {n}",
    "jobs_prev": "← Назад",
    "jobs_next": "Вперёд →",
    "jobs_col_id": "задание",
    "jobs_col_book": "книга",
    "jobs_col_pair": "пара",
    "jobs_col_mode": "режим",
    "jobs_col_status": "статус",
    "jobs_col_progress": "прогресс (реальные единицы)",
    "jobs_col_alignment": "выравнивание",
    "jobs_col_created": "создано",
    "jobs_col_updated": "обновлено",
    "jobs_empty": "Заданий нет. Загрузите книгу в разделе «Книги» и создайте задание.",
    "jobs_empty_filtered": "Заданий, соответствующих фильтру, нет.",
    "onb_title": "Как начать",
    "onb_step1": "Загрузите EPUB-книгу (раздел «Книги»)",
    "onb_step2": "Выберите пару языков и режим в форме «Создать задание»",
    "onb_step3": "Создайте задание — перевод запустится автоматически",
    "onb_go_books": "Перейти к книгам",
    "jobs_deleted": "Задание удалено.",
    "jobs_logs_cleared": "Логи очищены.",
    "jobs_purge_title": "Массовая очистка (purge)",
    "jobs_purge_hint": (
        "Удаляются только задания в терминальных статусах, обновлённые не "
        "позднее {days} дн. назад (настройка ui.purge_min_age_days). Шаг 1 — "
        "dry-run: перечень job_id, число, объём; шаг 2 — подтверждение, "
        "удаляются ровно перечисленные задания."
    ),
    "jobs_purge_statuses": "Статусы",
    "jobs_purge_book": "Книга",
    "jobs_purge_pair": "Пара языков",
    "jobs_purge_dry_btn": "Показать перечень (dry-run)",
    "jobs_purge_confirm_btn": "Удалить перечисленные",
    "jobs_purge_result": "Перечень: {count} заданий, объём {bytes}.",
    "jobs_purge_irreversible": (
        "Действие необратимо: при подтверждении будут удалены перечисленные "
        "задания вместе с файлами и строками БД."
    ),
    "jobs_purge_ids": "Задания ({n}) — будут удалены при подтверждении",
    "jobs_purge_removed": "Удалено: {count} заданий, объём {bytes}.",
    "jobs_purge_none": "Заданий, удовлетворяющих фильтру, нет.",
    # Серия R §10/§14: строки списков, сегментный фильтр, группировка,
    # 5 состояний (§14), точное время
    "jobs_seg_label": "Статусные группы",
    "jobs_seg_all": "Все",
    "jobs_seg_active": "Активные",
    "jobs_seg_waiting": "Ожидают",
    "jobs_seg_done": "Готовы",
    "jobs_seg_errors": "Ошибки",
    "jobs_stats_label": "Статистика",
    "jobs_stat_run": "В работе",
    "jobs_stat_wait": "В очереди",
    "jobs_stat_done7": "Готово за 7 дней",
    "jobs_group_active": "Активные",
    "jobs_group_queue": "Очередь",
    "jobs_group_done": "Законченные",
    "jobs_worker_band": "Воркер не отвечает или не запущен",
    "jobs_worker_line": "Воркер: {status}",
    "jobs_menu_sort": "Сортировка",
    "jobs_menu_filter": "Точный фильтр",
    "jobs_menu_purge": "Очистить старые",
    "jobs_sheet_filter_title": "Точный фильтр",
    "jobs_sheet_purge_title": "Очистить старые",
    "jobs_empty_line": "Заданий нет.",
    "jobs_empty_btn": "Новое задание",
    "state_not_found": "Ничего не найдено",
    "state_reset_filter": "Сбросить фильтр",
    "state_netbar": "Нет связи с сервером",
    "books_empty_line": "Здесь пока пусто",
    "books_empty_filtered": "Книг, соответствующих фильтру, нет.",
    "row_actions_label": "Действия",
    "time_min": "{n} мин",
    "time_hm": "{h} ч {m} мин",
    # ------------------------------------------------------------- Job (страница)
    "job_row_status": "статус",
    "job_row_pair": "пара",
    "job_row_mode": "режим / уровень",
    "job_row_progress": "прогресс (реальные единицы)",
    "job_row_phase": "фаза",
    "job_row_segments": "сегменты",
    "job_row_alignment": "выравнивание",
    "job_row_tokens": "токены",
    "job_row_time": "время",
    "job_row_errors": "ошибки / повторы",
    "job_row_created": "создано",
    "job_row_updated": "обновлено",
    "job_tokens_total": "всего",
    "job_time_elapsed": "прошло: {n} с",
    "job_time_eta": "осталось (оценка): {n} с",
    # реворк 2026-09-26 (п.9): метка времени в строке списка (место статуса)
    "job_time_left": "осталось ~{n}",
    "job_time_required": "требуется ~{n}",
    "job_paused_since": "на паузе с {t}",
    "job_pause_requested": "пауза запрошена — применится по завершении текущего шага",
    "job_cancel_requested": "отмена запрошена — применится по завершении текущего шага",
    "job_errors_segments": "сегментов с ошибками: {n}",
    "job_errors_retries": "повторов: {n}",
    "job_stages": "история переходов ({n})",
    "job_stages_col_time": "время",
    "job_stages_col_transition": "переход",
    "job_stages_col_reason": "причина",
    "job_warnings": "предупреждения:",
    "job_confirm_label": "Исходный язык",
    "job_confirm_detection": "детекция, уверенность {conf}",
    "job_confirm_btn": "Подтвердить и запустить",
    "job_pause_btn": "Пауза",
    "job_resume_btn": "Продолжить",
    "job_cancel_btn": "Отменить",
    "job_clear_logs_btn": "Очистить логи",
    "job_delete_btn": "Удалить задание",
    "job_clear_logs_confirm": (
        "Очистить job.log и errors.log задания {id}? Текущее содержимое будет "
        "усечено, в job.log запишется строка факта усечения. quality.log "
        "не затрагивается."
    ),
    "job_delete_confirm": (
        "Удалить задание {id}? Будут удалены строки БД (jobs, transitions, "
        "job_tokens, stage_results) и каталог задания с артефактами. "
        "Действие необратимо."
    ),
    "job_download_result": "Скачать результат (main)",
    "job_download_target": "Перевод (target.epub)",
    "job_download_learning": "Учебная книга (learning.epub)",
    "job_download_cards_csv": "Карточки (cards.csv)",
    "job_download_cards_anki": "Карточки Anki (cards.anki.tsv)",
    "job_download_cards_json": "Карточки (cards.json)",
    "job_download_quality": "Отчёт QC (quality.json)",
    "job_result_pending": "Результат появится после завершения задания.",
    "job_details_quality": "Качество (QC, выравнивание, согласованность L2, правки L3)",
    "job_details_logs": "Логи (job / errors / quality)",
    # V3 §7.5: вкладки экрана задания (пилюли).
    "job_tabs_label": "Разделы задания",
    "job_tab_logs": "Логи",
    "job_tab_artifacts": "Артефакты",
    "job_tab_quality": "Качество",
    "job_logs_lines": "Число строк:",
    "job_logs_more": "Докрутить",
    "job_logs_empty": "(пусто)",
    # ------------------------------------------------------------- серия R3:
    # первичные кнопки строки (§11; глиф + aria-label, зона 44×44)
    "job_row_confirm": "Подтвердить",
    "job_row_postpone": "Отложить",
    "job_row_pause": "Пауза",
    "job_row_resume": "Продолжить",
    "job_row_export": "Экспорт",
    "job_row_retry": "Повторить",
    # свайп влево (до 4 кнопок, набор по TRANSITIONS)
    "job_swipe_pause": "Пауза",
    "job_swipe_resume": "Продолжить",
    "job_swipe_cancel": "Отмена",
    "job_swipe_logs": "Логи",
    "job_swipe_delete": "Удалить",
    "job_swipe_queue": "Вперёд очереди",
    # тост (5 с, «Вернуть» — только обратимые операции)
    "toast_cancelled": "Задание отменено",
    "toast_undo": "Вернуть",
    "toast_bulk_result": "применено: {applied} · пропущено: {skipped}",
    "toast_queue_front": "Задание передвинуто в начало очереди",
    # массовые операции: режим выделения (долгое нажатие, JS)
    "jobs_selected": "Выбрано: {n}",
    "jobs_sel_pause": "Пауза",
    "jobs_sel_cancel": "Отмена",
    "jobs_sel_delete": "Удалить",
    "jobs_sel_done": "Готово",
    "jobs_del_title": "Удаление заданий",
    "jobs_del_question": "Удалить {n} заданий?",
    "jobs_del_irreversible": "Действие необратимо.",
    "jobs_del_ids": "Перечень заданий ({n})",
    "jobs_del_confirm_btn": "Удалить",
    "jobs_del_cancel_btn": "Отмена",
    "jobs_del_done": "Удалено: {n}",
    # лист «Создать задание» (R4 — мастер §12)
    "jobs_create_title": "Создать задание",
    "jobs_create_book": "Книга",
    "jobs_create_target": "Целевой язык",
    "jobs_create_mode": "Режим",
    "jobs_create_btn": "Запустить",
    "jobs_create_retry_of": "Повтор задания {id} — параметры исходного задания",
    "jobs_create_books_empty": "Книг нет — сначала добавьте книгу",
    # R4: мастер создания — шаги, строки сводки, «Ещё», чипы
    "create_step_book": "Книга",
    "create_step_params": "Параметры",
    "create_book_search": "Найти книгу",
    "create_book_none": "Выберите книгу",
    "create_change": "изменить",
    "create_row_languages": "Языки",
    "create_row_mode": "Режим",
    "create_row_level": "Уровень",
    "create_row_cefr": "Уровень · Estimated CEFR level",
    "create_row_materials": "Материалы",
    "create_last_time": "Как в прошлый раз",
    "create_default": "По умолчанию",
    "create_materials_translation": "Перевод",
    "create_materials_cards": "Перевод + карточки",
    "create_more": "Ещё",
    "create_more_volume": "Объём материалов",
    "create_more_glossary": "Глоссарий",
    "create_glossary_on": "включён (конфиг)",
    "create_glossary_off": "выключен (конфиг)",
    "create_more_llm": "LLM-профиль",
    "create_llm_mixed": "разные (одна настройка на все стадии)",
    "create_level_none": "Не задан",
    "create_level_none_hint": "Learning выключен — только перевод",
    # R4: подстадии S1–S5 (объём материалов; источник SUBSTAGE_LEVELS)
    "substage_s1": "лексика",
    "substage_s2": "выражения",
    "substage_s3": "грамматика",
    "substage_s4_summary": "summary",
    "substage_s4_questions": "вопросы",
    "substage_s4_exercises": "упражнения",
    "substage_s5": "сборка",
    # R4: чипы «отличается от умолчания» (сводка задания)
    "job_row_param_chips": "Отличается от умолчаний",
    "chip_target": "Языки",
    "chip_mode": "Режим",
    "chip_level": "Объём",
    "chip_cefr": "Уровень",
    # лист «Удалить задание» (одно; книга + кнопка)
    "job_delete_sheet_title": "Удалить задание",
    "job_delete_sheet_line": "Удалить задание «{book}»?",
    "job_delete_sheet_irreversible": (
        "Удалятся задание, все его записи (переходы, токены, результаты "
        "стадий) и каталог с артефактами. Действие необратимо."
    ),
    "job_delete_sheet_btn": "Удалить",
    "job_delete_sheet_cancel": "Отмена",
    # ------------------------------------------------------------- Экспорт (R7, TZ §9)
    # Лист: 4 объекта × 8 форматов, ZIP с осмысленными именами, макеты
    # билингвы (пост-обработка), карточки — отдельная группа (S5).
    "job_export_title": "Экспорт",
    "job_export_section_job": "Задание",
    "job_export_section_objects": "Что экспортировать",
    "job_export_section_formats": "Форматы",
    "job_export_section_layout": "Макет билингвы",
    "job_export_obj_translation": "Перевод",
    "job_export_obj_learning": "Учебная книга",
    "job_export_obj_cards": "Карточки",
    "job_export_obj_reports": "Отчёты",
    "job_export_obj_missing": "нет файла",
    "job_export_fmt_epub": "EPUB",
    "job_export_fmt_pdf": "PDF",
    "job_export_fmt_docx": "DOCX",
    "job_export_fmt_fb2": "FB2",
    "job_export_fmt_azw3": "AZW3",
    "job_export_fmt_txt": "TXT",
    "job_export_fmt_md": "Markdown",
    "job_export_fmt_htmlz": "HTMLZ",
    "job_export_layout_append": "Абзац за абзацем",
    "job_export_layout_only": "Только перевод",
    "job_export_layout_columns": "Две колонки (PDF, HTMLZ)",
    "job_export_btn": "Экспорт",
    "job_export_progress": "Конвертация… {done} из {total}",
    "job_export_files": "Файлы",
    "job_export_book_block": "Файл книги",
    "job_export_book_hint": "Исходный EPUB книги в другом формате.",
    "job_export_no_jobs": "Нет завершённых заданий — доступен экспорт файла книги.",
    "job_export_book_missing": "Книга не найдена",
    "job_export_status_converting": "Конвертация…",
    "job_export_status_done": "Экспорт завершён",
    "job_export_status_error": "Ошибка экспорта",
    "err_export_pending": "Задание ещё не завершено (статус: {status})",
    "err_export_in_progress": "Экспорт уже выполняется",
    "err_export_no_selection": "Выберите хотя бы один объект и один формат",
    "err_export_unknown_object": "Неизвестный объект экспорта: {value}",
    "err_export_unknown_format": "Неизвестный формат: {value}",
    "err_export_unknown_layout": "Неизвестный макет: {value}",
    "err_export_object_missing": "Файл объекта отсутствует: {name}",
    "err_export_layout_needs_two": "«Две колонки» — только для PDF или HTMLZ",
    "err_export_layout_unavailable": "Макет доступен только для билингвы (APPEND_BLOCK)",
    "err_export_book_not_found": "Файл книги не найден: {name}",
    "err_export_internal": "Экспорт не выполнен: {reason}",
    "err_export_not_found": "Экспорт не найден",
    # ------------------------------------------------------------- Качество
    "qc_title": "QC (пункты отчёта)",
    "qc_alignment_title": "Покрытие выравнивания (E6)",
    "qc_alignment_coverage": "покрытие: {pct}% ({aligned} / {total})",
    "qc_alignment_na": "не измерялось",
    "qc_glossary_title": "Согласованность глоссария (L2)",
    "qc_glossary_value": "доля согласованных вхождений: {share}%",
    "qc_glossary_na": "не измерялась",
    "qc_warnings": "Предупреждения",
    "qc_l3_title": "Правки по глоссарию (L3)",
    "qc_l3_stats": (
        "попыток: {attempted} · применено: {applied} · "
        "отклонено: {rejected} · остановлено: {halted}"
    ),
    "qc_l3_none": "правки не выполнялись",
    "qc_l3_col_segment": "сегмент",
    "qc_l3_col_doc": "документ",
    "qc_l3_col_term": "термин",
    "qc_l3_col_applied": "применено",
    "qc_l3_col_old": "было",
    "qc_l3_col_new": "стало",
    "qc_l3_col_reason": "причина",
    # ------------------------------------------------------------- Languages
    "languages_hint": (
        "Статусы пар — из резолвера L0 (та же функция, что используется при "
        "создании задания); подписи CEFR — единая точка cefr.label."
    ),
    "languages_h2": "Языки",
    "languages_col_code": "код",
    "languages_col_name": "название",
    "languages_col_spacy": "spaCy",
    "languages_col_cefr": "CEFR",
    "pairs_h2": "Пары (статусы — резолвер L0)",
    "pairs_col_source": "исходный",
    "pairs_col_target": "целевой",
    "pairs_col_status": "статус",
    "pairs_col_creatable": "задание",
    "pairs_creatable_yes": "можно создавать",
    "pairs_creatable_no": "нет",
    "pairs_col_missing": "не хватает",
    # ------------------------------------------------------------- Models
    "models_hint": (
        "LLM-профили (translation / fill / analysis). API-ключи не "
        "отображаются — только признак «задан»."
    ),
    "models_col_profile": "профиль",
    "models_col_endpoint": "endpoint",
    "models_col_model": "модель",
    "models_col_key": "API-ключ",
    "models_col_timeout": "timeout, с",
    "models_col_retry": "retry",
    "models_col_concurrency": "конкурентность",
    "models_col_cost": "стоимость",
    "models_key_set": "задан",
    "models_key_unset": "не задан",
    "models_cost_set": "заданы",
    "models_cost_unset": "не заданы (Cost: unknown)",
    # ------------------------------------------------------------- Prompts
    "prompts_hint": (
        "Версия промпта входит в идемпотентный ключ этапа "
        "(sha256(input + config + prompt_version + target_language)) и "
        "записывается в resolved_prompts: перезапись существующей версии "
        "запрещена — правка создаёт новую версию v(N+1)."
    ),
    "prompts_warning": (
        "Внимание: новая версия промпта меняет идемпотентный ключ этапа — "
        "новые задания будут переведены заново, завершённые задания не "
        "затрагиваются."
    ),
    "prompts_empty": "Промпты не найдены.",
    "prompts_col_task": "задача",
    "prompts_col_source": "исходный",
    "prompts_col_target": "целевой",
    "prompts_col_level": "уровень",
    "prompts_col_version": "версия",
    "prompts_col_file": "файл",
    "prompts_col_selected": "выберет резолвер",
    "prompts_level_pair": "пара",
    "prompts_level_source": "язык",
    "prompts_level_generic": "generic",
    "prompts_selected_no": "—",
    "prompts_view_btn": "Просмотр",
    "prompts_edit_btn": "Изменить (новая версия)",
    "prompts_delete_btn": "Удалить версию",
    "prompts_delete_confirm": (
        "Удалить {file}? Файл версии будет удалён из prompts_root. "
        "Действие необратимо."
    ),
    "prompts_create_title": "Новый промпт",
    "prompts_task_pair_required": "требуется уровень «пара»",
    "prompts_create_task": "Задача",
    "prompts_create_level": "Уровень",
    "prompts_create_source": "Исходный язык",
    "prompts_create_target": "Целевой язык",
    "prompts_create_yaml": "Содержимое (YAML: system + user_template)",
    "prompts_create_btn": "Создать",
    "prompts_create_btn_v": "Создать v{next}",
    "prompts_edit_title": "Новая версия: {file}",
    "prompts_save_btn": "Сохранить как {next}",
    "prompts_created": "Создана версия {file}.",
    "prompts_deleted": "Версия удалена: {file}.",
    "prompts_content_title": "Содержимое {file} (YAML, побайтово)",
    # ------------------------------------------------------------- Settings
    # серия R §5: корень /settings — маршрутные строки-список
    "settings_row_llm": "LLM и модели",
    "settings_row_languages": "Языки",
    "settings_row_prompts": "Промпты",
    "settings_row_maintenance": "Обслуживание",
    "settings_row_appearance": "Внешний вид",
    "settings_row_about": "О системе",
    "settings_group_models": "Модели",
    "settings_group_translation": "Перевод",
    "settings_group_app": "Приложение",
    "settings_h2_config": "Конфигурация",
    "settings_health_col_component": "компонент",
    "settings_health_col_status": "состояние",
    "common_save": "Сохранить",
    # ------------------------------------------------------------- R8: LLM-профили
    # /settings/llm — форма правки llm.profiles.* (ТЗ §13): ключи
    # редактируются, но НИКОГДА не отображаются (U1-8).
    "llm_hint": (
        "LLM-профили (translation / fill / analysis). API-ключи не "
        "отображаются — только признак «задан». Проба не пишет конфиг "
        "и не создаёт задание."
    ),
    # MTPE TZ §5.3 (точка 2): полоса на страницах при неполных настройках.
    "llm_incomplete_banner": (
        "Настройки LLM неполны для текущего способа перевода — "
        "создание заданий и сохранение будут отклонены, пока не заполнены: "
    ),
    "llm_shared_label": "Все профили используют один эндпоинт",
    "llm_base_url_label": "Эндпоинт (base_url)",
    "llm_base_url_hint": "http(s)://127.0.0.1:8004/v1",
    "llm_model_label": "Модель",
    "llm_key_label": "Ключ API",
    "llm_key_keep_hint": "пусто — оставить текущий",
    "llm_temperature_label": "Температура",
    "llm_retries_label": "Повторы",
    "llm_parallelism_label": "Параллелизм",
    "llm_extra_body_label": "extra_body (JSON)",
    "llm_extra_body_hint": "дополнительные поля тела запроса; пусто — без изменений",
    "llm_extra_reset": "Сброс",
    "llm_more_label": "Ещё",
    "llm_probe_btn": "Проверить",
    "llm_probe_ok": "Ответ {ms} мс · модель {model} · {tokens} токенов",
    "llm_probe_unreachable": "недоступно: {reason}",
    "llm_probe_http_401": "HTTP 401 (неверный ключ)",
    "llm_probe_http_404": "HTTP 404 (эндпоинт не найден)",
    "llm_probe_bad_json": "неверный JSON ответа",
    "llm_probe_http": "HTTP {code}",
    "llm_models_unavailable": "список моделей недоступен: {reason}",
    "llm_save_note": (
        "Новые значения применяются к следующему заданию. Текущее задание "
        "продолжает на стартовом профиле — идемпотентные ключи стадий "
        "включают конфиг."
    ),
    "llm_saved": "Сохранено — применится к следующему заданию.",
    "llm_probe_back": "Назад к LLM-настройкам",
    "llm_profile_title": "Проверка профиля {profile}",
    # ------------------------------------------------------------- MTPE M2 (§6):
    # способ перевода, карточки 5 профилей, поля mt, 409 пробы.
    "llm_pipeline_label": "Способ перевода",
    "llm_pipeline_single": "Одна модель",
    "llm_pipeline_mt_pe": "MT + постредактура",
    "llm_pipeline_desc_single": "Книгу переводит движок epub-translator моделью профиля «Перевод».",
    "llm_pipeline_desc_mt_pe": (
        "Черновик делает MT-модель, правку — модель постредактуры. "
        "Глоссарий и материалы — профиль «Анализ»."
    ),
    "llm_pipeline_pairs_prefix": "Пары: ",
    "llm_pipeline_unused_label": "Не используется в выбранном способе",
    "llm_title_translation": "Перевод",
    "llm_title_fill": "Разметка",
    "llm_title_analysis": "Анализ и материалы",
    "llm_title_mt": "MT-черновик",
    "llm_title_postedit": "Постредактура",
    "llm_key_set": "задан",
    "llm_key_unset": "не задан",
    "llm_api_label": "API",
    "llm_top_k_label": "top_k",
    "llm_max_tokens_ratio_label": "max_tokens_ratio",
    "llm_max_segment_tokens_label": "Максимум токенов сегмента",
    "llm_stop_label": "Стоп-последовательности",
    "llm_timeout_s_label": "Таймаут, с",
    "llm_warmup_label": "Таймаут прогрева, с",
    "llm_prompt_label": "Промпт",
    "llm_prompt_readonly_hint": "read-only — редактируется в файле",
    "llm_prompt_version": "«{task} · версия {version}»",
    "llm_prompts_link": "Промпты",
    "llm_save_incomplete": "Настройки неполны для выбранного способа. Поправьте проблемы:",
    "llm_key_delete_label": "Удалить ключ",
    "llm_probe_waiting": "Ожидание ответа, сервер может загружать модель",
    "llm_probe_model_not_listed": "модель не в списке эндпоинта",
    "llm_probe_model_mismatch": "модель ответа не совпадает с профилем",
    "llm_stage_translation": "перевод",
    "llm_stage_analysis": "анализ",
    "llm_probe_409": (
        "Идёт фаза {phase} задания {job_id} на этом эндпоинте; проба сменила бы модель"
    ),
    # ------------------------------------------------------------- R8: внешний вид
    "app_theme_label": "Тема",
    "app_theme_system": "Системная",
    "app_theme_light": "Светлая",
    "app_theme_dark": "Тёмная",
    "app_density_label": "Плотность списка",
    "app_density_normal": "Обычная",
    "app_density_compact": "Плотная",
    # ------------------------------------------------------------- R8: обслуживание
    "maint_h2_components": "Компоненты",
    "maint_jobs_size": "Размер jobs/",
    "maint_clear_logs_btn": "Очистить логи",
    "maint_clear_logs_done": "Логи очищены: {bytes} Б.",
    "maint_purge_title": "Удалить задания старше N дней",
    "maint_purge_days_label": "Дней",
    "maint_purge_btn": "Удалить",
    "maint_purge_confirm_title": "Подтверждение очистки",
    "maint_purge_confirm_info": "Заданий: {count} · {bytes}",
    "maint_purge_done": "Удалено: {count} · {bytes}",
    "maint_status_epubcheck": "EPUBCheck",
    "maint_status_converter": "Calibre (конвертер)",
    "maint_status_worker": "Воркер",
    "maint_status_llm": "LLM",
    "maint_config_hint": "Чтение только: правится через файлы конфигурации.",
    # ------------------------------------------------------------- R8: о системе
    "about_version": "Версия",
    "about_commit": "Коммит",
    "about_unknown": "неизвестно",
    "about_readme": "README",
    "about_readme_missing": "README.md не найден в корне проекта.",
    "about_readme_to_settings": "К настройкам",
    "about_logout_btn": "Выйти",
    # ------------------------------------------------------------- R8: ошибки
    "err_llm_bad_base_url": (
        "некорректный эндпоинт: {url} (схема http/https, путь до /v1)"
    ),
    "err_llm_bad_model": "модель: непустая строка",
    "err_llm_bad_temperature": "температура: число 0–2",
    "err_llm_bad_retries": "повторы: целое ≥ 0",
    "err_llm_bad_concurrency": "параллелизм: целое ≥ 1",
    "err_llm_bad_timeout": "таймаут: число > 0",
    "err_llm_bad_top_k": "top_k: целое ≥ 1",
    "err_llm_bad_max_tokens_ratio": "max_tokens_ratio: число ≥ 0",
    "err_llm_bad_max_segment_tokens": "max_segment_tokens: целое ≥ 1",
    "err_llm_bad_pipeline": "неизвестный способ перевода",
    "err_llm_bad_extra_body": "extra_body: JSON-объект ({reason})",
    "err_llm_unknown_profile": "неизвестный профиль: {profile}",
    "err_llm_key_conflict": (
        "один ключ API для translation и fill (LLM_API_KEY) — значения различаются"
    ),
    "err_llm_config_invalid": "новая конфигурация не валидирована: {reason}",
    "err_llm_paths_missing": "пути конфигурации не заданы",
    "err_purge_bad_days": "дни: число ≥ 0",
    # ------------------------------------------------------------- MTPE: единая
    # проверка настроек LLM (TZ §5.3, validate_llm_settings). Коды V1–V10 +
    # коды отказа создания задания. Текст — по коду; path — в SettingsIssue.
    "validate_pipeline_unknown": (
        "translation.pipeline: допустимы single | mt_pe (получено: {value})"
    ),
    "validate_base_url_empty": "эндпоинт (base_url) не задан",
    "validate_base_url_format": "эндпоинт: http(s)://… с путём, оканчивающимся на /v1",
    "validate_model_empty": "модель: непустое имя без пробельных символов",
    "validate_api_fixed": (
        "api: профиль mt — completions, остальные — chat (получено: {value})"
    ),
    "validate_prompt_missing": (
        "задача промпта {task} отсутствует или не имеет активной версии"
    ),
    "validate_mt_template_placeholders": (
        "mt_draft: активная версия содержит {src_name}, {tgt_name}, {text} — "
        "без других плейсхолдеров"
    ),
    "validate_out_of_limits": "значение вне допустимых границ",
    "validate_extra_body_invalid": "extra_body: ожидается JSON-объект",
    "validate_no_mt_pairs": (
        "нет пар со статусом supported для mt_pe: задайте mt_name у обоих языков"
    ),
    "pipeline_in_settings_only": (
        "способ перевода (pipeline) задаётся только на экране «LLM и модели», "
        "не в создании задания"
    ),
    "pair_unsupported_mt_pe": "пара {pair}: mt_pe недоступен (нет mt_name у обоих языков)",
    "logs_cleared_fact_global": (
        "{time}: логи очищены пользователем {user}: app.log — {bytes} Б"
    ),
    # ------------------------------------------------------------- health (покомпонентные детали)
    "health_read_write_mismatch": "чтение/запись не совпала",
    "health_na_target": "n/a (целевой язык)",
    "health_no_pipeline": "нет пайплайна {model}",
    "health_jar_missing": "jar не найден: {jar}",
    "health_java_missing": "java не найдена в PATH",
    # ------------------------------------------------------------- Not found
    "nf_body": "Страница или ресурс не найдены.",
    "nf_to_jobs": "К списку заданий",
    # ------------------------------------------------------------- прогресс (views.progress_facts)
    "progress_awaiting": "ожидание валидации (validation.json ещё не создан)",
    "progress_no_phases": "фазы перевода не обнаружены (книга пуста?)",
    "progress_all_finished": "все фазы перевода завершены (сегментов {total} / {total})",
    "progress_chapter": "глава {no} / {count}, сегментов {processed} / {total}",
    "progress_phase": "фаза: {label} (сегментов {processed} / {total})",
    # ------------------------------------------------------------- ошибки API (verbatim-причины)
    "auth_required_message": "требуется общий секрет (SERVER_AUTH_SECRET)",
    "auth_title": "Требуется доступ",
    "auth_body": (
        "Интерфейс защищён общим секретом. Введите его, чтобы продолжить — "
        "секрет задаётся переменной SERVER_AUTH_SECRET."
    ),
    "auth_label": "Общий секрет",
    "auth_btn": "Войти",
    "auth_brand": "EpubTranslator",
    "auth_wrong": "Секрет неверен. Попробуйте ещё раз.",
    "forbidden_title": "Доступ запрещён",
    "forbidden_back": "Вернуться к заданиям",
    "err_job_not_found": "задание не найдено: {id}",
    "err_book_not_found": "книга не найдена: {filename}",
    # Серия R §8 (R6): импорт — жёсткие отказы с точной причиной (T8,
    # без трейса)
    "err_upload_too_big": "файл больше {mb} МБ (limits.upload_max_mb)",
    "err_format_unsupported": (
        "формат .{ext} не поддерживается; поддерживаются: {formats}"
    ),
    "err_comics": "комиксы (.cbz/.cbr) не поддерживаются",
    "err_zip_multi": "ZIP-архив нескольких книг",
    "err_zip_no_book": "ZIP не содержит книгу",
    "err_zip_bad": "не является корректным ZIP-архивом",
    "err_audiobook": "аудиокниги не поддерживаются",
    "err_drm": "файл защищён DRM",
    "err_scan_pdf": "сканерный PDF (нет текстового слоя); OCR не выполняется",
    "err_converter_unavailable": "конвертация недоступна: не установлен calibre",
    "err_convert_failed": "конвертация не выполнена: {reason}",
    "err_import_epubcheck": "EPUBCheck: {n} ошибок: {first}",
    "err_import_scan": "определение языка не выполнено: {reason}",
    "err_import_internal": "импорт не выполнен: {reason}",
    "err_import_not_found": "импорт не найден: {id}",
    "err_book_required": "не выбран файл книги (book)",
    "err_result_pending": "результат не готов (статус: {status})",
    "err_artifact_not_found": "файл не найден: {name}",
    "err_job_not_terminal": (
        "задание не в терминальном статусе: {status}; сначала отмените его"
    ),
    "err_job_dir_unsafe": (
        "отказ: каталог задания вне storage.root — удаление не выполнялось"
    ),
    "err_mutation_requires_secret": (
        "мутирующие операции требуют SERVER_AUTH_SECRET"
    ),
    "err_csrf": "CSRF-токен отсутствует или недействителен",
    "err_book_referenced": "книгу удалять нельзя — на неё ссылаются задания: {ids}",
    # Серия R §7: метаданные и каскад удаления книги
    "err_book_bad_language": "неизвестный код языка: {code}",
    "err_book_bad_cefr": "неизвестный уровень CEFR: {code}",
    "err_book_bad_field": "неизвестное поле метаданных: {field}",
    "err_book_meta_too_long": "поле {field} длиннее {limit} символов",
    "err_book_meta_empty": "нет полей для правки (название, автор, языки, CEFR, заметка)",
    "err_book_purge_empty": "на книгу не ссылаются задания",
    "err_book_purge_nonterminal": (
        "на книгу ссылаются задания, не в терминальном статусе: {ids} — "
        "сначала отмените их"
    ),
    "err_book_purge_token": (
        "confirm_token недействителен или состав заданий изменился — "
        "повторите dry-run"
    ),
    "err_purge_no_match": (
        "нет заданий, соответствующих фильтру (только терминальные, "
        "старше {days} дн.)"
    ),
    "err_purge_token": (
        "confirm_token недействителен или состав заданий изменился — "
        "повторите dry-run"
    ),
    "err_purge_bad_status": "в purge только терминальные статусы: {status}",
    # Серия R §11: массовые операции и очередь
    "err_bulk_bad_action": "неизвестная массовая операция: {action}",
    "err_bulk_no_ids": "массовая операция: не указаны задания (ids)",
    "err_bulk_not_terminal": (
        "массовое удаление: задания не в терминальном статусе: {ids}"
    ),
    "err_bulk_token": (
        "confirm_token недействителен или состав заданий изменился — "
        "начните с dry-run"
    ),
    "bulk_skip_terminal": "недоступно в терминальном статусе: {status}",
    "err_job_patch_fields": "единственное изменяемое поле задания — queue_rank",
    "err_queue_rank_bad": "queue_rank: целое число не меньше 0",
    "err_sort_field": "сортировка: допустимы created_at | updated_at",
    "err_status_unknown": "неизвестный статус фильтра: {status}",
    "err_prompt_task_unknown": "задача не найдена в реестре: {task}",
    "err_prompt_pair_required": (
        "задача {task} требует промпт уровня «пара» (pair_required) — "
        "уровень {level} недопустим"
    ),
    "err_prompt_validation": "промпт не прошёл валидацию: {reason}",
    "err_prompt_last_version": (
        "нельзя удалить последнюю версию уровня {level}: задача {task} "
        "перестанет разрешаться"
    ),
    "err_prompt_not_found": "файл промпта не найден: {path}",
    "err_prompt_unsafe_path": "отказ: путь промпта вне prompts_root",
    "err_prompt_exists": "версия уже существует: {path}",
    "err_prompt_bad_level": "недопустимый уровень: {level} (допустимы: pair, source, generic)",
    "err_prompt_missing_lang": "для уровня {level} не задан язык: {missing}",
    "err_prompt_bad_lang": "недопустимый код языка: {code}",
    # ------------------------------------------------------------- факт усечения логов
    "logs_cleared_fact": (
        "{time} логи очищены {user}: job.log {job_bytes} Б, "
        "errors.log {err_bytes} Б"
    ),
    # ------------------------------------------------------- V1 §6: dev-kit (S14)
    "title_dev_kit": "Комплект компонентов",
    "kit_sec_buttons": "Кнопки",
    "kit_btn_primary": "Основная",
    "kit_btn_secondary": "Второстепенная",
    "kit_btn_text": "Текстовая",
    "kit_btn_danger": "Опасная",
    "kit_btn_tertiary": "Третичная",
    "kit_sec_fab": "FAB",
    "kit_fab_label": "Создать",
    "kit_sec_chips": "Чипы",
    "kit_sec_status_chips": "Статусы",
    "kit_st_wait": "Ожидание",
    "kit_st_run": "Выполняется",
    "kit_st_ok": "Успешно",
    "kit_st_warn": "Предупреждение",
    "kit_st_err": "Ошибка",
    "kit_sec_banners": "Сообщения",
    "kit_banner_ok": "История успешно сгенерирована",
    "kit_banner_warn": "Проверьте выделенные слова",
    "kit_sec_fields": "Поля",
    "kit_field_label": "Название",
    "kit_field_ph": "Книга или папка",
    "kit_sec_switches": "Переключатели",
    "kit_switch_a": "Светлая тема",
    "kit_switch_b": "Компактный режим",
    "kit_sec_icons": "Иконки",
    "kit_sec_swatches": "Токены",
    "kit_sec_formats": "Форматы",
    "kit_fmt_sub_epub": "книги",
    "kit_fmt_sub_docx": "документы",
    "kit_fmt_sub_pdf": "страницы",
    "kit_fmt_sub_md": "заметки",
    "kit_fmt_sub_txt": "текст",
    "kit_fmt_sub_fb2": "литература",
    "kit_fmt_sub_azw3": "Kindle",
    "kit_fmt_sub_htmlz": "сайты",
    "kit_sec_progress": "Прогресс",
    "kit_prog_run": "Перевод",
    "kit_prog_ok": "Готово",
    "kit_sec_glist": "Список",
    "kit_sec_status": "Статус (текст)",
    "kit_glist_a": "Тема",
    "kit_glist_b": "Плотность",
    "kit_glist_c": "Уведомления",
}
