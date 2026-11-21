"""Command line: python -m lv ingest|price <LV file> --project <ID>"""
import argparse
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
FOLDERS = {"ingest": DATA / "library", "price": DATA / "out"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="lv", description="Read LV files and prepare them for pricing.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("ingest", "add a priced LV to the price library"),
                       ("price", "read a new LV and write its result file")):
        command = commands.add_parser(name, help=text)
        command.add_argument("file", type=Path, help="the LV file")
        command.add_argument("--project", required=True, help="short project ID, e.g. P4")
    args = parser.parse_args()
    raise SystemExit(f"'{args.command}' is not built yet.")


if __name__ == "__main__":
    main()
