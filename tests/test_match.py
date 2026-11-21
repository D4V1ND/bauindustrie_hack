from collections import Counter
from pathlib import Path

import pytest

from lv.__main__ import main
from lv.extract import read
from lv.match import group_label
from lv.model import LineItem, load

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"


def item(**changes) -> LineItem:
    fields = dict(id="P1/05.01.36", oz="05.01.36", section=[], short_text="Flansch DN 150, PN 10",
                  long_text="Glatter Flansch, DN 150, PN 10 sonst wie vor.", quantity=1, unit="St")
    return LineItem(**(fields | changes))


def groups_with_more_than_one_item(file: str) -> list[int]:
    sizes = Counter(group_label(i) for i in read(RAW / file, "X").items)
    return [size for size in sizes.values() if size > 1]


# --- step 1: the group label ---------------------------------------------------------------------------------

@pytest.mark.parametrize("other", [
    item(short_text="FLANSCH dn 150, pn 10"),
    item(long_text="Glatter  Flansch,\n DN 150, PN 10\tsonst wie vor. "),
    item(id="P2/01.02.0003", oz="01.02.0003", quantity=40, unit_price=175.40, section=["05 Rohrleitungen"]),
])
def test_same_label_despite_differences_without_meaning(other):
    assert group_label(other) == group_label(item())


def test_same_label_despite_a_line_break_hyphen():
    assert group_label(item(long_text="Baustellenzufahrten und We- ge")) \
        == group_label(item(long_text="Baustellenzufahrten und Wege"))


@pytest.mark.parametrize("other", [
    item(short_text="Flansch DN 200, PN 10"),
    item(long_text="Glatter Flansch, DN 200, PN 10 sonst wie vor."),
    item(unit="m"),
])
def test_different_label_when_one_character_of_meaning_or_the_unit_differs(other):
    assert group_label(other) != group_label(item())


def test_short_and_long_text_are_not_mixed_up():
    assert group_label(item(short_text="Rohr", long_text="DN 100")) != group_label(item(short_text="Rohr DN", long_text="100"))


@pytest.mark.parametrize("written", ["[PERSON_0000115]-Flansch", "PERSON_115-Flansch", "PER000115-Flansch"])
def test_a_hidden_word_has_one_label_however_the_anonymiser_spelled_it(written):
    assert group_label(item(short_text=written)) == group_label(item(short_text="[PERSON_115]-Flansch"))


def test_two_different_hidden_words_keep_different_labels():
    assert group_label(item(short_text="[PERSON_115]-Flansch")) != group_label(item(short_text="[PERSON_116]-Flansch"))
    assert group_label(item(short_text="[PERSON_115]-Flansch")) != group_label(item(short_text="[ORT_115]-Flansch"))


def test_groups_in_the_real_files():
    p2 = groups_with_more_than_one_item("[NUMMER_001]_leer.X83")
    p4 = groups_with_more_than_one_item("501-24-01_GAEB-XML_sas.x83")

    assert len(p2) >= 27 and sum(p2) >= 55
    assert len(p4) >= 7 and sum(p4) >= 15


def test_both_commands_write_the_group_label(tmp_path):
    main(["ingest", str(RAW / "StBALV23040.D86"), "--project", "P2"], data=tmp_path)
    main(["price", str(RAW / "GAEB-[PROJEKT_004].X83"), "--project", "P5"], data=tmp_path)

    for path in (tmp_path / "library" / "P2.json", tmp_path / "out" / "P5.json"):
        assert all(len(i.group) == 12 and i.group == group_label(i) for i in load(path).items)
