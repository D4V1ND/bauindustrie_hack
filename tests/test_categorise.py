from pathlib import Path

import pytest
from pydantic import ValidationError

from lv.categorise import OTHER, TRADES, Proposal, ProposedType, propose, type_cap
from lv.extract import read
from lv.model import LV, LineItem

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
P1_NEW, P5_NEW = "8209.003_Leistungsverzeichnis_leer.X83", "GAEB-[PROJEKT_004].X83"


def item(id: str, short_text: str, long_text: str = "", **changes) -> LineItem:
    fields = dict(id=id, oz=id.split("/")[1], section=["05 Rohrleitungen"], short_text=short_text,
                  long_text=long_text, quantity=1.0, unit="St")
    return LineItem(**(fields | changes))


def lv(*items: LineItem) -> LV:
    return LV(project="P4", name="", date=None, source_file="", items=list(items))


def a_type(name: str, trade: str = "Rohrleitungsbau", description: str = "what belongs here") -> ProposedType:
    return ProposedType(name=name, trade=trade, description=description)


class FakeProposer:
    """Answers with a fixed list and keeps what it was sent."""

    def __init__(self, *types: ProposedType):
        self.types, self.sent = list(types), []

    def __call__(self, question: str) -> Proposal | None:
        self.sent.append(question)
        return Proposal(item_types=self.types)


# --- task 6: propose the item type list ------------------------------------------------------------------------

def test_the_fixed_trade_list_has_17_names_and_ends_with_the_one_for_leftovers():
    assert len(TRADES) == len(set(TRADES)) == 17 and TRADES[-1] == OTHER == "Sonstiges"


@pytest.mark.parametrize("items, cap", [(159, 40), (296, 74), (1000, 100), (50, 20), (2, 20)])
def test_cap_is_about_one_type_per_four_items_between_20_and_100(items, cap):
    assert type_cap(items) == cap


def test_the_model_reads_sections_short_texts_and_the_cap_and_never_a_price():
    new = lv(item("P4/1", "Flansch DN 150, PN 10", "Glatter Flansch aus Edelstahl, " * 30, unit_price=7777.77),
             item("P4/2", "Zulage Flansch DN 200", "wie vor", quantity=4321.0),
             item("P4/3", "Baustelle einrichten", "Geräte bereitstellen", section=["01 Baustelleneinrichtung", "01.01 Allgemein"], unit="psch"))
    asked = FakeProposer(a_type("Flansch"))

    propose(new, asked)

    question = asked.sent[0]
    assert "At most 20 item types." in question
    assert all(f'"{trade}"' in question for trade in TRADES)
    assert "## 05 Rohrleitungen\n- P4/1 | St | Flansch DN 150, PN 10 — Glatter Flansch aus Edelstahl" in question
    assert "## 01 Baustelleneinrichtung > 01.01 Allgemein\n- P4/3 | psch | Baustelle einrichten — Geräte bereitstellen" in question
    assert question.count("## 05 Rohrleitungen") == 1  # a section title once, its items below it
    assert "7777" not in question and "4321" not in question
    assert max(len(line) for line in question.splitlines()) < 400  # long texts are cut


def test_the_list_is_cleaned_in_code_whatever_the_model_answers():
    asked = FakeProposer(a_type(" Flansch "), a_type("flansch"), a_type("Zulage Flansch"), a_type("Sonstiges"),
                         a_type(""), a_type("Rohr", "Rohrleitungsbau"), a_type("Schieber", "Armaturen"))

    types = propose(lv(item("P4/1", "Flansch DN 150")), asked)

    assert [(t.name, t.trade) for t in types] == [("Flansch", "Rohrleitungsbau"), ("Rohr", "Rohrleitungsbau"), ("Schieber", "Armaturen")]
    assert all(t.description for t in types)


def test_a_trade_outside_the_fixed_list_is_not_accepted():
    with pytest.raises(ValidationError):
        a_type("Flansch", trade="Sanitär")


def test_no_list_stops_with_a_message():
    with pytest.raises(RuntimeError, match="item type"):
        propose(lv(item("P4/1", "Flansch")), lambda question: None)
    with pytest.raises(RuntimeError, match="item type"):
        propose(lv(item("P4/1", "Flansch")), FakeProposer(a_type("Zulage")))


def test_p1_gets_40_types_or_fewer_even_if_the_model_proposes_more():
    p1 = read(RAW / P1_NEW, "P1")
    asked = FakeProposer(*[a_type(f"Typ {n}") for n in range(60)])

    assert len(propose(p1, asked)) == 40
    assert "At most 40 item types." in asked.sent[0] and asked.sent[0].count("\n- P1/") == 159
