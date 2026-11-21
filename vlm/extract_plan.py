"""Read a construction plan (PNG/JPG/PDF) with the vision model: component list
with counts and approximate x/y positions (relative 0..1, origin top-left).

Usage: python extract_plan.py plans/plan.png [--runs 3] [--pages 1,2] [--dpi 150]
"""
import argparse
import base64
import csv
import json
import sys
from pathlib import Path

from llm_common import chat_json, client_and_model

PROMPT = """Du liest einen deutschen Bauplan (Grundriss, Ausführungsplan oder Fenster-/Türliste).
Erstelle eine Liste aller zählbaren Bauteile, damit sie mit einem Leistungsverzeichnis verglichen werden kann.

Regeln:
- Wenn der Plan eine Fenster-, Tür- oder Bauteilliste (Tabelle) enthält, übernimm deren Zahlen und setze quelle="Liste".
- Zähle sonst Symbole im Grundriss und setze quelle="gezaehlt".
- Nutze die Legende des Plans, um Kürzel (z. B. LS, NA, FL) in bezeichnung auszuschreiben.
- Erfinde nichts. Wenn etwas unlesbar oder unsicher ist, setze konfidenz="niedrig" und schreibe den Grund in bemerkung.
- Maße wie auf dem Plan angegeben (z. B. "1,01 x 1,26"), sonst leer.
- marker: Position jedes gezählten Objekts bzw. seiner Beschriftung im Bild, x und y relativ zur Bildbreite/-höhe
  (0 bis 1, Ursprung oben links). Bei mehreren Objekten ein Eintrag je Objekt. Schätze, wenn nötig; lass die Liste
  leer, wenn die Position nicht erkennbar ist (z. B. Zahl nur aus einer Tabelle). seite = Nummer des Bildes (ab 1).

Antworte NUR mit JSON in diesem Format:
{"positionen": [{"kategorie": "Fenster|Tür|Sanitär|Leuchte|Sonstiges",
  "typ_bezeichnung": "...", "bezeichnung": "ausgeschriebener Name laut Legende", "masse": "...", "anzahl": 0,
  "geschoss": "...", "raum": "...", "quelle": "Liste|gezaehlt",
  "konfidenz": "hoch|mittel|niedrig", "bemerkung": "...",
  "marker": [{"seite": 1, "x": 0.0, "y": 0.0}]}]}"""


def page_images(path: Path, pages: list[int] | None, dpi: int) -> list[bytes]:
    """PNG bytes for each image / PDF page."""
    if path.suffix.lower() == ".pdf":
        import pymupdf

        doc = pymupdf.open(path)
        idx = [p - 1 for p in pages] if pages else range(len(doc))
        return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in idx]
    return [path.read_bytes()]


def image_size(path: Path) -> tuple[int, int]:
    import pymupdf

    r = pymupdf.open(path)[0].rect
    return int(r.width), int(r.height)


def extract_runs(path: Path, runs: int = 3, pages: list[int] | None = None, dpi: int = 150,
                 client=None, model=None) -> list[list[dict]]:
    if client is None:
        client, model = client_and_model()
    images = page_images(path, pages, dpi)
    content = [{"type": "text", "text": PROMPT}]
    for img in images:
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}})
    print(f"{path.name}: {len(images)} Bild(er), {runs} Läufe", flush=True)
    out = []
    for n in range(runs):
        out.append(chat_json(client, model, content).get("positionen", []))
        print(f"  Lauf {n + 1}/{runs}: {len(out[-1])} Einträge", flush=True)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--pages")
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args()
    plan = Path(a.plan)
    runs = extract_runs(plan, a.runs, [int(p) for p in a.pages.split(",")] if a.pages else None, a.dpi)
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    (out / f"{plan.stem}.json").write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["kategorie", "typ_bezeichnung", "bezeichnung", "masse", "anzahl", "geschoss", "raum",
            "quelle", "konfidenz", "bemerkung"]
    with open(out / f"{plan.stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(runs[0])
    print(f"Gespeichert in {out}")
