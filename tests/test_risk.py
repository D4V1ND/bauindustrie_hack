import pytest

from lv.__main__ import main
from lv.model import LV, LineItem, Prediction, Source, load, save
from lv.risk import Checked, Checks, assess, comment


def item(id: str, method: str, quantity: float = 1.0, prices: tuple = (), short_text: str = "", **changes) -> LineItem:
    """A line item as step 3 leaves it: `prices` are the unit prices of its sources."""
    sources = [Source(id=f"P1/{n}", unit_price=price, verdict="same" if method == "similar" else "type_only")
               for n, price in enumerate(prices)]
    priced = method in ("copied", "similar", "adjusted", "reference")
    prediction = Prediction(method=method, sources=sources, count=len(prices) if priced else 0,
                            min=min(prices) if priced else None, max=max(prices) if priced else None,
                            median=sorted(prices)[len(prices) // 2] if priced else None)
    return LineItem(id=id, oz=id, section=[], short_text=short_text or id, long_text="", quantity=quantity, unit="m",
                    prediction=prediction, **changes)


def an_lv(*items: LineItem) -> LV:
    return LV(project="P9", name="", date=None, source_file="", items=list(items))


def says(description: str, missing: tuple = (), calls: list | None = None):
    """Stands in for the model: every item gets the same verdict. Keeps the questions it was sent."""
    def ask(questions):
        if calls is not None:
            calls.extend(questions)
        return [Checks(items=[Checked(id=line.split()[1], description=description, missing=list(missing), why="Because.")
                              for line in q.splitlines() if line.startswith("### ")]) for q in questions]
    return ask


def test_price_risk_follows_the_step_3_method_and_a_wide_spread_raises_it():
    lv = an_lv(item("a", "copied", prices=(10.0,)), item("b", "similar", prices=(10.0,)),
               item("c", "similar", prices=(10.0, 10.5)), item("d", "similar", prices=(10.0, 14.0)),
               item("e", "adjusted", prices=(10.0, 20.0)), item("f", "reference", prices=(10.0,)), item("g", "none"))

    assess(lv, None)

    assert [i.risk.price_risk for i in lv.items] == [5, 25, 25, 40, 45, 75, 100]
    assert "more than 10% apart: 10.00 to 14.00" in lv.items[3].risk.price_reason
    assert all(i.risk.score == i.risk.price_risk and i.risk.description is None for i in lv.items)  # nothing was asked


def test_the_score_is_60_percent_price_risk_and_40_percent_description_risk():
    lv = an_lv(item("a", "similar", prices=(10.0,)), item("b", "none"))

    assess(lv, says("vague", missing=("size", "material", "scope", "a fourth")))

    a, b = (i.risk for i in lv.items)
    assert (a.score, a.price_risk, a.description_risk) == (51, 25, 90)  # 0.6 x 25 + 0.4 x 90
    assert (b.score, b.description, b.missing) == (96, "vague", ["size", "material", "scope"])  # at most 3 are kept
    assert a.description_reason == "Because."


def test_the_value_comes_from_the_price_then_from_a_typical_item():
    lv = an_lv(item("a", "similar", quantity=10, prices=(20.0,)),             # 200 by its price
               item("b", "reference", quantity=2, prices=(100.0, 300.0, 200.0)),  # 2 x its price, the middle 200 = 400
               item("c", "none", quantity=999))                                # nothing: the middle of 200 and 400

    assess(lv, None)

    assert [(i.risk.value, i.risk.value_basis) for i in lv.items] == [(200.0, "price"), (400.0, "price"), (300.0, "assumed")]
    assert lv.risk_summary.basis == {"price": 2, "reference": 0, "assumed": 1} and lv.risk_summary.value == 900.0


def test_the_lv_score_weighs_items_by_value_and_leaves_optional_items_out():
    lv = an_lv(item("big", "copied", quantity=1000, prices=(100.0,)),   # 100,000 at risk 5
               item("small", "reference", quantity=1, prices=(100.0,)),  # 100 at risk 75
               item("maybe", "none", kind="optional"))

    assess(lv, None)

    assert lv.risk_summary.score == 5  # (100000 x 5 + 100 x 75) / 100100 = 5.07
    assert lv.items[2].risk.score == 100 and sum(lv.risk_summary.basis.values()) == 2  # scored, but not in the LV's score


def test_identical_items_are_checked_once_and_an_unanswered_item_keeps_its_price_risk():
    calls = []
    lv = an_lv(item("a", "none", short_text="Bauzaun"), item("b", "none", short_text="Bauzaun"))
    assess(lv, says("gaps", calls=calls))
    assert len(calls) == 1 and calls[0].count("### ") == 1
    assert [i.risk.score for i in lv.items] == [80, 80]  # 0.6 x 100 + 0.4 x 50

    lv = an_lv(item("a", "reference", prices=(5.0,)))
    assess(lv, lambda questions: [None])
    assert (lv.items[0].risk.score, lv.items[0].risk.description) == (75, None)


def test_confidence_is_100_minus_the_score_with_a_price_and_0_without():
    lv = an_lv(item("a", "similar", quantity=10, prices=(10.0,)),  # value 100, risk 25
               item("b", "reference", quantity=30, prices=(10.0,)),  # value 300, risk 75
               item("c", "none"))                                    # typical value 200, no price

    assess(lv, None)

    assert [i.risk.confidence for i in lv.items] == [75, 25, 0]
    assert lv.risk_summary.confidence == 25  # (100 x 75 + 300 x 25) / (100 + 300 + 200)


def test_a_comment_with_a_price_gives_confidence_100_and_keeps_the_checked_description():
    lv = an_lv(item("a", "similar", quantity=10, prices=(10.0,)), item("b", "none", quantity=4))
    assess(lv, says("gaps", missing=("size",)))
    assert (lv.items[1].risk.confidence, lv.risk_summary.confidence) == (0, 32)  # a: 100 - (0.6 x 25 + 0.4 x 50) = 65

    changed = comment(lv, "b", text="asked the supplier", price=50.0)  # no model is asked again

    assert changed is lv.items[1] and changed.comments[0].text == "asked the supplier"
    risk = changed.risk
    assert (risk.confidence, risk.score, risk.price_risk, risk.value, risk.value_basis) == (100, 0, 0, 200.0, "price")
    assert "entered by the estimator" in risk.price_reason
    assert (risk.description, risk.missing) == ("gaps", ["size"])  # still there to show
    assert lv.items[0].risk.confidence == 65 and lv.risk_summary.confidence == 88  # (100 x 65 + 200 x 100) / 300


def test_a_doubt_lowers_a_found_price_a_note_changes_nothing_and_a_later_price_wins():
    lv = an_lv(item("a", "copied", prices=(10.0,)), item("b", "none"))
    assess(lv, None)

    comment(lv, "a", text="call them on Monday")
    assert lv.items[0].risk.confidence == 95
    comment(lv, "a", text="this material got more expensive", doubt=True)
    comment(lv, "a", text="just a note")  # the doubt before it still counts
    assert (lv.items[0].risk.price_risk, lv.items[0].risk.confidence) == (75, 25)
    assert "doubts" in lv.items[0].risk.price_reason
    comment(lv, "b", text="looks odd", doubt=True)
    assert lv.items[1].risk.confidence == 0  # nothing to doubt: there is no price
    comment(lv, "a", price=12.0)
    assert lv.items[0].risk.confidence == 100

    with pytest.raises(ValueError, match="no item"):
        comment(lv, "zzz", price=1.0)
    with pytest.raises(ValueError):
        comment(lv, "a", price=-5.0)
    assert len(lv.items[0].comments) == 4  # the refused price was not saved


def test_the_comment_command_rewrites_the_result_file(tmp_path, capsys):
    lv = an_lv(item("P9/01.1", "similar", prices=(10.0,)), item("P9/01.2", "none", quantity=3))
    assess(lv, None)
    save(lv, tmp_path / "out")

    main(["comment", "P9", "01.2", "--price", "20", "--text", "from the supplier"], data=tmp_path)

    again = load(tmp_path / "out" / "P9.json")
    assert again.items[1].comments[0].price == 20.0 and again.items[1].risk.confidence == 100
    assert again.risk_summary.confidence == 96  # (10 x 75 + 60 x 100) / 70
    assert "confidence" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no item"):
        main(["comment", "P9", "99", "--price", "20"], data=tmp_path)
    with pytest.raises(SystemExit, match="P8"):
        main(["comment", "P8", "01.2", "--price", "20"], data=tmp_path)
