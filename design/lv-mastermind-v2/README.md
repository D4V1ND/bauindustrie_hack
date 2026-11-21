# LV Mastermind V2 (frontend redesign)

Standalone dashboard for the example tender *Neubau Kita Sonnenhof, München-Riem*.
One file, no build step: open `index.html` in a browser.

- Paper-and-ink design (Geist / Geist Mono, hairlines, square corners), light and dark mode.
- [01] Kostenschätzung range bar, [02] Bid / No Bid gauge, [03] summary, [04] LV table with filters, sorting, grouping, bulk actions and CSV copy.
- Position drawer with price options, custom price, sticky confirm bar (press and hold when the price is more than 10 % off), next steps, team comments, external requests and agent chat.
- Confirming a price animates the row, stamp and progress strip, then moves on to the next open position after a short countdown.
- GAEB X84 export overlay that warns about unconfirmed prices and downloads a demo `.X84` file.

All data is mock data and stays in the browser. V1 (Claude design canvas source) is in `../lv-mastermind/`.
