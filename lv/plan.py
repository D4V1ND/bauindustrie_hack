"""Plan check (optional): compare the LV's quantities with a construction plan (Bauplan) (SPEC.md, plan check).

The plan is read and compared by the VLM prototype in `vlm/`: an OpenAI vision model counts the plan's components
(`vlm/extract_plan.py`), and `vlm/compare.py` matches them to the LV and writes findings with markers. The LV itself
comes from step 0's reader, not from an LLM, so `vlm/gaeb_llm.py` is not used here.
"""
import sys
import threading
import time
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


def check(lv_positions: dict, plan: Path, client_and_model: tuple, runs: int = RUNS) -> dict:
    """Reads the plan `runs` times and compares it with the LV's positions (from `positions`): the prototype's
    result, joined to the LV by `oz`."""
    _on_path()
    import compare
    import extract_plan

    openai_client, model = client_and_model
    plan_runs, rooms = extract_plan.extract_runs(plan, runs, client=openai_client, model=model)  # rooms: since PR #5
    width, height = extract_plan.image_size(plan)
    meta = {"id": plan.stem, "datei": plan.name, "breite_px": width, "hoehe_px": height}
    return compare.build_result(lv_positions, plan_runs, meta, openai_client, model, rooms)


def overview(result: dict, plan: Path, out_dir: Path, project: str) -> tuple[Path, Path]:
    """The prototype's fixed overview of a plan check: the plan with every located item numbered (PNG), and all items
    with status and findings (Excel). Written as <project>__<plan>_uebersicht.png and .xlsx."""
    _on_path()
    import overview as prototype_overview

    return prototype_overview.make(result, plan, out_dir, project)


class Background:
    """Runs one job in a thread while the LV pipeline goes on, and hands back its result or its error.
    A daemon thread: when the LV run stops with an error, the command ends without waiting for the plan check."""

    def __init__(self, job, *args):
        self.result_value, self.error, self.started = None, None, time.monotonic()
        self.thread = threading.Thread(target=self._run, args=(job, args), daemon=True)
        self.thread.start()

    def _run(self, job, args) -> None:
        try:
            self.result_value = job(*args)
        except BaseException as error:  # handed to the caller by result(); a thread cannot raise into the main one
            self.error = error

    def done(self) -> bool:
        return not self.thread.is_alive()

    def result(self):
        self.thread.join()
        if self.error is not None:
            raise self.error
        return self.result_value


def _on_path() -> None:
    """vlm/ is a folder of scripts that import each other by plain name, not a package.
    ponytail: puts the folder on the import path; make vlm/ a package if more code depends on it."""
    if str(VLM) not in sys.path:
        sys.path.insert(0, str(VLM))
