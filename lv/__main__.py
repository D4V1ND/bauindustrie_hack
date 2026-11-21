"""Command line: python -m lv ingest|price <LV file> --project <ID> [--plan <plan>]"""
import argparse
import json
import re
import time
from pathlib import Path

from lv import ai, plan
from lv.categorise import Assignments, Proposal, assign, overview, propose
from lv.extract import add_texts, read
from lv.match import group_label
from lv.model import LV, load, save
from lv.predict import NO_ANSWER, JudgeAnswer, predict
from lv.risk import Checks, assess

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
        if name == "ingest":
            command.add_argument("--texts", type=Path, nargs="+", default=[], metavar="PDF",
                                 help="PDF prints of the same LV, when the priced file holds prices without texts")
        if name == "price":
            command.add_argument("--copy-only", action="store_true",
                                 help="only copy the prices of exact matches: no item types, no search, no AI, "
                                      "no API key needed")
            command.add_argument("--plan", type=Path, metavar="PLAN",
                                 help="a construction plan (PNG, JPG or PDF): also compare the LV's quantities with it "
                                      "and write <project>_plan.json (needs OPENAI_API_KEY and OPENAI_MODEL)")
    args = parser.parse_args(argv)
    plan_ai = None
    if getattr(args, "plan", None):  # checked first, so a missing plan or key costs nothing
        if args.copy_only:
            raise SystemExit("The plan check asks AI models, so it cannot run with --copy-only.")
        try:
            plan.check_file(args.plan)
        except ValueError as error:
            raise SystemExit(str(error))
        plan_ai = plan.client()

    try:
        lv = read(args.file, args.project)
        notes = add_texts(lv, args.texts) if args.command == "ingest" and args.texts else []
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    plan_job = None
    if plan_ai:  # needs only step 0's items, so it runs beside steps 1 to 4
        print(f"plan check: started in parallel, {args.plan.name} with {plan_ai[1]}, read {plan.RUNS} times", flush=True)
        plan_job = plan.Background(plan.check, plan.positions(lv), args.plan, plan_ai)
    if args.command == "ingest" and not lv.priced:
        raise SystemExit(f"{args.file.name} has no prices, so it cannot go into the price library.")
    if args.command == "ingest" and args.texts:
        from_pdf = [item for item in lv.items if item.read_by == "pdf"]
        print(f"texts from PDF: {len(from_pdf)} of {_items(len(lv.items))}, "
              f"{sum(1 for item in from_pdf if item.long_text)} of them with a long text")
        for note in notes:
            print(f"  {note}")
    for item in lv.items:
        item.group = group_label(item)
    judge_log: list[dict] = []
    if args.command == "price":
        library = [load(path) for path in sorted((data / "library").glob("*.json"))]
        priced = sum(1 for past in library for item in past.items if item.unit_price)
        print(f"price library: {priced} line items with a price from {len(library)} project(s)")
        try:
            if lv.items and not args.copy_only:  # step 2
                lv.item_types = propose(lv, ask_types)
                asked = assign(lv, lv.item_types, ask_assignments)
                print(f"  {_items(asked)} asked, {len(lv.items) - asked} copied from an identical item")
            embed = ai.embed if ai.voyage_key() and not args.copy_only else None
            if not args.copy_only:
                print(f"search: by words and by meaning ({ai.EMBED_MODEL})" if embed
                      else "search: by words only (no VOYAGE_API_KEY in .env)")
            judge = None if args.copy_only else lambda questions: ask_judge(questions, judge_log)
            predict(lv, library, judge=judge, embed=embed)  # step 3
            link_judge_calls(lv, judge_log)
            assess(lv, None if args.copy_only else ask_descriptions)  # step 4
        except (ai.MissingKey, RuntimeError) as stop:  # RuntimeError: every call of one kind failed
            raise SystemExit(str(stop))
        print_overview(lv)
        print_summary(lv)
        print_risk(lv)
    path = save(lv, data / FOLDERS[args.command])
    print(f"{len(lv.items)} line items -> {path}")
    if judge_log:
        log = path.with_name(f"{lv.project}_judge.json")
        log.write_text(json.dumps(judge_log, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(judge_log)} judge calls with the judge's thinking -> {log}")
    if plan_job:  # collected after the LV result is saved: a failing plan check loses nothing of it
        if not plan_job.done():
            print("plan check: still running, waiting for it ...", flush=True)
        try:
            result = plan_job.result()
        except Exception as error:
            raise SystemExit(f"The plan check failed; the LV result above is saved. {type(error).__name__}: {error}")
        out = path.with_name(f"{lv.project}_plan.json")
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        counts = result["zusammenfassung"]
        print(f"  {counts['befunde']} findings, {counts['geprueft']} items compared, {time.monotonic() - plan_job.started:.0f} s from its start -> {out}")
        try:  # the prototype's fixed overview: plan with the items numbered (PNG) and the item list (Excel)
            png, xlsx = plan.overview(result, args.plan, path.parent, lv.project)
        except Exception as error:
            raise SystemExit(f"The plan overview failed; both result files above are saved. {type(error).__name__}: {error}")
        print(f"  overview -> {png.name}, {xlsx.name}")
        plan.merge(lv, result, out.name, [png.name, xlsx.name])
        save(lv, path.parent)  # the one structured result: the LV with its plan check merged in
        print(f"plan check merged into {path.name}: {sum(1 for item in lv.items if item.plan)} items, "
              f"{len(lv.plan.findings)} findings")


def _ask(job: str, model: str, effort: str, rules: str, questions: list[str], shape: type,
         thinking: bool = False) -> list:
    """One real AI job: says what is asked, asks, and says how long it took."""
    calls = "1 call" if len(questions) == 1 else f"{len(questions)} calls"
    print(f"asking {model} ({effort} effort) {job}: {calls} ...", flush=True)  # whole lines: a plan check may print
    start = time.monotonic()                                                       # in between (see lv/plan.py)
    answers = (ai.ask_with_thinking if thinking else ai.ask)(model, rules, questions, shape, effort=effort)
    print(f"  {job}: done in {time.monotonic() - start:.0f} s", flush=True)
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


def link_judge_calls(lv: LV, judge_log: list[dict]) -> None:
    """Points each item's prediction at the judge call that decided it: the last call that asked about the item (an
    item whose part of a pack answer did not fit is asked again on its own, and that later call counts). A copy of
    an identical item points at the call of the item that was judged."""
    calls: dict[str, int] = {}
    for call in judge_log:
        for id in call["items"]:
            calls[id] = call["call"]  # later calls overwrite earlier ones
    for item in lv.items:
        if item.prediction:
            item.prediction.judge_call = calls.get(item.prediction.judged_item or item.id)


def ask_judge(questions: list[str], log: list[dict] | None = None) -> list[JudgeAnswer | None]:
    """The real judge: one Claude call per question (a pack of items of one item type, or one item). With `log`,
    every call is added to it: the items asked about, the question, the judge's summarised thinking, its answer."""
    pairs = _ask("to judge candidates", ai.JUDGE_MODEL, ai.JUDGE_EFFORT, ai.JUDGE_RULES, questions, JudgeAnswer,
                 thinking=True)
    if log is not None:
        before = len(log)  # read once: extend() grows the list while it runs
        log.extend({"call": before + n + 1, "items": re.findall(r"^### (\S+)", question, re.M), "question": question,
                    "thinking": thinking, "answer": answer.model_dump() if answer else None}
                   for n, (question, (answer, thinking)) in enumerate(zip(questions, pairs)))
    return [answer for answer, _ in pairs]


def ask_descriptions(questions: list[str]) -> list[Checks | None]:
    return _ask("whether the descriptions are complete", ai.RISK_MODEL, ai.RISK_EFFORT, ai.DESCRIPTION_RULES,
                questions, Checks)


def print_risk(lv: LV) -> None:
    """Step 4: the LV's risk score, what it rests on, and the items that weigh most."""
    risk = lv.risk_summary
    basis = ", ".join(f"{count} by {name}" for name, count in risk.basis.items())
    print(f"risk: {risk.score} of 100, weighted by value over the normal items (value: {basis})")
    top = sorted((item for item in lv.items if item.kind == "normal"),
                 key=lambda item: -(item.risk.value or 0.0) * item.risk.score)[:5]
    for item in top:
        print(f"  {item.risk.score:>3}  {item.risk.value or 0:>12,.2f} ({item.risk.value_basis})  {item.oz}  {item.short_text[:50]}")


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
