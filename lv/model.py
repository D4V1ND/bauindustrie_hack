"""The line item table: the one shape every LV file is read into (SPEC.md, step 0)."""
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, computed_field


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
    read_by: Literal["gaeb", "ai"] = "gaeb"
    group: str = ""  # step 1: the same label for every exact match, inside one LV and across the price library


class LV(BaseModel):
    project: str
    name: str
    date: date | None
    source_file: str
    items: list[LineItem]

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
