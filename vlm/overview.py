"""One fixed overview per plan + LV: the plan with ALL located positions numbered, plus a legend,
as PNG (paste into Excel/Word/slides, show in a frontend) and as Excel (all positions + the picture).

Usage: python overview.py out/<lv>_ergebnis.json plans/<plan>.png
Writes out/<lv>__<plan>_uebersicht.png and .xlsx (run.py does this automatically).
"""
import sys
from pathlib import Path

import pymupdf

RGB = {"ok": (0.18, 0.56, 0.36), "fehler": (0.78, 0.2, 0.12), "risiko": (0.88, 0.47, 0.1),
       "hinweis": (0.18, 0.42, 0.87), "grau": (0.3, 0.45, 0.45)}
RANK = {"fehler": 3, "risiko": 2, "hinweis": 1}
STATUS_DE = {"ok": "OK", "ok_instabil": "OK, Zahl schwankt", "abweichung": "Abweichung",
             "nicht_im_plan": "Nicht im Plan gefunden", "einheit_nicht_vergleichbar": "Einheit nicht vergleichbar",
             "nicht_geprueft": "Nicht gegen Plan geprüft"}


def _txt(s) -> str:
    """Base-14 PDF fonts only know Latin-1/cp1252."""
    return str(s).replace("→", "->").replace("×", "x").encode("cp1252", "replace").decode("cp1252")


def _color(pos: dict, finds: list[dict]) -> str:
    top = max((f["schwere"] for f in finds), key=lambda s: RANK.get(s, 0), default=None)
    if top:
        return top
    return "ok" if pos.get("pruefung") == "ok" else "grau"


def build_entries(result: dict) -> list[dict]:
    """Numbered entries: located LV positions (1, 2, ...) and plan parts without an LV position (P1, P2, ...)."""
    by_id = {f["id"]: f for f in result["befunde"]}
    entries, n = [], 0
    for p in result["positionen"]:
        pl = p.get("plan")
        if not (pl and pl.get("marker")):
            continue
        n += 1
        finds = [by_id[i] for i in p["befund_ids"] if i in by_id]
        entries.append({"nr": str(n), "oz": p["oz"], "text": p["kurztext"], "color": _color(p, finds),
                        "markers": pl["marker"], "lv": f"{p['menge']:g} {p['einheit']}" if p["menge"] is not None else "",
                        "plan": f"{pl['menge']:g}" if pl.get("menge") is not None else ""})
    k = 0
    for f in result["befunde"]:
        if f["oz"] is None and f["marker"]:
            k += 1
            entries.append({"nr": f"P{k}", "oz": "-", "text": "Im Plan, nicht im LV: " + f["titel"].split(": ")[-1],
                            "color": f["schwere"] if f["schwere"] in RGB else "hinweis", "markers": f["marker"],
                            "lv": "", "plan": f"{f['plan']['menge']:g}" if f.get("plan") else ""})
    return entries


def render_overview(result: dict, plan_path: Path, out_png: Path) -> list[dict]:
    entries = build_entries(result)
    pix = pymupdf.open(plan_path)[0].get_pixmap()
    W, H = pix.width, pix.height
    S = max(W, H)
    fs = S * 0.0135                                # legend font size
    lh = fs * 1.45
    rows = max(1, int((H - fs * 4) // lh))
    cols = max(1, -(-len(entries) // rows))
    colw = fs * 38
    doc = pymupdf.open()
    page = doc.new_page(width=W + cols * colw + fs, height=H)
    page.insert_image(pymupdf.Rect(0, 0, W, H), filename=str(plan_path))

    # group entries that share the same marker (several positions -> one room frame / one symbol)
    shared: dict[tuple, dict] = {}
    for e in entries:
        for m in e["markers"]:
            key = (m.get("seite", 1), round(m["x"], 3), round(m["y"], 3), round(m.get("breite") or 0, 3))
            s = shared.setdefault(key, {"m": m, "nrs": [], "color": e["color"]})
            s["nrs"].append(e["nr"])
            if RANK.get(e["color"], 0) > RANK.get(s["color"], 0):
                s["color"] = e["color"]
    r, sw = S * 0.0105, S * 0.0028
    for s in sorted(shared.values(), key=lambda s: -(s["m"].get("breite") or 0)):   # big frames first
        m, col = s["m"], RGB[s["color"]]
        x, y = m["x"] * W, m["y"] * H
        label = _txt(",".join(dict.fromkeys(s["nrs"])))
        if m.get("breite"):
            bw, bh = m["breite"] * W / 2, m["hoehe"] * H / 2
            page.draw_rect(pymupdf.Rect(x - bw, y - bh, x + bw, y + bh), color=col, fill=col, fill_opacity=0.07, width=sw)
            lx, ly = x - bw + sw * 2, y - bh + sw * 2
        else:
            page.draw_circle((x, y), r, color=(1, 1, 1), width=sw * 2.6)
            page.draw_circle((x, y), r, color=col, width=sw)
            lx, ly = x + r * 0.9, y - r * 0.9
        tw = pymupdf.get_text_length(label, fontsize=fs * 0.95) + fs * 0.4
        page.draw_rect(pymupdf.Rect(lx, ly, lx + tw, ly + fs * 1.15), color=col, fill=(1, 1, 1), width=sw * 0.6)
        page.insert_text((lx + fs * 0.2, ly + fs * 0.9), label, fontsize=fs * 0.95, color=col)

    # legend to the right of the plan
    x0 = W + fs
    page.insert_text((x0, fs * 1.6), _txt(f"{result['eingaben']['lv']['datei']}  |  Plan {result['eingaben']['plan']['id']}"),
                     fontsize=fs * 0.9, color=(0.1, 0.1, 0.1))
    for i, e in enumerate(entries):
        c, rr = divmod(i, rows)
        x, y = x0 + c * colw, fs * 3.4 + rr * lh
        page.draw_rect(pymupdf.Rect(x, y - fs * 0.75, x + fs * 0.9, y + fs * 0.1), color=RGB[e["color"]], fill=RGB[e["color"]])
        line = f"{e['nr']:>3}  {e['oz']:<8} {e['text'][:34]}"
        if e["lv"] or e["plan"]:
            line += f"  [LV {e['lv'] or '-'} / Plan {e['plan'] or '-'}]"
        page.insert_text((x + fs * 1.2, y), _txt(line), fontsize=fs * 0.8, color=(0.1, 0.1, 0.1))
    page.get_pixmap(dpi=72).save(out_png)
    return entries


def write_xlsx(result: dict, entries: list[dict], png: Path, out_xlsx: Path) -> None:
    import openpyxl
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill

    nr_of = {e["oz"]: e["nr"] for e in entries if e["oz"] != "-"}
    by_id = {f["id"]: f for f in result["befunde"]}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Positionen"
    ws.append(["Nr. im Plan", "OZ", "Position", "LV-Menge", "Einheit", "Plan-Menge", "Status", "Verortung", "Befunde"])
    for p in result["positionen"]:
        pl = p.get("plan") or {}
        loc = ("Raum: " + ", ".join(pl["raeume"]) if pl.get("raeume") else "Bauteil") if pl.get("marker") else "-"
        finds = "; ".join(f"[{by_id[i]['schwere']}] {by_id[i]['titel']}" for i in p["befund_ids"] if i in by_id)
        ws.append([nr_of.get(p["oz"], "-"), p["oz"], p["kurztext"], p["menge"], p["einheit"], pl.get("menge"),
                   STATUS_DE.get(p["pruefung"], p["pruefung"]), loc, finds])
    for e in (x for x in entries if x["oz"] == "-"):
        ws.append([e["nr"], "-", e["text"], None, "", None, "Im Plan, nicht im LV", "Bauteil", ""])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F6F6A")
    for col, w in zip("ABCDEFGHI", (11, 10, 60, 11, 8, 11, 26, 26, 60)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet("Plan")
    img = XLImage(str(png))
    scale = 1800 / img.width
    img.width, img.height = int(img.width * scale), int(img.height * scale)
    ws2.add_image(img, "A1")
    wb.save(out_xlsx)


def make(result: dict, plan_path: Path, out_dir: Path, lv_stem: str) -> tuple[Path, Path]:
    base = out_dir / f"{lv_stem}__{plan_path.stem}_uebersicht"
    png, xlsx = base.with_suffix(".png"), base.with_suffix(".xlsx")
    entries = render_overview(result, plan_path, png)
    write_xlsx(result, entries, png, xlsx)
    return png, xlsx


if __name__ == "__main__":
    import json

    rp, pp = Path(sys.argv[1]), Path(sys.argv[2])
    res = json.loads(rp.read_text(encoding="utf-8"))
    a, b = make(res, pp, rp.parent, rp.stem.replace("_ergebnis", ""))
    print(f"{a}\n{b}")
