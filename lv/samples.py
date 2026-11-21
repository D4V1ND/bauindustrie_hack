"""Test LVs as Excel and PDF files, written from LVs read from GAEB files.

The GAEB reading is the answer key: reading a written file back must give the same items, texts, quantities, units,
prices and optional flags. `python -m lv.samples` writes the test files to data/raw/generated/.
"""
import sys
from pathlib import Path

import openpyxl
import pymupdf

from lv.extract import read
from lv.model import LV

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
SOURCES = {  # project: GAEB file in the dataset
    "P1": "8209.003_Leistungsverzeichnis_leer.X83",
    "P2": "StBALV23040.D86",  # priced: its Excel copy carries the prices
    "P4": "501-24-01_GAEB-XML_sas.x83",  # 24 optional items
    "P5": "GAEB-[PROJEKT_004].X83",
}
# Two column layouts. "a": long text in its own column. "b": other header names, long text in the row below.
COLUMNS = {
    "a": ("OZ", "Kurztext", "Langtext", "Menge", "Einheit", "EP", "GP", "Art"),
    "b": ("Pos.", "Bezeichnung", None, "Menge", "ME", "Einheitspreis", "Gesamtpreis", "Positionsart"),
}
OPTIONAL = "Bedarfsposition"
EXCEL_CELL = 32767  # the most characters an Excel cell holds
PDF_WORD = 80  # a longer word does not fit on a PDF line
# Characters the PDF's built-in font cannot write, as a print would show them
PDF_CHARS = str.maketrans({"≥": ">=", "≤": "<=", "“": '"', "”": '"', "„": '"', "‚": ",", "–": "-", "—": "-",
                           "…": "...", "ﬂ": "fl", "ﬁ": "fi"})


def fitted(lv: LV, kind: str) -> LV:
    """The LV as a file of this kind can hold it: what reading the file back should give. In the dataset this changes
    one item, P4 03.03.0018, whose long text holds an image as one 192,000-character word, and in a PDF about 50
    rare characters such as "≥" and "…"."""
    copy = lv.model_copy(deep=True)
    for item in copy.items:
        if kind == "xlsx":
            item.long_text = item.long_text[:EXCEL_CELL]
            continue
        item.long_text = " ".join(word[:PDF_WORD] for word in item.long_text.translate(PDF_CHARS).split(" "))
        item.short_text = item.short_text.translate(PDF_CHARS)
        item.section = [title.translate(PDF_CHARS) for title in item.section]
    return copy


def write_xlsx(lv: LV, path: Path, style: str = "a") -> Path:
    """One sheet: the LV's name, a header row, then a row per section title and per item."""
    oz, short, long, quantity, unit, unit_price, total_price, kind = COLUMNS[style]
    lv = fitted(lv, "xlsx")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "LV"
    sheet.append([lv.name])
    sheet.append([])
    sheet.append([name for name in COLUMNS[style] if name])
    written: set[str] = set()
    for item in lv.items:
        for title in item.section:
            if title not in written:
                written.add(title)
                number, _, text = title.partition(" ")
                sheet.append([number, text])
        row = {oz: item.oz, short: item.short_text, long: item.long_text, quantity: item.quantity, unit: item.unit,
               unit_price: item.unit_price, total_price: item.total_price,
               kind: OPTIONAL if item.kind == "optional" else ""}
        sheet.append([row[name] for name in COLUMNS[style] if name])
        if not long and item.long_text:
            sheet.append([None, item.long_text])
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return path


# The PDF copies the print layout of the dataset's P6 files: an A4 "Anfrage" with the columns OZ,
# Leistungsbeschreibung, Menge ME, Einheitspreis, Gesamtbetrag. Prices are left blank, as in a tender.
X_OZ, X_TEXT, X_QTY, X_PRICE, X_TOTAL = 54, 125, 315, 385, 467
TOP, BOTTOM, STEP, SIZE, RIGHT = 160, 780, 11.4, 8, 580
TEXT_WIDTH = X_PRICE - X_TEXT - 5
DOTS = "." * 25


def write_pdf(lv: LV, path: Path) -> Path:
    """Every item needs a quantity: in this layout an item without one cannot be told from a section title."""
    lv = fitted(lv, "pdf")
    rows: list[list[tuple[float, str]]] = []  # an empty row is a blank line
    written: set[str] = set()
    for item in lv.items:
        if item.quantity is None:
            raise ValueError(f"{item.id} has no quantity, so it cannot be written to the PDF layout.")
        for title in item.section:
            if title not in written:
                written.add(title)
                number, _, text = title.partition(" ")
                rows += [[(X_OZ, number), (X_TEXT, text)], []]
        if item.kind == "optional":
            rows += [[(X_OZ, item.oz), (X_TEXT, f"*** {OPTIONAL} ohne GB")], [(X_TEXT, item.short_text)]]
        else:
            rows.append([(X_OZ, item.oz), (X_TEXT, item.short_text)])
        rows += [[(X_TEXT, line)] for line in _wrap(item.long_text)]
        amount = f"{item.quantity:,.3f}".replace(",", " ").replace(".", ",").replace(" ", ".")
        rows += [[(X_QTY, f"{amount} {item.unit}".strip()), (X_PRICE, DOTS), (X_TOTAL, DOTS)], []]
    rows += [[(X_TEXT, "Summe"), (X_PRICE, DOTS)], [], [(X_TEXT, "Zuzüglich der gesetzlichen Mehrwertsteuer aus")]]

    doc = pymupdf.open()
    page, y = _page(doc, lv.name), TOP
    for row in rows:
        if y > BOTTOM:
            page.insert_text((394, y), "Übertrag:", fontname="helv", fontsize=SIZE)
            page, y = _page(doc, lv.name), TOP
            page.insert_text((394, y), "Übertrag:", fontname="helv", fontsize=SIZE)
            y += 2 * STEP
        for x, text in row:  # a long short text or section title stays on one line, in smaller letters
            width = pymupdf.get_text_length(text, fontname="helv", fontsize=SIZE)
            size = min(SIZE, SIZE * (RIGHT - x) / width) if width else SIZE
            page.insert_text((x, y), text, fontname="helv", fontsize=size)
        y += STEP
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=3, deflate=True)
    return path


def _page(doc, name: str):
    page = doc.new_page(width=595, height=842)
    number = len(doc)
    for x, y, text in ((287, 63, "Anfrage"), (X_OZ, 89, "Projekt:"), (X_TEXT, 89, name), (X_OZ, 101, "LV:"),
                       (X_OZ, 124, "OZ"), (X_TEXT, 124, "Leistungsbeschreibung"), (314, 124, "Menge ME"),
                       (391, 124, "Einheitspreis"), (469, 124, "Gesamtbetrag"), (421, 136, "in EUR"),
                       (503, 136, "in EUR"), (55, 806, "Druckdatum:"), (536, 806, f"Seite: {number}")):
        page.insert_text((x, y), text, fontname="helv", fontsize=SIZE)
    return page


def _wrap(text: str) -> list[str]:
    """Lines that fit the text column, broken between words only, so joining them with spaces gives the text."""
    lines: list[str] = []
    for word in text.split():
        if lines and pymupdf.get_text_length(f"{lines[-1]} {word}", fontname="helv", fontsize=SIZE) <= TEXT_WIDTH:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines


def main(out: Path = RAW / "generated") -> None:
    dataset = RAW / "Challenge A Dataset"
    for project, name in SOURCES.items():
        lv = read(dataset / name, project)
        written = [write_xlsx(lv, out / f"{project}_a.xlsx", "a"), write_xlsx(lv, out / f"{project}_b.xlsx", "b")]
        if all(item.quantity is not None for item in lv.items):
            written.append(write_pdf(lv, out / f"{project}.pdf"))
        print(f"{project}: {len(lv.items)} items -> {', '.join(path.name for path in written)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RAW / "generated")
