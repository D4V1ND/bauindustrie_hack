"""Command line: python -m lv ingest|price <LV file> --project <ID>"""
import argparse
from pathlib import Path

from lv.extract import read
from lv.model import save

DATA = Path(__file__).resolve().parent.parent / "data"
FOLDERS = {"ingest": "library", "price": "out"}


def main(argv: list[str] | None = None, data: Path = DATA) -> None:
    parser = argparse.ArgumentParser(prog="lv", description="Read LV files and prepare them for pricing.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("ingest", "add a priced LV to the price library"),
                       ("price", "read a new LV and write its result file")):
        command = commands.add_parser(name, help=text)
        command.add_argument("file", type=Path, help="the LV file")
        command.add_argument("--project", required=True, help="short project ID, e.g. P4")
    args = parser.parse_args(argv)

    try:
        lv = read(args.file, args.project)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    if args.command == "ingest" and not lv.priced:
        raise SystemExit(f"{args.file.name} has no prices, so it cannot go into the price library.")
    path = save(lv, data / FOLDERS[args.command])
    print(f"{len(lv.items)} line items -> {path}")


if __name__ == "__main__":
    main()
