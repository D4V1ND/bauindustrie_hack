"""Plan check (optional): compare the LV's quantities with a construction plan (Bauplan) (SPEC.md, plan check).

The plan is read and compared by the VLM prototype in `vlm/`: an OpenAI vision model counts the plan's components
(`vlm/extract_plan.py`), and `vlm/compare.py` matches them to the LV and writes findings with markers. The LV itself
comes from step 0's reader, not from an LLM, so `vlm/gaeb_llm.py` is not used here.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

from lv.model import LV

ROOT = Path(__file__).resolve().parent.parent
VLM = ROOT / "vlm"
PLAN_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}
RUNS = 3  # the plan is read this often; vlm/compare.py takes the median count and flags counts that vary


def check_file(path: Path) -> None:
    """Refuses a plan the vision model cannot read, before any AI call is made."""
    if path.suffix.lower() not in PLAN_SUFFIXES:
        raise ValueError(f"A plan must be an image or a PDF ({', '.join(sorted(PLAN_SUFFIXES))}): {path.name}")
    if not path.is_file():
        raise ValueError(f"Plan not found: {path}")


def client() -> tuple:
    """The prototype's OpenAI client and model. The key and model may sit in the root .env or in vlm/.env.
    Called before the LV run starts, so a missing key stops the command before anything is paid for."""
    _on_path()
    load_dotenv(ROOT / ".env")
    import llm_common

    return llm_common.client_and_model()  # exits with the prototype's message when the key or the model is missing


def positions(lv: LV) -> dict:
    """Step 0's line items in the shape vlm/compare.py reads, the shape vlm/gaeb_llm.py builds with an LLM."""
    return {"datei": lv.source_file,
            "positionen": [{"oz": item.oz, "gruppe": item.section[-1] if item.section else "",
                            "kurztext": item.short_text, "menge": item.quantity, "einheit": item.unit}
                           for item in lv.items],
            "pruefung": {"quelle": "lv step 0, the GAEB reader", "gelesen": len(lv.items)}}


def check(lv: LV, plan: Path, client_and_model: tuple, runs: int = RUNS) -> dict:
    """Reads the plan `runs` times and compares it with the LV: the prototype's result, joined to the LV by `oz`."""
    _on_path()
    import compare
    import extract_plan

    openai_client, model = client_and_model
    plan_runs = extract_plan.extract_runs(plan, runs, client=openai_client, model=model)
    width, height = extract_plan.image_size(plan)
    meta = {"id": plan.stem, "datei": plan.name, "breite_px": width, "hoehe_px": height}
    return compare.build_result(positions(lv), plan_runs, meta, openai_client, model)


def _on_path() -> None:
    """vlm/ is a folder of scripts that import each other by plain name, not a package.
    ponytail: puts the folder on the import path; make vlm/ a package if more code depends on it."""
    if str(VLM) not in sys.path:
        sys.path.insert(0, str(VLM))
