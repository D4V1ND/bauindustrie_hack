"""Web API for the frontend (README.md, "Backend API"): upload an LV and an optional plan, follow the run, read the
result, the judge log and the plan files, and save the estimator's comments.

A run is the command line's `lv price` in its own process, so the API adds no second pipeline: its printed lines are
the run's log. Start it with:  python -m uvicorn lv.api:app --port 8000
"""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lv.__main__ import DATA
from lv.extract import READERS
from lv.model import LV, LineItem, RiskSummary, load, save
from lv.plan import PLAN_SUFFIXES, ROOT
from lv.risk import comment

PROJECT = re.compile(r"[A-Za-z0-9-]{1,40}")  # no "_", so a project never collides with <project>_judge.json
LOG_LINES = 40  # lines of the run's log in a status answer

app = FastAPI(title="LV pricing API", version="1.0",
              description="Prices a new LV (GAEB file) from past LVs, scores its risk and, with a construction plan, "
                          "checks its quantities against the plan.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # ponytail: any
# origin, fine for the hackathon; name the frontend's origin before it runs anywhere public
runs: dict[str, subprocess.Popen] = {}  # project -> its latest run started by this server
saving = threading.Lock()  # ponytail: one lock for all comments, per-project locks if many estimators comment at once


class Status(BaseModel):
    project: str
    status: Literal["running", "done", "failed"]  # done: the run finished, or a result file exists from before
    has_result: bool  # <project>.json exists: GET /projects/{project} answers (during a re-run: the previous result)
    error: str = ""  # failed: the run's last line, the reason it stopped
    log: list[str] = []  # the run's last printed lines


class Project(BaseModel):
    project: str
    status: Literal["running", "done", "failed"]
    items: int | None = None  # None while the first run has no result yet
    total: float | None = None  # the price estimate: normal items with a price, median x quantity
    confidence: int | None = None  # 0 to 100, weighted by value
    plan_findings: int | None = None  # None: no plan was checked


class NewComment(BaseModel):
    text: str = ""
    price: float | None = Field(None, gt=0, description="the estimator's own unit price: the item gets confidence 100")
    doubt: bool = Field(False, description="the found price does not fit: lowers the item's confidence")


class Commented(BaseModel):
    item: LineItem
    risk_summary: RiskSummary


class JudgeCall(BaseModel):
    call: int
    items: list[str]  # the line items asked about in this call
    question: str
    thinking: str
    answer: dict | None  # None: the judge gave no usable answer


@app.post("/projects", status_code=202, response_model=Status)
def start(project: str = Form(..., description="short project ID, e.g. H1: letters, digits and '-'"),
          lv: UploadFile = File(..., description="the new LV: GAEB XML (.x83) or GAEB 90 (.d83, .d84, .d86)"),
          plan: UploadFile | None = File(None, description="optional construction plan: PNG, JPG or PDF"),
          copy_only: bool = Form(False, description="only copy exact matches: no AI, no cost, no plan check")) -> Status:
    """Starts a run: steps 0 to 4 on the LV and, with a plan, the plan check beside them. Answers at once; poll
    GET /projects/{project}/status until it is no longer running. A new run of a project replaces its result."""
    if not PROJECT.fullmatch(project):
        raise HTTPException(422, "The project ID may only hold letters, digits and '-', at most 40.")
    if _running(project):
        raise HTTPException(409, f"{project} is already running.")
    has_plan = plan is not None and bool(plan.filename)
    if has_plan and copy_only:
        raise HTTPException(422, "The plan check asks AI models, so it cannot run with copy_only.")
    folder = DATA / "uploads" / project
    folder.mkdir(parents=True, exist_ok=True)
    args = ["price", str(_store(lv, folder, set(READERS))), "--project", project]
    if has_plan:
        args += ["--plan", str(_store(plan, folder, PLAN_SUFFIXES))]
    if copy_only:
        args.append("--copy-only")
    out = DATA / "out"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{project}.log", "w", encoding="utf-8") as log:
        runs[project] = subprocess.Popen(
            [sys.executable, "-u", "-m", "lv", *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "LV_DATA": str(DATA), "PYTHONIOENCODING": "utf-8"})
    return _status(project)


@app.get("/projects", response_model=list[Project])
def projects() -> list[Project]:
    """Every project with a result file or a run, A to Z."""
    names = {path.stem for path in (DATA / "out").glob("*.json") if PROJECT.fullmatch(path.stem)} | set(runs)
    found = []
    for name in sorted(names):
        entry = Project(project=name, status=_status(name).status)
        path = DATA / "out" / f"{name}.json"
        if path.exists():
            lv = load(path)
            entry.items, entry.total = len(lv.items), lv.summary.total if lv.summary else None
            entry.confidence = lv.risk_summary.confidence if lv.risk_summary else None
            entry.plan_findings = len(lv.plan.findings) if lv.plan else None
        found.append(entry)
    return found


@app.get("/projects/{project}/status", response_model=Status)
def status(project: str) -> Status:
    return _status(project)


@app.get("/projects/{project}", response_model=LV)
def result(project: str) -> LV:
    """The one structured result: every line item with its prediction, risk, comments and plan check
    (docs/result-format.md)."""
    return _lv(project)


@app.get("/projects/{project}/judge", response_model=list[JudgeCall])
def judge_log(project: str) -> list[dict]:
    """Every judge call of the last run: the question, the judge's summarised thinking and its answer."""
    path = DATA / "out" / f"{_known(project)}_judge.json"
    if not path.exists():
        raise HTTPException(404, f"{project} has no judge log (a copy-only run asks no judge).")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/projects/{project}/judge/{call}", response_model=JudgeCall)
def judge_call(project: str, call: int) -> dict:
    """One judge call: an item's `prediction.judge_call` names it."""
    found = next((entry for entry in judge_log(project) if entry["call"] == call), None)
    if found is None:
        raise HTTPException(404, f"{project} has no judge call {call}.")
    return found


@app.get("/projects/{project}/files/{name}")
def file(project: str, name: str) -> FileResponse:
    """A plan file named in the result's `plan`: the plan image (`plan.file`, to draw the markers on), the raw plan
    check (`plan.raw_file`) or an overview (`plan.overview_files`)."""
    lv = _lv(project)
    drawing = lv.plan
    places = {} if drawing is None else {
        drawing.file: DATA / "uploads" / project / drawing.file, drawing.raw_file: DATA / "out" / drawing.raw_file,
        **{overview: DATA / "out" / overview for overview in drawing.overview_files}}
    path = places.get(name)
    if path is None or not path.is_file():
        raise HTTPException(404, f"{project} has no file {name}.")
    return FileResponse(path)


@app.post("/projects/{project}/items/{item}/comments", response_model=Commented)
def add_comment(project: str, item: str, new: NewComment) -> Commented:
    """Saves the estimator's comment on one item (by item number, e.g. 04.0050) and recalculates every confidence.
    No AI is asked."""
    if new.price is None and not new.text.strip() and not new.doubt:
        raise HTTPException(422, "Give a text, a price or a doubt.")
    if _running(project):
        raise HTTPException(409, f"{project} is running; comment when it is done.")
    with saving:
        lv = _lv(project)
        try:
            commented = comment(lv, item, new.text, new.price, new.doubt)
        except ValueError as error:
            raise HTTPException(404, str(error))
        save(lv, DATA / "out")
    return Commented(item=commented, risk_summary=lv.risk_summary)


def _store(upload: UploadFile, folder: Path, suffixes: set[str]) -> Path:
    """Saves an upload under its own file name, made safe: the name shows in the result (`source_file`, `plan.file`)."""
    name = re.sub(r"[^\w.-]", "_", Path(upload.filename or "").name)
    if Path(name).suffix.lower() not in suffixes:
        raise HTTPException(422, f"{upload.filename or 'The file'}: expected {', '.join(sorted(suffixes))}.")
    path = folder / name
    path.write_bytes(upload.file.read())
    return path


def _running(project: str) -> bool:
    run = runs.get(project)
    return run is not None and run.poll() is None


def _status(project: str) -> Status:
    has_result = (DATA / "out" / f"{_known(project)}.json").exists()
    run = runs.get(project)
    if run is None and not has_result:
        raise HTTPException(404, f"No project {project}.")
    log = DATA / "out" / f"{project}.log"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:] if log.exists() else []
    code = None if run is None else run.poll()
    state = "running" if run is not None and code is None else "failed" if code else "done"
    return Status(project=project, status=state, has_result=has_result, log=lines,
                  error=next((line for line in reversed(lines) if line.strip()), "") if state == "failed" else "")


def _known(project: str) -> str:
    """A project ID safe to put in a file name."""
    if not PROJECT.fullmatch(project):
        raise HTTPException(404, f"No project {project}.")
    return project


def _lv(project: str) -> LV:
    path = DATA / "out" / f"{_known(project)}.json"
    if not path.exists():
        raise HTTPException(404, f"{project} has no result yet." if project in runs else f"No project {project}.")
    return load(path)
