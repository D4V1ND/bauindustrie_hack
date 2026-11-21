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

Ausgabe `out/<lv>_ergebnis.json`: `eingaben`, `positionen` (mit `pruefung`, `befund_ids`), `befunde`
(`beschreibung`, `marker` mit relativen x/y, `entscheidung`), `zusammenfassung`.
