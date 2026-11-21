# VLM-Prototyp: GAEB-LV (+ Bauplan) -> Befunde als JSON

Kein Parser: Das Modell liest die GAEB-Datei direkt (`gaeb_llm.py`) und den Plan als Bild (`extract_plan.py`).
Der Vergleich (`compare.py`) schreibt das JSON fuers Dashboard.

Einrichtung: `vlm/.env` aus `.env.example` anlegen (`OPENAI_API_KEY`, `OPENAI_MODEL` = API-ID, z. B. `gpt-6.2-sol`).
Pläne und LVs in `vlm/plans/` (git-ignoriert), Ergebnisse in `vlm/out/` (git-ignoriert).

```powershell
cd vlm
.venv\Scripts\python.exe run.py --lv plans\hoersaal_H1.X83 --plan plans\hoersaal_mock.png --runs 3
.venv\Scripts\python.exe run.py --lv plans\hoersaal_H1.X83        # nur LV (Positionen)
```
Zwischenergebnisse werden in `out/` zwischengespeichert (`--neu` liest neu).

Aus der Pipeline (vom Projektordner aus): `python -m lv price <LV> --project P4 --plan <Plan>`. Das LV liest dann der
GAEB-Reader aus Schritt 0 (nicht `gaeb_llm.py`), das Ergebnis steht in `data/out/<Projekt>_plan.json`.
`OPENAI_API_KEY` und `OPENAI_MODEL` dürfen dafür auch in der `.env` im Projektordner stehen.

Ausgabe `out/<lv>_ergebnis.json`: `eingaben`, `positionen` (mit `pruefung`, `befund_ids`), `befunde`
(`beschreibung`, `marker` mit relativen x/y, `entscheidung`), `zusammenfassung`.

## Koordinaten fuer das Frontend
Jede Position in `positionen` hat das Feld `plan`: `null` (nicht im Plan verortbar) oder
`{"menge", "einheit", "zuordnung", "marker": [...]}`. Ein Marker ist `{"plan_id", "seite", "x", "y", "form", "genauigkeit", "beschriftung"}`;
`x`/`y` sind relativ (0..1, Ursprung oben links, Mittelpunkt), bei `"form": "rahmen"` kommen `breite`/`hoehe` dazu.
Plan-Datei und Pixelgroesse stehen in `eingaben.plan`. Das Frontend zeichnet beim Klick auf eine Position deren Marker
ueber den Plan (Umrechnung: `x * breite_px`, `y * hoehe_px`).

Flaechenpositionen (m2) haben `plan.zuordnung = "Raum"`: Ihre Marker sind Rahmen (`form: "rahmen"`) um die zugeordneten
Raeume (`plan.raeume`, `plan.menge` = Raumflaeche laut Plan, nur Info, kein Vergleich). Die Raeume liest `extract_plan.py`
zusammen mit den Bauteilen aus (`raeume` in `out/<plan>_laeufe<N>.json`).

## Uebersicht pro Plan + LV
`run.py` schreibt bei jedem Lauf mit Plan automatisch `out/<lv>__<plan>_uebersicht.png` (Plan mit allen verorteten Positionen
durchnummeriert, Raeume als Rahmen, Legende rechts; "P1, P2" = im Plan, nicht im LV) und `.xlsx` (Blatt "Positionen" mit allen
Positionen inkl. Nr., Status, Verortung, Befunde; Blatt "Plan" mit dem Bild). Manuell: `python overview.py out/<lv>_ergebnis.json plans/<plan>.png`.
