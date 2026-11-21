from pathlib import Path

import pytest

from lv.__main__ import main
from lv.extract import read
from lv.model import LV, LineItem, load
from lv.predict import predict

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
P2_PRICED, P2_NEW = "StBALV23040.D86", "[NUMMER_001]_leer.X83"


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
