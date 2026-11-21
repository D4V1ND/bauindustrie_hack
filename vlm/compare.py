"""Compare LV positions (read from GAEB by the LLM) with the component list from a plan
and build the result JSON for the dashboard: positions, findings (with a detailed
description and x/y markers) and a decision field per finding."""
import re
import statistics

COUNT_UNITS = {"st", "stk", "stück"}
CODE = re.compile(r"\b([A-ZÄÖÜ]{1,3})(\d{1,2})\b")
RANGE = re.compile(r"\b([A-ZÄÖÜ]{1,3})(\d{1,2})\s*[–\-]\s*(?:[A-ZÄÖÜ]{1,3})?(\d{1,2})\b")
DIM = re.compile(r"(\d+[.,]\d+)\s*[x×]\s*(\d+[.,]\d+)")


def codes(text: str) -> set[str]:
    """Plan codes like F1, T3. Ranges like F1–F4 are expanded."""
    out = set()
    for m in RANGE.finditer(text or ""):
        out.update(f"{m.group(1)}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1))
    out.update(m.group(0) for m in CODE.finditer(text or ""))
    return out


def dims(text: str) -> str | None:
    m = DIM.search(text or "")
    return f"{m.group(1).replace('.', ',')}x{m.group(2).replace('.', ',')}" if m else None


def words(s: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", (s or "").lower())]


def plan_items(runs: list[list[dict]]) -> list[dict]:
    """Median count per (kategorie, typ) over all runs; markers from the first run that has them."""
    per_key: dict[tuple, list[float]] = {}
    meta: dict[tuple, dict] = {}
    for r in runs:
        seen: dict[tuple, float] = {}
        for p in r:
            k = (p.get("kategorie", ""), p.get("typ_bezeichnung", ""), p.get("bezeichnung", ""))
            seen[k] = seen.get(k, 0) + float(p.get("anzahl") or 0)
            if k not in meta or (p.get("marker") and not meta[k].get("marker")):
                meta[k] = p
        for k, v in seen.items():
            per_key.setdefault(k, []).append(v)
    items = []
    for k, vals in per_key.items():
        vals = vals + [0.0] * (len(runs) - len(vals))
        p = meta[k]
        items.append({
            "kategorie": k[0], "typ": k[1], "name": k[2], "anzahl": statistics.median(vals),
            "laeufe": vals, "masse": p.get("masse") or "",
            # stable = runs differ by at most 1 or 5 % of the median (whichever is larger)
            "stabil": (max(vals) - min(vals)) <= max(1.0, 0.05 * statistics.median(vals)),
            "konfidenz": p.get("konfidenz", ""), "marker": p.get("marker") or [],
            "codes": codes(k[1]), "dim": dims(p.get("masse") or "") or dims(k[1]),
        })
    return items


MAX_POINT_MARKERS = 10  # more objects than this -> one frame around all of them


def _markers(items: list[dict], plan_id: str, label: str | None = None) -> list[dict]:
    """Markers of one finding. x/y = centre, relative 0..1. Many objects -> a single frame (breite/hoehe)."""
    pts = []
    for p in items:
        for m in p["marker"]:
            if m.get("x") is None or m.get("y") is None:
                continue
            pts.append((m.get("seite", 1), float(m["x"]), float(m["y"]), label or p["typ"] or p["name"]))
    if len(pts) > MAX_POINT_MARKERS:
        xs, ys = [p[1] for p in pts], [p[2] for p in pts]
        pad = 0.01
        x0, x1, y0, y1 = max(0, min(xs) - pad), min(1, max(xs) + pad), max(0, min(ys) - pad), min(1, max(ys) + pad)
        return [{"plan_id": plan_id, "seite": pts[0][0], "x": round((x0 + x1) / 2, 4), "y": round((y0 + y1) / 2, 4),
                 "breite": round(x1 - x0, 4), "hoehe": round(y1 - y0, 4), "form": "rahmen",
                 "anzahl_objekte": len(pts), "genauigkeit": "grob", "beschriftung": pts[0][3]}]
    return [{"plan_id": plan_id, "seite": s, "x": round(x, 4), "y": round(y, 4), "form": "punkt",
             "genauigkeit": "grob", "beschriftung": b} for s, x, y, b in pts]


def _fmt(x) -> str:
    return f"{x:g}"


def match(row: dict, plan: list[dict]) -> tuple[list[int], str]:
    hits = [i for i, p in enumerate(plan) if row["codes"] & p["codes"]]
    if hits:
        return hits, "Plankürzel"
    d = dims(row["kurztext"])
    hits = [i for i, p in enumerate(plan)
            if d and p["dim"] == d and p["kategorie"].lower()[:4] in row["kurztext"].lower()]
    if hits:
        return hits, "Maße"
    return [], ""



MATCH_PROMPT = """Ordne Bauteile aus einem Bauplan den passenden Positionen eines Leistungsverzeichnisses (LV) zu.
Jeder Plan-Eintrag gehört zu höchstens einer LV-Position. Mehrere Plan-Einträge können zu einer Position
gehören. Ordne nur zu, wenn es inhaltlich eindeutig dasselbe Bauteil ist (z. B. Sitzplätze = Klappsitze).
Ordne NICHT zu, wenn unsicher. Die Menge ist für die Zuordnung egal.
Gehe die LV-Positionen der Reihe nach durch und gib für JEDE Position genau einen Eintrag aus
(plan_indizes leer, wenn kein Plan-Eintrag passt). Beachte: ein Wort wie "Notausgang" kann ein Schild, eine
Leuchte oder eine Tür meinen; entscheide nach der Art des Bauteils.

LV-Positionen (oz, Text, Menge, Einheit):
{lv}

Plan-Einträge (Index, Typ, Name, Anzahl):
{plan}

Antworte NUR mit JSON: {{"zuordnung": [{{"oz": "...", "plan_indizes": [0], "sicherheit": "hoch|mittel|niedrig"}}]}}"""


def llm_match(client, model, lv_rows: list[dict], plan: list[dict], idx: list[int]) -> list[dict]:
    from llm_common import chat_json

    lv_txt = "\n".join(f"{p['oz']} | {p['kurztext']} | {p['menge']} {p['einheit']}" for p in lv_rows)
    pl_txt = "\n".join(f"{i} | {plan[i]['typ']} | {plan[i]['name']} | {plan[i]['anzahl']:g}" for i in idx)
    res = chat_json(client, model, [{"type": "text", "text": MATCH_PROMPT.format(lv=lv_txt, plan=pl_txt)}])
    return res.get("zuordnung", [])


def build_result(lv: dict, plan_runs: list[list[dict]] | None, plan_meta: dict | None,
                 client=None, model=None) -> dict:
    positions = [dict(p) for p in lv["positionen"]]
    for p in positions:
        p["menge"] = float(p["menge"]) if p.get("menge") not in (None, "") else None
        p.update({"pruefung": "nicht_geprueft", "befund_ids": []})
    findings: list[dict] = []

    def add(**kw):
        f = {"id": f"b-{len(findings) + 1:03d}", "entscheidung": {"status": "offen", "kommentar": None,
                                                                   "von": None, "am": None}, **kw}
        findings.append(f)
        return f["id"]

    if plan_runs is not None:
        plan = plan_items(plan_runs)
        pid = plan_meta["id"]
        is_st = lambda p: str(p.get("einheit", "")).strip().lower() in COUNT_UNITS and p["menge"] is not None
        by_oz = {p["oz"]: p for p in positions}
        matches: dict[str, tuple[list[int], str]] = {}
        other_unit: dict[str, list[int]] = {}
        used: set[int] = set()

        # Stage 1: deterministic (plan codes like F1-F4, then dimensions)
        for pos in positions:
            if not is_st(pos):
                continue
            hits, how = match({"kurztext": pos.get("kurztext", ""), "codes": codes(pos.get("kurztext", ""))}, plan)
            if hits:
                matches[pos["oz"]] = (hits, how)
                used.update(hits)

        # Stage 2: the LLM matches the rest (only the unsure ones), any unit
        open_plan = [i for i, p in enumerate(plan) if i not in used and p["anzahl"]]
        open_lv = [p for p in positions if p["oz"] not in matches and p["menge"] is not None]
        if client and open_plan and open_lv:
            votes: dict[tuple, int] = {}
            for _ in range(3):  # majority vote: the LLM answer varies between calls
                for z in llm_match(client, model, open_lv, plan, open_plan):
                    if z.get("plan_indizes") and z.get("sicherheit") != "niedrig":
                        key = (z.get("oz"), tuple(sorted(z["plan_indizes"])))
                        votes[key] = votes.get(key, 0) + 1
            for (oz_, idxs), v in sorted(votes.items()):
                pos = by_oz.get(oz_)
                hits = [i for i in idxs if i in open_plan and i not in used]
                if v < 2 or not pos or not hits:
                    continue
                used.update(hits)
                if is_st(pos):
                    matches[pos["oz"]] = (hits, "KI-Zuordnung")
                else:
                    other_unit[pos["oz"]] = hits

        for pos in positions:
            if not is_st(pos):
                continue
            oz, txt = pos["oz"], pos["kurztext"]
            row_codes = codes(txt)
            if oz not in matches:
                has_code = bool(row_codes)
                bid = add(
                    oz=oz, art="nicht_im_plan", schwere="fehler" if has_code else "hinweis",
                    titel="Position im Plan nicht gefunden",
                    beschreibung=(
                        f"Das LV führt unter {oz} „{txt}“ mit {_fmt(pos['menge'])} {pos['einheit']}. "
                        f"Auf dem Plan {pid} wurde kein passendes Bauteil gefunden"
                        + (f" (gesucht nach {', '.join(sorted(row_codes))})" if has_code else "")
                        + ". Mögliche Ursachen: Das Bauteil fehlt im Plan, es ist anders bezeichnet, "
                          "es steht auf einem anderen Plan oder der Planstand ist veraltet. "
                        + ("Bitte vor der Kalkulation prüfen." if has_code else
                           "Ohne Kürzel im LV-Text ist die Zuordnung unsicher, deshalb nur ein Hinweis.")),
                    lv={"menge": pos["menge"], "einheit": pos["einheit"]}, plan={"menge": 0},
                    regel="stueckzahl_vergleich", quelle="vlm", konfidenz="mittel", marker=[])
                pos["pruefung"] = "nicht_im_plan"
                pos["befund_ids"].append(bid)
                continue
            hits, how = matches[oz]
            sel = [plan[i] for i in hits]
            n = sum(p["anzahl"] for p in sel)
            stable = all(p["stabil"] for p in sel)
            if n == pos["menge"] and stable:
                pos["pruefung"] = "ok"
                continue
            if n == pos["menge"]:
                pos["pruefung"] = "ok_instabil"
                bid = add(
                    oz=oz, art="instabile_zaehlung", schwere="hinweis",
                    titel="Planzahl stimmt, schwankt aber zwischen den Läufen",
                    beschreibung=(f"{oz} „{txt}“: LV und Plan nennen {_fmt(n)} {pos['einheit']}, die Zählung "
                                  f"am Plan war aber nicht in allen Läufen gleich "
                                  f"({', '.join(_fmt(v) for p in sel for v in p['laeufe'])}). "
                                  "Zahl bitte kurz am Plan prüfen."),
                    lv={"menge": pos["menge"], "einheit": pos["einheit"]}, plan={"menge": n},
                    regel="stueckzahl_vergleich", quelle="vlm", konfidenz="niedrig",
                    marker=_markers(sel, pid))
                pos["befund_ids"].append(bid)
                continue
            diff = n - pos["menge"]
            bid = add(
                oz=oz, art="menge_abweichung", schwere="fehler",
                titel=f"Menge weicht ab: LV {_fmt(pos['menge'])}, Plan {_fmt(n)}",
                beschreibung=(
                    f"Das LV führt unter {oz} „{txt}“ mit {_fmt(pos['menge'])} {pos['einheit']}. Auf dem Plan {pid} "
                    f"wurden {_fmt(n)} gezählt ({', '.join(p['typ'] or p['name'] for p in sel)}), Zuordnung über {how}. "
                    f"Differenz Plan minus LV: {diff:+g}. "
                    + ("Der Plan zeigt mehr als das LV: Es könnte Menge im LV fehlen (Nachtragsrisiko). "
                       if diff > 0 else "Das LV enthält mehr als der Plan zeigt: Möglich sind eine Überschätzung "
                                       "im LV oder ein Bauteil, das im Plan fehlt. ")
                    + "Bitte beide Quellen prüfen und die Menge klären."),
                lv={"menge": pos["menge"], "einheit": pos["einheit"]}, plan={"menge": n},
                regel="stueckzahl_vergleich", quelle="vlm" if how != "KI-Zuordnung" else "vlm+ki_zuordnung",
                konfidenz="mittel" if stable and how != "KI-Zuordnung" else "niedrig",
                marker=_markers(sel, pid))
            pos["pruefung"] = "abweichung"
            pos["befund_ids"].append(bid)

        for oz, hits in other_unit.items():
            pos, sel = by_oz[oz], [plan[i] for i in hits]
            n = sum(p["anzahl"] for p in sel)
            bid = add(
                oz=oz, art="einheit_nicht_vergleichbar", schwere="hinweis",
                titel=f"Plan zählt Stück, LV rechnet in {pos['einheit']}",
                beschreibung=(f"{oz} „{pos['kurztext']}“ steht im LV mit {_fmt(pos['menge'])} {pos['einheit']}. "
                              f"Auf dem Plan {pid} wurden dazu {_fmt(n)} Stück gezählt "
                              f"({', '.join(p['typ'] or p['name'] for p in sel)}). Die Einheiten sind nicht direkt "
                              "vergleichbar. Die Menge muss über Maße (Länge, Fläche) geprüft werden."),
                lv={"menge": pos["menge"], "einheit": pos["einheit"]}, plan={"menge": n, "einheit": "St"},
                regel="einheit_vergleich", quelle="vlm+ki_zuordnung", konfidenz="niedrig",
                marker=_markers(sel, pid))
            pos["pruefung"] = "einheit_nicht_vergleichbar"
            pos["befund_ids"].append(bid)

        for i, p in enumerate(plan):
            if i in used or not p["anzahl"]:
                continue
            label = f"{p['kategorie']} {p['typ']} {p['name']}".strip()
            add(
                oz=None, art="fehlt_im_lv", schwere="hinweis" if not p["typ"] else "risiko",
                titel=f"Im Plan vorhanden, im LV keine passende Position: {p['typ'] or p['name']}",
                beschreibung=(f"Auf dem Plan {pid} wurde „{label}“ ({_fmt(p['anzahl'])} Stück) erkannt. Im LV "
                              "wurde keine passende Position gefunden. Mögliche Ursachen: Die Leistung fehlt "
                              "im LV (Nachtragsrisiko), sie steckt in einer Sammelposition, oder die Zuordnung "
                              "ist unscharf. Bitte prüfen."),
                lv=None, plan={"menge": p["anzahl"], "einheit": "St"}, regel="stueckzahl_vergleich",
                quelle="vlm", konfidenz=p["konfidenz"] or "mittel", marker=_markers([p], pid))

    counts = {}
    for f in findings:
        counts[f["schwere"]] = counts.get(f["schwere"], 0) + 1
    return {
        "version": 1,
        "eingaben": {"lv": {"datei": lv["datei"], "format": "GAEB", "positionen": len(positions),
                            "pruefung": lv.get("pruefung")},
                     "plan": plan_meta},
        "positionen": positions,
        "befunde": findings,
        "zusammenfassung": {"befunde": len(findings), **counts,
                            "geprueft": sum(p["pruefung"] != "nicht_geprueft" for p in positions)},
    }
