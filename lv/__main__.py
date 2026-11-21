"""Command line: python -m lv ingest|price <LV file> --project <ID>"""
import argparse
from pathlib import Path

from lv.extract import read
from lv.match import group_label
from lv.model import LV, load, save
from lv.predict import predict

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
                                 help="only copy the prices of exact matches: no search, no AI, no API key needed")
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
        predict(lv, library, judge=None)
        print_summary(lv)
    path = save(lv, data / FOLDERS[args.command])
    print(f"{len(lv.items)} line items -> {path}")


def print_summary(lv: LV) -> None:
    for method, entry in lv.summary.methods.items():
        print(f"  {method:<10}{entry.items:>5} items{entry.total:>16,.2f}")
    print(f"  {'total':<10}{len(lv.items):>5} items{lv.summary.total:>16,.2f}  (normal items with a price; median x quantity)")


if __name__ == "__main__":
    main()
