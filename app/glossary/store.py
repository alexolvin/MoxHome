"""Хранилище данных книги (Л2.3/L2.7/L2.8): глоссарий, стилевой профиль,
расхождения — в `books/_book_store/<book_key>/<source_hash>/` под storage.root
(D81: зарезервированное поддерево — исходная книга может лежать в `books/`
самой под именем `<book_key>`, и каталог store с тем же именем столкнулся бы
с файлом: `mkdir(exist_ok=True)` не гасит FileExistsError, если путь — файл).

Привязка к версии книги (Л2.8): ключ версии — sha256 исходного файла
(`Job.input_sha256`). Изменение исходного файла (другой хэш) — новая версия:
старые версии помечаются `superseded` (относятся к предыдущей версии) и НЕ
применяются молча — загрузка глоссария идёт ТОЛЬКО по точному хэшу
текущей версии книги (T-L2-9).

`book_key` — имя исходного файла (basename `Job.source_path`): тот же файл
с новым содержимым — та же книга, новая версия. (Коллизия имён разных книг —
документированное ограничение MVP: каталоги не учитываются.)

Формат файлов — YAML (глоссарий/стиль) и JSON (расхождения, отчёт L2.6 —
`ConsistencyReport.to_dict`). Запись — атомарная (`.partial` → `Path.replace`),
как и у выхода тракт T (E5.6: один механизм, Rule 8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.domain.glossary import (
    Glossary,
    GlossaryTerm,
    Paradigm,
    StyleProfile,
)

VERSION_CURRENT = "current"
VERSION_SUPERSEDED = "superseded"


def book_key_of(source_path: str | Path) -> str:
    """Ключ книги — имя исходного файла (L2.8: версия = хэш, книга = имя)."""
    return Path(source_path).name


@dataclass(frozen=True)
class BookVersion:
    hash: str
    status: str  # current | superseded


class BookStore:
    #: Зарезервированное поддерево store внутри `books/` (D81): имя не может
    #: быть именем книги-файла без риска столкновения с данными store.
    _STORE_SUBDIR = "_book_store"

    def __init__(self, root: Path) -> None:
        self._root = root / self._STORE_SUBDIR

    # ------------------------------------------------------------------ версии

    def book_dir(self, book_key: str) -> Path:
        return self._root / book_key

    def meta_path(self, book_key: str) -> Path:
        return self.book_dir(book_key) / "meta.yaml"

    def _read_meta(self, book_key: str) -> dict[str, Any]:
        path = self.meta_path(book_key)
        if not path.is_file():
            return {"name": book_key, "versions": []}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
            raise ValueError(f"meta.yaml повреждён: {path}")
        return data

    def register_version(self, book_key: str, source_hash: str) -> BookVersion:
        """Зарегистрировать версию книги (L2.8): новая/текущая — `current`,
        все остальные — `superseded` (помечены как предыдущая версия)."""
        meta = self._read_meta(book_key)
        versions: list[dict[str, Any]] = []
        for entry in meta["versions"]:
            status = (
                VERSION_CURRENT if entry["hash"] == source_hash else VERSION_SUPERSEDED
            )
            versions.append({**entry, "status": status})
        if not any(entry["hash"] == source_hash for entry in versions):
            versions.append({"hash": source_hash, "status": VERSION_CURRENT})
        self._write_meta(book_key, meta["name"], versions)
        return BookVersion(hash=source_hash, status=VERSION_CURRENT)

    def versions(self, book_key: str) -> tuple[BookVersion, ...]:
        meta = self._read_meta(book_key)
        return tuple(
            BookVersion(hash=str(e["hash"]), status=str(e.get("status", VERSION_CURRENT)))
            for e in meta["versions"]
        )

    def is_current(self, book_key: str, source_hash: str) -> bool:
        for version in self.versions(book_key):
            if version.hash == source_hash:
                return version.status == VERSION_CURRENT
        return False

    def _write_meta(self, book_key: str, name: str, versions: list[dict[str, Any]]) -> None:
        _write_yaml_atomic(self.meta_path(book_key), {"name": name, "versions": versions})

    # ------------------------------------------------------------------ файлы

    def version_dir(self, book_key: str, source_hash: str) -> Path:
        return self.book_dir(book_key) / source_hash

    def glossary_path(self, book_key: str, source_hash: str) -> Path:
        return self.version_dir(book_key, source_hash) / "glossary.yaml"

    def style_path(self, book_key: str, source_hash: str) -> Path:
        return self.version_dir(book_key, source_hash) / "style.yaml"

    def mismatches_path(self, book_key: str, source_hash: str) -> Path:
        return self.version_dir(book_key, source_hash) / "mismatches.json"

    # ------------------------------------------------------------------ глоссарий

    def load_glossary(self, book_key: str, source_hash: str) -> Glossary | None:
        """Глоссарий ТОЛЬКО точного хэша (L2.8): версия, помеченная
        `superseded`, не применяется молча (T-L2-9)."""
        path = self.glossary_path(book_key, source_hash)
        if not path.is_file():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return glossary_from_dict(data)

    def save_glossary(
        self, book_key: str, source_hash: str, glossary: Glossary
    ) -> None:
        _write_yaml_atomic(
            self.glossary_path(book_key, source_hash), glossary_to_dict(glossary)
        )

    # ------------------------------------------------------------------ стиль

    def load_style(self, book_key: str, source_hash: str) -> StyleProfile | None:
        path = self.style_path(book_key, source_hash)
        if not path.is_file():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("style"):
            return None
        return StyleProfile(
            style=str(data["style"]),
            region=data.get("region"),
            note=data.get("note"),
        )

    def save_style(self, book_key: str, source_hash: str, style: StyleProfile) -> None:
        _write_yaml_atomic(
            self.style_path(book_key, source_hash),
            {
                "style": style.style,
                "region": style.region,
                "note": style.note,
            },
        )

    # ------------------------------------------------------------------ расхождения

    def save_mismatches(
        self, book_key: str, source_hash: str, report_dict: dict[str, Any]
    ) -> None:
        path = self.mismatches_path(book_key, source_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        partial.write_text(
            json.dumps(report_dict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        partial.replace(path)

    def load_mismatches(self, book_key: str, source_hash: str) -> dict[str, Any] | None:
        path = self.mismatches_path(book_key, source_hash)
        if not path.is_file():
            return None
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------- сериализация


def _write_yaml_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    partial.replace(path)


def _paradigm_to_dict(paradigm: Paradigm | None) -> dict[str, Any] | None:
    if paradigm is None:
        return None
    return {
        "inflected": paradigm.inflected,
        "form_names": list(paradigm.form_names),
        "forms": list(paradigm.forms),
        "coincidences": dict(paradigm.coincidences),
    }


def _paradigm_from_dict(data: dict[str, Any] | None) -> Paradigm | None:
    if data is None:
        return None
    return Paradigm(
        inflected=bool(data["inflected"]),
        form_names=tuple(data.get("form_names") or ()),
        forms=tuple(data.get("forms") or ()),
        coincidences=tuple((str(k), str(v)) for k, v in (data.get("coincidences") or {}).items()),
    )


def glossary_to_dict(glossary: Glossary) -> dict[str, Any]:
    return {
        "source_language": glossary.source_language,
        "target_language": glossary.target_language,
        "source_hash": glossary.source_hash,
        "terms": [
            {
                "term": term.term,
                "source_forms": list(term.source_forms),
                "translation": term.translation,
                "pos": term.pos,
                "first_segment_id": term.first_segment_id,
                "occurrences": term.occurrences,
                "note": term.note,
                "user_confirmed": term.user_confirmed,
                "paradigm": _paradigm_to_dict(term.paradigm),
                "paradigm_status": term.paradigm_status,
                "paradigm_reason": term.paradigm_reason,
            }
            for term in glossary.terms
        ],
    }


def glossary_from_dict(data: dict[str, Any]) -> Glossary:
    terms: list[GlossaryTerm] = []
    for raw in data.get("terms") or ():
        terms.append(
            GlossaryTerm(
                term=str(raw["term"]),
                source_forms=tuple(raw.get("source_forms") or (str(raw["term"]),)),
                translation=str(raw["translation"]),
                pos=str(raw["pos"]),
                first_segment_id=str(raw["first_segment_id"]),
                occurrences=int(raw["occurrences"]),
                note=raw.get("note"),
                user_confirmed=bool(raw.get("user_confirmed", False)),
                paradigm=_paradigm_from_dict(raw.get("paradigm")),
                paradigm_status=str(raw.get("paradigm_status", "not_applicable")),
                paradigm_reason=raw.get("paradigm_reason"),
            )
        )
    return Glossary(
        source_language=str(data["source_language"]),
        target_language=str(data["target_language"]),
        terms=tuple(terms),
        source_hash=str(data.get("source_hash") or ""),
    )
