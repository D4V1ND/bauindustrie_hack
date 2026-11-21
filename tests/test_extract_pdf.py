"""P3: prices from the GAEB 90 bid file, texts from two PDF prints of the same LV, joined by item number."""
import re
from pathlib import Path

import pytest

from lv import extract
from lv.__main__ import main
from lv.extract import PdfItem, add_texts, read
from lv.model import load

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
PRICES = RAW / "Angebot AN24-0305.D84"
BID_PDF = RAW / "Angebot AN24-0305.pdf"        # the priced bid: item number, quantity, unit, short text
LV_PDF = RAW / "Leistungsverzeichnis.pdf"      # the tender LV: short and long text
MANHOLES_PDF = RAW / "LV-Schachtbauwerk.pdf"   # the tender LV's manholes section only: 28 items
REPEAT = re.compile(r"\bwie (vor|zuvor)\b", re.I)


@pytest.fixture(scope="module")
def p3():
    lv = read(PRICES, "P3")
    notes = add_texts(lv, [BID_PDF, LV_PDF])
    return lv, notes, {item.oz: item for item in lv.items}


def test_every_priced_item_gets_its_text_and_nothing_is_reported(p3):
    lv, notes, _ = p3

    assert len(lv.items) == 274 and notes == []  # no item without text, no quantity or unit that disagrees
    assert all(item.unit_price and item.short_text and item.read_by == "pdf" for item in lv.items)


def test_prices_quantities_and_units_still_come_from_the_prices_file(p3):
    lv, _, by_oz = p3
    untouched = {item.oz: item for item in read(PRICES, "P3").items}

    assert all((item.quantity, item.unit, item.unit_price, item.total_price) ==
               (untouched[item.oz].quantity, untouched[item.oz].unit, untouched[item.oz].unit_price, untouched[item.oz].total_price)
               for item in lv.items)
    assert sum(item.total_price for item in lv.items) == pytest.approx(988_913.89, abs=0.005)
    assert (by_oz["2.1.20"].quantity, by_oz["2.1.20"].unit, by_oz["2.1.20"].unit_price) == (3600.0, "m2", 0.05)  # a real bid price


def test_texts_are_the_pdfs_words(p3):
    _, _, by_oz = p3

    item = by_oz["1.1.20"]
    assert item.short_text == "Baustelleneinrichtung vorhalten"
    assert item.long_text == (
        "Vorhalten der Baustelleneinrichtung. Mieten, Pacht, Gebühren udgl. gehören zum Leistungsumfang. Außer den "
        "vollen Monaten werden Teilzeiten nach Kalendertagen zu 1/30 des Einheitspreises abgerechnet. "
        "Stillstandszeiten und Bauzeitverlängerungen werden nur vergütet, wenn die Ursachen vom AG zu vertreten sind.")
    # a short text over two lines
    assert by_oz["2.1.50"].short_text == "Wiesen- und Ackerflächen herrichten (Baufeldbreite 12 m bis 14 m)"
    assert by_oz["2.1.50"].long_text.startswith("Bei Trassenverlauf in Wiesen wird der Humus nur im unmittel- barem Grabenbereich")
    # a repeat item: one line, and the PDF has no long text for it
    assert (by_oz["3.2.20"].short_text, by_oz["3.2.20"].long_text) == ("wie vor jedoch, GGG DN 250 verlegen", "")
    # the one item whose text is missing in the bid print: taken from the tender print
    assert (by_oz["3.2.160"].short_text, by_oz["3.2.160"].long_text) == ("wie vor, jedoch MMK 300/45°", "")
    # an hourly rate: one line only
    assert (by_oz["7.1.20"].short_text, by_oz["7.1.20"].long_text, by_oz["7.1.20"].unit) == ("BG I, Werkpolier", "", "h")


def test_a_long_text_runs_across_pages_without_page_headers_or_pictures(p3):
    _, _, by_oz = p3
    long = by_oz["2.1.60"].long_text  # starts on page 6 and ends on page 8

    assert by_oz["2.1.60"].short_text == "Rohrgrabenaushub HB 1 - Schotter"
    assert long.startswith("Rohrgrabenaushub in Maschinenschachtung für [ORT_031] Spül- und Entleerungsleitungen")
    assert "Abzufahrendes [PERSON_093] wird gesondert vergütet." in long  # a line the plain reading order misplaces
    assert long.endswith("Maschinenschachtung und Einbau in allen Tiefen für Homogenbereich 1 - Schotter")
    assert not any(re.search(r"Seite: \d|Kurz- und Langtext|Ordnungszahl|Gesamtbetrag|\[BILD", item.short_text + item.long_text)
                   for item in by_oz.values())  # no page header, page number or picture mark in any text
    assert by_oz["2.1.100"].long_text.endswith("Ein Zaunelement weißt folgende Maße auf: L=2,0m H=2,0m")


def test_items_without_a_long_text_have_none_in_the_pdf(p3):
    """106 of 274. Checked against the PDF: for each of them the print shows the short text and then the quantity.
    89 are repeat items ("wie vor, jedoch ..."), 16 are hourly rates and one is a fitting, each one line long."""
    lv, _, _ = p3
    without = [item for item in lv.items if not item.long_text]

    assert (len(lv.items) - len(without), len(without)) == (168, 106)
    assert sum(bool(REPEAT.search(item.short_text)) for item in without) == 89
    assert sorted(item.oz.rsplit(".", 1)[0] for item in without if not REPEAT.search(item.short_text)) == ["5.3"] + ["7.1"] * 16


def test_sections_come_from_the_pdf_titles(p3):
    _, _, by_oz = p3

    assert by_oz["1.1.10"].section == ["1 Baustelleneinrichtung", "1.1 Baustelleneinrichtung"]
    assert by_oz["2.2.10"].section == ["2 Rohrgraben", "2.2 Wasserhaltung"]
    assert all(len(item.section) == 2 and "...." not in " ".join(item.section) for item in by_oz.values())


def test_repeat_and_surcharge_items_are_recognised_in_the_pdf_texts(p3):
    from lv.match import describe

    lv, _, _ = p3
    described = {d.item.oz: d for d in describe(lv)}

    assert described["2.1.70"].refers_to.startswith("Rohrgrabenaushub HB 1 - Schotter — Rohrgrabenaushub in Maschinenschachtung")
    assert described["2.1.80"].surcharge and not described["2.1.60"].surcharge  # "Zulage Rohrgrabenaushub am Wasserwerk ..."


def test_one_pdf_alone_gives_what_it_holds():
    bid_only, lv_only = read(PRICES, "P3"), read(PRICES, "P3")

    assert add_texts(bid_only, [BID_PDF]) == ["Angebot AN24-0305.pdf has no text for 1 of the 274 items: 3.2.160"]
    assert sum(bool(i.short_text) for i in bid_only.items) == 273 and not any(i.long_text for i in bid_only.items if i.read_by == "pdf")

    assert add_texts(lv_only, [LV_PDF]) == []
    by_oz = {i.oz: i for i in lv_only.items}
    assert by_oz["1.1.20"].short_text == "Baustelleneinrichtung vorhalten" and by_oz["1.1.20"].long_text.startswith("Vorhalten der")
    # without the bid print the short text is the first line; the second line of a two-line short text goes to the long text
    assert by_oz["2.1.50"].short_text == "Wiesen- und Ackerflächen herrichten (Baufeldbreite 12 m"


def test_priced_items_without_any_text_are_listed_and_kept():
    lv = read(PRICES, "P3")

    notes = add_texts(lv, [MANHOLES_PDF])

    assert len(lv.items) == 274 and sum(i.read_by == "pdf" for i in lv.items) == 28
    assert len(notes) == 1 and notes[0].startswith("LV-Schachtbauwerk.pdf has no text for 246 of the 274 items: 1.1.10, 1.1.20, ")
    assert all(i.unit_price for i in lv.items)  # nothing was dropped


def test_a_text_whose_quantity_or_unit_disagrees_with_the_prices_file_is_not_used(monkeypatch):
    def texts_of_another_lv(path):
        return {"1.1.10": PdfItem(["Baustelle einrichten"], 1.0, "psch", whole=False),
                "1.1.20": PdfItem(["Bauzaun"], 500.0, "m", whole=False),            # the prices file says 6.25 Mt
                "1.1.30": PdfItem(["Baustelle räumen", "Alles abbauen."], 1.0, "St", whole=True)}, {}  # ... says psch

    monkeypatch.setattr(extract, "pdf_texts", texts_of_another_lv)
    lv = read(PRICES, "P3")

    notes = add_texts(lv, [Path("other.pdf")])

    assert [(i.oz, i.short_text, i.read_by) for i in lv.items[:3]] == [
        ("1.1.10", "Baustelle einrichten", "pdf"), ("1.1.20", "", "gaeb"), ("1.1.30", "", "gaeb")]
    assert notes[0] == ("other.pdf: quantity or unit differs from the prices file for 2 items, so their text was not "
                        "used: 1.1.20 (500 m, prices file 6.25 Mt), 1.1.30 (1 St, prices file 1 psch)")
    assert notes[1].startswith("other.pdf has no text for 273 of the 274 items: 1.1.20, 1.1.30, ")


def test_a_file_that_is_not_a_readable_pdf_is_refused():
    with pytest.raises(ValueError, match="StBALV23040.D84"):
        add_texts(read(PRICES, "P3"), [RAW / "StBALV23040.D84"])


def test_ingest_command_takes_the_prices_file_and_its_text_pdfs(tmp_path, capsys):
    main(["ingest", str(PRICES), "--project", "P3", "--texts", str(BID_PDF), str(LV_PDF)], data=tmp_path)

    printed = capsys.readouterr().out
    saved = load(tmp_path / "library" / "P3.json")
    assert "texts from PDF: 274 of 274 items, 168 of them with a long text" in printed
    assert len(saved.items) == 274 and all(i.short_text and i.unit_price and i.group and i.read_by == "pdf" for i in saved.items)


def test_ingest_command_prints_what_is_missing(tmp_path, capsys):
    main(["ingest", str(PRICES), "--project", "P3", "--texts", str(MANHOLES_PDF)], data=tmp_path)

    printed = capsys.readouterr().out
    assert "texts from PDF: 28 of 274 items" in printed and "has no text for 246 of the 274 items" in printed
