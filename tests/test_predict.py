import re
from pathlib import Path

import pytest

from lv import ai
from lv.__main__ import main
from lv.ai import JUDGE_RULES
from lv.categorise import Assigned, Assignments, Proposal, ProposedType
from lv.extract import read
from lv.model import LV, LineItem, load
from lv.predict import MAX_PACK, NO_ANSWER, JudgeAnswer, Judged, Verdicts, predict

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
P2_PRICED, P2_NEW, P5_NEW = "StBALV23040.D86", "[NUMMER_001]_leer.X83", "GAEB-[PROJEKT_004].X83"


def item(id: str, short_text: str, long_text: str = "", **changes) -> LineItem:
    fields = dict(id=id, oz=id.split("/")[1], section=[], short_text=short_text, long_text=long_text,
                  quantity=1.0, unit="St")
    return LineItem(**(fields | changes))


def lv(project: str, *items: LineItem) -> LV:
    return LV(project=project, name="", date=None, source_file="", items=list(items))


def plain(oz: str) -> str:
    return ".".join(part.lstrip("0") for part in oz.split("."))


# --- copied: an exact match in the price library ---------------------------------------------------------------

def test_copied_price_comes_from_every_library_item_with_the_same_label():
    library = [lv("P1", item("P1/1", "Flansch DN 150", unit_price=100.0), item("P1/2", "Flansch DN 200", unit_price=999.0)),
               lv("P2", item("P2/1", "FLANSCH  DN 150", unit_price=130.0), item("P2/2", "Flansch DN 150", unit_price=160.0))]
    new = lv("P4", item("P4/1", "Flansch DN 150", quantity=3.0))

    predict(new, library, judge=None)

    found = new.items[0].prediction
    assert (found.method, found.min, found.max, found.median, found.mean, found.count) == ("copied", 100.0, 160.0, 130.0, 130.0, 3)
    assert [(s.id, s.unit_price, s.verdict) for s in found.sources] == [
        ("P1/1", 100.0, "exact"), ("P2/1", 130.0, "exact"), ("P2/2", 160.0, "exact")]
    assert found.reason


def test_a_library_item_without_a_unit_price_is_never_a_source():
    library = [lv("P1", item("P1/1", "Flansch DN 150"), item("P1/2", "Flansch DN 150", unit_price=0.0))]
    new = lv("P4", item("P4/1", "Flansch DN 150"))

    predict(new, library, judge=None)

    assert (new.items[0].prediction.method, new.items[0].prediction.sources) == ("none", [])


def test_a_different_unit_is_not_an_exact_match():
    new = lv("P4", item("P4/1", "Flansch DN 150", unit="m"))

    predict(new, [lv("P1", item("P1/1", "Flansch DN 150", unit_price=100.0))], judge=None)

    assert new.items[0].prediction.method == "none"


def test_the_prediction_is_never_written_into_the_unit_price():
    new = lv("P4", item("P4/1", "Flansch DN 150"))

    predict(new, [lv("P1", item("P1/1", "Flansch DN 150", unit_price=100.0))], judge=None)

    assert (new.items[0].unit_price, new.items[0].total_price, new.priced) == (None, None, False)


# --- the total -------------------------------------------------------------------------------------------------

def test_total_is_median_times_quantity_over_normal_items_with_a_price():
    library = [lv("P1", item("P1/1", "Rohr DN 100", unit_price=10.0), item("P1/2", "Rohr DN 100", unit_price=20.0),
                  item("P1/3", "Rohr DN 100", unit_price=90.0), item("P1/4", "Bogen DN 100", unit_price=7.5))]
    new = lv("P4",
             item("P4/1", "Rohr DN 100", quantity=3.0),                    # median 20 x 3 = 60
             item("P4/2", "Bogen DN 100", quantity=2.0),                   # 7.5 x 2 = 15
             item("P4/3", "Rohr DN 100", quantity=100.0, kind="optional"),  # optional: counted as an item, not in the sum
             item("P4/4", "Schieber DN 100", quantity=5.0))                # nothing found

    predict(new, library, judge=None)

    assert new.summary.total == 75.0
    assert (new.summary.methods["copied"].items, new.summary.methods["copied"].total) == (3, 75.0)
    assert (new.summary.methods["none"].items, new.summary.methods["none"].total) == (1, 0.0)
    assert list(new.summary.methods) == ["copied", "similar", "adjusted", "reference", "none"]


def test_an_item_without_a_quantity_adds_nothing():
    new = lv("P4", item("P4/1", "Rohr DN 100", quantity=None))

    predict(new, [lv("P1", item("P1/1", "Rohr DN 100", unit_price=10.0))], judge=None)

    assert (new.summary.methods["copied"].items, new.summary.total) == (1, 0.0)


# --- the judge: replaced by a fake that answers with fixed verdicts ------------------------------------------

def blocks_in(question: str) -> list[tuple[str, list[str]]]:
    """The items a question asks about: (item id, ids of its candidates), in the order of the question."""
    blocks = []
    for line in question.splitlines():
        if line.startswith("### "):
            blocks.append((line.split()[1], []))
        elif line.startswith("- "):
            blocks[-1][1].append(line[2:].split(": ")[0])
    return blocks


class FakeJudge:
    """Answers every item with the same verdicts and keeps the questions it was sent."""

    def __init__(self, *judged: Judged, reason: str = "The judge's sentence."):
        self.judged, self.reason, self.sent = list(judged), reason, []

    def __call__(self, questions: list[str]) -> list[JudgeAnswer | None]:
        self.sent += questions
        return [JudgeAnswer(items=[Verdicts(id=id, judged=self.judged, reason=self.reason) for id, _ in blocks_in(q)])
                for q in questions]


class RuleJudge:
    """Answers each item by a rule: rule(item id, ids of its candidates, number of earlier calls) gives the verdicts,
    or None to leave the item out of the answer. Keeps the questions of every call."""

    def __init__(self, rule):
        self.rule, self.calls = rule, []

    def __call__(self, questions: list[str]) -> list[JudgeAnswer | None]:
        earlier = len(self.calls)
        self.calls.append(questions)
        answers = []
        for question in questions:
            judged = [(id, self.rule(id, cands, earlier)) for id, cands in blocks_in(question)]
            answers.append(JudgeAnswer(items=[Verdicts(id=id, judged=verdicts, reason=f"Reason for {id}.")
                                              for id, verdicts in judged if verdicts is not None]))
        return answers


def said(cand: str, verdict: str, cand_size: float | None = None, target_size: float | None = None,
         dimension: str | None = None, difference: str = "") -> Judged:
    return Judged(cand=cand, verdict=verdict, cand_size=cand_size, target_size=target_size,
                  dimension=dimension if dimension or cand_size is None else "DN", difference=difference)


def flanges() -> list[LV]:
    """The price library of the judge tests: the same flange in four sizes, and a pipe."""
    return [lv("P1", item("P1/50", "Flansch DN 50, PN 10", "Glatter Flansch aus Edelstahl", unit_price=40.0),
               item("P1/100", "Flansch DN 100, PN 10", "Glatter Flansch aus Edelstahl", unit_price=80.0),
               item("P1/150", "Flansch DN 150, PN 10", "Glatter Flansch aus Edelstahl", unit_price=120.0),
               item("P1/200", "Flansch DN 200, PN 10", "Glatter Flansch aus Edelstahl", unit_price=7777.77),
               item("P1/rohr", "Rohr DN 125", "Edelstahlrohr geschweisst", unit="m", unit_price=55.0))]


def a_flange(**changes) -> LV:
    return lv("P4", item("P4/1", "Flansch DN 125, PN 10", "Glatter Flansch, Edelstahl", quantity=3.0, **changes))


def found_with(*judged: Judged, new: LV | None = None, **judge_settings):
    new = new or a_flange()
    predict(new, flanges(), judge=FakeJudge(*judged, **judge_settings))
    return new.items[0].prediction


def test_similar_when_a_candidate_is_the_same_item():
    found = found_with(said("P1/100", "same", difference="wording"), said("P1/150", "same"), said("P1/50", "different"))

    assert (found.method, found.min, found.max, found.median, found.mean, found.count) == ("similar", 80.0, 120.0, 100.0, 100.0, 2)
    assert [(s.id, s.unit_price, s.verdict, s.difference) for s in found.sources] == [
        ("P1/100", 80.0, "same", "wording"), ("P1/150", 120.0, "same", "")]
    assert found.reason == "The judge's sentence."


def test_similar_wins_over_size_only_candidates():
    found = found_with(said("P1/100", "size_only", 100, 125), said("P1/150", "size_only", 150, 125), said("P1/50", "same"))

    assert (found.method, found.median, [s.id for s in found.sources]) == ("similar", 40.0, ["P1/50"])


def test_a_size_only_candidate_with_two_equal_sizes_counts_as_the_same_item():
    found = found_with(said("P1/100", "size_only", 125, 125))

    assert (found.method, found.median, found.count) == ("similar", 80.0, 1)


def test_adjusted_interpolates_between_a_smaller_and_a_larger_size():
    found = found_with(said("P1/100", "size_only", 100, 125, difference="DN 100 vs DN 125"),
                       said("P1/150", "size_only", 150, 125, difference="DN 150 vs DN 125"))

    assert (found.method, found.min, found.max, found.median, found.mean, found.count) == ("adjusted", 80.0, 120.0, 100.0, 100.0, 2)
    assert (found.rule, found.calculation, found.result) == (
        "linear interpolation by DN", "80 + (125 - 100) / (150 - 100) x (120 - 80)", 100.0)
    assert [(s.id, s.unit_price, s.verdict, s.difference) for s in found.sources] == [
        ("P1/100", 80.0, "size_only", "DN 100 vs DN 125"), ("P1/150", 120.0, "size_only", "DN 150 vs DN 125")]


def test_adjusted_uses_the_nearest_smaller_and_the_nearest_larger_size_and_keeps_the_cents():
    found = found_with(said("P1/50", "size_only", 50, 125), said("P1/100", "size_only", 100, 125),
                       said("P1/150", "size_only", 150, 125), said("P1/200", "size_only", 200, 125),
                       new=lv("P4", item("P4/1", "Flansch DN 125, PN 10", "Glatter Flansch aus Edelstahl")))
    assert [s.id for s in found.sources] == ["P1/100", "P1/150"]

    found = found_with(said("P1/150", "size_only", 150, 160), said("P1/200", "size_only", 200, 160))
    assert found.calculation == "120 + (160 - 150) / (200 - 150) x (7777.77 - 120)"
    assert found.result == 1651.55 == round(eval(found.calculation.replace("x", "*")), 2)  # recomputing gives the result


@pytest.mark.parametrize("judged", [
    [said("P1/100", "size_only", 100, 125), said("P1/50", "size_only", 50, 125)],          # both smaller
    [said("P1/150", "type_only", difference="galvanised steel vs stainless")],            # another material
    [said("P1/100", "size_only", 100, 125), said("P1/150", "size_only", 150, 130)],        # two different targets
    [said("P1/100", "size_only", 100, 125), said("P1/150", "size_only", 150, 125, dimension="PN")],  # two dimensions
    [said("P1/100", "size_only"), said("P1/150", "size_only", 150, 125)],                  # a size is missing
])
def test_reference_lists_the_sources_and_proposes_no_price(judged):
    found = found_with(*judged)

    assert (found.method, found.min, found.max, found.median, found.mean, found.count) == ("reference", None, None, None, None, 0)
    assert {s.id for s in found.sources} == {j.cand for j in judged}
    assert all(s.unit_price > 0 for s in found.sources) and found.rule is None and found.reason


def test_none_when_every_candidate_is_different():
    found = found_with(said("P1/100", "different"), said("P1/150", "different"), reason="All are other products.")

    assert (found.method, found.sources, found.median, found.reason) == ("none", [], None, "All are other products.")


def test_a_verdict_for_an_item_that_was_not_offered_is_ignored():
    found = found_with(said("P9/404", "same"), said("P1/rohr", "same"))  # an invented id; an item with another unit

    assert (found.method, found.sources) == ("none", [])


def test_none_when_the_judge_gives_no_answer_for_the_item():
    new = a_flange()

    predict(new, flanges(), judge=lambda blocks: [None for _ in blocks])

    assert (new.items[0].prediction.method, new.items[0].prediction.sources) == ("none", [])
    assert "no answer" in new.items[0].prediction.reason


def test_the_judge_is_only_asked_about_items_that_have_candidates_and_no_exact_match():
    judge = FakeJudge(said("P1/100", "same"))
    new = lv("P4", item("P4/1", "Flansch DN 100, PN 10", "Glatter Flansch aus Edelstahl"),   # exact match
             item("P4/2", "Flansch DN 125, PN 10", "Glatter Flansch aus Edelstahl"),         # has candidates
             item("P4/3", "Bauzaun", "Mobilzaun umsetzen"))                                  # nothing like it

    predict(new, flanges(), judge=judge)

    assert [i.prediction.method for i in new.items] == ["copied", "similar", "none"]
    assert len(judge.sent) == 1 and judge.sent[0].startswith("### P4/2 ")
    assert "No library item" in new.items[2].prediction.reason


def test_the_judge_is_not_called_when_nothing_needs_judging():
    def judge(blocks):
        raise AssertionError("must not be called")

    new = lv("P4", item("P4/1", "Bauzaun", "Mobilzaun umsetzen"))
    predict(new, flanges(), judge=judge)

    assert new.items[0].prediction.method == "none"


def test_the_judge_reads_texts_and_never_a_price():
    library = [lv("P1", item("P1/1", "Zulage Flansch DN 100", "Glatter Flansch aus Edelstahl", unit_price=7777.77, total_price=8888.88),
                  item("P1/2", "Zulage Flansch DN 150", "wie vor, jedoch DN 150", unit_price=6666.66, quantity=4321.0))]
    new = lv("P4", item("P4/1", "Zulage Flansch DN 80", "Glatter Flansch aus Edelstahl, PN 10", quantity=3.0),
             item("P4/2", "Zulage Flansch DN 125", "wie vor, jedoch DN 125", quantity=12.0))
    judge = FakeJudge()

    predict(new, library, judge=judge)

    assert judge.sent[1] == "\n".join([
        "### P4/2 | unit St | qty 12 | SURCHARGE ITEM",
        "NEW: Zulage Flansch DN 125 — wie vor, jedoch DN 125",
        "NEW REFERS TO PREVIOUS ITEM: Zulage Flansch DN 80 — Glatter Flansch aus Edelstahl, PN 10",
        "- P1/2: Zulage Flansch DN 150 — wie vor, jedoch DN 150 [REFERS TO PREVIOUS ITEM: Zulage Flansch DN 100 — Glatter Flansch aus Edelstahl]",
        "- P1/1: Zulage Flansch DN 100 — Glatter Flansch aus Edelstahl",
    ])
    assert not any(number in block for block in judge.sent for number in ("7777", "8888", "6666", "4321"))


def test_long_texts_are_cut_before_they_go_to_the_judge():
    new = lv("P4", item("P4/1", "Flansch DN 125", "Glatter Flansch aus Edelstahl, " * 60))
    judge = FakeJudge()

    predict(new, flanges(), judge=judge)

    assert len(judge.sent[0].splitlines()[1]) < 480


def test_the_rules_the_judge_gets_are_the_estimators_rules():
    assert "You never state, estimate or guess a\nprice." in JUDGE_RULES
    assert "When in doubt between two verdicts, pick the\nstricter one." in JUDGE_RULES
    assert set(Judged.model_fields) == {"cand", "verdict", "dimension", "cand_size", "target_size", "difference"}
    assert set(Verdicts.model_fields) == {"id", "judged", "reason"}  # the shape named in the rules' "Output" part


def test_total_counts_similar_and_adjusted_but_not_reference():
    new = lv("P4", item("P4/1", "Flansch DN 125, PN 10", "Glatter Flansch, Edelstahl", quantity=3.0))
    predict(new, flanges(), judge=FakeJudge(said("P1/100", "size_only", 100, 125), said("P1/150", "size_only", 150, 125)))
    assert (new.summary.total, new.summary.methods["adjusted"].total) == (300.0, 300.0)

    new = a_flange()
    predict(new, flanges(), judge=FakeJudge(said("P1/100", "type_only")))
    assert (new.summary.total, new.summary.methods["reference"].items) == (0.0, 1)


def test_every_source_is_a_real_item_of_the_price_library():
    library = flanges()
    ids = {i.id for past in library for i in past.items}
    new = lv("P4", *[item(f"P4/{dn}", f"Flansch DN {dn}, PN 10", "Glatter Flansch aus Edelstahl") for dn in (100, 110, 125)])

    predict(new, library, judge=FakeJudge(said("P1/100", "size_only", 100, 110), said("P1/150", "type_only"), said("P7/1", "same")))

    assert all(s.id in ids for i in new.items for s in i.prediction.sources)
    assert all(i.prediction.sources for i in new.items if i.prediction.method != "none")


# --- one search and one judgement per set of identical items -----------------------------------------------------

def test_identical_items_are_judged_once_and_all_carry_the_result_and_the_item_that_was_judged():
    new = lv("P4", item("P4/1", "Flansch DN 125, PN 10", "Glatter Flansch, Edelstahl", quantity=2.0),
             item("P4/2", "Bauzaun", "Mobilzaun umsetzen"),
             item("P4/3", "FLANSCH DN 125, PN 10", "Glatter Flansch,  Edelstahl", quantity=5.0),
             item("P4/4", "Bauzaun", "Mobilzaun umsetzen"))
    judge = FakeJudge(said("P1/100", "same", difference="wording"))

    predict(new, flanges(), judge=judge)

    first, fence, twin, second_fence = (i.prediction for i in new.items)
    assert [id for q in judge.sent for id, _ in blocks_in(q)] == ["P4/1"]  # asked about once
    assert (first.method, first.median, first.judged_item) == ("similar", 80.0, None)
    assert (twin.method, twin.median, twin.judged_item) == ("similar", 80.0, "P4/1")
    assert twin.sources == first.sources and twin.reason == first.reason and twin is not first
    assert (fence.method, fence.judged_item, second_fence.method, second_fence.judged_item) == ("none", None, "none", "P4/2")
    assert new.summary.total == 80.0 * 2 + 80.0 * 5  # each item counts with its own quantity


def test_two_repeat_items_with_the_same_words_after_different_items_are_judged_separately():
    new = lv("P4", item("P4/1", "Flansch DN 100, PN 16", "Glatter Flansch, Edelstahl"), item("P4/2", "Ausführung", "wie vor, jedoch DN 125"),
             item("P4/3", "Blindflansch DN 100", "Edelstahl, PN 10"), item("P4/4", "Ausführung", "wie vor, jedoch DN 125"))
    judge = FakeJudge(said("P1/100", "type_only"))

    predict(new, flanges(), judge=judge)

    assert [id for q in judge.sent for id, _ in blocks_in(q)] == ["P4/1", "P4/2", "P4/3", "P4/4"]
    assert all(i.prediction.judged_item is None for i in new.items)


# --- packs: one judge call per item type -------------------------------------------------------------------------

def typed(*sizes: int, item_type: str = "Flansch", **more) -> list[LineItem]:
    """New flanges that step 2 sorted under one item type. Each finds the four library flanges as candidates."""
    return [item(f"P4/{dn}", f"Flansch DN {dn}, PN 10", "Glatter Flansch, Edelstahl", item_type=item_type,
                 trade="Rohrleitungsbau", **more) for dn in sizes]


def all_different(id: str, cands: list[str], earlier_calls: int) -> list[Judged]:
    return [said(cand, "different") for cand in cands]


def test_items_go_to_the_judge_in_packs_by_item_type_of_at_most_15():
    new = lv("P4", *typed(*range(301, 311)), *typed(401, 402, 403, item_type="Blindflansch"), *typed(*range(311, 321)),
             item("P4/rohr", "Rohr DN 125", "Edelstahlrohr", unit="m"))  # an item step 2 gave no type
    judge = RuleJudge(all_different)

    predict(new, flanges(), judge=judge)

    packs = sorted((q.splitlines()[0], [id for id, _ in blocks_in(q)]) for q in judge.calls[0])
    assert len(judge.calls) == 1 and MAX_PACK == 15
    assert packs == [
        ("### P4/rohr | unit m | qty 1", ["P4/rohr"]),  # no item type: asked on its own, as before
        ('HINT: the 15 new items below were sorted under "Flansch" (trade: Rohrleitungsbau) by an earlier step. '
         "Decide by the texts only.", [f"P4/{dn}" for dn in range(301, 316)]),  # file order inside the pack
        ('HINT: the 3 new items below were sorted under "Blindflansch" (trade: Rohrleitungsbau) by an earlier step. '
         "Decide by the texts only.", ["P4/401", "P4/402", "P4/403"]),
        ('HINT: the 5 new items below were sorted under "Flansch" (trade: Rohrleitungsbau) by an earlier step. '
         "Decide by the texts only.", [f"P4/{dn}" for dn in range(316, 321)]),
    ]
    assert all(q.count("HINT:") == (0 if q.startswith("###") else 1) for q in judge.calls[0])
    assert all(i.prediction.method == "none" and i.prediction.reason.startswith("Reason for") for i in new.items)


def test_a_pack_holds_each_item_in_the_same_form_as_a_single_question():
    new = lv("P4", *typed(125, 160))
    judge, single = RuleJudge(all_different), FakeJudge()

    predict(new, flanges(), judge=judge)
    predict(lv("P4", *typed(125, 160)), flanges(), judge=single, packed=False)

    assert judge.calls[0][0].split("\n\n") == [judge.calls[0][0].splitlines()[0], *single.sent]
    assert len(single.sent) == 2 and not any("HINT" in q for q in single.sent)  # packing can be switched off


def test_an_item_missing_from_a_pack_answer_is_asked_again_on_its_own():
    new = lv("P4", *typed(125, 160, 180))

    def rule(id, cands, earlier_calls):
        if id == "P4/160" and earlier_calls == 0:
            return None  # left out of the pack answer
        return [said(cand, "same" if cand == "P1/150" and id == "P4/160" else "different") for cand in cands]

    judge = RuleJudge(rule)
    predict(new, flanges(), judge=judge)

    assert [len(call) for call in judge.calls] == [1, 1]
    assert judge.calls[1][0].startswith("### P4/160 | unit St") and "HINT" not in judge.calls[1][0]
    assert [i.prediction.method for i in new.items] == ["none", "similar", "none"]
    assert new.items[1].prediction.median == 120.0


@pytest.mark.parametrize("bad_answer", [
    lambda cands: [said(cand, "different") for cand in cands] + [said("P1/rohr", "same")],  # another item's candidate
    lambda cands: [said(cand, "different") for cand in cands[1:]],                          # one candidate missing
    lambda cands: [said(cand, "different") for cand in cands] + [said(cands[0], "same")],   # a candidate twice
    lambda cands: [said(cand, "different") for cand in cands] + [said("P9/404", "same")],   # an invented id
])
def test_a_pack_answer_that_does_not_fit_the_items_own_candidates_is_rejected_and_asked_again(bad_answer):
    new = lv("P4", *typed(125), item("P4/rohr", "Rohr DN 100", "Edelstahlrohr geschweisst", unit="m",
                                     item_type="Flansch", trade="Rohrleitungsbau"))

    def rule(id, cands, earlier_calls):
        if id == "P4/rohr":
            return [said(cand, "same") for cand in cands]
        return bad_answer(cands) if earlier_calls == 0 else [said(cand, "type_only") for cand in cands]

    judge = RuleJudge(rule)
    predict(new, flanges(), judge=judge)

    flange, pipe = (i.prediction for i in new.items)
    assert [[id for q in call for id, _ in blocks_in(q)] for call in judge.calls] == [["P4/125", "P4/rohr"], ["P4/125"]]
    assert flange.method == "reference" and {s.id for s in flange.sources} == {"P1/50", "P1/100", "P1/150", "P1/200"}
    assert (pipe.method, [s.id for s in pipe.sources]) == ("similar", ["P1/rohr"])  # the good part of the answer is kept


def test_what_still_fails_after_the_single_question_counts_as_unanswered():
    def leave_out_125(id, cands, earlier_calls):
        return None if id == "P4/125" else [said(cand, "different") for cand in cands]

    new = lv("P4", *typed(125, 160))
    predict(new, flanges(), judge=RuleJudge(leave_out_125))
    assert [i.prediction.reason for i in new.items] == [NO_ANSWER, "Reason for P4/160."]

    calls = []

    def judge(questions):  # the pack call fails as a whole, and then every single question fails too
        calls.append(len(questions))
        if len(calls) == 1:
            return [None for _ in questions]
        raise RuntimeError("Every call to the judge failed.")

    new = lv("P4", *typed(125, 160))
    predict(new, flanges(), judge=judge)
    assert calls == [1, 2] and [i.prediction.reason for i in new.items] == [NO_ANSWER, NO_ANSWER]


def test_without_item_types_every_item_is_asked_on_its_own_as_before():
    new = lv("P4", *[item(f"P4/{dn}", f"Flansch DN {dn}, PN 10", "Glatter Flansch, Edelstahl") for dn in (125, 160, 180)])
    judge = FakeJudge(said("P1/100", "type_only"), said("P9/404", "same"))  # an invented id is ignored, as before

    predict(new, flanges(), judge=judge)

    assert len(judge.sent) == 3 and all(q.startswith("### P4/") and len(blocks_in(q)) == 1 for q in judge.sent)
    assert all(i.prediction.method == "reference" for i in new.items)


def test_the_rules_say_that_the_hint_line_is_only_a_hint():
    before_the_reading_rules = JUDGE_RULES.split("# How to read the texts")[0]

    assert "HINT" in before_the_reading_rules and "only a hint" in before_the_reading_rules


# --- measured on the real files: P2's tender file priced from P2's own priced file ----------------------------

def test_p2_priced_from_itself_copies_only_correct_prices():
    """The check behind "can we trust a copied price": the tender file (.X83) and the priced file (.D86) hold the
    same items, so every price that is copied must be the price of that very item."""
    priced, new = read(RAW / P2_PRICED, "P2"), read(RAW / P2_NEW, "P2new")
    real = {plain(i.oz): i.unit_price for i in priced.items} | {"8.1.3": 32.74}  # its number was anonymised in the .D86

    predict(new, [priced], judge=None)

    copied = [i for i in new.items if i.prediction.method == "copied"]
    assert all(i.prediction.method in ("copied", "none") for i in new.items)
    assert len(copied) == 160  # of 248: the two files word the other items differently (SPEC.md, known limits)
    assert all(real[plain(i.oz)] in [s.unit_price for s in i.prediction.sources] for i in copied)

    # P2 priced two pairs of identical items differently in different sections (e.g. 318 m against 18 m of the same
    # pipe). For them the range shows both prices and the median lies between. Every other copied price is exact.
    two_prices = {i.oz: (i.prediction.min, i.prediction.max, i.prediction.median) for i in copied
                  if i.prediction.min != i.prediction.max}
    assert two_prices == {"3.2.23": (19.60, 21.00, 20.30), "5.2.21": (19.60, 21.00, 20.30),
                          "7.1.1": (236.47, 610.86, 423.67), "9.1.1": (236.47, 610.86, 423.67)}
    assert all(i.prediction.median == real[plain(i.oz)] for i in copied if i.oz not in two_prices)


# --- the command -------------------------------------------------------------------------------------------------

def test_price_command_stops_without_a_key_unless_it_only_copies(tmp_path):
    main(["ingest", str(RAW / P2_PRICED), "--project", "P2"], data=tmp_path)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY="):
        main(["price", str(RAW / P5_NEW), "--project", "P5"], data=tmp_path)
    assert not (tmp_path / "out" / "P5.json").exists()

    main(["price", str(RAW / P5_NEW), "--project", "P5", "--copy-only"], data=tmp_path)
    assert load(tmp_path / "out" / "P5.json").summary.methods["none"].items == 50


def test_the_judge_needs_no_key_when_nothing_has_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "ask", fake_step_2)  # step 2 answered by a fake; the price library is empty

    main(["price", str(RAW / P5_NEW), "--project", "P5"], data=tmp_path)

    assert load(tmp_path / "out" / "P5.json").summary.methods["none"].items == 50


def fake_step_2(model, system, questions, shape, effort):
    """Stands in for the two step 2 calls: one item type, and every item gets it."""
    if shape is Proposal:
        return [Proposal(item_types=[ProposedType(name="Bauleistung", trade="Erdarbeiten", description="everything")])]
    assert shape is Assignments, "no key is set up, so the real judge must not be reached"
    return [Assignments(items=[Assigned(id=id, item_type="Bauleistung", reason="Everything is one type.") for id in re.findall(r"^### (\S+) \|", q, flags=re.M)])
            for q in questions]


def test_price_command_asks_the_judge_and_writes_what_code_made_of_the_verdicts(tmp_path, capsys, monkeypatch):
    asked = []

    def fake_ask(model, system, questions, shape, effort):
        if shape is not JudgeAnswer:
            return fake_step_2(model, system, questions, shape, effort)
        asked.append((model, effort, system, shape, len(questions)))
        answers = []
        for question in questions:  # the first candidate of every item is "the same"; the last item gets no answer
            items = [Verdicts(id=id, reason="First candidate matches.",
                              judged=[said(cand, "same" if n == 0 else "different") for n, cand in enumerate(cands)])
                     for id, cands in blocks_in(question)]
            answers.append(JudgeAnswer(items=items[:-1]))
        return answers

    monkeypatch.setattr(ai, "ask", fake_ask)
    main(["ingest", str(RAW / P2_PRICED), "--project", "P2"], data=tmp_path)
    main(["price", str(RAW / P5_NEW), "--project", "P5"], data=tmp_path)

    printed = capsys.readouterr().out
    result, library = load(tmp_path / "out" / "P5.json"), load(tmp_path / "library" / "P2.json")
    price = {i.id: i.unit_price for i in library.items}
    methods = result.summary.methods
    # step 2 put all items under one type: 28 items with candidates go out in 2 packs (15 + 13). Each pack comes
    # back without its last item, so 2 items are asked again on their own, and those 2 calls come back empty.
    assert asked == [("claude-sonnet-5-5", "high", JUDGE_RULES, JudgeAnswer, 2)] * 2
    assert methods["similar"].items == 26 and sum(m.items for m in methods.values()) == 50
    assert methods["similar"].items + 2 + sum(1 for i in result.items if "No library item" in i.prediction.reason) == 50
    assert all(price[s.id] == s.unit_price == i.prediction.median for i in result.items for s in i.prediction.sources)
    assert printed.count("to judge candidates: 2 calls") == 2 and "gave no answer for 2 of these items" in printed
    assert result.summary.total == pytest.approx(sum(i.prediction.median * i.quantity for i in result.items
                                                     if i.prediction.method == "similar"))
    assert not result.priced


def test_price_command_prints_the_summary_and_writes_it(tmp_path, capsys):
    main(["ingest", str(RAW / P2_PRICED), "--project", "P2"], data=tmp_path)
    main(["price", str(RAW / P2_NEW), "--project", "P2new", "--copy-only"], data=tmp_path)

    printed = capsys.readouterr().out
    result = load(tmp_path / "out" / "P2new.json")
    assert "price library: 249 line items with a price from 1 project" in printed
    assert "copied" in printed and "total" in printed
    assert result.summary.methods["copied"].items == 160
    assert result.summary.total == sum(m.total for m in result.summary.methods.values()) > 0
    assert all(i.prediction is not None and i.unit_price is None for i in result.items)
