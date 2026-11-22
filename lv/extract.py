"""Step 0: read an LV file into the line item table. One reader per file format.

A reader copies the file faithfully: one row per item, in file order, nothing compared, nothing merged.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from itertools import groupby
from pathlib import Path

import openpyxl
import pymupdf
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from lv.model import LV, LineItem

# One spelling per unit. A unit that is not listed is kept as written, without a trailing dot.
UNITS = {
    "st": "St", "stk": "St", "stck": "St", "stück": "St",
    "psch": "psch", "pau": "psch", "pausch": "psch",  # P6 PDFs print PSCH, Psch and pau (pauschal: a lump sum)
    "std.": "h",  # with the dot only: P2's own unit list defines "Std" as piece x day ("Stück x Tag")
    "m²": "m2", "qm": "m2",
    "m³": "m3", "cbm": "m3",
    "to": "t",
}


def clean_unit(written: str) -> str:
    unit = written.strip()
    return UNITS.get(unit.lower()) or UNITS.get(unit.rstrip(".").lower(), unit.rstrip("."))


def read(path: Path, project: str) -> LV:
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"Cannot read {path.suffix.lstrip('.').lower() or 'this kind of'} files yet: {path.name}")
    return reader(path, project)


# ponytail: one fixed share. P6's trade files share at most 27% of their items; the same LV in another format or an
# excerpt shares 100%. A file in between is most likely the same LV with changes, and the tool cannot tell which to keep.
SAME_LV = 0.5


def read_all(paths: list[Path], project: str) -> tuple[LV, list[str]]:
    """Step 0 for an LV that comes as several files, e.g. one per trade (P6): reads them in name order into one LV.
    Returns the LV and lines for the run's log. An item is already read when an earlier file has the same item number,
    unit and quantity (texts are not compared: they differ between formats, as in P1's GAEB XML and GAEB 90 files).
    - A file whose items are all already read adds nothing and is skipped: the same LV again, or an excerpt of one.
    - A file with SAME_LV or more of them is refused, with both files named.
    - Otherwise its items are added. When an item number is in more than one file, every number gets its file's place
      in front, e.g. "F02-1.1.10", so each stays unique and the comments and the dashboard still find it."""
    lvs: list[LV] = []
    notes: list[str] = []
    seen: dict[tuple, str] = {}  # (item number, unit, quantity) -> the file it was first read from
    for path in sorted(paths, key=lambda p: p.name.lower()):
        lv = read(path, project)
        keys = [(item.oz, item.unit.lower(), item.quantity) for item in lv.items]
        known = sorted({seen[key] for key in keys if key in seen})
        repeated = sum(1 for key in keys if key in seen)
        if keys and repeated == len(keys):
            notes.append(f"{path.name}: skipped, all {len(keys)} of its items are already in {', '.join(known)}")
            continue
        if keys and repeated >= SAME_LV * len(keys):
            raise ValueError(f"{path.name}: {repeated} of its {len(keys)} items are already in {', '.join(known)} "
                             f"(same item number, unit and quantity). Is it the same LV twice? Leave one of them out.")
        for item, key in zip(lv.items, keys):
            item.source_file = path.name
            seen.setdefault(key, path.name)
        lvs.append(lv)
    if len(lvs) == 1:
        return lvs[0], notes
    files_per_number: dict[str, int] = {}
    for lv in lvs:
        for oz in {item.oz for item in lv.items}:
            files_per_number[oz] = files_per_number.get(oz, 0) + 1
    if any(count > 1 for count in files_per_number.values()):
        for place, lv in enumerate(lvs, 1):
            for item in lv.items:
                item.oz = f"F{place:02}-{item.oz}"
                item.id = f"{project}/{item.oz}"
        notes.append(f"item numbers repeat across files: each now starts with its file's place, F01- to "
                     f"F{len(lvs):02}- (the order of source_file)")
    return LV(project=project, name=next((lv.name for lv in lvs if lv.name), ""),
              date=next((lv.date for lv in lvs if lv.date), None),
              source_file=", ".join(lv.source_file for lv in lvs),
              items=[item for lv in lvs for item in lv.items]), notes


# --- GAEB XML (.X83) -------------------------------------------------------------------------------------------

def read_x83(path: Path, project: str) -> LV:
    raw = path.read_bytes()
    if b"<!ENTITY" in raw:  # LV files come from outside; entity definitions are how XML bombs are built
        raise ValueError(f"Refusing an XML file that defines entities: {path.name}")
    root = ET.fromstring(raw)
    boq = _first(root, "BoQ")
    items: list[LineItem] = []
    _walk(_child(boq, "BoQBody"), [], [], project, items)
    label = _first(boq, "LblBoQ")
    day = _first(_child(root, "GAEBInfo"), "Date")
    return LV(
        project=project,
        name=_text(label),
        date=date.fromisoformat(_text(day)) if day is not None else None,
        source_file=path.name,
        items=items,
    )


def _walk(body: ET.Element, numbers: list[str], titles: list[str], project: str, items: list[LineItem]) -> None:
    """Sections nest; items sit in the innermost section. Order of the file is kept."""
    for el in body:
        if _tag(el) == "BoQCtgy":
            inner = _child(el, "BoQBody")
            if inner is not None:
                number = numbers + [el.get("RNoPart", "")]
                title = f"{'.'.join(number)} {_text(_child(el, 'LblTx'))}".strip()
                _walk(inner, number, titles + [title], project, items)
        elif _tag(el) == "Itemlist":
            items.extend(_item(el_item, numbers, titles, project) for el_item in el if _tag(el_item) == "Item")


def _item(el: ET.Element, numbers: list[str], titles: list[str], project: str) -> LineItem:
    oz = ".".join(numbers + [el.get("RNoPart", "")])
    quantity = _text(_child(el, "Qty"))
    # ponytail: only "priced but not in the total" counts as optional; add a value if a file marks it otherwise
    optional = _text(_child(el, "Provis")) == "WithoutTotal"
    return LineItem(
        id=f"{project}/{oz}",
        oz=oz,
        section=titles,
        short_text=_text(_first(_child(el, "Description"), "TextOutlTxt")),
        long_text=" ".join(_text(d) for d in el.iter() if _tag(d) == "DetailTxt"),
        quantity=float(quantity) if quantity else None,
        unit=clean_unit(_text(_child(el, "QU"))),
        kind="optional" if optional else "normal",
    )


def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]  # GAEB versions differ in namespace, not in element names


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    return None if el is None else next((c for c in el if _tag(c) == name), None)


def _first(el: ET.Element | None, name: str) -> ET.Element | None:
    return None if el is None else next((d for d in el.iter() if _tag(d) == name), None)


def _text(el: ET.Element | None) -> str:
    return "" if el is None else re.sub(r"\s+", " ", " ".join(el.itertext())).strip()


# --- GAEB 90 (.D83, .D84, .D86) --------------------------------------------------------------------------------
# Lines of 80 characters; the first two name the record. The layout was read off the dataset files, and the tests
# check it against the sums the files state themselves:
#   00 header with the item number mask       01 project name and date
#   11 section start, 12 its title            31 section end, 32 section sum
#   21 item: number, kind flags, quantity, unit          23 its unit price and total price
#   25 short text line, 26 long text line     24 sub-description inside an item, 28 "repeats another item"
#   20, 41, 42 notes without a price          30 price components (left out on purpose)

def read_gaeb90(path: Path, project: str) -> LV:
    raw = path.read_bytes()
    # DOS files (code page 850) keep umlauts in 0x80-0x9F, where Windows files (latin-1) have nothing printable
    lines = raw.decode("cp850" if re.search(rb"[\x80-\x9f]", raw) else "latin-1").splitlines()
    head = next((line for line in lines if line.startswith("00")), "")
    widths = [len(list(same)) for _, same in groupby(head[62:71].strip())] or [2, 2, 4, 1]  # mask "1122PPPPI"

    def number(field: str) -> str:
        parts = [field[sum(widths[:i]):sum(widths[:i + 1])].strip() for i in range(len(widths))]
        return ".".join(part for part in parts if part)

    name, day = "", None
    sections: list[tuple[str, str]] = []  # (number, title) of the open sections, outermost first
    items: list[LineItem] = []
    item: LineItem | None = None
    in_sub = False
    for line in lines:
        record, body = line[:2], line[2:72]
        if record == "21":
            oz = number(line[2:11])
            item = LineItem(
                id=f"{project}/{oz}",
                oz=oz,
                section=[f"{n} {title}".strip() for n, title in sections],
                short_text="",
                long_text="",
                quantity=_decimal(line[23:34], 3),
                unit=clean_unit(line[34:38]),
                # ponytail: "E" (optional item) is the standard's flag; no file in the dataset has one to check
                kind="optional" if line[12] == "E" else "normal",
            )
            items.append(item)
            in_sub = False
        elif record == "23" and item:
            item.unit_price, item.total_price = _decimal(line[12:23], 3), _decimal(line[24:36], 2)
            if abs((item.quantity or 0) * item.unit_price - item.total_price) > max(0.01, item.total_price * 0.001):
                raise ValueError(f"{path.name}, item {item.oz}: quantity x unit price is not the total price. "
                                 "The file may use a layout this reader does not know.")
        elif record == "24" and item:
            in_sub = True
        elif record == "25" and item and not in_sub:
            item.short_text += " " + body
        elif record in ("25", "26") and item:
            item.long_text += " " + body
        elif record not in ("28", "30"):
            item = None  # a section record or a note: the text lines that follow do not belong to an item
            if record == "01":
                name = body[:40].strip()
                day = datetime.strptime(body[40:48], "%d.%m.%y").date() if body[40:48].strip() else None
            elif record == "11":
                sections.append((number(line[2:11]), ""))
            elif record == "12" and sections:
                sections[-1] = (sections[-1][0], body.strip())
            elif record == "31" and sections:
                sections.pop()
    if not items:
        raise ValueError(f"Found no line items in {path.name}. A file with prices only cannot be read on its own.")
    for item in items:
        item.short_text = _flow(item.short_text)
        item.long_text = _flow(_ADDITION.sub(r"\1 \2 \3", _flow(item.long_text)))
    return LV(project=project, name=name, date=day, source_file=path.name, items=items)


# A text addition by the client or the bidder: "[TA11 caption [ text ] tail]". The words stay, the markup goes,
# which gives the same text as the XML reader. A markup bracket stands alone; a bracket inside the text
# ("[ORT_030]") touches its word.
_ADDITION = re.compile(r"\[T[AB]\d\d (.*?)\[ (.*?)(?<= )\]([^\[\]]*)\]")


def _decimal(field: str, decimals: int) -> float | None:
    """Numbers are written without a point. P1 and P2 leave the third decimal of the unit price blank."""
    return int(field.replace(" ", "0")) / 10 ** decimals if field.strip() else None


def _flow(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --- Excel (.xlsx) and PDF prints (.pdf): numbered rows -----------------------------------------------------------
# Both list numbered rows in LV order. A row is an item when it has a quantity, a unit or a price; otherwise it is
# a section title. A section's level is the number of parts in its number ("15.1" is 2); it stays open for the
# items below it until a section of the same or a higher level starts.

# An item number in a PDF: digits, or parts joined by dots (15.1.10. · 1.18 · 1.2..5 · .1 · TE.L0.0009). Strict,
# because long-text lines can start in the number column. In Excel the column says it: no space and a digit.
OZ = re.compile(r"^(?=\S*\d)(?:\d+|[0-9A-Z]*(?:\.+[0-9A-Z]*)+)$")
OZ_CELL = re.compile(r"^\S*\d\S*$")


@dataclass
class Row:
    oz: str
    lines: list[str]  # short text first, then the long text's lines
    quantity: float | None = None
    unit: str = ""
    unit_price: float | None = None
    total_price: float | None = None
    optional: bool = False
    item: bool = False


def _rows_to_items(rows: list[Row], project: str, read_by: str) -> list[LineItem]:
    sections: list[tuple[str, str]] = []
    items: list[LineItem] = []
    for row in rows:
        lines = [_flow(line) for line in row.lines if line and line.strip()]
        if not row.item:
            level = len([part for part in row.oz.split(".") if part])
            while sections and len([part for part in sections[-1][0].split(".") if part]) >= level:
                sections.pop()
            sections.append((row.oz, lines[0] if lines else ""))
            continue
        items.append(LineItem(
            id=f"{project}/{row.oz}", oz=row.oz, section=[f"{n} {title}".strip() for n, title in sections],
            short_text=lines[0] if lines else "", long_text=" ".join(lines[1:]),
            quantity=row.quantity, unit=clean_unit(row.unit), unit_price=row.unit_price, total_price=row.total_price,
            kind="optional" if row.optional else "normal", read_by=read_by))
    return items


# Excel: the header row is found by its column names. Checked in this order, so "Einheitspreis" is not "Einheit"
# and "Mengeneinheit" is not "Menge". Names of 3 letters or fewer must match whole, longer ones as a beginning.
COLUMNS = (("long", ("langtext",)), ("kind", ("art", "positionsart")),
           ("unit_price", ("einheitspreis", "ep")), ("total_price", ("gesamtpreis", "gesamtbetrag", "gp")),
           ("unit", ("me", "einheit", "mengeneinheit")), ("quantity", ("menge",)),
           ("oz", ("oz", "ordnungszahl", "position", "pos", "posnr", "nr")),
           ("short", ("kurztext", "bezeichnung", "leistungsbeschreibung", "beschreibung", "leistung", "text")))
NEEDED = ("oz", "short", "quantity", "unit")


def read_xlsx(path: Path, project: str) -> LV:
    """The first sheet. A row without a number but with text continues the long text of the item above it."""
    try:
        sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    except Exception as error:  # openpyxl's ways of saying "not a usable workbook"
        raise ValueError(f"Cannot read {path.name} as an Excel file: {error}")
    table = [list(row) for row in sheet.iter_rows(values_only=True)]
    found = next(((n, columns) for n, row in enumerate(table[:50]) if all(k in (columns := _columns(row)) for k in NEEDED)),
                 None)
    if found is None:
        raise ValueError(f"{path.name}: no header row with the columns OZ, Kurztext, Menge and Einheit (or Pos., "
                         "Bezeichnung, Menge, ME) in the first 50 rows.")
    start, columns = found
    cell = lambda row, key: row[columns[key]] if key in columns and columns[key] < len(row) else None
    rows: list[Row] = []
    for values in table[start + 1:]:
        oz = _text_of(cell(values, "oz"))
        texts = [_text_of(cell(values, key)) for key in ("short", "long")]
        if not OZ_CELL.match(oz):
            if rows and any(texts) and cell(values, "quantity") is None:
                rows[-1].lines.append(" ".join(text for text in texts if text))
            continue
        row = Row(oz=oz.rstrip(".") or oz, lines=texts, quantity=_number(cell(values, "quantity")),
                  unit=_text_of(cell(values, "unit")), unit_price=_number(cell(values, "unit_price")),
                  total_price=_number(cell(values, "total_price")),
                  optional="bedarf" in _text_of(cell(values, "kind")).lower())
        row.item = row.quantity is not None or bool(row.unit) or row.unit_price is not None
        if row.unit_price is not None and row.total_price is not None and row.quantity is not None and \
                abs(row.quantity * row.unit_price - row.total_price) > max(0.01, abs(row.total_price) * 0.001):
            raise ValueError(f"{path.name}, item {row.oz}: quantity x unit price is not the total price. "
                             "A column may be read as the wrong one.")
        rows.append(row)
    items = _rows_to_items(rows, project, "excel")
    if not items:
        raise ValueError(f"Found no line items in {path.name}: no row below the header has a quantity, unit or price.")
    name = next((_text_of(value) for row in table[:start] for value in row if _text_of(value)), "")
    return LV(project=project, name=name, date=None, source_file=path.name, items=items)


def _columns(row: list) -> dict[str, int]:
    columns: dict[str, int] = {}
    for n, value in enumerate(row):
        name = re.sub(r"[^a-zäöüß]", "", _text_of(value).lower())
        key = next((key for key, names in COLUMNS
                    if any(name == known or (len(known) > 3 and name.startswith(known)) for known in names)), None)
        if name and key and key not in columns:
            columns[key] = n
    return columns


def _text_of(value) -> str:
    return "" if value is None else str(value).strip()


def _number(value) -> float | None:
    """A number cell, or German written as text ("1.234,50")."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    return _german(text) if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?|-?\d+(?:,\d+)?", text) else None


# PDF: the print layout of the dataset's P6 ("Anfrage", columns OZ, Leistungsbeschreibung, Menge ME, Einheitspreis,
# Gesamtbetrag). Read by code: text lines are put in page order by position, which also puts back the lines the
# dataset's anonymiser moved to the end of a page. No price is taken from a PDF: an LV read from one is unpriced.
QUANTITY = re.compile(r"^(\d{1,3}(?:\.\d{3})*,\d+)(?:\s+(\S+))?$")
STOP = ("Zuzüglich der gesetzlichen Mehrwertsteuer", "Das LV besteht aus")  # the closing page: VAT and signature


def read_pdf(path: Path, project: str) -> LV:
    try:
        doc = pymupdf.open(path)
    except Exception as error:  # pymupdf's ways of saying "not a usable PDF"
        raise ValueError(f"Cannot read {path.name} as a PDF: {error}")
    rows: list[Row] = []
    names: dict[str, str] = {}
    for page in doc:
        lines = _page_lines(page)
        head = next((n for n, line in enumerate(lines) if any(t == "Leistungsbeschreibung" for _, t in line)), None)
        if head is None:
            continue
        for line in lines[:head]:
            if line[0][1] in ("Projekt:", "LV:"):
                names.setdefault(line[0][1], " ".join(t for _, t in line[1:]))
        text_x = next(x for x, t in lines[head] if t == "Leistungsbeschreibung")
        quantity_x = next((x for x, t in lines[head] if t.startswith("Menge")), 300.0)
        price_x = next((x for x, t in lines[head] if t.startswith("Einheitspreis")), 380.0)
        for line in lines[head + 1:]:
            x, first = line[0]
            words = re.sub(r"\.{4,}", " ", " ".join(t for _, t in line)).split()
            joined = " ".join(words)
            if first.startswith("Seite:") or any(t.startswith("Seite:") for _, t in line):
                break
            if joined.startswith(STOP):
                return _pdf_lv(path, project, rows, names)
            if not joined:  # only dots: a line to write in, when it stands in the text column
                if rows and not rows[-1].item and x < quantity_x - 20:
                    rows[-1].lines.append(" ".join(t for _, t in line))
                continue
            if first == "Summe" or re.fullmatch(r"Übertrag:?|(in EUR ?)+", joined):
                continue
            if x < text_x - 5 and OZ.match(first):
                rows.append(Row(oz=first.rstrip(".") or first, lines=[" ".join(t for _, t in line[1:])]))
            elif rows and not rows[-1].item and x >= quantity_x - 20 and (match := QUANTITY.match(
                    " ".join(re.sub(r"\.{4,}", " ", " ".join(t for x, t in line if x < price_x - 5)).split()))):
                rows[-1].quantity, rows[-1].unit, rows[-1].item = _german(match[1]), match[2] or "", True
            elif rows and not rows[-1].item:
                rows[-1].lines.append(" ".join(t for _, t in line))
    return _pdf_lv(path, project, rows, names)


def _pdf_lv(path: Path, project: str, rows: list[Row], names: dict[str, str]) -> LV:
    for row in rows:  # "*** Bedarfsposition ohne GB" before the short text marks an optional item; other marks stay
        marks = [line for line in row.lines if line.strip().startswith("***")][:1]
        if row.lines and marks and row.lines[0] == marks[0] or row.lines and not row.lines[0].strip():
            first = row.lines.pop(0)
            row.optional = "bedarf" in first.lower()
            if first.strip() and not row.optional:
                row.lines.insert(1 if row.lines else 0, first)
    items = _rows_to_items(rows, project, "pdf")
    if not items:
        raise ValueError(f"Found no line items in {path.name}. Only one PDF layout can be read: a print with the "
                         "columns OZ, Leistungsbeschreibung, Menge ME, Einheitspreis, Gesamtbetrag.")
    name = " – ".join(part for part in (names.get("Projekt:", ""), names.get("LV:", "")) if part)
    return LV(project=project, name=_flow(name), date=None, source_file=path.name, items=items)


def _page_lines(page) -> list[list[tuple[float, str]]]:
    """The page's text as lines top to bottom, each a list of (x, text) left to right. Pieces less than 3 points
    apart in height are one line: an item number sits a little lower than its short text."""
    pieces = sorted((span["bbox"][1], span["bbox"][0], span["text"].strip())
                    for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])
                    for span in line["spans"] if span["text"].strip())
    lines: list[tuple[float, list[tuple[float, str]]]] = []
    for y, x, text in pieces:
        if lines and y - lines[-1][0] < 3:
            lines[-1][1].append((x, text))
        else:
            lines.append((y, [(x, text)]))
    return [sorted(line) for _, line in lines]


READERS = {".x83": read_x83, ".d83": read_gaeb90, ".d84": read_gaeb90, ".d86": read_gaeb90, ".xlsx": read_xlsx,
           ".pdf": read_pdf}


# --- texts from PDF prints of an LV whose prices come from another file (P3) -------------------------------------
# A bid file can hold prices without texts. The texts are then read from PDF prints of the same LV and joined by
# item number. Read by code, no AI. No price is ever taken from a PDF: prices, quantities and units stay with the
# prices file; a PDF's quantity and unit are only used to check that a text belongs to the item it is joined to.
# Two print layouts are known, both from P3:
#   tender LV   "2.1.60.   <short text>" at the left edge, then the long text, then a line "550,000 m³ ......"
#   priced bid  "2.1.60   550,000 m³ ... prices" on one line, then the short text on the indented lines below

@dataclass
class PdfItem:
    lines: list[str]  # the item's text lines, in reading order
    quantity: float | None
    unit: str
    whole: bool  # True: short and long text (tender LV). False: the short text only (priced bid).


_LV_ITEM = re.compile(r"^(\d+\.\d+\.\d+)\.(?:\s+(.*))?$")
_LV_QUANTITY = re.compile(r"^\s+([\d.]+,\d{3})\s+(\S+)\s+\.{5,}")
_LV_SECTION = re.compile(r"^(\d+(?:\.\d+)?)\.\s+(\S.*)$")
_BID_ITEM = re.compile(r"^(\s*)(\d+\.\d+\.\d+)\s+([\d.]+,\d{3})\s+(\S+)")
_BID_SECTION = re.compile(r"^\s*(?:Bereich|Abschnitt)\s+(\d+(?:\.\d+)?)\.\s+(\S.*)$")
_PAGE_FURNITURE = re.compile(r"^(Projekt:|LV:?\s|Ordnungszahl\s)|^\s*(Leistungsverzeichnis Kurz- und Langtext\s*$"
                             r"|Seite: \d+ von \d+\s*$|\[BILD(_\d+)?\]\s*$)")


@cache  # reading 70 pages takes seconds, and one run may ask for the same file more than once
def pdf_texts(path: Path) -> tuple[dict[str, PdfItem], dict[str, str]]:
    """The item texts of one PDF print by item number, and its section titles by section number."""
    try:
        pages = [page.extract_text(extraction_mode="layout") or "" for page in PdfReader(path).pages]
    except (PyPdfError, ValueError, KeyError, TypeError) as error:  # pypdf's ways of saying "not a usable PDF"
        raise ValueError(f"Cannot read {path.name} as a PDF: {error}")
    items: dict[str, PdfItem] = {}
    sections: dict[str, str] = {}
    item: PdfItem | None = None
    left = 0  # where the item line of a priced bid starts; its short text is indented further
    for line in (line for page in pages for line in [*page.splitlines(), "\f"]):
        if line == "\f":  # a long text of a tender LV runs on over the page; a short text of a bid does not
            item = item if item and item.whole else None
        elif not line.strip() or _PAGE_FURNITURE.match(line):
            continue
        elif found := _BID_ITEM.match(line):
            left = len(found[1])
            item = items[found[2]] = PdfItem([], _german(found[3]), clean_unit(found[4]), whole=False)
        elif found := _LV_ITEM.match(line):
            item = items[found[1]] = PdfItem([found[2] or ""], None, "", whole=True)
        elif found := _LV_SECTION.match(line) or _BID_SECTION.match(line):
            sections[found[1]] = re.sub(r"\s*\.{5,}.*$", "", _flow(found[2]))
            item = None
        elif found := _LV_QUANTITY.match(line):  # the last line of an item in a tender LV
            if item and item.whole:
                item.quantity, item.unit = _german(found[1]), clean_unit(found[2])
            item = None
        elif item and (item.whole or (len(line) - len(line.lstrip()) >= left + 10 and "Übertrag:" not in line)):
            item.lines.append(line.strip())
        else:
            item = None  # a note between items, a sum line, a page footer
    return items, sections


def add_texts(lv: LV, pdfs: list[Path]) -> list[str]:
    """Gives the items of a priced LV their texts from PDF prints of the same LV, by item number.

    Returns what the user should know: items whose PDF quantity or unit disagrees with the prices file (their text is
    not used) and items left without any text. Nothing is dropped.
    """
    names = ", ".join(path.name for path in pdfs)
    notes: list[str] = []
    shorts: dict[str, str] = {}  # item number -> short text from a priced bid
    wholes: dict[str, list[str]] = {}  # item number -> text lines from a tender LV
    titles: dict[str, str] = {}
    for path in pdfs:
        items, sections = pdf_texts(path)
        titles |= sections
        differing = []
        for item in lv.items:
            found = items.get(item.oz)
            if found is None or not any(found.lines):
                continue
            if (found.quantity, found.unit) != (item.quantity, item.unit):
                differing.append(f"{item.oz} ({found.quantity:.10g} {found.unit}, prices file {item.quantity:.10g} {item.unit})")
            elif found.whole:
                wholes.setdefault(item.oz, found.lines)
            else:
                shorts.setdefault(item.oz, _flow(" ".join(found.lines)))
        if differing:
            notes.append(f"{path.name}: quantity or unit differs from the prices file for {len(differing)} items, "
                         f"so their text was not used: {', '.join(differing)}")

    without = []
    for item in lv.items:
        short, lines = shorts.get(item.oz, ""), wholes.get(item.oz)
        whole = _flow(" ".join(lines)) if lines else ""
        if not short and not whole:
            without.append(item.oz)
            continue
        if not (short and whole.startswith(short)):  # no bid print, or it words the short text differently
            short = short if not whole else _flow(lines[0])
        item.short_text, item.long_text, item.read_by = short, whole[len(short):].strip(), "pdf"
        if not item.section:
            parts = item.oz.split(".")
            item.section = [f"{number} {titles[number]}" for number in (".".join(parts[:n]) for n in range(1, len(parts)))
                            if number in titles]
    if without:
        notes.append(f"{names} {'has' if len(pdfs) == 1 else 'have'} no text for {len(without)} of the {len(lv.items)} "
                     f"items: {', '.join(without)}")
    return notes


def _german(number: str) -> float:
    return float(number.replace(".", "").replace(",", "."))
