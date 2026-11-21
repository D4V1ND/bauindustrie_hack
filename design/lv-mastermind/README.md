# LV Mastermind – V1 + Positionstabelle (design)

Source export of the Claude design canvas "LV Mastermind – V1 + Positionstabelle"
(Projekt-Dashboard for the example tender *Neubau Kita Sonnenhof, München-Riem*).

## Files
- `Main.dc.html` – the full interactive dashboard (1440 px page, Design Component format).
  It loads the Claude design runtime via `./support.js`, so it renders inside the
  Claude design canvas, not as a plain standalone HTML file. Markup, styles and
  logic (`class Component extends DCLogic`) are all readable in this one file.
- `canvas.json` – canvas index (artboard size, title, notes).

## What's in the dashboard
- **[01] Kostenschätzung** – range 0,82–1,58 Mio. € from 3 Alt-LVs / 9.120 priced positions,
  predicted award 1,21 Mio. €, our AI calculation 1,14 Mio. €.
- **[02] Bid / No Bid** – score 72, "Bieten empfohlen".
- **[03] Zusammenfassung** – AI summary with sources, top cost drivers.
- **[04] Leistungsverzeichnis** (new in V1) – search, sortable columns, filters with counts
  (Alle, Hohes Risiko, Zu prüfen, Kostentreiber 80 %, Bündelbar), filter by trade/assignee,
  compact / long-text view, grouping by trade with subtotals, multi-select
  (assign, confirm prices, bundle), per-row ✓ to confirm the AI price, CSV copy (semicolon, Excel).
- **Position drawer** – price history from own Alt-LVs, custom price with ±10 % warning,
  comments with @mentions, supplier requests via Outlook, notes, ask-the-agent box.
- Export GAEB X84 button.

All state lives in the browser only (mock data, no backend yet).
