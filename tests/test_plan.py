import json
import re
import threading
from pathlib import Path

import pytest

from lv import ai, plan
from lv.categorise import Assigned, Assignments, Proposal, ProposedType
from lv.__main__ import main
from lv.model import LV, LineItem, load

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


def test_a_background_job_hands_back_its_result_or_its_error():
    assert plan.Background(lambda a, b: a + b, 2, 3).result() == 5
    with pytest.raises(ValueError, match="no plan"):
        plan.Background(lambda: (_ for _ in ()).throw(ValueError("no plan"))).result()


def fake_ai(monkeypatch):
    """Stands in for every Claude call of steps 2 to 4: one item type, no judge answers, no description checks."""
    def ask(model, system, questions, shape, effort):
        if shape is Proposal:
            return [Proposal(item_types=[ProposedType(name="Bauleistung", trade="Erdarbeiten", description="all")])]
        if shape is Assignments:
            return [Assignments(items=[Assigned(id=id, item_type="Bauleistung", reason="All one type.")
                                       for id in re.findall(r"^### (\S+) \|", q, flags=re.M)]) for q in questions]
        return [None for _ in questions]
    monkeypatch.setattr(ai, "ask", ask)
    monkeypatch.setattr(ai, "ask_with_thinking", lambda *a, **k: [(None, "") for _ in ask(*a, **k)])
    monkeypatch.setattr(ai, "voyage_key", lambda: None)
    monkeypatch.setattr(plan, "client", lambda: ("openai client", "vision-model"))


def test_the_plan_check_runs_in_its_own_thread_and_writes_its_own_file(tmp_path, monkeypatch, capsys):
    fake_ai(monkeypatch)
    ran_on = []

    def check(lv_positions, plan_path, client_and_model):
        ran_on.append(threading.current_thread() is not threading.main_thread())
        return {"positionen": lv_positions["positionen"], "befunde": [], "zusammenfassung": {"befunde": 0, "geprueft": 0}}

    monkeypatch.setattr(plan, "check", check)
    monkeypatch.setattr(plan, "overview", lambda result, drawing, out, project: (out / "o.png", out / "o.xlsx"))
    drawing = tmp_path / "EG.png"
    drawing.write_bytes(b"not read by the fake")
    main(["price", str(RAW / P5_NEW), "--project", "P5", "--plan", str(drawing)], data=tmp_path)

    assert ran_on == [True]
    written = json.loads((tmp_path / "out" / "P5_plan.json").read_text(encoding="utf-8"))
    assert len(written["positionen"]) == 50 and (tmp_path / "out" / "P5.json").exists()
    assert "plan check: started in parallel" in capsys.readouterr().out
    merged = load(tmp_path / "out" / "P5.json")  # the one structured result holds the plan check
    assert merged.plan.raw_file == "P5_plan.json" and all(i.plan.status == "not_checked" for i in merged.items)


def test_a_failing_plan_check_keeps_the_lv_result(tmp_path, monkeypatch):
    fake_ai(monkeypatch)

    def check(lv_positions, plan_path, client_and_model):
        raise ConnectionError("vision model unreachable")

    monkeypatch.setattr(plan, "check", check)
    drawing = tmp_path / "EG.png"
    drawing.write_bytes(b"x")
    with pytest.raises(SystemExit, match="plan check failed; the LV result above is saved. ConnectionError"):
        main(["price", str(RAW / P5_NEW), "--project", "P5", "--plan", str(drawing)], data=tmp_path)
    assert (tmp_path / "out" / "P5.json").exists() and not (tmp_path / "out" / "P5_plan.json").exists()


def test_check_passes_the_plan_readings_and_the_rooms_on_to_the_comparison(tmp_path, monkeypatch):
    """Guards the link to the prototype: since PR #5, extract_runs returns the readings and the rooms as a pair."""
    plan._on_path()
    import extract_plan

    reading = [{"kategorie": "Fenster", "typ_bezeichnung": "F1", "bezeichnung": "Fenster", "anzahl": 2,
                "marker": [{"seite": 1, "x": 0.2, "y": 0.3}]}]
    rooms = [{"name": "Hörsaal", "flaeche": 80, "seite": 1, "x": 0.5, "y": 0.5, "breite": 0.4, "hoehe": 0.4}]
    monkeypatch.setattr(extract_plan, "extract_runs", lambda path, runs, client, model: ([reading] * runs, rooms))
    monkeypatch.setattr(extract_plan, "image_size", lambda path: (1000, 800))
    lv = an_lv(item("03.01.0010", "Fenster F1", 2), item("03.01.0030", "Linoleum Hörsaal", 80, "m2"))

    result = plan.check(plan.positions(lv), tmp_path / "EG.png", (None, "no model"))  # no client: no AI call

    assert result["eingaben"]["plan"] == {"id": "EG", "datei": "EG.png", "breite_px": 1000, "hoehe_px": 800}
    assert {p["oz"]: p["pruefung"] for p in result["positionen"]}["03.01.0010"] == "ok"


def test_the_overview_writes_a_plan_image_and_an_excel_file(tmp_path):
    import pymupdf

    drawing = tmp_path / "EG.png"
    page = pymupdf.open().new_page(width=400, height=300)
    page.insert_text((40, 60), "EG")
    page.get_pixmap().save(drawing)
    plan._on_path()
    import compare

    lv = an_lv(item("03.01.0010", "Fenster F1", 2))
    reading = [{"kategorie": "Fenster", "typ_bezeichnung": "F1", "bezeichnung": "Fenster", "anzahl": 3,
                "marker": [{"seite": 1, "x": 0.2, "y": 0.3}]}]
    result = compare.build_result(plan.positions(lv), [reading] * 3,
                                  {"id": "EG", "datei": "EG.png", "breite_px": 400, "hoehe_px": 300})

    png, xlsx = plan.overview(result, drawing, tmp_path, "P9")

    assert (png.name, xlsx.name) == ("P9__EG_uebersicht.png", "P9__EG_uebersicht.xlsx")
    assert png.stat().st_size > 0 and xlsx.stat().st_size > 0


def test_the_plan_check_is_merged_into_the_lv_result_in_english_keys(tmp_path):
    plan._on_path()
    import compare
    from lv.model import load, save

    lv = an_lv(item("03.01.0010", "Fenster F1", 2), item("03.01.0020", "Tür T1", 3), item("03.01.0030", "Estrich", 80, "m2"))
    reading = [{"kategorie": "Fenster", "typ_bezeichnung": "F1", "bezeichnung": "Fenster", "anzahl": 2,
                "marker": [{"seite": 1, "x": 0.2, "y": 0.3}]},
               {"kategorie": "Tür", "typ_bezeichnung": "T1", "bezeichnung": "Tür", "anzahl": 4,
                "marker": [{"seite": 1, "x": 0.6, "y": 0.4}]},
               {"kategorie": "Sanitär", "typ_bezeichnung": "WB", "bezeichnung": "Waschbecken", "anzahl": 1,
                "marker": [{"seite": 1, "x": 0.9, "y": 0.1}]}]
    result = compare.build_result(plan.positions(lv), [reading] * 3,
                                  {"id": "EG", "datei": "EG.png", "breite_px": 1000, "hoehe_px": 800})

    plan.merge(lv, result, "P9_plan.json", ["P9__EG_uebersicht.png", "P9__EG_uebersicht.xlsx"])
    lv = load(save(lv, tmp_path))  # the merged shape survives the file

    window, door, screed = (i.plan for i in lv.items)
    assert (window.status, door.status, screed.status) == ("ok", "differs", "not_checked")
    assert (door.quantity, door.matched_by, door.markers[0].x, door.markers[0].shape) == (4, "plan_code", 0.6, "point")
    finding = next(f for f in lv.plan.findings if f.id == door.findings[0])
    assert (finding.kind, finding.severity, finding.lv_quantity, finding.plan_quantity) == ("quantity_differs", "error", 3, 4)
    extra = [f for f in lv.plan.findings if f.oz is None]
    assert [(f.kind, f.plan_quantity, f.markers[0].x) for f in extra] == [("missing_in_lv", 1, 0.9)]  # the basin
    assert (lv.plan.file, lv.plan.width_px, lv.plan.raw_file) == ("EG.png", 1000, "P9_plan.json")
