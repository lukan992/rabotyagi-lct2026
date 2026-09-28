"""Календарный план из Excel (.xlsx) или CSV: шаблон для заполнения и разбор заполненного файла.

Строка файла — работа плана: «Этап» (укрупнённый), «Работа», «Начало», «Окончание», «Правило «этап → техника»»,
«Вид работ по справочнику». Пустой «Этап» — тот же, что строкой выше: так читаются и объединённые ячейки.
Строка с этапом без работы задаёт даты самого этапа; без неё этап длится от начала первой своей работы до конца последней.
Правило можно не указывать: подойдёт правило с таким же названием, как у работы.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.datavalidation import DataValidation

from app.models import Rule
from app.services.analytics.catalog import Catalog

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
PLAN_SHEET = "План"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# колонка → как она может называться в файле (без регистра, «ё» = «е», лишние пробелы и кавычки не важны)
COLUMNS = {
    "phase": ("этап", "укрупненный этап", "этап работ", "раздел"),
    "work": ("работа", "работы", "наименование работы", "вид работы", "наименование"),
    "start": ("начало", "дата начала", "начало работ", "с"),
    "end": ("окончание", "дата окончания", "конец", "окончание работ", "по"),
    "rule": ("правило этап техника", "правило", "правило этап → техника"),
    "catalog": ("вид работ по справочнику", "вид работ справочник", "вид работ (справочник)", "id вида работ", "справочник"),
}
TITLES = {
    "phase": "Этап",
    "work": "Работа",
    "start": "Начало",
    "end": "Окончание",
    "rule": "Правило «этап → техника»",
    "catalog": "Вид работ по справочнику",
}
DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y", "%d-%m-%Y")
EXCEL_EPOCH = date(1899, 12, 30)


class PlanFileError(Exception):
    """Файл целиком не годится: не читается, нет нужных колонок, слишком большой."""


@dataclass
class PlanRow:
    line: int  # номер строки в файле — чтобы найти её в Excel
    phase: str
    work: str | None  # None — строка задаёт даты самого этапа
    start: date | None
    end: date | None
    rule_key: str | None = None
    catalog_stage_id: int | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PlanPhase:
    name: str
    line: int
    start: date | None = None
    end: date | None = None
    works: list[PlanRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _key(text: object) -> str:
    return re.sub(r"[\s«»\"'“”]+", " ", str(text or "").casefold().replace("ё", "е")).strip()


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


# ---------- чтение файла ----------
def read_table(filename: str, content: bytes) -> list[tuple[int, list[object]]]:
    """Строки таблицы с их номерами в файле: лист «План» (или первый) книги Excel или CSV."""
    if len(content) > MAX_BYTES:
        raise PlanFileError("Файл больше 2 МБ — в плане столько строк не бывает; проверьте, тот ли это файл")
    name = filename.lower()
    if name.endswith(".xls") or content[:4] == b"\xd0\xcf\x11\xe0":
        raise PlanFileError("Это файл старого Excel (.xls) — сохраните его как .xlsx («Книга Excel») или .csv")
    if name.endswith((".xlsx", ".xlsm")) or content[:2] == b"PK":
        try:
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (InvalidFileException, KeyError, OSError, ValueError) as exc:
            raise PlanFileError(f"Файл Excel не читается: {exc}") from None
        sheet = book[PLAN_SHEET] if PLAN_SHEET in book.sheetnames else book.worksheets[0]
        rows = [(n, list(row)) for n, row in enumerate(sheet.iter_rows(values_only=True), start=1)]
        book.close()
        return rows
    for encoding in ("utf-8-sig", "cp1251"):  # Excel в Windows сохраняет CSV в cp1251
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PlanFileError("Не удалось прочитать текст файла — сохраните его как .xlsx или CSV в UTF-8")
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";" if text.count(";") >= text.count(",") else ","  # Excel с русскими настройками пишет «;»
    return [(n, list(row)) for n, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1)]


def _columns(rows: list[tuple[int, list[object]]]) -> tuple[int, dict[str, int]]:
    """Строка заголовка (первая, где есть «Работа» и «Начало») и номера колонок."""
    aliases = {_key(alias): column for column, names in COLUMNS.items() for alias in names}
    for index, (_, cells) in enumerate(rows[:20]):
        found: dict[str, int] = {}
        for position, value in enumerate(cells):
            column = aliases.get(_key(value))
            if column and column not in found:
                found[column] = position
        if {"work", "start", "end"} <= found.keys():
            return index, found
    need = ", ".join(f"«{TITLES[c]}»" for c in ("phase", "work", "start", "end"))
    raise PlanFileError(f"Не нашли заголовок таблицы: нужны колонки {need}. Проще всего — заполнить шаблон")


def _date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and 20000 < value < 80000:  # дата, которую Excel отдал числом дней
        return EXCEL_EPOCH + timedelta(days=int(value))
    text = _cell(value).split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(text)


# ---------- разбор ----------
def parse(
    rows: list[tuple[int, list[object]]],
    rules: list[Rule],
    catalog: Catalog | None,
    catalog_problem: str | None,
    object_type: str | None,
) -> list[PlanPhase]:
    """Этапы с работами. Ошибки и пояснения — у строк; сами строки ничего не меняют в базе."""
    header, col = _columns(rows)
    body = [(n, cells) for n, cells in rows[header + 1 :] if any(_cell(c) for c in cells)]
    if not body:
        raise PlanFileError("В таблице нет ни одной работы — заполните строки под заголовком")
    if len(body) > MAX_ROWS:
        raise PlanFileError(f"Строк больше {MAX_ROWS} — разделите план на несколько файлов")
    by_name = {_key(r.stage_name): r for r in rules}
    by_key = {r.key: r for r in rules}
    works_by_name: dict[str, list[int]] = {}
    if catalog:
        for w in catalog.works_for(object_type):
            works_by_name.setdefault(_key(w.name), []).append(w.stage_id)

    def value(cells: list[object], column: str) -> object:
        position = col.get(column)
        return cells[position] if position is not None and position < len(cells) else None

    phases: dict[str, PlanPhase] = {}
    phase_name = ""
    for line, cells in body:
        if name := _cell(value(cells, "phase")):
            phase_name = name
        phase = phases.get(_key(phase_name))
        if phase is None:
            phase = phases[_key(phase_name)] = PlanPhase(name=phase_name, line=line)
            if not phase_name:
                phase.errors.append(f"Строка {line}: не указан этап — впишите его в колонку «Этап»")
            elif len(phase_name) > 200:
                phase.errors.append(f"Строка {line}: название этапа длиннее 200 символов")
        work = _cell(value(cells, "work")) or None
        row = PlanRow(line=line, phase=phase_name, work=work, start=None, end=None)
        for column in ("start", "end"):
            try:
                setattr(row, column, _date(value(cells, column)))
            except ValueError as exc:
                row.errors.append(f"«{TITLES[column]}»: не дата — «{exc}». Пишите как 15.09.2026")
        if row.start and row.end and row.end < row.start:
            row.errors.append("Окончание раньше начала")
        if work is None:  # строка самого этапа: его даты (без дат — просто заголовок этапа)
            if (row.start is None) != (row.end is None) and not row.errors:
                row.errors.append("у этапа нужны обе даты — или оставьте их пустыми, даты сложатся из работ")
            if row.errors:
                phase.errors += [f"Строка {line}: {e[0].lower()}{e[1:]}" for e in row.errors]
            elif row.start and row.end:
                phase.start, phase.end = row.start, row.end
            continue
        if len(work) < 2 or len(work) > 200:
            row.errors.append("Название работы — от 2 до 200 символов")
        if (row.start is None or row.end is None) and not row.errors:  # нераспознанная дата уже названа
            row.errors.append("Нужны даты начала и окончания")
        _rule(row, _cell(value(cells, "rule")), by_key, by_name)
        _catalog(row, _cell(value(cells, "catalog")), catalog, catalog_problem, object_type, works_by_name)
        phase.works.append(row)

    for phase in phases.values():
        dated = [w for w in phase.works if w.start and w.end and not w.errors]
        if phase.start is None and dated:
            phase.start, phase.end = min(w.start for w in dated), max(w.end for w in dated)
        elif phase.start and phase.end:
            for w in dated:
                if w.start < phase.start or w.end > phase.end:
                    w.notes.append("Выходит за даты этапа")
        if not phase.works and phase.start is None and not phase.errors:
            phase.errors.append(f"Строка {phase.line}: у этапа нет ни работ, ни дат")
    return list(phases.values())


def _rule(row: PlanRow, given: str, by_key: dict[str, Rule], by_name: dict[str, Rule]) -> None:
    if given:
        rule = by_key.get(given) or by_name.get(_key(given))
        if rule is None:
            row.errors.append(f"Нет правила «{given}» — выберите из списка правил или оставьте пустым")
        else:
            row.rule_key = rule.key
        return
    if rule := by_name.get(_key(row.work)):
        row.rule_key = rule.key
        row.notes.append(f"Правило «{rule.stage_name}» — по названию работы")
    else:
        row.notes.append("Без правила: технику на этой работе не сверяем")


def _catalog(
    row: PlanRow,
    given: str,
    catalog: Catalog | None,
    problem: str | None,
    object_type: str | None,
    works_by_name: dict[str, list[int]],
) -> None:
    if not given:
        return
    if catalog is None:
        row.notes.append(f"Вид работ не сохранится: {problem or 'справочник сервисов аналитики не подключён'}")
        return
    if number := re.match(r"^\s*(\d+)\b", given):  # «47» или «47 — Устройство котлована»
        stage_id = int(number.group(1))
    else:
        found = works_by_name.get(_key(given), [])
        if len(found) != 1:
            row.errors.append(
                f"Вид работ «{given}» " + ("в справочнике не один — укажите его номер" if found else "не найден в справочнике")
            )
            return
        stage_id = found[0]
    if issue := catalog.step_problem(stage_id, object_type):
        row.errors.append(f"Вид работ: {issue}")
    else:
        row.catalog_stage_id = stage_id


# ---------- шаблон ----------
def template(rules: list[Rule], catalog: Catalog | None, object_type: str | None, site_name: str) -> bytes:
    """Книга Excel: лист «План» с заголовком и списками выбора, пример, правила, виды работ, как заполнять."""
    book = Workbook()
    head_font, head_fill = Font(bold=True), PatternFill("solid", fgColor="E8EEF9")
    plan = book.active
    plan.title = PLAN_SHEET
    plan.append([TITLES[c] for c in COLUMNS])
    for cell in plan[1]:
        cell.font, cell.fill = head_font, head_fill
    for letter, width in zip("ABCDEF", (28, 40, 13, 13, 34, 46), strict=True):
        plan.column_dimensions[letter].width = width
    plan.freeze_panes = "A2"
    for letter in "CD":
        for row in range(2, MAX_ROWS + 2):
            plan[f"{letter}{row}"].number_format = "DD.MM.YYYY"
    plan["A1"].comment = Comment("Укрупнённый этап. Пустая ячейка — тот же этап, что строкой выше", "СтройКонтроль")
    plan["E1"].comment = Comment(
        "Выберите из списка. Пусто — подойдёт правило с таким же названием, как у работы", "СтройКонтроль"
    )

    ref = book.create_sheet("Правила")
    ref.append(["Правило «этап → техника»", "Что входит в этап"])
    for rule in rules:
        ref.append([rule.stage_name, rule.description])
    ref.column_dimensions["A"].width, ref.column_dimensions["B"].width = 40, 60
    for cell in ref[1]:
        cell.font = head_font
    if rules:
        rule_list = DataValidation(type="list", formula1=f"'Правила'!$A$2:$A${len(rules) + 1}", allow_blank=True)
        rule_list.error, rule_list.errorTitle = "Выберите правило из списка или оставьте пустым", "Нет такого правила"
        rule_list.showErrorMessage = True
        plan.add_data_validation(rule_list)
        rule_list.add(f"E2:E{MAX_ROWS + 1}")

    works = catalog.works_for(object_type) if catalog else []
    if works:
        kinds = book.create_sheet("Виды работ")
        kinds.append(["Вид работ", "Раздел справочника"])
        for w in works:
            path = w.path[:-1] if w.path and w.path[-1] == w.name else w.path
            kinds.append([f"{w.stage_id} — {w.name}", " / ".join(path)])
        kinds.column_dimensions["A"].width, kinds.column_dimensions["B"].width = 60, 60
        for cell in kinds[1]:
            cell.font = head_font
        work_list = DataValidation(type="list", formula1=f"'Виды работ'!$A$2:$A${len(works) + 1}", allow_blank=True)
        work_list.showErrorMessage = False  # можно вписать и просто номер
        plan.add_data_validation(work_list)
        work_list.add(f"F2:F{MAX_ROWS + 1}")
        plan["F1"].comment = Comment(f"Справочник {catalog.version}. Выберите из списка или впишите номер", "СтройКонтроль")
    else:
        plan["F1"].comment = Comment("Сервисы аналитики не подключены — колонку можно не заполнять", "СтройКонтроль")

    example = book.create_sheet("Пример")
    example.append([TITLES[c] for c in COLUMNS])
    for cell in example[1]:
        cell.font, cell.fill = head_font, head_fill
    today = date.today()
    sample = [
        ("Подготовительный период", "Подготовка площадки", 0, 18, "Подготовка площадки"),
        ("Земляные работы", "Разработка котлована", 21, 52, "Разработка котлована"),
        ("", "Вывоз грунта", 49, 60, ""),
        ("Нулевой цикл", "Бетонирование фундаментной плиты", 63, 95, ""),
        ("", "Гидроизоляция фундамента", 96, 104, ""),
    ]
    for phase, work, start, end, rule in sample:
        example.append([phase, work, today + timedelta(days=start), today + timedelta(days=end), rule, ""])
    for letter, width in zip("ABCDEF", (28, 40, 13, 13, 34, 46), strict=True):
        example.column_dimensions[letter].width = width
    for letter in "CD":
        for row in range(2, len(sample) + 2):
            example[f"{letter}{row}"].number_format = "DD.MM.YYYY"

    guide = book.create_sheet("Как заполнять")
    guide.column_dimensions["A"].width = 110
    for line in (
        f"План работ объекта «{site_name}» — заполните лист «План» и загрузите файл на странице объекта.",
        "Одна строка — одна работа. «Этап» — укрупнённый этап; пустая ячейка — тот же этап, что строкой выше.",
        "Даты — в виде 15.09.2026. Даты этапа складываются из его работ; чтобы задать их явно — строка с этапом без работы.",
        "Правило «этап → техника» — из списка. Пусто — подойдёт правило с таким же названием, как у работы;",
        "  если такого нет, работа будет в плане, но технику на ней система не сверяет.",
        "Вид работ по справочнику сервисов аналитики — из списка или номером; можно не заполнять.",
        "Перед загрузкой система покажет, что получится, и строки с ошибками — план изменится только после подтверждения.",
        "Лист «Пример» — образец заполнения, он не загружается.",
    ):
        guide.append([line])

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
