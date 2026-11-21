import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from lv.__main__ import main
from lv.extract import read
from lv.model import load

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
PRICED = {"P1": "[ORT_024] LV24069.D86", "P2": "StBALV23040.D86"}
NEW = {"P1": "8209.003_Leistungsverzeichnis_leer.X83", "P2": "[NUMMER_001]_leer.X83"}


@pytest.fixture(scope="module")
def priced():
    return {project: read(RAW / name, project) for project, name in PRICED.items()}


@pytest.fixture(scope="module")
def new():
    return {project: read(RAW / name, project) for project, name in NEW.items()}


def stated_sums(name: str) -> dict[str, float]:
    """The section sums as written in the file ("32" records): section number -> sum."""
    lines = (RAW / name).read_bytes().decode("cp850").splitlines()
    return {".".join(part.strip() for part in (line[2:4], line[4:6]) if part.strip()): int(line[12:24]) / 100
            for line in lines if line.startswith("32")}


def plain(oz: str) -> str:
    """Item number without leading zeros: P2 writes 01.01.0002 in the .D86 and 1.1.2 in the .X83."""
    return ".".join(part.lstrip("0") for part in oz.split("."))


HIDDEN = re.compile(r"PERSON_\d+|PER\d+")


def same_but_for_hidden_words(a: str, b: str) -> bool:
    """The dataset's anonymiser hid different words in each file format: "PER000115-Rohr DN 65" in the .D83 is
    "Edelstahl-Rohr DN 65" in the .X83, and "[PERSON_0118]" is "[PERSON_118]". A hidden word matches any word."""
    words_a, words_b = re.split(r"[\s-]+", a), re.split(r"[\s-]+", b)
    return len(words_a) == len(words_b) and all(
        x == y or HIDDEN.search(x) or HIDDEN.search(y) for x, y in zip(words_a, words_b))


# --- the layout, checked on the one LV that exists in both formats -------------------------------------------

def test_p1_d83_gives_the_same_items_as_p1_x83(new):
    d83 = read(RAW / "8209.003_Leistungsverzeichnis_leer.D83", "P1")
    x83 = new["P1"]

    assert len(d83.items) == 159
    for a, b in zip(d83.items, x83.items, strict=True):
        assert (a.oz, a.quantity, a.unit, a.kind) == (b.oz, b.quantity, b.unit, b.kind)
        assert same_but_for_hidden_words(a.short_text, b.short_text), a.id
    assert sum(a.short_text == b.short_text for a, b in zip(d83.items, x83.items)) == 112
    assert not d83.priced


def test_header_and_sections_are_read(priced):
    lv = priced["P1"]

    assert (lv.project, lv.name, lv.date) == ("P1", "Maschinentechnik - Anlagenbau", date(2024, 2, 27))
    assert lv.source_file == PRICED["P1"]
    assert lv.items[0].section == ["01 Baustelleneinrichtung - Montagepläne - I", "01.01 Baustelleneinrichtung und Dokumentation"]
    assert priced["P2"].date == date(2023, 3, 15)


def test_first_item_of_p1_is_read_in_full(priced):
    item = priced["P1"].items[0]

    assert (item.id, item.oz) == ("P1/01.01.1", "01.01.1")
    assert item.short_text == "Baustelleneinrichtung und -räumung"
    assert item.long_text.startswith("Vollständige Einrichtung der Baustelle, vorhalten und nach Ende der Baumaßnahme")
    assert (item.quantity, item.unit, item.unit_price, item.total_price) == (1.0, "psch", 5700.0, 5700.0)
    assert (item.kind, item.read_by) == ("normal", "gaeb")


def test_prices_are_read_to_the_cent(priced):
    by_oz = {item.oz: item for item in priced["P2"].items}

    item = by_oz["03.02.0004"]
    assert (item.quantity, item.unit, item.unit_price, item.total_price) == (20.0, "St", 15.40, 308.00)
    item = by_oz["07.01.0001"]
    assert (item.quantity, item.unit, item.unit_price, item.total_price) == (318.0, "m", 236.47, 75197.46)


def test_item_ids_are_unique(priced):
    for lv in priced.values():
        assert len({item.id for item in lv.items}) == len(lv.items)


# --- prices: every number is checked against what the file itself states ---------------------------------------

@pytest.mark.parametrize("project", ["P1", "P2"])
def test_quantity_times_unit_price_is_the_total_price(priced, project):
    for item in priced[project].items:
        assert item.total_price == pytest.approx(item.quantity * item.unit_price, abs=0.005), item.id


@pytest.mark.parametrize("project, total", [("P1", 536_991.65), ("P2", 897_305.45)])
def test_item_totals_add_up_to_the_sums_stated_in_the_file(priced, project, total):
    items = [item for item in priced[project].items if item.kind == "normal"]
    sums = stated_sums(PRICED[project])

    for number, stated in sums.items():  # by section, not by item number: one item number of P2 was anonymised
        own = sum(item.total_price for item in items if any(title.split()[0] == number for title in item.section))
        assert own == pytest.approx(stated, abs=0.005), number
    assert sum(item.total_price for item in items) == pytest.approx(total, abs=0.005)
    assert sum(stated for number, stated in sums.items() if "." not in number) == pytest.approx(total, abs=0.005)


def test_notes_without_a_price_do_not_leak_into_items(priced):
    by_oz = {item.oz: item for item in priced["P2"].items}

    # 03.01.0001 follows a note ("20" record); 07.01.0001 follows a description block ("41"/"42" records)
    assert "Kalkulationsgrundlage" not in by_oz["07.01.0001"].short_text
    assert all(len(item.long_text) < 9000 for lv in priced.values() for item in lv.items)


def test_text_additions_are_read_without_their_markup(priced):
    by_oz = {item.oz: item for item in priced["P2"].items}

    assert by_oz["01.01.0007"].long_text == ("Bauwasseranschluss am Hydranten vorhalten, Positionsmenge = Produkt aus "
                                             "1 Stück(Vorhaltemenge) mal 28 Wochen (Vorhaltedauer).")
    assert ("des AN, Anlage (Bezeichnung/Ort) .......................... vom Bieter einzutragen vom Bieter einzutragen,"
            " Abfall") in by_oz["03.01.0001"].long_text  # the tail is written twice, in the XML file too
    assert by_oz["01.01.0003"].long_text.endswith("[ORT_00030] für Herstellung des Rohranschlusses sind einzukalkulieren. .")
    assert not any(re.search(r"\[T[AB]\d|(^| )[\[\]]( |$)", item.long_text) for item in priced["P2"].items)


def test_units_come_out_in_the_same_spelling_as_from_xml(priced, new):
    for project in PRICED:
        with_unit = Counter(item.unit for item in priced[project].items if item.unit)
        assert set(with_unit) == {item.unit for item in new[project].items}
    assert Counter(item.unit for item in priced["P1"].items)["m3"] == 2  # written "m³"


def test_std_without_a_dot_is_not_hours(priced, new):
    """P2's own unit list says "Std  Stück x Tag" (piece x day). Hours are written "h" there, "Std." in P5."""
    for lv in (priced["P2"], new["P2"]):
        units = Counter(item.unit for item in lv.items)
        assert (units["Std"], units["h"]) == (3, 5)


# --- why the priced file has more items than the tender file ---------------------------------------------------

def test_p1_extra_items_are_the_change_orders_and_they_are_kept(priced, new):
    """197 item records against 159: the 38 extra ones are section 8 "Nachträge" (change orders agreed after the
    tender). They are kept, because they are real items with real unit prices. The file gives them no quantity,
    no unit and no total, so they add nothing to the sums and can never be offered for an item that has a unit."""
    tender = {item.oz for item in new["P1"].items}
    extra = [item for item in priced["P1"].items if item.oz not in tender]

    assert (len(priced["P1"].items), len(tender), len(extra)) == (197, 159, 38)
    assert all(item.section[0] == "8 Nachträge" for item in extra)
    assert all(item.unit_price > 0 and (item.quantity, item.unit, item.total_price) == (0.0, "", 0.0) for item in extra)
    assert {item.oz for item in priced["P1"].items} - {item.oz for item in extra} == tender


def test_p2_extra_item_is_one_change_order_and_one_item_number_was_anonymised(priced, new):
    """249 item records against 248: the extra one is 10.01.0001 in section 10 "Nachträge" (kept, as in P1).
    All other items are the tender's items. One of them, 08.01.0003, has lost its number: the dataset's
    anonymiser replaced "08010003" by "TEL00009". It is kept as written; its section still says where it sits."""
    tender = {plain(item.oz) for item in new["P2"].items}
    own = {plain(item.oz): item for item in priced["P2"].items}

    assert (len(own), len(tender)) == (249, 248)
    assert set(own) - tender == {"10.1.1", "TE.L0.9"}
    assert tender - set(own) == {"8.1.3"}
    assert own["10.1.1"].section[0] == "10 Nachträge"
    assert own["TE.L0.9"].section[0].startswith("08 ")
    assert own["TE.L0.9"].short_text == next(i for i in new["P2"].items if i.oz == "8.1.3").short_text


# --- the other GAEB 90 files and the command -------------------------------------------------------------------

def test_p3_bid_file_is_read_with_its_own_spelling_of_prices_and_umlauts():
    """P3's .D84 writes unit prices with 3 decimals and uses another character set. Its texts are in a PDF."""
    lv = read(RAW / "Angebot AN24-0305.D84", "P3")

    assert len(lv.items) == 274
    assert Counter(item.unit for item in lv.items)["m3"] == 34
    assert (lv.items[1].quantity, lv.items[1].unit, lv.items[1].unit_price, lv.items[1].total_price) == (6.25, "Mt", 750.0, 4687.5)


def test_a_file_with_prices_but_no_items_is_refused():
    with pytest.raises(ValueError, match="no line items"):
        read(RAW / "StBALV23040.D84", "P2")  # prices only, no texts, quantities or units


def test_ingest_command_writes_the_library_file(tmp_path, capsys):
    main(["ingest", str(RAW / PRICED["P2"]), "--project", "P2"], data=tmp_path)

    lv = load(tmp_path / "library" / "P2.json")
    assert (len(lv.items), lv.priced) == (249, True)
    assert "249 line items" in capsys.readouterr().out
