"""Step 0 for Excel and PDF LVs. Test files are written from GAEB files by lv.samples, read back and compared
with the GAEB reading; the dataset's P6 exists only as PDFs and is read as it is."""
from collections import Counter
from pathlib import Path

import openpyxl
import pytest

from lv.__main__ import main
from lv.extract import read
from lv.model import LV, load
from lv.samples import SOURCES, fitted, write_pdf, write_xlsx

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"


@pytest.fixture(scope="module")
def gaeb() -> dict[str, LV]:
    return {project: read(RAW / name, project) for project, name in SOURCES.items()}


def fields(lv: LV, prices: bool = True) -> list[tuple]:
    """What a reader must carry over from the file, item by item."""
    return [(i.oz, i.section, i.short_text, i.long_text, i.quantity, i.unit, i.kind)
            + ((i.unit_price, i.total_price) if prices else ()) for i in lv.items]


@pytest.mark.parametrize("style", ["a", "b"])
@pytest.mark.parametrize("project", list(SOURCES))
def test_an_excel_lv_reads_back_like_its_gaeb_file(gaeb, tmp_path, project, style):
    lv = read(write_xlsx(gaeb[project], tmp_path / f"{project}.xlsx", style), project)

    assert fields(lv) == fields(fitted(gaeb[project], "xlsx"))
    assert lv.name == gaeb[project].name and lv.source_file == f"{project}.xlsx"
    assert {item.read_by for item in lv.items} == {"excel"}


@pytest.mark.parametrize("project", list(SOURCES))
def test_a_pdf_lv_reads_back_like_its_gaeb_file_without_its_prices(gaeb, tmp_path, project):
    lv = read(write_pdf(gaeb[project], tmp_path / f"{project}.pdf"), project)

    assert fields(lv, prices=False) == fields(fitted(gaeb[project], "pdf"), prices=False)
    assert lv.name == gaeb[project].name and not lv.priced  # no price is ever taken from a PDF
    assert {item.read_by for item in lv.items} == {"pdf"}


def test_optional_items_survive_both_formats(gaeb, tmp_path):
    optional = sum(1 for item in gaeb["P4"].items if item.kind == "optional")
    assert optional == 24
    for path in (write_xlsx(gaeb["P4"], tmp_path / "P4.xlsx", "b"), write_pdf(gaeb["P4"], tmp_path / "P4.pdf")):
        assert sum(1 for item in read(path, "P4").items if item.kind == "optional") == optional


def test_a_priced_excel_lv_goes_into_the_price_library(gaeb, tmp_path):
    path = write_xlsx(gaeb["P2"], tmp_path / "P2.xlsx")

    main(["ingest", str(path), "--project", "P2"], data=tmp_path)

    library = load(tmp_path / "library" / "P2.json")
    assert [i.unit_price for i in library.items] == [i.unit_price for i in gaeb["P2"].items]
    assert library.priced and len(library.items) == 249


def test_an_excel_file_that_does_not_fit_is_refused(gaeb, tmp_path):
    book = openpyxl.Workbook()
    book.active.append(["Nummer", "Was", "Wieviel"])
    book.save(tmp_path / "odd.xlsx")
    with pytest.raises(ValueError, match="header row"):
        read(tmp_path / "odd.xlsx", "X")

    path = write_xlsx(gaeb["P2"], tmp_path / "P2.xlsx")
    book = openpyxl.load_workbook(path)
    sheet = book.active
    row = next(r for r in sheet.iter_rows(min_row=4) if r[3].value)  # the first item
    row[6].value = row[6].value * 2  # GP no longer equals quantity x EP: columns mixed up somewhere
    book.save(path)
    with pytest.raises(ValueError, match="quantity x unit price"):
        read(path, "P2")


def test_a_pdf_in_another_layout_is_refused_and_p3s_tender_print_is_read():
    with pytest.raises(ValueError, match="layout"):
        read(RAW / "LV-Ausdruck-[PROJEKT_004].pdf", "P5")
    p3 = read(RAW / "Leistungsverzeichnis.pdf", "P3")  # the unpriced P3, printed by the same program as P6
    assert len(p3.items) == 274 and (p3.items[0].oz, p3.items[0].quantity, p3.items[0].unit) == ("1.1.10", 1.0, "psch")


@pytest.fixture(scope="module")
def p6() -> dict[str, LV]:
    return {path.stem: read(path, f"P6-{path.stem[3:6].strip('_')}") for path in sorted(RAW.glob("LV_*.pdf"))}


def test_every_p6_pdf_is_read_with_a_quantity_and_unit_for_each_item(p6):
    assert len(p6) == 27 and all(lv.items for lv in p6.values())
    items = [item for lv in p6.values() for item in lv.items]
    for lv in p6.values():
        ids = [item.id for item in lv.items]
        assert len(ids) == len(set(ids)), lv.source_file
    assert len(items) == 1131 and sum(1 for item in items if item.kind == "optional") == 138
    assert all(item.quantity is not None for item in items)
    # As printed: 9 items with a quantity but no unit, and one empty row "13.4.210. 0,000" in LV_21
    assert sorted(item.oz for item in items if not item.unit) == ["1.4.7", "13.4.210"] + [f"2.6..{n}" for n in range(1, 9)]
    assert [item.oz for item in items if not item.short_text] == ["13.4.210"]
    units = Counter(item.unit for item in items)
    assert {"St", "m", "m2", "m3", "psch", "h", "t"} <= set(units) and not {"PSCH", "Psch", "pau"} & set(units)


def test_p6_items_keep_their_texts_sections_and_optional_flag(p6):
    paint = {item.oz: item for item in p6["LV_03_Malerarbeiten"].items}
    item = paint["15.1.20"]
    assert item.short_text == "Dispersionsfarbe Betonstützwand und Sockel,bis h=2,30m"
    assert item.long_text.startswith("Innenanstrich für Wandflächen aus Beton (Betonstützwand und Betonsockel)")
    assert item.long_text.endswith("Angebotenes [PERSON_129] .........................................")
    assert (item.quantity, item.unit, item.kind) == (48.0, "m2", "normal")
    assert item.section == ["15 Malerarbeiten", "15.1 Malerarbeiten [PERSON_114]"]
    assert paint["15.1.10"].long_text.startswith("Überstehende [PERSON_074] der Bodenplatte")  # a line the anonymiser moved
    assert paint["15.1.50"].kind == "optional" and not paint["15.1.50"].short_text.startswith("***")

    sealing = p6["LV_15_Abdichtung_Aussenwaende"].items
    assert [(i.oz, i.quantity, i.unit) for i in sealing] == [("1.1.10", 181.0, "m2"), ("1.1.20", 181.0, "m2")]
    assert sealing[1].long_text.endswith("Ausführungshöhe ca. 3,70 m, nach Angabe Bauleitung")  # VAT lines stay out
