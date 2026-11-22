import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lv import api
from lv.model import LV, LineItem, PlanSummary, save

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "Challenge A Dataset"
P5_NEW = RAW / "GAEB-[PROJEKT_004].X83"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA", tmp_path)
    monkeypatch.setattr(api, "runs", {})
    monkeypatch.setattr(api, "ingests", {})
    return TestClient(api.app)


@pytest.mark.skipif(not P5_NEW.exists(), reason="needs the challenge dataset in data/raw")
def test_an_upload_runs_the_pipeline_and_the_result_takes_comments(client):
    started = client.post("/projects", data={"project": "P5", "copy_only": "true"},
                          files={"lv": (P5_NEW.name, P5_NEW.read_bytes())})
    assert started.status_code == 202 and started.json()["status"] == "running"
    assert client.post("/projects", data={"project": "P5", "copy_only": "true"},
                       files={"lv": (P5_NEW.name, P5_NEW.read_bytes())}).status_code == 409
    deadline = time.monotonic() + 120
    while (state := client.get("/projects/P5/status").json())["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
    assert state["status"] == "done" and state["has_result"], state
    assert any("line items ->" in line for line in state["log"])

    listed = client.get("/projects").json()
    assert [(p["project"], p["status"], p["items"], p["plan_findings"]) for p in listed] == [("P5", "done", 50, None)]
    lv = client.get("/projects/P5").json()
    assert len(lv["items"]) == 50 and lv["source_file"] == "GAEB-_PROJEKT_004_.X83"  # the upload's name, made safe
    oz = lv["items"][0]["oz"]
    answer = client.post(f"/projects/P5/items/{oz}/comments", json={"price": 12.5, "text": "supplier quote"})
    assert answer.status_code == 200 and answer.json()["item"]["risk"]["confidence"] == 100
    assert client.get("/projects/P5").json()["items"][0]["comments"][0]["price"] == 12.5  # saved to the file
    assert client.get("/projects/P5/judge").status_code == 404  # a copy-only run asks no judge


def test_bad_uploads_and_unknown_projects_are_refused_before_anything_runs(client):
    lv = {"lv": ("new.x83", b"<GAEB/>")}
    assert client.post("/projects", data={"project": "H1_judge"}, files=lv).status_code == 422
    assert client.post("/projects", data={"project": "H1"}, files={"lv": ("new.docx", b"PK")}).status_code == 422
    assert client.post("/projects", data={"project": "H1", "copy_only": "true"},
                       files={**lv, "plan": ("plan.png", b"png")}).status_code == 422
    assert not api.runs
    assert client.get("/projects/H9/status").status_code == 404
    assert client.get("/projects/H9").status_code == 404
    assert client.post("/projects/H9/items/01.0010/comments", json={}).status_code == 422
    twice = [("lv", ("new.x83", b"<GAEB/>")), ("lv", ("new.x83", b"<GAEB/>"))]
    assert client.post("/projects", data={"project": "H1"}, files=twice).status_code == 422  # one name, two files
    assert not api.runs


P6_FILES = ["LV_00_Roh_und_Erdauarbeiten.pdf", "LV_01_Fertigteiltreppen.pdf", "LV_10_Stahlbetonfertigeile_Stuetzen_Binder.pdf"]


@pytest.mark.skipif(not (RAW / P6_FILES[0]).exists(), reason="needs the challenge dataset in data/raw")
def test_an_lv_in_several_files_is_uploaded_and_read_into_one_result(client, tmp_path):
    files = [("lv", (name, (RAW / name).read_bytes())) for name in P6_FILES]  # three of P6's trade files, unpriced
    assert client.post("/projects", data={"project": "P6", "copy_only": "true"}, files=files).status_code == 202
    deadline = time.monotonic() + 120
    while (state := client.get("/projects/P6/status").json())["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
    assert state["status"] == "done", state
    log = (tmp_path / "out" / "P6.log").read_text(encoding="utf-8")
    assert "step 0: LV_01_Fertigteiltreppen.pdf: skipped, all 6 of its items are already in" in log

    lv = client.get("/projects/P6").json()
    assert lv["source_file"] == "LV_00_Roh_und_Erdauarbeiten.pdf, LV_10_Stahlbetonfertigeile_Stuetzen_Binder.pdf"
    assert len({item["id"] for item in lv["items"]}) == len(lv["items"])  # both files start at 1.1.10
    last = lv["items"][-1]
    assert last["oz"].startswith("F02-") and last["source_file"] == P6_FILES[2]
    answer = client.post(f"/projects/P6/items/{last['oz']}/comments", json={"text": "check the drawing"})
    assert answer.status_code == 200 and answer.json()["item"]["comments"][0]["text"] == "check the drawing"


def test_the_library_lists_past_projects_and_never_overwrites_one(client, tmp_path):
    def lv(project: str, price: float | None) -> LV:
        return LV(project=project, name=f"{project} project", date=None, source_file=f"{project}.x83",
                  items=[LineItem(id=f"{project}/01.0010", oz="01.0010", section=[], short_text="Baustelle",
                                  long_text="", quantity=1, unit="psch", unit_price=price)])
    save(lv("P1", 5700.0), tmp_path / "library")
    save(lv("H1", None), tmp_path / "out")
    assert client.get("/library").json() == [{"project": "P1", "status": "done", "name": "P1 project", "date": None,
                                              "items": 1, "priced": 1, "error": "", "log": []}]
    upload = {"lv": ("old.x83", b"<GAEB/>")}
    assert client.post("/library", data={"project": "P1"}, files=upload).status_code == 409  # P1 stays as it is
    assert client.post("/library", data={"project": "H1"}, files=upload).status_code == 409  # a tender's ID
    assert client.post("/library", data={"project": "P_4"}, files=upload).status_code == 422
    assert client.post("/library", data={"project": "P4"}, files={"lv": ("old.docx", b"PK")}).status_code == 422
    assert client.post("/library", data={"project": "P4"},
                       files={**upload, "texts": ("print.png", b"png")}).status_code == 422
    assert not api.ingests


@pytest.mark.skipif(not P5_NEW.exists(), reason="needs the challenge dataset in data/raw")
def test_a_library_upload_without_prices_fails_with_the_reason(client):
    started = client.post("/library", data={"project": "P9"}, files={"lv": (P5_NEW.name, P5_NEW.read_bytes())})
    assert started.status_code == 202 and started.json()["status"] == "running"
    deadline = time.monotonic() + 120
    while (entry := client.get("/library").json()[0])["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
    assert entry["status"] == "failed" and "has no prices" in entry["error"], entry  # stops before any AI call


def test_with_a_passcode_set_every_call_needs_it(client, monkeypatch):
    monkeypatch.setattr(api, "PASSCODE", "s3cret")
    site = {"Origin": "https://frontend.example"}
    denied = client.get("/projects", headers=site)
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == "*"  # the page can read it
    assert client.get("/projects", headers={"X-Passcode": "wrong"}).status_code == 401
    assert client.get("/projects", headers={"X-Passcode": "s3cret"}).json() == []
    assert client.get("/projects?passcode=s3cret").status_code == 200  # for an <img> of the plan
    assert client.options("/projects", headers={**site, "Access-Control-Request-Method": "GET",
                                                "Access-Control-Request-Headers": "x-passcode"}).status_code == 200
    assert client.get("/assistant").status_code == 200 and client.get("/openapi.json").status_code == 200


def test_the_judge_log_and_the_plan_files_are_served_by_name_only(client, tmp_path):
    lv = LV(project="H1", name="", date=None, source_file="H1.x83",
            items=[LineItem(id="H1/01.0010", oz="01.0010", section=[], short_text="Baustelle", long_text="",
                            quantity=1, unit="psch")],
            plan=PlanSummary(file="plan.png", checked=1, findings=[], raw_file="H1_plan.json",
                             overview_files=["H1__plan_uebersicht.png"]))
    save(lv, tmp_path / "out")
    (tmp_path / "out" / "H1_judge.json").write_text(json.dumps(
        [{"call": 1, "items": ["H1/01.0010"], "question": "q", "thinking": "t", "answer": {"items": []}}]))
    (tmp_path / "uploads" / "H1").mkdir(parents=True)
    (tmp_path / "uploads" / "H1" / "plan.png").write_bytes(b"the plan")
    (tmp_path / "out" / "secret.txt").write_text("not served")

    assert client.get("/projects/H1/judge/1").json()["thinking"] == "t"
    assert client.get("/projects/H1/judge/2").status_code == 404
    assert client.get("/projects/H1/files/plan.png").content == b"the plan"
    assert client.get("/projects/H1/files/H1_plan.json").status_code == 404  # named, but not written
    assert client.get("/projects/H1/files/secret.txt").status_code == 404
    assert client.post("/projects/H1/items/09.9999/comments", json={"text": "?"}).status_code == 404
