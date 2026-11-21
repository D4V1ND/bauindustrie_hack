"""The chat assistant (README.md, "Assistant"): answers from the search index, writes comments and drafts bidder
questions with tools. Each chat tab is one conversation, saved as data/assistant/tabs/<id>.json.

The rules the model follows are in lv/assistant_prompt.md. What the model may not do is also enforced here, in code:
it writes only to the tender of its own tab, never to the price library, and never while that tender is running.
"""
import json
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from lv import ai, index
from lv.model import LV, load, save
from lv.risk import assess, comment

PROMPT = (Path(__file__).resolve().parent / "assistant_prompt.md").read_text(encoding="utf-8")
MAX_TURNS = 10  # model calls per message: enough for a few searches and a write
MAX_MEMORY = 60  # facts shown to the model; older ones stay in the file
HISTORY = 40  # messages of a tab sent along; ponytail: summarise older ones if tabs grow very long
saving = threading.Lock()  # the same idea as lv.api.saving: one writer at a time for result files


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:12]


# --- tabs --------------------------------------------------------------------------------------------------------

class Shown(BaseModel):
    """One message as the panel shows it."""
    role: Literal["user", "assistant"]
    text: str
    at: str
    actions: list[str] = []  # what the assistant changed, e.g. "Kommentar an 03.0040 gespeichert"
    positions: list[str] = []  # card ids the answer looked at


class Tab(BaseModel):
    id: str
    title: str
    project: str | None  # the tender this tab is about; None: only the price library
    created: str
    updated: str
    shown: list[Shown] = []
    messages: list[dict] = []  # the conversation as sent to Claude, content blocks unchanged


class TabInfo(BaseModel):
    id: str
    title: str
    project: str | None
    created: str
    updated: str
    messages: int


def tabs_dir(data: Path) -> Path:
    folder = data / "assistant" / "tabs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def new_tab(data: Path, project: str | None, title: str = "") -> Tab:
    created = datetime.now(timezone.utc).isoformat(timespec="microseconds")  # orders tabs made in one second
    tab = Tab(id=_id(), title=title.strip() or (f"Chat {project}" if project else "Chat"), project=project,
              created=created, updated=created)
    save_tab(data, tab)
    return tab


def save_tab(data: Path, tab: Tab) -> None:
    index._write(tabs_dir(data) / f"{tab.id}.json", tab.model_dump_json(indent=1))


def get_tab(data: Path, tab_id: str) -> Tab | None:
    path = tabs_dir(data) / f"{tab_id}.json"
    return Tab.model_validate_json(path.read_text(encoding="utf-8")) if re.fullmatch(r"[0-9a-f]{12}", tab_id) \
        and path.exists() else None


def list_tabs(data: Path, project: str | None = None) -> list[TabInfo]:
    found = []
    for path in tabs_dir(data).glob("*.json"):
        tab = Tab.model_validate_json(path.read_text(encoding="utf-8"))
        if project is None or tab.project == project:
            found.append(TabInfo(id=tab.id, title=tab.title, project=tab.project, created=tab.created,
                                 updated=tab.updated, messages=len(tab.shown)))
    return sorted(found, key=lambda t: t.created)


def delete_tab(data: Path, tab_id: str) -> bool:
    if get_tab(data, tab_id) is None:
        return False
    (tabs_dir(data) / f"{tab_id}.json").unlink()
    return True


# --- memory ------------------------------------------------------------------------------------------------------

class Fact(BaseModel):
    id: str
    text: str
    project: str | None = None  # None: holds for every project
    created: str
    by: Literal["assistant", "estimator"] = "assistant"


def memory_file(data: Path) -> Path:
    return data / "assistant" / "memory.json"


def memory(data: Path) -> list[Fact]:
    path = memory_file(data)
    return [Fact(**fact) for fact in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []


def remember(data: Path, text: str, project: str | None = None, by: str = "assistant") -> Fact:
    with saving:
        facts = memory(data)
        fact = Fact(id=_id()[:8], text=text.strip()[:500], project=project, created=_now(), by=by)
        facts.append(fact)
        _save_memory(data, facts)
    return fact


def forget(data: Path, fact_id: str) -> bool:
    with saving:
        facts = memory(data)
        kept = [fact for fact in facts if fact.id != fact_id]
        _save_memory(data, kept)
    return len(kept) < len(facts)


def _save_memory(data: Path, facts: list[Fact]) -> None:
    memory_file(data).parent.mkdir(parents=True, exist_ok=True)
    index._write(memory_file(data), json.dumps([fact.model_dump() for fact in facts], ensure_ascii=False, indent=1))


# --- bidder questions --------------------------------------------------------------------------------------------

class BidderQuestion(BaseModel):
    id: str
    items: list[str]  # item numbers the question is about
    subject: str
    question: str  # the text for the contracting authority: neutral, no prices, no strategy
    reason_internal: str = ""  # why it matters for our price; never sent
    status: Literal["draft", "ready", "discarded"] = "draft"
    created: str
    updated: str


def questions_file(data: Path, project: str) -> Path:
    return data / "out" / f"{project}_bidder_questions.json"


def bidder_questions(data: Path, project: str) -> list[BidderQuestion]:
    path = questions_file(data, project)
    return [BidderQuestion(**q) for q in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []


def save_bidder_questions(data: Path, project: str, found: list[BidderQuestion]) -> None:
    index._write(questions_file(data, project),
                 json.dumps([q.model_dump() for q in found], ensure_ascii=False, indent=1))


# --- readiness ---------------------------------------------------------------------------------------------------

class Readiness(BaseModel):
    state: Literal["ready", "extracting", "indexing", "missing", "failed"]
    message: str  # German, shown in the panel
    library: list[str]  # past projects the assistant can search


def readiness(data: Path, project: str | None, run_state: Callable[[str], str | None],
              start_index: Callable[[str, str], None]) -> Readiness:
    """Whether the assistant may answer for this tab. While the tender's run is going (extraction, pricing,
    indexing), it may not. An index that is missing or older than its result file is built in the background."""
    library = sorted(path.stem for path in (data / "library").glob("*.json"))
    for name in library:
        if index.state(data, name, "library") in ("missing", "stale"):
            start_index(name, "library")
    if project is None:
        return Readiness(state="ready", message="", library=library)
    run = run_state(project)
    if run == "running":
        return Readiness(state="extracting", library=library, message=(
            f"Die Extraktion von {project} läuft noch. Ich kann Fragen zu diesem LV beantworten, sobald sie fertig "
            "und alles für mich indexiert ist. Bitte warte, bis die Extraktion abgeschlossen ist."))
    if not (data / "out" / f"{project}.json").exists():
        return Readiness(state="failed" if run == "failed" else "missing", library=library, message=(
            f"Für {project} gibt es noch kein Ergebnis" + (", die letzte Extraktion ist fehlgeschlagen." if run ==
                                                            "failed" else ". Lade zuerst ein LV hoch.")))
    built = index.state(data, project, "tender")
    if built in ("missing", "stale"):
        start_index(project, "tender")
        built = "indexing"
    if built == "indexing":
        return Readiness(state="indexing", library=library, message=(
            f"Die Positionen von {project} werden gerade für mich indexiert. Bitte warte einen Moment, bis das "
            "fertig ist."))
    return Readiness(state="ready", message="", library=library)


# --- tools -------------------------------------------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties,
                             "required": list(properties) if required is None else required,
                             "additionalProperties": False}}


_TEXT = {"type": "string"}
_ID = {"type": "string", "description": "the project ID, e.g. P3 or H1"}
_OZ = {"type": "string", "description": "the item number (OZ) as printed, e.g. 03.0040"}
TOOLS = [
    _tool("search_positions",
          "Find line items by meaning and words: short text first, comments second, long text third. Returns "
          "the best positions with project, item number, unit, quantity, price (library) or predicted price "
          "(tender). Search the open tender, the past projects (library) or both.",
          {"query": {"type": "string", "description": "what to look for, in the words of an LV, e.g. "
                                                        "'Absperrschieber DN 200' or 'Rohrgraben ausheben Tiefe 2 m'"},
           "scope": {"type": "string", "enum": ["all", "tender", "library"]},
           "unit": {"type": ["string", "null"], "description": "only items with this unit, e.g. m, m2, m3, St"},
           "limit": {"type": ["integer", "null"], "description": "how many, 1 to 20; default 8"}}),
    _tool("get_position", "Everything about one line item: texts, quantity, unit, price or prediction with its "
                          "sources and calculation, risk, comments, plan check.",
          {"project": _ID, "oz": _OZ}),
    _tool("project_overview", "One project at a glance: name, number of items, total (price estimate or offered "
                              "total), confidence, the biggest cost drivers, item types and plan findings.",
          {"project": _ID}),
    _tool("list_projects", "The open tender and every past project in the price library, with their names.", {}),
    _tool("save_comment", "Save a comment on a line item of the open tender. Only when the estimator asks. A price "
                          "is the estimator's own unit price; doubt means the found price does not fit.",
          {"oz": _OZ, "text": _TEXT, "price": {"type": ["number", "null"]}, "doubt": {"type": "boolean"}}),
    _tool("edit_comment", "Change the text of one existing comment on a line item of the open tender. Only when the "
                          "estimator asks. comment_number counts from 1 as shown by get_position. To change a "
                          "price, save a new comment instead: the latest comment with a price decides.",
          {"oz": _OZ, "comment_number": {"type": "integer"}, "text": _TEXT}),
    _tool("draft_bidder_question", "Save a draft Bieterfrage for the open tender. The question text goes to the "
                                   "contracting authority and every competitor reads the answer, so it must never "
                                   "mention our prices or calculation; that goes into reason_internal.",
          {"items": {"type": "array", "items": {"type": "string"}, "description": "item numbers concerned"},
           "subject": _TEXT, "question": _TEXT, "reason_internal": _TEXT}),
    _tool("update_bidder_question", "Change a draft Bieterfrage of the open tender: any of its texts or its status. "
                                    "Null keeps a field as it is.",
          {"id": _TEXT, "subject": {"type": ["string", "null"]}, "question": {"type": ["string", "null"]},
           "reason_internal": {"type": ["string", "null"]},
           "status": {"anyOf": [{"type": "string", "enum": ["draft", "ready", "discarded"]}, {"type": "null"}]}}),
    _tool("list_bidder_questions", "The Bieterfragen drafted so far for the open tender.", {}),
    _tool("remember", "Store a short fact the estimator told you or confirmed, for later conversations.",
          {"text": _TEXT, "only_this_project": {"type": "boolean"}}),
    _tool("forget", "Remove a stored fact by its id.", {"id": _TEXT}),
]
WRITES = {"save_comment", "edit_comment", "draft_bidder_question", "update_bidder_question", "remember", "forget"}


class ToolError(Exception):
    """A tool call that cannot be done; the message goes back to the model."""


class Session:
    """What the tools of one message may see and change."""

    def __init__(self, data: Path, tab: Tab, running: Callable[[str], bool]):
        self.data, self.tab, self.running = data, tab, running
        self.actions: list[str] = []
        self.positions: list[str] = []

    # read --------------------------------------------------------------------------------------------------------

    def library(self) -> list[str]:
        return sorted(path.stem for path in (self.data / "library").glob("*.json"))

    def search_positions(self, query: str, scope: str, unit: str | None, limit: int | None) -> dict:
        projects = ([] if scope == "library" or not self.tab.project else [self.tab.project]) + \
                   ([] if scope == "tender" else self.library())
        embed = index.voyage_embed if ai.voyage_key() else None
        hits, how = index.search(index.indexes(self.data, projects), query, embed,
                                 max(1, min(limit or 8, 20)), unit)
        self.positions += [hit.card.id for hit in hits]
        return {"found_by": how, "positions": [_card(hit) for hit in hits]}

    def get_position(self, project: str, oz: str) -> dict:
        lv = self._lv(project)
        item = _item(lv, oz)
        self.positions.append(item.id)
        found = item.model_dump(exclude={"group", "plan"} if not item.plan else {"group"}, exclude_none=True)
        found["source"] = "library" if project in self.library() else "tender"
        found["comments"] = [{"number": n, **c.model_dump()} for n, c in enumerate(item.comments, start=1)]
        return found

    def project_overview(self, project: str) -> dict:
        lv = self._lv(project)
        library = project in self.library()

        def value(item) -> float:
            if library:
                return item.total_price or (item.unit_price or 0) * (item.quantity or 0)
            return (item.prediction.median or 0) * (item.quantity or 0) if item.prediction else 0.0

        normal = [item for item in lv.items if item.kind == "normal"]
        total = sum(value(item) for item in normal)
        drivers = sorted(normal, key=value, reverse=True)[:15]
        types: dict[str, int] = {}
        for item in lv.items:
            if item.item_type:
                types[item.item_type] = types.get(item.item_type, 0) + 1
        return {
            "project": lv.project, "name": lv.name, "date": str(lv.date) if lv.date else None,
            "source": "library" if library else "tender", "items": len(lv.items),
            "optional_items": len(lv.items) - len(normal),
            "total": round(total, 2), "total_means": "offered total of the normal items (unit price × quantity)"
            if library else "price estimate: found median × quantity, only items with a found price",
            "items_with_price": sum(1 for item in normal if value(item) > 0),
            "confidence": lv.risk_summary.confidence if lv.risk_summary else None,
            "methods": lv.summary.model_dump() if lv.summary else None,
            "cost_drivers": [{"oz": item.oz, "short_text": item.short_text, "quantity": item.quantity,
                              "unit": item.unit, "value": round(value(item), 2),
                              "share": round(value(item) / total, 3) if total else None} for item in drivers],
            "item_types": dict(sorted(types.items(), key=lambda kv: -kv[1])[:30]),
            "plan_findings": len(lv.plan.findings) if lv.plan else None}

    def list_projects(self) -> dict:
        def name(path: Path) -> str:
            found = index.indexes(self.data, [path.stem])
            return found[0].name if found else load(path).name
        return {"open_tender": self.tab.project,
                "library": [{"project": p.stem, "name": name(p)}
                            for p in sorted((self.data / "library").glob("*.json"))]}

    def list_bidder_questions(self) -> dict:
        return {"questions": [q.model_dump() for q in bidder_questions(self.data, self._own())]}

    # write -------------------------------------------------------------------------------------------------------

    def save_comment(self, oz: str, text: str, price: float | None, doubt: bool) -> dict:
        if price is not None and price <= 0:
            raise ToolError("A price must be above 0.")
        if not text.strip() and price is None and not doubt:
            raise ToolError("Give a text, a price or a doubt.")
        lv, item = self._write_item(oz, lambda lv: comment(lv, oz, text, price, doubt))
        item.comments[-1].author = "assistant"
        self._save(lv)
        self.actions.append(f"Kommentar an {item.oz} gespeichert")
        return {"saved": item.comments[-1].model_dump(), "confidence": item.risk.confidence if item.risk else None}

    def edit_comment(self, oz: str, comment_number: int, text: str) -> dict:
        def change(lv: LV):
            item = _item(lv, oz)
            if not 1 <= comment_number <= len(item.comments):
                raise ToolError(f"{item.oz} has {len(item.comments)} comment(s); there is no number {comment_number}.")
            item.comments[comment_number - 1].text = text.strip()
            item.comments[comment_number - 1].edited = True
            assess(lv, None)
            return item
        lv, item = self._write_item(oz, change)
        self._save(lv)
        self.actions.append(f"Kommentar {comment_number} an {item.oz} geändert")
        return {"comment": item.comments[comment_number - 1].model_dump()}

    def draft_bidder_question(self, items: list[str], subject: str, question: str, reason_internal: str) -> dict:
        project = self._own(write=True)
        lv = self._lv(project)
        numbers = [_item(lv, oz).oz for oz in items]
        with saving:
            found = bidder_questions(self.data, project)
            draft = BidderQuestion(id=f"BF{len(found) + 1:02d}", items=numbers, subject=subject.strip(),
                                   question=question.strip(), reason_internal=reason_internal.strip(),
                                   created=_now(), updated=_now())
            save_bidder_questions(self.data, project, found + [draft])
        self.actions.append(f"Bieterfrage {draft.id} entworfen")
        return {"saved": draft.model_dump()}

    def update_bidder_question(self, id: str, subject: str | None, question: str | None,
                               reason_internal: str | None, status: str | None) -> dict:
        project = self._own(write=True)
        with saving:
            found = bidder_questions(self.data, project)
            draft = next((q for q in found if q.id == id), None)
            if draft is None:
                raise ToolError(f"{project} has no bidder question {id}.")
            for field, new in (("subject", subject), ("question", question), ("reason_internal", reason_internal),
                               ("status", status)):
                if new is not None:
                    setattr(draft, field, new.strip() if field != "status" else new)
            draft.updated = _now()
            save_bidder_questions(self.data, project, found)
        self.actions.append(f"Bieterfrage {id} geändert")
        return {"saved": draft.model_dump()}

    def remember(self, text: str, only_this_project: bool) -> dict:
        fact = remember(self.data, text, self.tab.project if only_this_project else None)
        self.actions.append("Gemerkt: " + fact.text[:60])
        return {"saved": fact.model_dump()}

    def forget(self, id: str) -> dict:
        if not forget(self.data, id):
            raise ToolError(f"No stored fact {id}.")
        self.actions.append(f"Vergessen: {id}")
        return {"forgotten": id}

    # helpers -----------------------------------------------------------------------------------------------------

    def _own(self, write: bool = False) -> str:
        if not self.tab.project:
            raise ToolError("This tab has no open tender; comments and bidder questions need one.")
        if write and self.running(self.tab.project):
            raise ToolError(f"{self.tab.project} is running; nothing can be changed until it is done.")
        return self.tab.project

    def _lv(self, project: str) -> LV:
        if project in self.library():
            return load(self.data / "library" / f"{project}.json")
        if project != self.tab.project:
            raise ToolError(f"No project {project} here. Use the open tender ({self.tab.project}) or a library "
                            f"project ({', '.join(self.library())}).")
        path = self.data / "out" / f"{project}.json"
        if not path.exists():
            raise ToolError(f"{project} has no result yet.")
        return load(path)

    def _write_item(self, oz: str, change: Callable[[LV], Any]):
        project = self._own(write=True)
        with saving:
            lv = self._lv(project)
            try:
                item = change(lv)
            except ValueError as error:
                raise ToolError(str(error)) from error
        return lv, item

    def _save(self, lv: LV) -> None:
        with saving:
            save(lv, self.data / "out")
        index.refresh(lv, self.data, "tender")

    def run(self, name: str, args: dict) -> dict:
        if name not in {tool["name"] for tool in TOOLS}:
            raise ToolError(f"No tool {name}.")
        return getattr(self, name)(**args)


def _item(lv: LV, oz: str):
    wanted = oz.strip().removeprefix(f"{lv.project}/")
    item = next((item for item in lv.items if item.oz == wanted), None) or \
        next((item for item in lv.items if item.oz.lstrip("0") == wanted.lstrip("0")), None)
    if item is None:
        raise ToolError(f"{lv.project} has no item {oz}.")
    return item


def _card(hit: index.Hit) -> dict:
    card = hit.card
    found = {"id": card.id, "source": card.source, "oz": card.oz, "section": " > ".join(card.section[-2:]),
             "short_text": card.short_text, "long_text": card.long_text[:400], "unit": card.unit,
             "quantity": card.quantity, "kind": card.kind}
    if card.source == "library":
        found["unit_price"] = card.unit_price
    else:
        found |= {"predicted_price": card.median, "method": card.method, "confidence": card.confidence,
                  "item_type": card.item_type, "trade": card.trade}
    if card.comments:
        found["comments"] = card.comments
    found["match"] = {"meaning": hit.meaning, "words": hit.words}
    return found


# --- the conversation --------------------------------------------------------------------------------------------

class Reply(BaseModel):
    state: Literal["answered", "waiting", "refused", "error"]
    text: str
    actions: list[str] = []
    positions: list[str] = []
    tab: TabInfo


def _context(data: Path, tab: Tab) -> str:
    facts = [f for f in memory(data) if f.project in (None, tab.project)][-MAX_MEMORY:]
    lines = [f"Today: {datetime.now().date().isoformat()}.",
             f"Open tender in this tab: {tab.project or 'none (only the price library)'}."]
    lines.append("Memory:" if facts else "Memory: nothing stored yet.")
    lines += [f"- [{f.id}] {f.text}" + (f" (only {f.project})" if f.project else "") for f in facts]
    return "\n".join(lines)


def _history(messages: list[dict]) -> list[dict]:
    """The last HISTORY messages, starting at a plain user message so no tool result loses its tool call."""
    if len(messages) <= HISTORY:
        return messages
    start = len(messages) - HISTORY
    while start < len(messages) and not (messages[start]["role"] == "user"
                                         and isinstance(messages[start]["content"], str)):
        start += 1
    return messages[start:]


def ask(data: Path, tab: Tab, text: str, ready: Readiness, running: Callable[[str], bool],
        create: Callable[..., Any] | None = None) -> Reply:
    """One message from the estimator: the model searches and acts with the tools until it answers."""
    info = lambda: TabInfo(id=tab.id, title=tab.title, project=tab.project, created=tab.created,  # noqa: E731
                           updated=tab.updated, messages=len(tab.shown))
    if ready.state != "ready":  # no model call, nothing saved: the question can be asked again when it is done
        return Reply(state="waiting", text=ready.message, tab=info())
    create = create or ai.anthropic_client().beta.messages.create
    session = Session(data, tab, running)
    system = [{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": _context(data, tab)}]
    messages = [*tab.messages, {"role": "user", "content": text}]
    state, answer = "answered", ""
    for _ in range(MAX_TURNS):
        response = create(model=ai.ASSISTANT_MODEL, max_tokens=16000, system=system, tools=TOOLS,
                          messages=_history(messages), thinking={"type": "adaptive"},
                          output_config={"effort": ai.ASSISTANT_EFFORT},
                          betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        blocks = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        messages.append({"role": "assistant", "content": blocks})
        if response.stop_reason == "refusal":
            state, answer = "refused", "Dabei kann ich nicht helfen. Frag mich gern etwas zu den LVs und Preisen."
            break
        if response.stop_reason == "pause_turn":
            continue
        uses = [block for block in response.content if block.type == "tool_use"]
        answer = "\n\n".join(block.text for block in response.content if block.type == "text").strip()
        if response.stop_reason != "tool_use" or not uses:
            if response.stop_reason == "max_tokens":
                answer += "\n\n(Die Antwort wurde abgeschnitten.)"
            break
        results = []
        for use in uses:
            try:
                if not isinstance(use.input, dict):
                    raise ToolError("The tool input was not a JSON object.")
                result = session.run(use.name, use.input)
                results.append({"type": "tool_result", "tool_use_id": use.id,
                                "content": json.dumps(result, ensure_ascii=False, default=str)})
            except (ToolError, TypeError) as error:
                results.append({"type": "tool_result", "tool_use_id": use.id, "content": str(error),
                                "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        state = "error"
        answer = (answer + "\n\n" if answer else "") + "Ich habe zu viele Schritte gebraucht und hier abgebrochen."
    if state == "refused":
        messages = messages[:-1]  # a refused turn is not kept, so the next message starts clean
        messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
    tab.messages = messages
    tab.shown += [Shown(role="user", text=text, at=_now()),
                  Shown(role="assistant", text=answer or "(keine Antwort)", at=_now(), actions=session.actions,
                        positions=list(dict.fromkeys(session.positions)))]
    tab.updated = _now()
    save_tab(data, tab)
    return Reply(state=state, text=answer, actions=session.actions,
                 positions=list(dict.fromkeys(session.positions)), tab=info())
