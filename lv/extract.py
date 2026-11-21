"""Step 0: read an LV file into the line item table. One reader per file format.

A reader copies the file faithfully: one row per item, in file order, nothing compared, nothing merged.
"""
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from lv.model import LV, LineItem

# One spelling per unit. A unit that is not listed is kept as written, without a trailing dot.
UNITS = {
    "st": "St", "stk": "St", "stck": "St", "stück": "St",
    "std": "h",
    "m²": "m2", "qm": "m2",
    "m³": "m3", "cbm": "m3",
    "to": "t",
}


def clean_unit(written: str) -> str:
    unit = written.strip().rstrip(".")
    return UNITS.get(unit.lower(), unit)


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


READERS = {".x83": read_x83}
