from pathlib import Path

import pytest

from lv import plan
from lv.__main__ import main
from lv.model import LV, LineItem

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
P5_NEW = "GAEB-[PROJEKT_004].X83"


def item(oz: str, short_text: str, quantity: float, unit: str = "St") -> LineItem:
    return LineItem(id=f"P9/{oz}", oz=oz, section=["03 Ausbau", "03.01 Fenster und Türen"], short_text=short_text,
                    long_text="", quantity=quantity, unit=unit)


def an_lv(*items: LineItem) -> LV:
    return LV(project="P9", name="", date=None, source_file="P9.X83", items=list(items))


def test_step_0_items_become_the_positions_the_prototype_reads():
    found = plan.positions(an_lv(item("03.01.0010", "Fenster F1, 1,01 x 1,26", 2)))

    assert found["datei"] == "P9.X83" and found["pruefung"]["gelesen"] == 1
    assert found["positionen"] == [{"oz": "03.01.0010", "gruppe": "03.01 Fenster und Türen",
                                    "kurztext": "Fenster F1, 1,01 x 1,26", "menge": 2, "einheit": "St"}]


def test_the_prototype_compares_step_0_items_with_a_plan_without_any_ai_call():
    plan._on_path()
    import compare

    lv = an_lv(item("03.01.0010", "Fenster F1", 2), item("03.01.0020", "Tür T1", 3), item("03.01.0030", "Estrich", 80, "m2"))
    reading = [{"kategorie": "Fenster", "typ_bezeichnung": "F1", "bezeichnung": "Fenster", "anzahl": 2,
                "marker": [{"seite": 1, "x": 0.2, "y": 0.3}]},
               {"kategorie": "Tür", "typ_bezeichnung": "T1", "bezeichnung": "Tür", "anzahl": 4,
                "marker": [{"seite": 1, "x": 0.6, "y": 0.4}]}]

    result = compare.build_result(plan.positions(lv), [reading] * 3, {"id": "EG", "datei": "EG.png"})  # no client: codes only

    checked = {p["oz"]: p["pruefung"] for p in result["positionen"]}
    assert checked == {"03.01.0010": "ok", "03.01.0020": "abweichung", "03.01.0030": "nicht_geprueft"}
    finding = result["befunde"][0]
    assert (finding["oz"], finding["lv"]["menge"], finding["plan"]["menge"]) == ("03.01.0020", 3, 4)
    assert finding["marker"][0]["x"] == 0.6  # the item can be shown on the plan


@pytest.mark.parametrize("args, message", [
    (["--plan", "EG.png", "--copy-only"], "cannot run with --copy-only"),
    (["--plan", "EG.dwg"], "must be an image or a PDF"),
    (["--plan", "missing.pdf"], "Plan not found"),
])
def test_a_plan_that_cannot_be_checked_stops_the_command_before_anything_runs(tmp_path, args, message):
    with pytest.raises(SystemExit, match=message):
        main(["price", str(RAW / P5_NEW), "--project", "P5", *args], data=tmp_path)
    assert not (tmp_path / "out").exists()  # nothing was read, asked or written
