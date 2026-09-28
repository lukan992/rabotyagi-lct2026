"""Справочник сервисов аналитики (GET /v1/catalog): версия, классы техники и виды работ.

В запросе stage_id — вид работ из справочника, а не наш id работы, а класс техники — код справочника. Работы плана
сопоставляет человек в редакторе плана (по похожему названию нельзя — раздел 6 контракта). Технику — по явной таблице
соответствия CLASS_TABLE (раздел 5), а для других справочников — по кодам и синонимам (aliases) или SK_ANALYTICS_CLASSES.
"""

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings

log = logging.getLogger("stroykontrol.analytics")

CATALOG_SCHEMA = "frame-analysis-catalog-v1"
# вид объекта по справочнику; остальные наши виды (соцобъект без уточнения, промышленный, другое) — «неизвестен»
OBJECT_TYPES = ("housing", "education", "healthcare", "sports", "culture", "administrative", "preschool", "office", "roads")
FRESH_S = 600.0  # справочник версионирован и меняется редко — спрашиваем не чаще раза в 10 минут
# Пункт плана — работа справочника: concrete (видна по технике) или no_class (без техники: геодезия, отселение — по ней
# сервисы следят только за сроками и отметками, по кадрам не определяют; так договорились с коллегой 26.09).
# Сводные этапы (summary) в план не идут — у нас это этапы плана уровня 1, группировки работ
PLAN_KINDS = ("concrete", "no_class")
KIND_NAMES = {"summary": "сводный этап справочника (у нас это этап плана, а не работа)"}

# Явная таблица соответствия (раздел 5 контракта): наш тип техники → код класса в справочнике коллеги (0.2.0: коды
# вида DumpTruck, синонимов почти нет). Код берётся, только если он есть в справочнике. Грузовика (truck) в справочнике
# нет — такую технику не отправляем, а в запросе остаётся пометка
CLASS_TABLE = {
    "excavator": "Excavator",
    "dump_truck": "DumpTruck",
    "roller": "RoadRoller",
    "manipulator": "TruckMountedCrane",
    "mixer": "ConcreteMixerTruck",
    "bulldozer": "Bulldozer",
    "crane": "MobileCrane",
}


class CatalogError(Exception):
    """Справочника нет: сервисы не подключены, недоступны или ответили не по формату."""


@dataclass(frozen=True)
class CatalogWork:
    stage_id: int
    name: str
    path: tuple[str, ...]
    kind: str  # concrete | summary | no_class
    object_types: frozenset[str]


@dataclass(frozen=True)
class Catalog:
    version: str
    classes: dict[str, str]  # код класса → название
    aliases: dict[str, str]  # код или синоним (нормализованный) → код; синонимы нескольких классов не участвуют
    works: dict[int, CatalogWork]

    def class_code(self, equipment_type: str) -> str | None:
        """Наш тип техники → код класса справочника; None — такого класса в справочнике нет."""
        overrides = get_settings().analytics_classes
        if equipment_type in overrides:
            code = overrides[equipment_type]
            return code if code in self.classes else None
        if CLASS_TABLE.get(equipment_type) in self.classes:
            return CLASS_TABLE[equipment_type]
        return self.aliases.get(_norm(equipment_type))

    def step_problem(self, stage_id: int, object_type: str | None) -> str | None:
        """Почему этот вид работ не может быть пунктом плана (раздел 6); None — может."""
        work = self.works.get(stage_id)
        if work is None:
            return f"вида работ {stage_id} нет в справочнике версии {self.version}"
        if work.kind not in PLAN_KINDS:
            return f"«{work.name}» — {KIND_NAMES.get(work.kind, work.kind)}, а пунктом плана может быть только работа"
        if object_type and work.object_types and object_type not in work.object_types:
            return f"«{work.name}» по справочнику не относится к такому виду объекта"
        return None

    def works_for(self, object_type: str | None) -> list[CatalogWork]:
        """Работы, которые можно выбрать для объекта этого вида (и без техники) — в порядке справочника."""
        return [
            w
            for w in self.works.values()
            if w.kind in PLAN_KINDS and (not object_type or not w.object_types or object_type in w.object_types)
        ]


def object_type(site_kind: str) -> str | None:
    return site_kind if site_kind in OBJECT_TYPES else None


def _norm(name: str) -> str:
    """«Dump truck», «dump-truck», «DUMP_TRUCK» → «dump_truck»."""
    return re.sub(r"[\s\-_]+", "_", name.strip().lower())


def _path(value: Any) -> tuple[str, ...]:
    """stage_path — строка «Раздел / Подраздел / Работа» (так у сервиса коллеги) или список разделов."""
    parts = value.split(" / ") if isinstance(value, str) else value if isinstance(value, list) else []
    return tuple(str(p).strip() for p in parts if str(p).strip())


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"в справочнике {what} — не строка")
    return value.strip()


def parse_catalog(payload: Any) -> Catalog:
    """Ответ GET /v1/catalog → Catalog. Не по формату — CatalogError с объяснением."""
    if not isinstance(payload, dict) or payload.get("schema_version") != CATALOG_SCHEMA:
        raise CatalogError(f"ответ не похож на справочник: нужен schema_version = {CATALOG_SCHEMA}")
    version = _text(payload.get("catalog_version"), "catalog_version")
    classes, aliases, taken = {}, {}, {}
    for item in payload.get("equipment_classes") or []:
        code = _text(item.get("code") if isinstance(item, dict) else None, "код класса техники")
        classes[code] = str(item.get("name_ru") or code)
        for name in {code, *(a for a in item.get("aliases") or [] if isinstance(a, str) and a.strip())}:
            key = _norm(name)
            taken.setdefault(key, set()).add(code)
    for key, codes in taken.items():
        if len(codes) == 1:  # синоним у двух классов — неоднозначен, по нему не сопоставляем
            aliases[key] = next(iter(codes))
    works = {}
    for item in payload.get("work_stages") or []:
        if not isinstance(item, dict) or isinstance(item.get("stage_id"), bool) or not isinstance(item.get("stage_id"), int):
            raise CatalogError("в справочнике вид работ без целого stage_id")
        path = _path(item.get("stage_path"))
        works[item["stage_id"]] = CatalogWork(
            stage_id=item["stage_id"],
            name=_text(item.get("name_ru"), f"название вида работ {item['stage_id']}"),
            path=path,
            kind=str(item.get("stage_kind") or "concrete"),
            object_types=frozenset(str(t) for t in item.get("object_type_codes") or []),
        )
    if not classes or not works:
        raise CatalogError("справочник пуст: нет классов техники или видов работ")
    return Catalog(version=version, classes=classes, aliases=aliases, works=works)


class CatalogCache:
    """Справочник в памяти (обновляется раз в FRESH_S) и копией в файле: редактор плана работает, даже если сервисы
    сейчас не отвечают, а сервер перезапустили."""

    def __init__(self, fetch: Callable[[], Awaitable[Any]], path: Path | None) -> None:
        self._fetch, self._path = fetch, path
        self._catalog: Catalog | None = None
        self._fetched = -float("inf")
        self.error: str | None = None  # почему последний запрос справочника не удался

    def peek(self) -> Catalog | None:
        """Что есть без запроса к сервису: в памяти или сохранённое."""
        if self._catalog is None:
            self._catalog = self._load()
        return self._catalog

    async def get(self, *, fresh_s: float = FRESH_S) -> Catalog:
        if self._catalog is not None and time.monotonic() - self._fetched < fresh_s:
            return self._catalog
        try:
            payload = await self._fetch()
            catalog = parse_catalog(payload)
        except CatalogError as exc:
            self.error = str(exc)
            if cached := self.peek():
                log.warning("Справочник аналитики не обновился, работаем с сохранённым (%s): %s", cached.version, exc)
                return cached
            raise
        if self._catalog is None or self._catalog.version != catalog.version:
            log.info("Справочник аналитики: версия %s, видов работ %d", catalog.version, len(catalog.works))
            self._save(payload)
        self._catalog, self._fetched, self.error = catalog, time.monotonic(), None
        return catalog

    def _load(self) -> Catalog | None:
        if self._path is None:
            return None
        try:
            return parse_catalog(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError, CatalogError):
            return None

    def _save(self, payload: Any) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("Не удалось сохранить справочник аналитики: %s", exc)
