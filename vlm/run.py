"""End to end: GAEB LV (+ optional plan image) -> result JSON for the dashboard.

Usage:
  python run.py --lv plans/hoersaal.X83                    # LV only (positions)
  python run.py --lv plans/hoersaal.X83 --plan plans/hoersaal_mock.png --runs 3
Steps are cached in out/ (use --neu to read again).
"""
import argparse
import json
import sys
from pathlib import Path

from compare import build_result
from extract_plan import extract_runs, image_size
from gaeb_llm import extract_positions
from llm_common import client_and_model

OUT = Path(__file__).parent / "out"


def cached(path: Path, neu: bool, make):
    if path.exists() and not neu:
        print(f"(aus Cache: {path.name})")
        return json.loads(path.read_text(encoding="utf-8"))
    data = make()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--lv", required=True)
    ap.add_argument("--plan")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--neu", action="store_true", help="ignore the cache")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    client, model = client_and_model()

    lv_path = Path(a.lv)
    lv = cached(OUT / f"{lv_path.stem}_positionen.json", a.neu,
                lambda: extract_positions(lv_path, client, model))

    plan_runs = plan_meta = None
    if a.plan:
        pp = Path(a.plan)
        plan_runs = cached(OUT / f"{pp.stem}_laeufe{a.runs}.json", a.neu,
                           lambda: extract_runs(pp, a.runs, client=client, model=model))
        w, h = image_size(pp)
        plan_meta = {"id": pp.stem, "datei": pp.name, "breite_px": w, "hoehe_px": h}

    result = build_result(lv, plan_runs, plan_meta, client, model)
    dest = OUT / f"{lv_path.stem}_ergebnis.json"
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    z = result["zusammenfassung"]
    print(f"\n{result['eingaben']['lv']['positionen']} Positionen, {z['befunde']} Befunde -> {dest}")
    for f in result["befunde"]:
        print(f"  [{f['schwere']}] {f['id']} {f['oz'] or '-'}: {f['titel']}")
