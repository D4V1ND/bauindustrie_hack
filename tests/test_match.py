from collections import Counter
from pathlib import Path

import pytest

from lv.__main__ import main
from lv.extract import read
from lv.match import describe, group_label, searcher
from lv.model import LV, LineItem, load

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
    main(["price", str(RAW / "GAEB-[PROJEKT_004].X83"), "--project", "P5", "--copy-only"], data=tmp_path)

    for path in (tmp_path / "library" / "P2.json", tmp_path / "out" / "P5.json"):
        assert all(len(i.group) == 12 and i.group == group_label(i) for i in load(path).items)


# --- step 3: candidate search ------------------------------------------------------------------------------------

def an_lv(project: str, *texts: tuple, unit: str = "St") -> LV:
    """Items from (short text, long text) or (short text, long text, unit)."""
    items = [item(id=f"{project}/{n}", oz=str(n), short_text=t[0], long_text=t[1], unit=t[2] if len(t) > 2 else unit)
             for n, t in enumerate(texts, start=1)]
    return LV(project=project, name="", date=None, source_file="", items=items)


def search_in(library: LV, new: LV) -> dict[str, list[str]]:
    search = searcher(describe(library))
    return {d.item.id: [found.item.id for found in search(d)] for d in describe(new)}


def test_a_candidate_has_the_same_unit():
    library = an_lv("P1", ("Rohr DN 100 Edelstahl", "liefern und verlegen", "m"),
                    ("Rohr DN 100 Edelstahl", "liefern und verlegen", "St"),
                    ("Rohr DN 150 Edelstahl", "liefern und verlegen", "m"))
    new = an_lv("P4", ("Rohr DN 125 Edelstahl", "liefern und verlegen", "m"), ("Rohr DN 125 Edelstahl", "liefern", ""))

    assert search_in(library, new) == {"P4/1": ["P1/1", "P1/3"], "P4/2": []}


def test_a_surcharge_item_only_meets_surcharge_items_and_the_other_way_round():
    library = an_lv("P1", ("Flansch DN 150, PN 10", "Glatter Flansch aus Edelstahl"),
                    ("Flansch DN 150, PN 10", "Glatter Flansch aus Edelstahl, als Zulage zur Rohrleitung"),
                    ("Mehrpreis Flansch DN 200", "Glatter Flansch aus Edelstahl"))
    new = an_lv("P4", ("Flansch DN 125, PN 10", "Glatter Flansch aus Edelstahl"),
                ("Zuschlag Flansch DN 125, PN 10", "Glatter Flansch aus Edelstahl"))

    assert search_in(library, new) == {"P4/1": ["P1/1"], "P4/2": ["P1/2", "P1/3"]}


def test_a_repeat_item_is_searched_with_the_text_of_the_item_it_refers_to():
    library = an_lv("P1", ("Schieber DN 200", "Keilflachschieber aus Gusseisen mit Handrad, liefern und einbauen"),
                    ("Hydrant DN 80", "Unterflurhydrant, liefern und einbauen"))
    new = an_lv("P4", ("Schieber DN 100", "Keilflachschieber aus Gusseisen mit Handrad"), ("Armatur", "wie vor, jedoch DN 150"))
    described = describe(new)

    assert described[1].refers_to == "Schieber DN 100 — Keilflachschieber aus Gusseisen mit Handrad"
    assert described[0].refers_to == ""
    assert search_in(library, new)["P4/2"] == ["P1/1"]
    assert search_in(library, an_lv("P5", ("Armatur", "wie vor, jedoch DN 150")))["P5/1"] == []  # alone it says too little


def test_a_repeat_item_takes_over_the_surcharge_status_and_skips_other_repeat_items():
    new = an_lv("P4", ("Zulage Bogen DN 100", "Edelstahl"), ("Bogen DN 125", "wie vor"), ("Bogen DN 150", "wie Pos. 1"),
                ("Rohr DN 100", "Edelstahl"), ("Rohr DN 125", "Leistung wie zuvor, jedoch DN 125"))

    assert [(d.surcharge, d.refers_to) for d in describe(new)] == [
        (True, ""), (True, "Zulage Bogen DN 100 — Edelstahl"), (True, "Zulage Bogen DN 100 — Edelstahl"),
        (False, ""), (False, "Rohr DN 100 — Edelstahl")]


def test_the_first_item_of_an_lv_refers_to_nothing_even_if_it_says_wie_vor():
    assert describe(an_lv("P4", ("Rohr DN 125", "wie vor, jedoch DN 125")))[0].refers_to == ""


def test_at_most_five_candidates_best_first_and_none_that_is_far_off():
    library = an_lv("P1", *[(f"Rohr DN {dn} Edelstahl geschweisst", "liefern und verlegen") for dn in (50, 65, 80, 100, 125, 150)],
                    ("Rohr DN 100 Edelstahl geschweisst", "liefern und verlegen im Graben"),
                    ("Baustelleneinrichtung", "Container aufstellen, vorhalten und wieder abbauen"))
    new = an_lv("P4", ("Rohr DN 100 Edelstahl geschweisst", "liefern und verlegen im Rohrgraben"),
                ("Bauzaun", "Mobilzaun mit Betonfuss umsetzen"))

    found = search_in(library, new)

    assert len(found["P4/1"]) == 5 and set(found["P4/1"][:2]) == {"P1/4", "P1/7"}  # the two with DN 100 come first
    assert "P1/8" not in found["P4/1"]
    assert found["P4/2"] == []  # nothing in the library shares a word with it
