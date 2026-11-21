"""Step 0: read an LV file into the line item table. One reader per file format.

A reader copies the file faithfully: one row per item, in file order, nothing compared, nothing merged.
"""
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from itertools import groupby
from pathlib import Path

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
