# Step 4 (risk and error analysis) and step 5 (summary): brainstorm notes

Status: brainstorm in progress, nothing confirmed yet. Terms follow [CONTEXT.md](../CONTEXT.md).
No build plan here; this file records decisions only.

## Starting point

- The user's first idea: the output is a list of line items we are not sure about. Four cases:
  1. texts match 1:1, prices differ by 10% or more: flagged, a person decides risk or error;
  2. texts and price match 1:1: fine, no flag;
  3. nothing found: flagged;
  4. similar texts ("Lampe" vs "Lampe mit Kabeln"): flagged, one product may be split into several items.
- What testing showed: step 3 finds few prices (P5: 4 of 50 priced, 27 reference only, 19 nothing; strict
  accuracy check: about 1% of 720 hidden items priced). Flags that need a found price fire rarely. Flags that
  read only the new LV work on every LV.

## Facts checked in the dataset (2026-11-21)

Counted on the parsed files of the dataset-explore session (library = P1, P2, P3 priced; P4, P5 new).

| What | P1 | P2 | P3 | P4 (new) | P5 (new) |
|---|---|---|---|---|---|
| Line items | 197 | 249 | 274 | 296 | 50 |
| Optional items | 0 | 0 | 0 | 24 | 0 |
| Lump-sum items (unit `psch`) | 14 | 6 | 11 | 38 | 6 |
| Lump sums, share of project total | 6.9% | 0.8% | 9.0% | | |
| Surcharge items ("Zulage") | 46 | 0 | 8 | 89 | 0 |
| "wie vor / wie Position ..., jedoch" items | 29 | 4 | 110 | 33 | 8 |
| "oder gleichwertig" (or equivalent product) | 30 | 1 | 4 | 23 | 4 |
| Groups with more than one item / items in them | 0 / 0 | 22 / 46 | 11 / 22 | 7 / 16 | 0 / 0 |
| Same group, different unit price | 0 | 2 | 5 | | |
| Unit price below 1.00 | 0 | 22 | 44 | | |
| Quantity x unit price differs from total price | 0 | 0 | 0 | | |

Concrete examples found:

- **Same item, two prices in one LV.** P2 `07.01.0008` and `09.01.0004` "Anschluss an Rohrltg ...": 2,100.00 and
  1,400.00 per piece (33% apart). P3 `2.1.320` and `4.1.120` "wie vor, jedoch Zuschlag Kanalkreuzung bis D = 40":
  50.00 and 5.00.
- **Same kind of machine, two price levels in one LV.** P3 `2.5.10` "Bagger (25 to) mit Humusschaufel" 1.13 per hour
  (50 h) and P3 `7.1.100` "Hydraulikbagger 1,0 m3" 113.50 per hour (2 h). Likely priced on purpose (the cost sits in
  another item), so the tool cannot tell error from tactic.
- **Tiny prices.** P3 `1.1.100` "Ortsfesten Schutzzaun (h = 2,00 m)" 0.10 per m; P2 `01.02.0003` "Bauzaun H 2m
  vorhalten" 0.03 per m and week. Some are real (price per week), some tactical.
- **Text depends on the item before.** P5 `03.0002` long text is only "wie vor, jedoch 02) DN 900 mm". P4
  `01.01.0004` short text is "Leistung wie Position zuletzt in vollem Wortlaut beschrieben,". P3's "identical" groups
  are partly false: two "wie vor" items with the same words but different parents.
- **Identical items with very different quantities.** P4 `03.02.0009` and `04.04.0010` "Zulage zur Position GGG Rohr
  DN 300": 160 and 1 pieces.
- **Optional items are the hourly rates.** All P4 optional items sampled are daywork rates with quantity 1 h
  (`05.01.0001` "Polier-in ...").
- **Almost no text.** P5 `04.0003` "Facharbeiter", `04.0006` "Bagger bis 1 m3": long text equals short text.
- **Product named by the client.** P4 `02.03.0034` "Geotextil Robustheitsklasse 3 ... Fabrikat: Secutex 201-GRK 3 C
  oder gleichwertiger Art".
- **Time-based units.** P2 uses `StWo`, `mWo`, `m2d` (per piece and week, per metre and week, per m2 and day); the
  new LVs P4 and P5 do not. A past price per week must never be read as a price per piece.
- **No dates.** The parsed library has no project dates, so "outdated price" cannot be tested yet.

## Decisions

(none yet)

## Open questions

1. Risk vs error: definition, and who decides.
2. What step 4 checks: the new LV only, or also the price library and a priced LV.
3. The flag list (rule, example, price needed or not, code or AI).
4. Mapping of the four cases onto the step 3 outcomes; where the 10% threshold applies.
5. "Never priced before" when it covers most of an LV.
6. Output order, size and the flag record.
7. Step 5 summary.
8. Hackathon scope: must-build vs stretch.
