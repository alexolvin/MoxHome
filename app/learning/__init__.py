# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Learning-материалы (TЗ_03, S1–S5): отбор, генерация, валидация, выпуск.

Материалы строятся ТОЛЬКО по выровненному подмножеству E6 и ТОЛЬКО по тексту
перевода ПОСЛЕ L3 (стадия LEARNING следует за GLOSSARY_FIX). Каждый артефакт
привязан к источнику (chapter_id/segment_id/source_text_hash) и к версии
(промпт/профиль/модель/пара/уровень пользователя/хэш глоссария).
"""

from collections.abc import Mapping

#: ТЗ_03 таблица уровней: какой уровень включает какую подстадию.
#: Minimal — только оригинал+перевод (learning-материалов нет);
#: Balanced — + ключевая лексика (S1); Learning — + выражения (S2),
#: грамматические заметки (S3), вопросы на понимание (S4); Intensive — +
#: упражнения (S4) и summary (S4). Сборка учебной книги (S5) — на всех
#: уровнях: состав секций определяет наличие артефактов вышестоящих стадий
#: (T-S5-10: у Minimal в книге нет секций лексики/грамматики/вопросов).
#: Подстадия, не входящая в уровень, не исполняется и артефактов не
#: выпускает (стадия существует — как no-op).
SUBSTAGE_LEVELS: Mapping[str, frozenset[str]] = {
    "s1": frozenset({"Balanced", "Learning", "Intensive"}),
    "s2": frozenset({"Learning", "Intensive"}),
    "s3": frozenset({"Learning", "Intensive"}),
    "s4_summary": frozenset({"Intensive"}),
    "s4_questions": frozenset({"Learning", "Intensive"}),
    "s4_exercises": frozenset({"Intensive"}),
    "s5": frozenset({"Minimal", "Balanced", "Learning", "Intensive"}),
}
