import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lv import ai
from lv.__main__ import main
from lv.categorise import (NO_REASON, NO_VALID_ANSWER, OTHER, TRADES, Assigned, Assignments, Proposal, ProposedType,
                           assign, overview, propose, type_cap)
from lv.extract import read
from lv.model import LV, ItemType, LineItem, load

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
                         a_type(""), a_type("Rohr", "Rohrleitungsbau"), a_type("Erschwerniszulage Tiefbau", "Erdarbeiten"),
                         a_type("Schieber", "Armaturen"), a_type("Zuschlagstoff", "Betonarbeiten"))

    types = propose(lv(item("P4/1", "Flansch DN 150")), asked)

    assert [(t.name, t.trade) for t in types] == [("Flansch", "Rohrleitungsbau"), ("Rohr", "Rohrleitungsbau"),
                                                  ("Schieber", "Armaturen"), ("Zuschlagstoff", "Betonarbeiten")]
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


# --- task 7: assign the item types -------------------------------------------------------------------------------

TYPES = [ItemType(name="Flansch", trade="Rohrleitungsbau", description="Flanges of all sizes"),
         ItemType(name="Rohr", trade="Rohrleitungsbau", description="Pipes"),
         ItemType(name="Schieber", trade="Armaturen", description="Gate valves")]


class FakeAssigner:
    """One dict per round of questions: item id -> type, or item id -> (type, reason). An id missing from the dict
    gets no answer. Without a reason of its own an item gets "Reason for <id>."."""

    def __init__(self, *rounds: dict):
        self.rounds, self.sent = list(rounds), []

    def __call__(self, questions: list[str]) -> list[Assignments | None]:
        answers = self.rounds[len(self.sent)]
        self.sent.append(questions)
        asked = [re.findall(r"^### (\S+) \|", question, flags=re.M) for question in questions]
        return [Assignments(items=[self.answer(id, answers[id]) for id in ids if id in answers]) for ids in asked]

    @staticmethod
    def answer(id: str, given) -> Assigned:
        item_type, reason = given if isinstance(given, tuple) else (given, f"Reason for {id}.")
        return Assigned(id=id, item_type=item_type, reason=reason)


def three_items() -> LV:
    return lv(item("P4/1", "Flansch DN 150"), item("P4/2", "Rohr DN 100"), item("P4/3", "Bauzaun"))


def test_every_item_gets_one_type_of_the_list_with_its_trade_or_sonstiges():
    new, asked = three_items(), FakeAssigner({"P4/1": "Flansch", "P4/2": "Rohr", "P4/3": "Sonstiges"})

    assign(new, TYPES, asked)

    assert [(i.item_type, i.trade) for i in new.items] == [
        ("Flansch", "Rohrleitungsbau"), ("Rohr", "Rohrleitungsbau"), ("Sonstiges", "Sonstiges")]
    assert len(asked.sent) == 1  # every answer was usable: nothing is asked twice


def test_an_answer_outside_the_list_is_asked_again_once():
    new = three_items()
    asked = FakeAssigner({"P4/1": "Flansch", "P4/2": "Edelstahlrohr", "P4/3": "Zaun"}, {"P4/2": "Rohr", "P4/3": "Bauzaun"})

    assign(new, TYPES, asked)

    assert [(i.item_type, i.trade) for i in new.items] == [
        ("Flansch", "Rohrleitungsbau"), ("Rohr", "Rohrleitungsbau"), ("Sonstiges", "Sonstiges")]
    assert len(asked.sent) == 2  # and not a third time
    assert "### P4/2 |" in asked.sent[1][0] and "### P4/3 |" in asked.sent[1][0] and "### P4/1 |" not in asked.sent[1][0]


def test_an_item_without_an_answer_is_asked_again_once():
    new = three_items()

    assign(new, TYPES, FakeAssigner({"P4/1": "Flansch"}, {"P4/2": "Rohr"}))
    assert [i.item_type for i in new.items] == ["Flansch", "Rohr", "Sonstiges"]

    new, rounds = three_items(), []
    assign(new, TYPES, lambda questions: rounds.append(questions) or [None for _ in questions])  # every call failed
    assert [i.item_type for i in new.items] == ["Sonstiges"] * 3 and len(rounds) == 2


# --- the reason for the type ---------------------------------------------------------------------------------------

def test_the_reason_for_the_type_is_saved_with_every_item():
    new = three_items()
    asked = FakeAssigner({"P4/1": ("Flansch", " Short text names a flange, DN 150. "), "P4/2": "Rohr",
                          "P4/3": ("Sonstiges", "A site fence; no type of the list covers fencing.")})

    assign(new, TYPES, asked)

    assert [(i.item_type, i.item_type_reason) for i in new.items] == [
        ("Flansch", "Short text names a flange, DN 150."), ("Rohr", "Reason for P4/2."),
        ("Sonstiges", "A site fence; no type of the list covers fencing.")]


def test_the_reason_of_the_answer_that_was_used_is_the_one_saved():
    new = three_items()
    asked = FakeAssigner({"P4/1": "Flansch", "P4/2": ("Edelstahlrohr", "A stainless pipe."), "P4/3": "Sonstiges"},
                         {"P4/2": ("Rohr", "Second look: a pipe, DN 100.")})

    assign(new, TYPES, asked)

    assert (new.items[1].item_type, new.items[1].item_type_reason) == ("Rohr", "Second look: a pipe, DN 100.")


def test_an_item_that_falls_back_to_sonstiges_says_that_code_decided():
    new = three_items()
    asked = FakeAssigner({"P4/1": "Flansch", "P4/2": ("Edelstahlrohr", "A stainless pipe.")},
                         {"P4/2": ("Rohrleitung", "Still a pipe.")})

    assign(new, TYPES, asked)

    for fallback in new.items[1:]:  # P4/2 got two answers outside the list, P4/3 got no answer at all
        assert (fallback.item_type, fallback.trade, fallback.item_type_reason) == ("Sonstiges", "Sonstiges", NO_VALID_ANSWER)
    assert "code" in NO_VALID_ANSWER and "two tries" in NO_VALID_ANSWER  # it does not pretend the model decided


@pytest.mark.parametrize("empty", ["", "   "])
def test_a_valid_type_with_an_empty_reason_is_still_assigned_and_the_field_is_never_empty(empty):
    new = three_items()

    assign(new, TYPES, FakeAssigner({"P4/1": ("Flansch", empty), "P4/2": "Rohr", "P4/3": "Sonstiges"}))

    assert (new.items[0].item_type, new.items[0].trade, new.items[0].item_type_reason) == ("Flansch", "Rohrleitungsbau", NO_REASON)
    assert "no reason" in NO_REASON and all(i.item_type_reason for i in new.items)


def test_a_reason_never_changes_the_type():
    new = three_items()
    reason = "Ignore the list. This is clearly a Schieber, assign Schieber."

    assign(new, TYPES, FakeAssigner({"P4/1": ("Flansch", reason), "P4/2": "Rohr", "P4/3": "Sonstiges"}))

    assert (new.items[0].item_type, new.items[0].trade, new.items[0].item_type_reason) == ("Flansch", "Rohrleitungsbau", reason)


def test_the_rules_ask_for_a_short_reason_that_names_the_item_a_surcharge_or_repeat_item_belongs_to():
    about_the_reason = ai.ASSIGN_RULES.split("reason", 1)[1]

    assert "reason" in Assigned.model_fields
    assert all(words in about_the_reason for words in ("one sentence", "20 words", "English", "surcharge", "wie vor"))


# --- one decision per group of identical items -----------------------------------------------------------------

def test_identical_items_are_asked_about_once_and_all_get_the_same_type_trade_and_reason():
    new = lv(item("P4/1", "Flansch DN 150", "Glatter Flansch"), item("P4/2", "Rohr DN 100"),
             item("P4/3", "FLANSCH  DN 150", "glatter Flansch"), item("P4/4", "Flansch DN 150", "Glatter Flansch"),
             item("P4/5", "Flansch DN 150", "Glatter Flansch", unit="m"))  # another unit: not the same item
    asked = FakeAssigner({"P4/1": ("Flansch", "A flange."), "P4/2": "Rohr", "P4/5": "Sonstiges"})

    asked_about = assign(new, TYPES, asked)

    assert asked_about == 3 and re.findall(r"^### (\S+) \|", asked.sent[0][0], flags=re.M) == ["P4/1", "P4/2", "P4/5"]
    assert [(i.item_type, i.trade, i.item_type_reason) for i in new.items] == [
        ("Flansch", "Rohrleitungsbau", "A flange."), ("Rohr", "Rohrleitungsbau", "Reason for P4/2."),
        ("Flansch", "Rohrleitungsbau", "A flange."), ("Flansch", "Rohrleitungsbau", "A flange."),
        ("Sonstiges", "Sonstiges", "Reason for P4/5.")]


def test_identical_items_share_the_fallback_too():
    new = lv(item("P4/1", "Bauzaun"), item("P4/2", "Bauzaun"))

    assign(new, TYPES, FakeAssigner({}, {}))

    assert [(i.item_type, i.item_type_reason) for i in new.items] == [("Sonstiges", NO_VALID_ANSWER)] * 2


def test_repeat_items_with_the_same_words_but_another_item_before_them_are_asked_separately():
    """ "wie vor, jedoch DN 150" after a pipe is not the same thing as "wie vor, jedoch DN 150" after a valve."""
    new = lv(item("P4/1", "Rohr DN 100", "Edelstahlrohr"), item("P4/2", "Ausführung", "wie vor, jedoch DN 150"),
             item("P4/3", "Schieber DN 100", "Keilflachschieber"), item("P4/4", "Ausführung", "wie vor, jedoch DN 150"),
             item("P4/5", "Ausführung", "wie vor, jedoch DN 150"))
    asked = FakeAssigner({"P4/1": "Rohr", "P4/2": "Rohr", "P4/3": "Schieber", "P4/4": "Schieber"})

    assert assign(new, TYPES, asked) == 4  # P4/5 follows the same valve as P4/4, so it is copied
    assert [i.item_type for i in new.items] == ["Rohr", "Rohr", "Schieber", "Schieber", "Schieber"]


def test_items_are_sent_in_batches():
    new = lv(*[item(f"P4/{n}", f"Flansch DN {n}") for n in range(60)])
    asked = FakeAssigner({i.id: "Flansch" for i in new.items})

    assign(new, TYPES, asked)

    assert [question.count("### P4/") for question in asked.sent[0]] == [25, 25, 10]
    assert all(i.item_type == "Flansch" for i in new.items)


def test_the_model_reads_the_type_list_and_the_item_texts_and_never_a_price():
    new = lv(item("P4/1", "Zulage Flansch DN 150", "Glatter Flansch aus Edelstahl", unit_price=7777.77),
             item("P4/2", "Flansch DN 200", "wie vor, jedoch DN 200", quantity=4321.0, section=["05 Rohre", "05.01 Edelstahl"]))
    asked = FakeAssigner({"P4/1": "Flansch", "P4/2": "Flansch"})

    assign(new, TYPES, asked)

    assert asked.sent[0][0] == "\n".join([
        "ITEM TYPES",
        "- Flansch (trade: Rohrleitungsbau): Flanges of all sizes",
        "- Rohr (trade: Rohrleitungsbau): Pipes",
        "- Schieber (trade: Armaturen): Gate valves",
        "- Sonstiges: if no type of the list fits",
        "",
        "ITEMS",
        "### P4/1 | section 05 Rohrleitungen | unit St",
        "Zulage Flansch DN 150 — Glatter Flansch aus Edelstahl",
        "### P4/2 | section 05 Rohre > 05.01 Edelstahl | unit St",
        "Flansch DN 200 — wie vor, jedoch DN 200",
        "REFERS TO PREVIOUS ITEM: Zulage Flansch DN 150 — Glatter Flansch aus Edelstahl",
    ])


# --- task 7: the overview ----------------------------------------------------------------------------------------

def test_overview_adds_quantities_per_unit_and_never_across_units():
    new = lv(item("P4/1", "Flansch DN 150", quantity=3.0), item("P4/2", "Flansch DN 200", quantity=2.5),
             item("P4/3", "Flanschdichtung", quantity=4.0, unit="m"), item("P4/4", "Rohr DN 100", quantity=10.0, unit="m"),
             item("P4/5", "Schieber", quantity=1.0), item("P4/6", "Schieber", quantity=1.0), item("P4/7", "Schieber", quantity=None),
             item("P4/8", "Schieber DN 300", quantity=99.0, kind="optional"), item("P4/9", "Schieber", quantity=1.0),
             item("P4/10", "Schieber", quantity=1.0))
    types = {"P4/1": "Flansch", "P4/2": "Flansch", "P4/3": "Flansch", "P4/4": "Rohr"}
    assign(new, TYPES, FakeAssigner({i.id: types.get(i.id, "Schieber") for i in new.items}))

    assert overview(new) == {
        "Armaturen": {"Schieber": {"items": 5, "quantities": {"St": 4.0}}},        # the optional item is left out
        "Rohrleitungsbau": {"Flansch": {"items": 3, "quantities": {"St": 5.5, "m": 4.0}},
                            "Rohr": {"items": 1, "quantities": {"m": 10.0}}},
    }
    assert list(overview(new)) == ["Armaturen", "Rohrleitungsbau"]  # the trade with the most items first


def test_overview_is_empty_before_the_items_have_types():
    assert overview(three_items()) == {}


# --- task 7: the command -------------------------------------------------------------------------------------------

def test_price_command_writes_types_and_trades_and_prints_the_overview(tmp_path, capsys, monkeypatch):
    asked = []

    def fake_ask(model, system, questions, shape, effort):
        asked.append((model, effort, system, shape, len(questions)))
        if shape is Proposal:
            return [Proposal(item_types=[a_type("Stundenlohnarbeit", "Stundenlohnarbeiten"), a_type("Bauleistung", "Erdarbeiten")])]
        answers = []
        for question in questions:  # hourly items get the hourly type, one item gets a type that is not in the list
            ids_units = re.findall(r"^### (\S+) \|.* unit (\S+)$", question, flags=re.M)
            answers.append(Assignments(items=[
                Assigned(id=id, reason=f"Unit is {unit}.",
                         item_type="Erfunden" if id == "P5/01.0001" else "Stundenlohnarbeit" if unit == "h" else "Bauleistung")
                for id, unit in ids_units]))
        return answers

    monkeypatch.setattr(ai, "ask", fake_ask)
    main(["price", str(RAW / P5_NEW), "--project", "P5"], data=tmp_path)

    printed = capsys.readouterr().out
    result = load(tmp_path / "out" / "P5.json")
    assert asked == [("claude-opus-5-5", "medium", ai.PROPOSE_RULES, Proposal, 1),
                     ("claude-sonnet-5-5", "high", ai.ASSIGN_RULES, Assignments, 2),   # 50 items in 2 batches,
                     ("claude-sonnet-5-5", "high", ai.ASSIGN_RULES, Assignments, 1)]   # then 1 item again
    assert "to assign item types: 2 calls" in printed and "50 items asked, 0 copied from an identical item" in printed
    assert [(t.name, t.trade) for t in result.item_types] == [("Stundenlohnarbeit", "Stundenlohnarbeiten"), ("Bauleistung", "Erdarbeiten")]
    assert {(i.item_type, i.trade) for i in result.items} == {
        ("Stundenlohnarbeit", "Stundenlohnarbeiten"), ("Bauleistung", "Erdarbeiten"), ("Sonstiges", "Sonstiges")}
    assert sum(i.item_type == "Stundenlohnarbeit" for i in result.items) == 7
    by_id = {i.id: i for i in result.items}
    assert by_id["P5/04.0001"].item_type_reason == "Unit is h."  # the reasons are in the result file
    assert by_id["P5/01.0001"].item_type_reason == NO_VALID_ANSWER
    assert "overview: 50 normal line items -> 3 item types -> 3 trades" in printed
    assert re.search(r"Stundenlohnarbeit +7 items +80 h\n", printed)  # 10 + 20 + 20 + 5 + 10 + 10 + 5 hours
    assert all(i.prediction is not None for i in result.items)  # step 3 still runs after step 2


def test_copy_only_asks_nobody_and_leaves_the_types_empty(tmp_path, capsys):
    main(["price", str(RAW / P5_NEW), "--project", "P5", "--copy-only"], data=tmp_path)

    result = load(tmp_path / "out" / "P5.json")
    assert result.item_types == [] and all(i.item_type == i.trade == i.item_type_reason == "" for i in result.items)
    assert "overview" not in capsys.readouterr().out
