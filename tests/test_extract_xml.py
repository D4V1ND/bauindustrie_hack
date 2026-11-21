from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from lv.__main__ import main
from lv.extract import clean_unit, read
from lv.model import load

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
FILES = {
    "P1": "8209.003_Leistungsverzeichnis_leer.X83",
    "P2": "[NUMMER_001]_leer.X83",
    "P4": "501-24-01_GAEB-XML_sas.x83",
    "P5": "GAEB-[PROJEKT_004].X83",
}


@pytest.fixture(scope="module")
def lvs():
    return {project: read(RAW / name, project) for project, name in FILES.items()}


def test_every_item_of_each_file_is_read(lvs):
    assert {p: len(lv.items) for p, lv in lvs.items()} == {"P1": 159, "P2": 248, "P4": 296, "P5": 50}


def test_item_ids_are_unique_and_carry_the_project(lvs):
    for project, lv in lvs.items():
        ids = [item.id for item in lv.items]
        assert len(set(ids)) == len(ids)
        assert all(item.id == f"{project}/{item.oz}" for item in lv.items)


def test_items_keep_the_order_of_the_file(lvs):
    assert [item.oz for item in lvs["P5"].items[:3]] == ["01.0001", "01.0002", "01.0003"]
    assert lvs["P1"].items[0].oz == "01.01.1"


def test_only_p4_has_optional_items(lvs):
    optional = {p: sum(item.kind == "optional" for item in lv.items) for p, lv in lvs.items()}

    assert optional == {"P1": 0, "P2": 0, "P4": 24, "P5": 0}


def test_units_come_out_in_one_spelling(lvs):
    units = {p: Counter(item.unit for item in lv.items) for p, lv in lvs.items()}

    assert not {"Stk", "Stk.", "Std.", "m³", "m²", "to"} & set().union(*units.values())
    assert (units["P5"]["St"], units["P5"]["h"], units["P5"]["t"]) == (10, 7, 2)
    assert units["P4"]["St"] == 132  # 127 written "St" and 5 written "Stk"
    assert units["P1"]["m3"] == 2  # written "m³"


@pytest.mark.parametrize("written, cleaned", [
    ("Stk.", "St"), ("Stück", "St"), ("Std.", "h"), ("Std", "Std"), ("m²", "m2"), ("m³", "m3"), ("to", "t"),
    ("psch", "psch"), ("StWo", "StWo"), (" m ", "m"),
])
def test_unit_is_cleaned_to_one_spelling(written, cleaned):
    assert clean_unit(written) == cleaned


def test_first_item_of_p1_is_read_in_full(lvs):
    item = lvs["P1"].items[0]

    assert item.short_text == "Baustelleneinrichtung und -räumung"
    assert item.long_text.startswith("Vollständige Einrichtung der Baustelle")
    assert (item.quantity, item.unit, item.kind, item.read_by) == (1.0, "psch", "normal", "gaeb")
    assert item.section[0] == "01 Baustelleneinrichtung - Montagepläne - Inbetriebnahme"
    assert len(item.section) == 2


def test_every_item_has_a_short_text_a_quantity_and_a_unit(lvs):
    for lv in lvs.values():
        assert all(item.short_text and item.unit and item.quantity is not None for item in lv.items)


def test_header_is_read(lvs):
    lv = lvs["P1"]

    assert (lv.project, lv.name, lv.date) == ("P1", "Maschinentechnik - Anlagenbau", date(2024, 2, 27))
    assert lv.source_file == FILES["P1"]


def test_a_new_lv_has_no_prices(lvs):
    assert not any(lv.priced for lv in lvs.values())


def test_unknown_file_type_is_refused():
    with pytest.raises(ValueError, match="docx"):
        read(RAW / "LV-Positionen.docx", "P3")


def test_price_command_writes_the_result_file(tmp_path):
    main(["price", str(RAW / FILES["P5"]), "--project", "P5", "--copy-only"], data=tmp_path)

    assert len(load(tmp_path / "out" / "P5.json").items) == 50


def test_ingest_refuses_an_lv_without_prices(tmp_path):
    with pytest.raises(SystemExit, match="no prices"):
        main(["ingest", str(RAW / FILES["P5"]), "--project", "P5"], data=tmp_path)

    assert not (tmp_path / "library").exists()
