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

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from lv.model import LV, LineItem

# One spelling per unit. A unit that is not listed is kept as written, without a trailing dot.
UNITS = {
    "st": "St", "stk": "St", "stck": "St", "stück": "St",
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


READERS = {".x83": read_x83, ".d83": read_gaeb90, ".d84": read_gaeb90, ".d86": read_gaeb90}


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
