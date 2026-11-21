"""The line item table: the one shape every LV file is read into (SPEC.md, step 0)."""
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, computed_field


class Source(BaseModel):
    """One past line item behind a prediction, or shown next to it as a reference."""
    id: str  # a line item in the price library
    short_text: str = ""  # that item's short text, so a view needs no library file to show it
    unit_price: float
    verdict: Literal["exact", "same", "size_only", "type_only", "different"]  # "exact": same group label, no judge
    difference: str = ""
    why: str = ""  # the judge's sentence on this candidate: why it is the match, or why not


class Prediction(BaseModel):
    """Step 3: what the lookup in the price library found for one line item (SPEC.md, step 3)."""
    method: Literal["copied", "similar", "adjusted", "reference", "none"]
    min: float | None = None
    max: float | None = None
    median: float | None = None  # the value used for totals
    mean: float | None = None
    count: int = 0  # number of past prices behind min, max, median and mean
    sources: list[Source] = []
    rule: str | None = None  # rule, calculation and result: only for "adjusted"
    calculation: str | None = None
    result: float | None = None
    reason: str = ""
    judged: list[Source] = []  # the best candidates the judge saw (at most 3), best first, each with its `why`
    judged_item: str | None = None  # set on a copy: the identical item of this LV that was searched and judged
    judge_call: int | None = None  # the `call` in <project>_judge.json that decided this item; None: no judge asked


class Risk(BaseModel):
    """Step 4: how unsure the tool is about one line item (SPEC.md, step 4). Scores run from 0 (safe) to 100 and
    are places on a scale, not probabilities."""
    score: int  # the item's risk: price risk and description risk combined
    price_risk: int  # from the step 3 method, by code
    price_reason: str
    description: Literal["complete", "gaps", "vague"] | None = None  # None: the description was not checked
    description_risk: int | None = None
    missing: list[str] = []  # what the text does not state, at most 3 short phrases
    description_reason: str = ""
    value: float | None = None  # roughly what the item is worth; a weight for the LV's score, never a price to show
    value_basis: Literal["price", "reference", "assumed"]  # found price, related items' prices, or a typical item


class RiskSummary(BaseModel):
    score: int | None  # the items' scores, weighted by value, over the normal items
    value: float  # the sum of those values
    basis: dict[str, int]  # normal items per value basis: price, reference, assumed


class MethodSum(BaseModel):
    items: int
    total: float  # sum of median x quantity over the normal items


class Summary(BaseModel):
    methods: dict[str, MethodSum]
    total: float


class ItemType(BaseModel):
    """Step 2: one item type of an LV's own list, with the one trade it belongs to."""
    name: str
    trade: str
    description: str = ""


class PlanMarker(BaseModel):
    """Where something is on the plan image: x and y relative to its width and height (0 to 1, from the top left)."""
    page: int = 1
    x: float
    y: float
    shape: Literal["point", "frame"] = "point"
    width: float | None = None  # width and height: only for a frame, relative like x and y
    height: float | None = None
    label: str = ""


class ItemPlan(BaseModel):
    """Plan check: what the construction plan says about one line item (SPEC.md, plan check)."""
    status: Literal["ok", "ok_unstable", "differs", "not_in_plan", "units_differ", "not_checked"]
    quantity: float | None = None  # what the plan shows: a count, or for room items the rooms' area
    unit: str = ""
    matched_by: Literal["plan_code", "dimensions", "ai_match", "room", ""] = ""
    rooms: list[str] = []  # for items in m2: the rooms they were placed in
    markers: list[PlanMarker] = []  # where to show the item on the plan
    findings: list[str] = []  # ids of its findings in LV.plan.findings


class PlanFinding(BaseModel):
    id: str
    oz: str | None  # None: something on the plan that no LV item covers
    kind: Literal["quantity_differs", "not_in_plan", "missing_in_lv", "units_differ", "unstable_count", "other"]
    severity: Literal["error", "risk", "note"]
    title: str  # title and description: written by the plan check in German
    description: str
    lv_quantity: float | None = None
    lv_unit: str = ""
    plan_quantity: float | None = None
    plan_unit: str = ""
    confidence: str = ""  # high, medium or low
    decision: str = "open"  # for the estimator: open until decided
    markers: list[PlanMarker] = []


class PlanSummary(BaseModel):
    """Plan check: the plan, all findings and the files behind them."""
    file: str
    width_px: int | None = None
    height_px: int | None = None
    checked: int  # items whose quantity was compared with the plan
    findings: list[PlanFinding]
    raw_file: str  # <project>_plan.json: the plan check's own result
    overview_files: list[str] = []  # <project>__<plan>_uebersicht.png and .xlsx


class LineItem(BaseModel):
    id: str  # project + "/" + oz, unique across all projects
    oz: str
    section: list[str]
    short_text: str
    long_text: str
    quantity: float | None
    unit: str
    unit_price: float | None = None  # empty in a new LV; never filled by the tool
    total_price: float | None = None
    kind: Literal["normal", "optional"] = "normal"
    read_by: Literal["gaeb", "pdf", "ai"] = "gaeb"  # "pdf": texts read by code from a PDF print; "ai": not built
    group: str = ""  # step 1: the same label for every exact match, inside one LV and across the price library
    item_type: str = ""  # step 2: one type from the LV's list, or "Sonstiges"
    trade: str = ""  # step 2: the trade of that item type
    item_type_reason: str = ""  # step 2: one sentence on why the item got this type; text to show, nothing more
    prediction: Prediction | None = None  # step 3; never written into unit_price
    risk: Risk | None = None  # step 4
    plan: ItemPlan | None = None  # plan check, only when a plan was given


class LV(BaseModel):
    project: str
    name: str
    date: date | None
    source_file: str
    items: list[LineItem]
    item_types: list[ItemType] = []  # step 2: the LV's own list of item types
    summary: Summary | None = None  # step 3: items and sums per method, and the project total
    risk_summary: RiskSummary | None = None  # step 4: the LV's risk score and what it rests on
    plan: PlanSummary | None = None  # plan check: the plan, its findings and files; only when a plan was given

    @computed_field
    @property
    def priced(self) -> bool:
        return any(item.unit_price is not None for item in self.items)


def save(lv: LV, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lv.project}.json"
    path.write_text(lv.model_dump_json(indent=1), encoding="utf-8")
    return path


def load(path: Path) -> LV:
    return LV.model_validate_json(path.read_text(encoding="utf-8"))
