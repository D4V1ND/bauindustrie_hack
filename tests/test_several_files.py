"""Step 0 for an LV that comes as several files. Real data: P6, the dataset's unpriced tender of 27 PDFs, one per trade."""
from pathlib import Path

import pytest

from lv import extract
from lv.extract import read_all
from lv.model import LV, LineItem

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
P6 = sorted(RAW.glob("LV_*.pdf"))
needs_p6 = pytest.mark.skipif(len(P6) != 27, reason="needs the challenge dataset in data/raw")


@needs_p6
def test_p6s_27_trade_files_become_one_lv_whose_item_numbers_stay_unique():
    lv, notes = read_all(P6, "P6")
    files = lv.source_file.split(", ")
    assert len(files) == 26 and "LV_01_Fertigteiltreppen.pdf" not in files
    assert notes[0] == ("LV_01_Fertigteiltreppen.pdf: skipped, all 6 of its items are already in "
                        "LV_00_Roh_und_Erdauarbeiten.pdf")  # the stairs file is an excerpt of the shell works
    assert len(lv.items) == 1125  # 1,131 read, minus LV_01's 6
    assert len({item.id for item in lv.items}) == len(lv.items)  # 91 numbers such as 1.1.10 are in several files
    assert lv.items[0].oz == "F01-1.1.10" and lv.items[0].id == "P6/F01-1.1.10"
    assert all(item.oz.startswith(f"F{files.index(item.source_file) + 1:02}-") for item in lv.items)
    assert lv.name.startswith("79-222 Ausbildungszentrum") and not lv.priced


@needs_p6
def test_files_whose_item_numbers_do_not_repeat_keep_them_and_are_read_in_name_order():
    lv, notes = read_all([RAW / "LV_03_Malerarbeiten.pdf", RAW / "LV_02_PVC_Fussbodenbelag.pdf"], "P6")
    assert lv.source_file == "LV_02_PVC_Fussbodenbelag.pdf, LV_03_Malerarbeiten.pdf" and notes == []
    assert lv.items[0].oz == "14.1.10" and lv.items[-1].source_file == "LV_03_Malerarbeiten.pdf"


@pytest.mark.skipif(not (RAW / "8209.003_Leistungsverzeichnis_leer.X83").exists(), reason="needs the dataset")
def test_the_same_unpriced_lv_in_two_formats_is_read_once():
    lv, notes = read_all([RAW / "8209.003_Leistungsverzeichnis_leer.X83",
                          RAW / "8209.003_Leistungsverzeichnis_leer.D83"], "P1N")
    assert lv.source_file == "8209.003_Leistungsverzeichnis_leer.D83" and len(lv.items) == 159
    assert notes == ["8209.003_Leistungsverzeichnis_leer.X83: skipped, all 159 of its items are already in "
                     "8209.003_Leistungsverzeichnis_leer.D83"]


def test_a_file_with_half_its_items_already_read_is_refused_and_less_is_combined(monkeypatch, tmp_path):
    def item(oz: str, quantity: float) -> LineItem:
        return LineItem(id=f"X/{oz}", oz=oz, section=[], short_text=oz, long_text="", quantity=quantity, unit="St")

    files = {"a.x83": [item("1", 1), item("2", 2), item("3", 3), item("4", 4)],
             "b.x83": [item("1", 1), item("2", 2), item("3", 9), item("5", 5)],  # 2 of 4 known: the same LV, changed
             "c.x83": [item("1", 1), item("6", 6), item("7", 7), item("8", 8)]}  # 1 of 4: another part of the LV
    monkeypatch.setattr(extract, "read", lambda path, project: LV(
        project=project, name="", date=None, source_file=path.name, items=[i.model_copy() for i in files[path.name]]))
    with pytest.raises(ValueError, match=r"b\.x83: 2 of its 4 items are already in a\.x83"):
        read_all([tmp_path / "b.x83", tmp_path / "a.x83"], "X")
    lv, _ = read_all([tmp_path / "c.x83", tmp_path / "a.x83"], "X")
    assert [i.oz for i in lv.items] == ["F01-1", "F01-2", "F01-3", "F01-4", "F02-1", "F02-6", "F02-7", "F02-8"]
