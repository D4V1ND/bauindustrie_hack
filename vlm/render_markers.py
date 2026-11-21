"""Preview of what the dashboard shows when ONE position/finding is clicked:
the plan with only that finding's markers (no static image with all findings).

Usage: python render_markers.py out/<lv>_ergebnis.json plans/<plan>.png [--id b-003]
Writes out/marker/<finding id>.png for every finding with markers (or only --id).
The dashboard does the same in the browser: it draws the markers of the selected
finding (x, y, optional breite/hoehe, relative 0..1) as an overlay on the plan image.
"""
import argparse
import json
from pathlib import Path

import pymupdf

COLORS = {"fehler": (0.85, 0.1, 0.1), "risiko": (0.95, 0.5, 0.0), "hinweis": (0.1, 0.4, 0.9)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("plan")
    ap.add_argument("--id")
    a = ap.parse_args()
    result = json.loads(Path(a.result).read_text(encoding="utf-8"))
    pix = pymupdf.open(a.plan)[0].get_pixmap()
    w, h = pix.width, pix.height
    r = max(w, h) * 0.014
    out_dir = Path(a.result).parent / "marker"
    out_dir.mkdir(exist_ok=True)
    n = 0
    for f in result["befunde"]:
        if not f["marker"] or (a.id and f["id"] != a.id):
            continue
        doc = pymupdf.open()
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, filename=a.plan)
        col = COLORS.get(f["schwere"], (0, 0, 0))
        for m in f["marker"]:
            x, y = m["x"] * w, m["y"] * h
            if m.get("breite"):
                bw, bh = m["breite"] * w / 2, m["hoehe"] * h / 2
                page.draw_rect(pymupdf.Rect(x - bw, y - bh, x + bw, y + bh), color=col, width=r / 4)
            else:
                page.draw_circle((x, y), r, color=col, width=r / 5)
        page.insert_text((r, r * 1.6), f"{f['id']}  {f['oz'] or '-'}  {f['titel']}"[:110], fontsize=r * 1.2, color=col)
        page.get_pixmap(dpi=72).save(out_dir / f"{f['id']}.png")
        n += 1
    print(f"{n} Bild(er) -> {out_dir}")
