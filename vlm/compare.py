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


AREA_UNITS = {"m²", "m2", "qm"}

ROOM_PROMPT = """Ordne Flächenpositionen eines Leistungsverzeichnisses (LV) den Räumen eines Bauplans zu.
Eine Position kann mehrere Räume betreffen. Ordne nur zu, wenn der Text es nahelegt (Raumnamen wie Kinosaal oder
Foyer, Nutzung, Gewerk), sonst lass die Liste leer. Nenne die Raumnamen exakt wie in der Raumliste.

LV-Flächenpositionen (oz | Text | Menge):
{lv}

Räume (Name | Fläche):
{rooms}

Antworte NUR mit JSON: {{"zuordnung": [{{"oz": "...", "raeume": ["Raumname"], "sicherheit": "hoch|mittel|niedrig"}}]}}"""


def _room_marker(r: dict, plan_id: str) -> dict:
    return {"plan_id": plan_id, "seite": r.get("seite", 1), "x": round(float(r["x"]), 4), "y": round(float(r["y"]), 4),
            "breite": round(float(r["breite"]), 4), "hoehe": round(float(r["hoehe"]), 4), "form": "rahmen",
            "genauigkeit": "grob", "beschriftung": r["name"], "flaeche_m2": r.get("flaeche_m2")}


def _assign_rooms(positions: list[dict], rooms: list[dict], plan_id: str, client, model) -> None:
    """Area positions (m²) are not countable, so they get the frame(s) of the room(s) they belong to."""
    from llm_common import chat_json

    ok = [r for r in rooms if r.get("name") and all(isinstance(r.get(k), (int, float)) for k in ("x", "y", "breite", "hoehe"))]
    area = [p for p in positions if p["plan"] is None and p["menge"] is not None
            and str(p.get("einheit", "")).strip().lower() in AREA_UNITS]
    if not (client and ok and area):
        return
    names = {r["name"] for r in ok}
    prompt = ROOM_PROMPT.format(
        lv="\n".join(f"{p['oz']} | {p['kurztext']} | {p['menge']:g} {p['einheit']}" for p in area),
        rooms="\n".join(f"{r['name']} | {r.get('flaeche_m2') or '?'} m²" for r in ok))
    votes: dict[tuple, int] = {}
    for _ in range(3):  # majority vote, the answer varies between calls
        for z in chat_json(client, model, [{"type": "text", "text": prompt}]).get("zuordnung", []):
            sel = tuple(sorted(n for n in z.get("raeume", []) if n in names))
            if sel and z.get("sicherheit") != "niedrig":
                votes[(z.get("oz"), sel)] = votes.get((z.get("oz"), sel), 0) + 1
    best: dict[str, tuple[tuple, int]] = {}
    for (oz, sel), v in votes.items():
        if v >= 2 and v > best.get(oz, ((), 0))[1]:
            best[oz] = (sel, v)
    by_name = {r["name"]: r for r in ok}
    for pos in area:
        if pos["oz"] in best:
            sel = [by_name[n] for n in best[pos["oz"]][0]]
            pos["plan"] = {"menge": sum(r.get("flaeche_m2") or 0 for r in sel) or None, "einheit": "m²",
                           "zuordnung": "Raum", "raeume": [r["name"] for r in sel],
                           "marker": [_room_marker(r, plan_id) for r in sel]}


def _is_st(p: dict) -> bool:
    return str(p.get("einheit", "")).strip().lower() in COUNT_UNITS and p["menge"] is not None


def _match_run(positions: list[dict], plan: list[dict], client, model):
    """Match ONE plan run to the LV: deterministic first (codes, dimensions), then the LLM for the rest."""
    by_oz = {p["oz"]: p for p in positions}
    matches: dict[str, tuple[list[int], str]] = {}
    other_unit: dict[str, list[int]] = {}
    used: set[int] = set()
    for pos in positions:
        if not _is_st(pos):
            continue
        hits, how = match({"kurztext": pos.get("kurztext", ""), "codes": codes(pos.get("kurztext", ""))}, plan)
        if hits:
            matches[pos["oz"]] = (hits, how)
            used.update(hits)
    open_plan = [i for i, p in enumerate(plan) if i not in used and p["anzahl"]]
    open_lv = [p for p in positions if p["oz"] not in matches and p["menge"] is not None]
    if client and open_plan and open_lv:
        for z in llm_match(client, model, open_lv, plan, open_plan):
            pos = by_oz.get(z.get("oz"))
            hits = [i for i in z.get("plan_indizes", []) if i in open_plan and i not in used]
            if not pos or not hits or z.get("sicherheit") == "niedrig":
                continue
            used.update(hits)
            if _is_st(pos):
                matches[pos["oz"]] = (hits, "KI-Zuordnung")
            else:
                other_unit[pos["oz"]] = hits
    return matches, other_unit, used


def build_result(lv: dict, plan_runs: list[list[dict]] | None, plan_meta: dict | None,
                 client=None, model=None, rooms: list[dict] | None = None) -> dict:
    positions = [dict(p) for p in lv["positionen"]]
    for p in positions:
        p["menge"] = float(p["menge"]) if p.get("menge") not in (None, "") else None
        p.update({"pruefung": "nicht_geprueft", "befund_ids": [], "plan": None})
    findings: list[dict] = []

    def add(**kw):
        f = {"id": f"b-{len(findings) + 1:03d}", "entscheidung": {"status": "offen", "kommentar": None,
                                                                   "von": None, "am": None}, **kw}
        findings.append(f)
        return f["id"]

    if plan_runs is not None:
        pid = plan_meta["id"]
        nr = len(plan_runs)
        need = (nr + 1) // 2  # a result counts if at least half of the runs agree on it
        # Each run is matched to the LV on its own (the model names the same part differently in each run),
        # then the outcome per LV position is combined over the runs.
        per_run = []
        for r in plan_runs:
            plan = plan_items([r])
            per_run.append((plan, *_match_run(positions, plan, client, model)))
        tol = lambda med: max(1.0, 0.05 * med)

        for pos in positions:
            if not _is_st(pos):
                continue
            oz, txt = pos["oz"], pos["kurztext"]
            row_codes = codes(txt)
            found = [(plan, m[oz]) for plan, m, o, u in per_run if oz in m]
            if len(found) < need:
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
            counts = [sum(plan[i]["anzahl"] for i in hits) for plan, (hits, how) in found]
            n = statistics.median(counts)
            stable = len(found) == nr and (max(counts) - min(counts)) <= tol(n)
            plan0, (hits0, how) = found[0]
            sel = [plan0[i] for i in hits0]
            pos["plan"] = {"menge": n, "einheit": "St", "zuordnung": how, "marker": _markers(sel, pid)}
            if n == pos["menge"] and stable:
                pos["pruefung"] = "ok"
                continue
            if n == pos["menge"]:
                pos["pruefung"] = "ok_instabil"
                bid = add(
                    oz=oz, art="instabile_zaehlung", schwere="hinweis",
                    titel="Planzahl stimmt, schwankt aber zwischen den Läufen",
                    beschreibung=(f"{oz} „{txt}“: LV und Plan nennen {_fmt(n)} {pos['einheit']}. Die Zählung am Plan "
                                  f"war aber nicht in allen Läufen gleich ({', '.join(_fmt(v) for v in counts)}; "
                                  f"in {len(found)} von {nr} Läufen zugeordnet). Zahl bitte kurz am Plan prüfen."),
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

        for oz in sorted({oz for _, _, o, _ in per_run for oz in o}):
            found = [(plan, o[oz]) for plan, m, o, u in per_run if oz in o]
            if len(found) < need:
                continue
            pos, plan0, hits0 = next(p for p in positions if p["oz"] == oz), found[0][0], found[0][1]
            sel = [plan0[i] for i in hits0]
            n = statistics.median(sum(plan[i]["anzahl"] for i in hits) for plan, hits in found)
            pos["plan"] = {"menge": n, "einheit": "St", "zuordnung": "KI-Zuordnung", "marker": _markers(sel, pid)}
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

        _assign_rooms(positions, rooms or [], pid, client, model)

        # Plan parts without an LV position: keep those that stay unmatched in at least half of the runs
        norm = lambda s: re.sub(r"\W+", "", s.lower())
        presence: dict[str, list[tuple[int, dict]]] = {}
        for ri, (plan, m, o, used) in enumerate(per_run):
            for i, p in enumerate(plan):
                if i not in used and p["anzahl"]:
                    presence.setdefault(norm(p["typ"] or p["name"]), []).append((ri, p))
        for key, entries in presence.items():
            if len({ri for ri, _ in entries}) < need:
                continue
            p = dict(entries[0][1])
            p["anzahl"] = statistics.median(q["anzahl"] for _, q in entries)
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
