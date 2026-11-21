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
