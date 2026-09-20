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
    "books_upload_label": "EPUB-файл",
    "books_upload_btn": "Загрузить",
    "books_upload_hint": (
        "Определение языка при загрузке — тот же механизм L0, что и стадия "
        "validating (детектор + dc:language); уверенность показана явно."
    ),
    "books_empty": "Книг нет. Загрузите EPUB — это первый шаг к переводу.",
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
    "jobs_create_btn": "Создать задание",
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
    "job_logs_lines": "Число строк:",
    "job_logs_more": "Докрутить",
    "job_logs_empty": "(пусто)",
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
    "settings_h2_health": "Компоненты (health)",
    "settings_h2_config": "Конфигурация",
    "settings_hint": (
        "Секреты (API-ключи, общий секрет доступа) не отображаются — "
        "только признак «задан»."
    ),
    "settings_health_col_component": "компонент",
    "settings_health_col_status": "состояние",
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
    "auth_wrong": "Секрет неверен. Попробуйте ещё раз.",
    "forbidden_title": "Доступ запрещён",
    "forbidden_back": "Вернуться к заданиям",
    "err_job_not_found": "задание не найдено: {id}",
    "err_book_not_found": "книга не найдена: {filename}",
    "err_expected_epub": "ожидается файл .epub",
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
    "err_purge_no_match": (
        "нет заданий, соответствующих фильтру (только терминальные, "
        "старше {days} дн.)"
    ),
    "err_purge_token": (
        "confirm_token недействителен или состав заданий изменился — "
        "повторите dry-run"
    ),
    "err_purge_bad_status": "в purge только терминальные статусы: {status}",
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
}
