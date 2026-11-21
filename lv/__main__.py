"""Command line: python -m lv ingest|price <LV file> --project <ID>"""
import argparse
import time
from pathlib import Path

from lv import ai
from lv.categorise import Assignments, Proposal, assign, overview, propose
from lv.extract import read
from lv.match import group_label
from lv.model import LV, load, save
from lv.predict import NO_ANSWER, JudgeAnswer, predict

DATA = Path(__file__).resolve().parent.parent / "data"
FOLDERS = {"ingest": "library", "price": "out"}


def main(argv: list[str] | None = None, data: Path = DATA) -> None:
    parser = argparse.ArgumentParser(prog="lv", description="Read LV files and look up prices in the price library.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("ingest", "add a priced LV to the price library"),
                       ("price", "read a new LV, look up its prices and write the result file")):
        command = commands.add_parser(name, help=text)
        command.add_argument("file", type=Path, help="the LV file")
        command.add_argument("--project", required=True, help="short project ID, e.g. P4")
        if name == "price":
            command.add_argument("--copy-only", action="store_true",
                                 help="only copy the prices of exact matches: no item types, no search, no AI, "
                                      "no API key needed")
    args = parser.parse_args(argv)

    try:
        lv = read(args.file, args.project)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    if args.command == "ingest" and not lv.priced:
        raise SystemExit(f"{args.file.name} has no prices, so it cannot go into the price library.")
    for item in lv.items:
        item.group = group_label(item)
    if args.command == "price":
        library = [load(path) for path in sorted((data / "library").glob("*.json"))]
        priced = sum(1 for past in library for item in past.items if item.unit_price)
        print(f"price library: {priced} line items with a price from {len(library)} project(s)")
        try:
            if lv.items and not args.copy_only:  # step 2
                lv.item_types = propose(lv, ask_types)
                asked = assign(lv, lv.item_types, ask_assignments)
                print(f"  {_items(asked)} asked, {len(lv.items) - asked} copied from an identical item")
            predict(lv, library, judge=None if args.copy_only else ask_judge)  # step 3
        except (ai.MissingKey, RuntimeError) as stop:  # RuntimeError: every call of one kind failed
            raise SystemExit(str(stop))
        print_overview(lv)
        print_summary(lv)
    path = save(lv, data / FOLDERS[args.command])
    print(f"{len(lv.items)} line items -> {path}")


def _ask(job: str, model: str, effort: str, rules: str, questions: list[str], shape: type) -> list:
    """One real AI job: says what is asked, asks, and says how long it took."""
    calls = "1 call" if len(questions) == 1 else f"{len(questions)} calls"
    print(f"asking {model} ({effort} effort) {job}: {calls} ...", end=" ", flush=True)
    start = time.monotonic()
    answers = ai.ask(model, rules, questions, shape, effort=effort)
    print(f"{time.monotonic() - start:.0f} s")
    return answers


def ask_types(question: str) -> Proposal | None:
    return _ask("for the list of item types", ai.PROPOSE_MODEL, ai.PROPOSE_EFFORT, ai.PROPOSE_RULES, [question], Proposal)[0]


def ask_assignments(questions: list[str]) -> list[Assignments | None]:
    return _ask("to assign item types", ai.ASSIGN_MODEL, ai.ASSIGN_EFFORT, ai.ASSIGN_RULES, questions, Assignments)


def print_overview(lv: LV) -> None:
    """Step 2: trade -> item type -> quantities per unit, for the normal items."""
    tree = overview(lv)
    if not tree:
        return
    normal = sum(entry["items"] for types in tree.values() for entry in types.values())
    print(f"overview: {normal} normal line items -> {sum(len(types) for types in tree.values())} item types "
          f"-> {len(tree)} trades")
    for trade, types in tree.items():
        print(f"  {trade} ({_items(sum(entry['items'] for entry in types.values()))})")
        for name, entry in types.items():
            quantities = " · ".join(f"{quantity:,.10g} {unit}" for unit, quantity in entry["quantities"].items())
            print(f"    {name:<34}{_items(entry['items']):>9}   {quantities}")
    optional = sum(1 for item in lv.items if item.kind == "optional")
    if optional:
        print(f"  ({optional} optional items are not in the overview)")


def ask_judge(questions: list[str]) -> list[JudgeAnswer | None]:
    """The real judge: one Claude call per question (a pack of items of one item type, or one item)."""
    return _ask("to judge candidates", ai.JUDGE_MODEL, ai.JUDGE_EFFORT, ai.JUDGE_RULES, questions, JudgeAnswer)


def print_summary(lv: LV) -> None:
    for method, entry in lv.summary.methods.items():
        print(f"  {method:<10}{_items(entry.items):>9}{entry.total:>16,.2f}")
    print(f"  {'total':<10}{_items(len(lv.items)):>9}{lv.summary.total:>16,.2f}  (normal items with a price; median x quantity)")
    unanswered = sum(1 for item in lv.items if item.prediction.reason == NO_ANSWER)
    if unanswered:
        print(f"  The judge gave no answer for {unanswered} of these items (counted under 'none'). Run again to retry.")


def _items(count: int) -> str:
    return f"{count} item" if count == 1 else f"{count} items"


if __name__ == "__main__":
    main()
