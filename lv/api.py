"""Web API for the frontend (README.md, "Backend API"): upload an LV and an optional plan, follow the run, read the
result, the judge log and the plan files, and save the estimator's comments.

A run is the command line's `lv price` in its own process, so the API adds no second pipeline: its printed lines are
the run's log. Start it with:  python -m uvicorn lv.api:app --port 8000
"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from lv import assistant, index
from lv.__main__ import DATA
from lv.extract import READERS
from lv.model import LV, LineItem, RiskSummary, load, save
from lv.plan import PLAN_SUFFIXES, ROOT
from lv import ai
from lv.risk import comment

PROJECT = re.compile(r"[A-Za-z0-9-]{1,40}")  # no "_", so a project never collides with <project>_judge.json
LOG_LINES = 40  # lines of the run's log in a status answer
# Set on the server: every call then needs it, as the header X-Passcode (or ?passcode= where a header cannot be sent,
# e.g. an <img> of the plan). Not set (on your own computer): the API is open.
PASSCODE = os.environ.get("LV_PASSCODE", "").strip()
OPEN = {"/assistant", "/docs", "/openapi.json"}  # pages without data: the chat panel asks for the passcode itself

app = FastAPI(title="LV pricing API", version="1.0",
              description="Prices a new LV (GAEB file) from past LVs, scores its risk and, with a construction plan, "
                          "checks its quantities against the plan.")


@app.middleware("http")
async def passcode(request: Request, call_next):
    """ponytail: one shared passcode for the whole team; per-person logins if the tool goes beyond the hackathon."""
    given = request.headers.get("x-passcode") or request.query_params.get("passcode") or ""
    if PASSCODE and request.method != "OPTIONS" and request.url.path not in OPEN \
            and not secrets.compare_digest(given.encode(), PASSCODE.encode()):
        return JSONResponse({"detail": "Wrong or missing passcode (header X-Passcode)."}, 401)
    return await call_next(request)


# Added after the passcode check, so it wraps it: a 401 carries the CORS headers and the frontend can read it.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # ponytail: any
# origin; the passcode guards the data. Name the frontend's origin if the passcode is ever not enough
runs: dict[str, subprocess.Popen] = {}  # project -> its latest run started by this server
saving = assistant.saving  # one lock for every result file write, shared with the assistant; ponytail: per-project locks if many estimators comment at once


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
          lv: UploadFile = File(..., description="the new LV: GAEB XML (.x83), GAEB 90 (.d83, .d84, .d86), Excel (.xlsx) or PDF (.pdf)"),
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
    index.refresh(lv, DATA, "tender")  # the assistant finds the new comment at once
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


# --- assistant (README.md, "Assistant") --------------------------------------------------------------------------

STATIC = Path(__file__).resolve().parent / "static"
indexing: set[str] = set()  # projects this server is indexing in the background
asking: dict[str, threading.Lock] = {}  # one message at a time per tab


class NewTab(BaseModel):
    project: str | None = Field(None, description="the tender of this tab; null: only the price library")
    title: str = ""


class TabTitle(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


class Message(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


class NewFact(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    project: str | None = None


class QuestionChange(BaseModel):
    subject: str | None = None
    question: str | None = None
    reason_internal: str | None = None
    status: Literal["draft", "ready", "discarded"] | None = None


def _run_state(project: str) -> str | None:
    run = runs.get(project)
    if run is None:
        return None
    code = run.poll()
    return "running" if code is None else "failed" if code else "done"


def _start_index(project: str, source: str) -> None:
    """Builds a missing or stale index in the background; the assistant waits until it is there."""
    if project in indexing or index.state(DATA, project, source) == "indexing":
        return
    indexing.add(project)

    def job() -> None:
        try:
            path = DATA / ("library" if source == "library" else "out") / f"{project}.json"
            index.build_file(path, DATA, source, say=lambda line: print(f"[{project}] {line}", flush=True))
        except Exception as error:  # the next status call tries again
            print(f"[{project}] assistant index failed: {error}", flush=True)
        finally:
            indexing.discard(project)

    threading.Thread(target=job, daemon=True).start()


def _ready(project: str | None) -> assistant.Readiness:
    return assistant.readiness(DATA, project, _run_state, _start_index)


def _tab(tab_id: str) -> assistant.Tab:
    tab = assistant.get_tab(DATA, tab_id)
    if tab is None:
        raise HTTPException(404, f"No tab {tab_id}.")
    return tab


@app.get("/assistant", include_in_schema=False)
def assistant_panel() -> FileResponse:
    """The chat panel with tabs (lv/static/assistant.html), to try the assistant or to embed it in the frontend."""
    return FileResponse(STATIC / "assistant.html")


@app.get("/assistant/status", response_model=assistant.Readiness)
def assistant_status(project: str | None = None) -> assistant.Readiness:
    """Whether the assistant can answer for this tender: `extracting` while its run is going, `indexing` while its
    positions are being embedded, `ready` when it can. Poll it while it is not ready."""
    return _ready(_known(project) if project else None)


@app.get("/assistant/tabs", response_model=list[assistant.TabInfo])
def assistant_tabs(project: str | None = None) -> list[assistant.TabInfo]:
    """The chat tabs, oldest first; with `project`, only that tender's tabs."""
    return assistant.list_tabs(DATA, project)


@app.post("/assistant/tabs", status_code=201, response_model=assistant.Tab)
def assistant_new_tab(new: NewTab) -> assistant.Tab:
    if new.project is not None:
        _known(new.project)
        if not (DATA / "out" / f"{new.project}.json").exists() and new.project not in runs:
            raise HTTPException(404, f"No project {new.project}.")
    return assistant.new_tab(DATA, new.project, new.title)


@app.get("/assistant/tabs/{tab_id}", response_model=assistant.Tab, response_model_exclude={"messages"})
def assistant_tab(tab_id: str) -> assistant.Tab:
    """One tab with its messages as the panel shows them."""
    return _tab(tab_id)


@app.patch("/assistant/tabs/{tab_id}", response_model=assistant.TabInfo)
def assistant_rename_tab(tab_id: str, change: TabTitle) -> assistant.TabInfo:
    tab = _tab(tab_id)
    tab.title = change.title.strip()
    assistant.save_tab(DATA, tab)
    return assistant.TabInfo(**tab.model_dump(include={"id", "title", "project", "created", "updated"}),
                             messages=len(tab.shown))


@app.delete("/assistant/tabs/{tab_id}", status_code=204)
def assistant_delete_tab(tab_id: str) -> None:
    if not assistant.delete_tab(DATA, tab_id):
        raise HTTPException(404, f"No tab {tab_id}.")


@app.post("/assistant/tabs/{tab_id}/messages", response_model=assistant.Reply)
def assistant_message(tab_id: str, message: Message) -> assistant.Reply:
    """Asks the assistant. While the tab's tender is extracting or indexing, the answer is `state: "waiting"` with
    the reason, no model is asked and nothing is saved. Takes seconds to a minute: the model searches first."""
    lock = asking.setdefault(tab_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "This tab is still answering the last message.")
    try:
        tab = _tab(tab_id)
        try:
            return assistant.ask(DATA, tab, message.text.strip(), _ready(tab.project), _running)
        except ai.MissingKey as error:
            raise HTTPException(503, str(error))
        except Exception as error:  # e.g. the Claude API is down or the key is wrong: say so instead of a bare 500
            if type(error).__module__.startswith("anthropic"):
                raise HTTPException(502, f"Claude API: {type(error).__name__}: {error}")
            raise
    finally:
        lock.release()


@app.get("/assistant/memory", response_model=list[assistant.Fact])
def assistant_memory() -> list[assistant.Fact]:
    """What the assistant remembers across tabs (data/assistant/memory.json)."""
    return assistant.memory(DATA)


@app.post("/assistant/memory", status_code=201, response_model=assistant.Fact)
def assistant_remember(fact: NewFact) -> assistant.Fact:
    return assistant.remember(DATA, fact.text, _known(fact.project) if fact.project else None, by="estimator")


@app.delete("/assistant/memory/{fact_id}", status_code=204)
def assistant_forget(fact_id: str) -> None:
    if not assistant.forget(DATA, fact_id):
        raise HTTPException(404, f"No stored fact {fact_id}.")


@app.get("/projects/{project}/bidder-questions", response_model=list[assistant.BidderQuestion])
def bidder_questions(project: str) -> list[assistant.BidderQuestion]:
    """The Bieterfragen drafted for this tender (by the assistant, on the estimator's request). Never sent anywhere."""
    return assistant.bidder_questions(DATA, _known(project))


@app.patch("/projects/{project}/bidder-questions/{question}", response_model=assistant.BidderQuestion)
def change_bidder_question(project: str, question: str, change: QuestionChange) -> assistant.BidderQuestion:
    with assistant.saving:
        found = assistant.bidder_questions(DATA, _known(project))
        draft = next((q for q in found if q.id == question), None)
        if draft is None:
            raise HTTPException(404, f"{project} has no bidder question {question}.")
        for field, new in change.model_dump(exclude_none=True).items():
            setattr(draft, field, new)
        draft.updated = assistant._now()
        assistant.save_bidder_questions(DATA, project, found)
    return draft
