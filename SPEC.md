# Spec: LV estimating backend, steps 0–4

Status: steps 0 to 4 are built and were run end to end with real AI calls on P5 and on P4 (P4: 9 min 29 s; numbers in `tasks/todo.md`).
The price library holds P1, P2 and P3. Not built: `lv check`, reading a PDF in a layout other than P6's. Terms in **bold** are defined in [CONTEXT.md](CONTEXT.md).

## Objective

A construction contractor's estimator receives a **new LV**: thousands of **line items** with quantities and no
prices. Today they summarise and price it by hand. This backend does the first pass:

1. reads LV files into one common table (step 0),
2. links identical line items (step 1),
3. sorts the new LV's items into **trades** and **item types** for an overview (step 2),
4. looks up a price for each item in the company's own **past projects**, with the source of every number, and
   proposes one only when the match is strict (step 3).

The estimator stays in charge: the tool proposes, the person decides. Every price must be traceable to a real line
item in the **price library**. No internet prices, no prices invented by an AI model.

Steps 4 (risk and error analysis) and 5 (summary) are not part of this spec. Steps 0–3 must deliver the facts they
will need (see "What step 3 hands to step 4").

### Two flows

```
A. Setup, once per past project
   priced LV file ─▶ step 0 extract ─▶ step 1 group label ─▶ data/library/<project>.json

B. Pricing, once per new LV
   new LV file ─▶ step 0 ─▶ step 1 ─┬─▶ step 2 item types ──┐
                                    └─▶ step 3 prediction ──┴─▶ data/out/<project>.json
                                              ▲
                              reads every file in data/library/
```

Steps 2 and 3 do not depend on each other. Step 2 runs on the new LV only; past projects get no item types.

### Demo roles

| Role | Projects |
|---|---|
| Price library | P1, P2, P3 (720 priced line items) |
| New LV | P4 |
| Accuracy check | hide one past project's prices, predict them from the other two, compare |

## Step 0: extraction

Reads one file and returns the line item table. It copies the file faithfully: one row per item, in file order,
nothing compared, nothing merged.

**A new LV in several files** (`lv price a.pdf b.pdf …`, or the `lv` upload field sent once per file), e.g. P6's
27 PDFs, one per trade: each file is read as above, in name order, into one LV and one result file. Every item keeps
the name of its file (`source_file`). An item is already read when an earlier file has the same item number, unit
and quantity (texts are not compared: they differ between formats):

- a file whose items are all already read is skipped, with a line in the log: the same LV in another format (P1's
  `.X83` and `.D83`), or an excerpt of another file (P6's 6 stairs items, also in the shell works file);
- a file with half or more already read is refused, naming both files: it is most likely the same LV with changes;
- otherwise its items are added. When an item number is in more than one file (P6: 91 numbers such as `1.1.10`),
  every item number gets its file's place in front, `F01-` to `F26-`, so each stays unique.

| Format | How | `read_by` | Order |
|---|---|---|---|
| GAEB XML (`.X83`) | code | `gaeb` | first |
| GAEB 90 (`.D83`, `.D84`, `.D86`) | code | `gaeb` | first |
| PDF prints of P3 (texts only, next to a prices file) | code, with `pypdf` | `pdf` | built |
| An LV that exists only as a PDF, in the print layout of P6 (`.pdf`) | code, with `pymupdf` | `pdf` | built |
| Excel (`.xlsx`) | code, with `openpyxl` | `excel` | built |
| A PDF in another layout | not built; planned via an AI model | `ai` | later |
| Word, old Excel (`.xls`) | not supported | | |

Each past project enters the price library exactly once (P1 and P2: the `.D86` file).

**Excel and PDF LVs.** Both readers turn numbered rows into items. A row is an item when it has a quantity, a unit
or a price; otherwise it is a section title. A section stays open for the items below it until a section of the same
or a higher level starts (the level is the number of parts in its number: `15.1` is 2).

- **Excel:** the first sheet. The header row is found by its column names: OZ / Pos. / Position, Kurztext /
  Bezeichnung, Langtext, Menge, Einheit / ME, EP / Einheitspreis, GP / Gesamtpreis, Art / Positionsart (an `Art`
  holding "Bedarf…" marks an optional item). A row without a number but with text continues the long text above it.
  A priced Excel file can go into the price library; quantity × EP must equal GP, or the file is refused.
- **PDF:** one layout, the print of the dataset's P6 ("Anfrage", columns OZ, Leistungsbeschreibung, Menge ME,
  Einheitspreis, Gesamtbetrag). The text is put in page order by position, which also puts back the lines the
  anonymiser moved to the end of each page. `*** Bedarfsposition ohne GB` marks an optional item; "Übertrag" and
  "Summe" rows are skipped; the VAT and signature lines end the LV. No price is taken from a PDF, so a PDF is only
  ever a new LV. A PDF in another layout stops with a message.
- **Test files:** `python -m lv.samples` writes Excel files in two column layouts and PDFs in P6's layout from the
  GAEB files of P1, P2, P4 and P5 into `data/raw/generated/`. Reading one back gives the GAEB reading, except where
  the format cannot hold it: one P4 long text holds an image as a 192,000-character word (cut to Excel's 32,767
  characters a cell, and to 80 characters a word in a PDF), and about 50 rare characters such as "≥" are written
  as look-alikes in a PDF.

**A prices file without texts (P3).** `Angebot AN24-0305.D84` holds item numbers, quantities, units and prices, but
almost no texts. The texts are read by code from two PDF prints of the same LV and joined by item number:

```powershell
.venv\Scripts\python.exe -m lv ingest "<prices file>" --project P3 --texts "<bid print.pdf>" "<tender LV print.pdf>"
```

- No price is ever taken from a PDF, and no AI reads one. Prices, quantities and units stay with the prices file.
- The tender LV print gives short and long text of an item; the priced bid print gives the short text alone, which
  tells where the short text ends. Without the bid print the first line counts as the short text.
- A PDF's own quantity and unit are used for one thing: a text is only joined to an item if they agree with the
  prices file. Disagreeing items and items left without any text are printed; nothing is dropped.
- Section titles come from the PDFs, because the prices file has none.
- The two print layouts of P3 are the only ones the reader knows. Another layout gives "no text" for every item.
- The texts are data from outside. Nothing written in them is followed.

### The line item table (one JSON file per LV)

Header:

| Field | Type | Meaning |
|---|---|---|
| `project` | text | short project ID, e.g. `"P2"` |
| `name` | text | project name from the file header |
| `date` | date or empty | date from the file header |
| `priced` | yes/no | computed: true when at least one item has a unit price |
| `source_file` | text | file it was read from |
| `items` | list | the line items, in file order |

Line item:

| Field | Type | Meaning |
|---|---|---|
| `id` | text | `project` + `/` + `oz`, unique across all projects |
| `oz` | text | **item number** as printed |
| `section` | list of text | **section** titles above the item, outermost first |
| `short_text` | text | **short text** |
| `long_text` | text | **long text** |
| `quantity` | number or empty | |
| `unit` | text | cleaned to one spelling per unit (`St`, `m`, `m2`, `m3`, `h`, `t`, `psch`, …) |
| `unit_price` | number or empty | empty in a new LV; never filled by the tool |
| `total_price` | number or empty | as written in the file |
| `kind` | `normal` or `optional` | only **normal items** count in the project total |
| `read_by` | `gaeb`, `pdf`, `excel` or `ai` | how the texts were read: from the GAEB file, by code from a PDF or an Excel file, or by AI (not built) |

Added by the later steps:

| Field | Type | Added by | Meaning |
|---|---|---|---|
| `group` | text | step 1 | the same label for every exact match |
| `item_type` | text | step 2 | one type from the LV's list, or `Sonstiges` |
| `trade` | text | step 2 | the trade of that item type |
| `item_type_reason` | text | step 2 | one sentence on why the item got this type |
| `prediction` | object or empty | step 3 | what the price lookup found (see step 3) |

Left out on purpose: price components (labour/material/equipment), notes without a price.

## Step 1: exact match

Code only. Every line item gets a `group` label. Two items get the same label exactly when they are an
**exact match**: same short text, long text and unit, after removing differences without meaning (letter case,
extra spaces and line breaks, line-break hyphens such as "We- ge").

The label depends only on the item itself, so it links items inside one LV (for adding up quantities) and across
the price library (for copying a price).

## Step 2: trades and item types (new LV only)

Three levels above the line item: **trade** → **item type** → **group**.

1. **Propose the list.** One AI call (`claude-opus-5-5`) reads the section titles, short texts and the first 200
   characters of each long text of the LV. It returns the list of item types, each with a one-line description and
   exactly one trade from the fixed trade list (17 stand-in names, `TRADES` in `lv/categorise.py`). Cap: about one
   type per four items, never below 20 and never above 100. Code removes double names, a type named after
   surcharges, a catch-all type and everything over the cap.
2. **Assign.** AI (`claude-sonnet-5-5`, 25 items per call) gives every item exactly one item type from that list,
   or `Sonstiges` if none fits, and one sentence on why. Code checks every type against the list. An answer outside
   the list, or a missing one, is asked again once; what is left then becomes `Sonstiges`.

Rules:
- Surcharge items ("Zulage …") get the type of the thing they are a surcharge for. A repeat item is sent together
  with the text of the item it refers to.
- **One decision per set of identical items.** Items with the same `group` label are asked about once; the others
  get a copy of the type, trade and reason. So identical items always land in the same type, and repeated items
  cost no extra call. Two repeat items ("wie vor …") with the same words count as identical only if they refer to
  the same item.
- **The reason** (`item_type_reason`) is one sentence in English, 20 words at most, naming what in the item's text
  decided the type; for a surcharge or a repeat item it names the item it belongs to. It is text to show ("why is
  this item in this category?") and never changes the outcome: only the type is checked against the list. The
  field is never empty after step 2: if the model gives a valid type without a reason, or no valid answer comes
  back after the second try, code writes a sentence that says exactly that.
- Types are for the overview, not for prices. Quantities add up per unit and are never added across units. The AI
  sees no price in this step either.

Each item gains `item_type`, `trade` and `item_type_reason` (`Sonstiges` has the trade `Sonstiges`). The result
file gains `item_types`: the LV's list with `name`, `trade` and `description`.

`lv price` prints the overview: trade → item type → number of items and quantities per unit. It counts normal
items only and names the number of optional items below. With `--copy-only` step 2 is skipped.

## Step 3: price lookup with evidence

A lookup, not an estimate. An item gets a price only when the price library holds the same item, or the same item
in a smaller and a larger size. Otherwise it gets the nearest related past items as **references**, whose middle price is the suggested
price, or nothing (with only a span from the nearest items, which differ). Low coverage is accepted; a wrong price is not.

Why this strict: in tests on this dataset, exact matches across projects were close to 0%. A loose "similar" rule
priced 17–30% of items and was typically 80–120% off. The strict rule below priced about 1% of 720 hidden items,
with no price off by more than 25%.

`lv price <new LV> --project <ID>` reads the new LV, gives each item its `group` label, loads every file in
`data/library/` and adds one `prediction` per item. Only library items with a unit price above zero are used.

For each item of the new LV, in this order:

| Order | Check | Who | `method` |
|---|---|---|---|
| 1 | A library item has the same `group` label | code | `copied` |
| 2 | Search for **candidates**, then the **judge** gives each candidate a **verdict** | code, then AI | |
| 2a | At least one candidate is `same` | code | `similar` |
| 2b | One `size_only` candidate is smaller and one is larger, in the same dimension | code | `adjusted` |
| 2c | Only `type_only` candidates, or `size_only` candidates on one side | code | `reference` |
| 3 | No candidate, or all candidates `different` | code | `none` |

### Search (code; meaning by Voyage AI)

- Only library items with the same unit and the same surcharge status: a **surcharge item** ("Zulage", "Zuschlag",
  "Mehrpreis", "Aufpreis", "Mehrkosten") only meets other surcharge items.
- A **repeat item** ("wie vor, jedoch …", "wie Pos. …") is compared together with the text of the item it refers
  to: the last item before it in the same LV that is fully described. It also takes over that item's surcharge status.
- **By words:** similarity of the words and numbers in short text + long text (rare words weigh more). Only items with
  a score of at least 0.10 are ranked.
- **By meaning** (only when `VOYAGE_API_KEY` is set): cosine of the embeddings from Voyage AI `voyage-4`. What is
  embedded per item: the text of the item a repeat item refers to, the short text and the first 500 characters of the
  long text, without anonymiser tokens. Library and new items are embedded the same way; every vector is saved in
  `data/embeddings/voyage-4.json`, so a text is sent once. It finds other words for the same thing ("Handrad" for
  "Handbetätigung", "Baufacharbeiter" for "Facharbeiter"), but blurs numbers ("DN 80" looks like "DN 800").
- **Shortlist:** with meaning, the best 20 of each ranking are merged by place (reciprocal rank fusion: an item earns
  1 / (60 + its place) in each ranking) and the 5 with the most points are the candidates. Without meaning, the 5 best
  by words. Scores and places only choose candidates; the judge decides, and the order of the 5 does not change a price.

### Judge (AI, `claude-sonnet-5-5`)

The judge reads the new item's text and the candidates' texts. It never sees a price, a quantity-times-price or a
total, and it never states one. Its rules are the estimator's own (`JUDGE_RULES` in `lv/ai.py`): compare kind,
material, scope, size, class and, for hourly rates, the kind of worker or machine.

How the questions are packed (code):

- **One per set of identical items.** Items of the new LV with the same `group` label (and, for repeat items, the
  same item they refer to) are searched and judged once. The others get a copy of the prediction, with
  `judged_item` naming the item that was actually judged.
- **Packs by item type.** Items that found candidates go to the judge together with the other items of their
  `item_type` from step 2, in file order, at most 15 per call. A pack starts with one hint line (`HINT: the N new
  items below were sorted under "<item type>" (trade: <trade>) by an earlier step. Decide by the texts only.`),
  then every item in exactly the form of a single question. The search still covers the whole library; the item
  type only decides which questions travel together.
- **Check.** An item's part of a pack answer counts only if it is there once and names each of the item's own
  candidates once and no other. Otherwise the item is asked again on its own. What still fails counts as
  unanswered.
- If step 2 did not run, every item is asked on its own.

For each item the judge answers in a fixed shape:

| Field | Meaning |
|---|---|
| `judged[]` | one entry per candidate: `cand` (library item id), `verdict`, `difference` (a few words), `why` (one sentence: why this is the same thing, or why it is not) |
| `verdict` | `same`, `size_only`, `type_only` or `different`; in doubt the stricter one |
| `dimension`, `cand_size`, `target_size` | only for `size_only`: what differs (e.g. `DN`) and both sizes as numbers |
| `reason` | one sentence for the item |

In an answer about a single item, verdicts for ids that were not offered are ignored. If the judge gives no answer
for an item, the item gets `none`.

### From verdicts to the prediction (code)

- `similar`: the prices of all `same` candidates. A `size_only` candidate whose two sizes are equal counts as `same`.
- `adjusted`: the nearest smaller and the nearest larger `size_only` candidate, linear interpolation:
  `smaller price + (target size − smaller size) ÷ (larger size − smaller size) × (larger price − smaller price)`,
  rounded to the cent. Both candidates must name the same dimension and the same target size.
- `reference`: the best `size_only` and `type_only` candidates, at most 3, are listed with their unit prices. Their median
  is the suggested price, counted in the total (changed 21 Nov 2026; before, a reference had no price).
- `none`: no price. When the judge answered, `min` and `max` are the lowest and highest price of the 3 candidates kept
  in `judged`, which all differ: a span to show, never a price.
- The judge reads the whole shortlist of 5. The prediction keeps the best 3 in `judged`, best first: what the price
  rests on, then `same`, `size_only`, `type_only`, `different`. On P5 a shortlist of 3 would have lost one of five
  prices (the match sat at place 5), so the cut is made after the judge, not before.

Each item gains a `prediction`:

| Field | Meaning |
|---|---|
| `method` | `copied`, `similar`, `adjusted`, `reference` or `none` |
| `min`, `max` | range of the past prices behind the prediction; for `none` only the span of `judged`, empty without it |
| `median` | the value used for totals. For `adjusted` it is the calculated result |
| `mean` | average of the same prices. For `adjusted` it is the calculated result |
| `count` | number of past prices behind the prediction, or behind the span for `none` |
| `sources` | list of `{id, short_text, unit_price, verdict, difference, why}`: every past item used or shown. `verdict` is `exact` for `copied` |
| `judged` | the same shape, with the judge's `why`, for the best 3 candidates the judge gave a verdict on, best first |
| `rule`, `calculation`, `result` | only for `adjusted`, e.g. `linear interpolation by DN`, `80 + (125 - 100) / (150 - 100) x (120 - 80)`, `100.0` |
| `reason` | the judge's sentence, or a fixed sentence when code decided alone |
| `judged_item` | only on a copy: the id of the identical item of this LV that was searched and judged |
| `judge_call` | the `call` in `<project>_judge.json` that decided the item (the last call that asked about it); empty when the judge was not asked |

The result file `data/out/<project>.json` also holds a `summary`: per method the number of items and the sum of
`median × quantity`, and the project `total`. Sums count only **normal items** whose method is `copied`, `similar`
or `adjusted`. `lv price` prints the same numbers.

Every judge call is logged in `data/out/<project>_judge.json`: the items it asked about, the question exactly as
sent, the judge's summarised thinking and its answer. The judge runs at effort `xhigh`: with structured output at
`high` the model answered without thinking. At `xhigh` it may still skip thinking on a call (1 of 20 on P5). The full format for a frontend:
`docs/result-format.md`, with JSON Schemas generated from the code.

`lv price … --copy-only` stops after check 1: no item types, no search, no AI call, no API key needed. Without
that option a missing API key stops the command with a message that says what to do, before anything is written.
The judge itself is only called if at least one item has candidates.

### What step 3 hands to step 4

Step 3 records facts and contains no thresholds. Step 4 reads `method`, `min`, `max`, `median`, `count` and
`sources[].unit_price`; its thresholds live in `lv/risk.py`.

### Known limits

- **Exact matches across file formats.** The dataset's anonymiser hid different words in each file, and GAEB 90
  files cut the short text at 70 characters. So the same item often has a different `group` label in a `.D86` and
  in an `.X83` file (P2 against itself: 160 of 248 items get the same label). Such items go to the judge instead.
- **P3's bid contains token prices** such as 0.05 per m2 or 0.10 per t: 56 of its 274 unit prices are below 2, and
  quantity times unit price equals the total for every one, so they are what the bidder wrote. Step 3 has no
  threshold for them. Whether such prices may be copied is the estimator's decision and is not settled yet.
- **106 of P3's 274 items have no long text**, because the PDF has none for them: 89 repeat items ("wie vor,
  jedoch ..."), 16 hourly rates and one fitting, each one line long.
- **P6 is read from its 27 PDFs, not priced yet.** 1,131 items, 138 optional. As printed, 9 items have a quantity
  but no unit and one row (`13.4.210` in LV_21) is empty; they are kept. A PDF in another layout cannot be read.
- **`lv check`** (hide one past project, predict it from the others, compare) is not built yet.
- A repeat item in P2's `.X83` names its reference in a separate field, not in its text. That field is not read.

## Step 4: risk score

Every item gets one risk score from 0 (safe) to 100, and the LV gets one score weighted by value. A score is a
place on a scale, not a probability. `lv price` runs step 4 after step 3 and writes `risk` on each item and
`risk_summary` on the LV.

An item's score has two parts:

| Part | Who | How |
|---|---|---|
| **Price risk** | code | From the step 3 method: `copied` 5, `similar` 25, `adjusted` 45, `reference` 75, `none` 100. A `similar` price whose past prices lie more than 10% apart ((max − min) ÷ median) gets 40 |
| **Description risk** | AI (`claude-sonnet-5-5`, `xhigh`) | Does the text state what an estimator needs to price the item: kind, material, size, class, scope, and for hourly rates the worker or machine? `complete` 10, `gaps` 50, `vague` 90. The model also names up to 3 missing things and gives one sentence. Rules: `DESCRIPTION_RULES` in `lv/ai.py`. 25 items per call; identical items are checked once |

`score` = 0.6 × price risk + 0.4 × description risk, rounded. Without a description check (`--copy-only`, or no
answer for the item) the score is the price risk alone. All numbers above are starting values to tune with the
estimator; they sit at the top of `lv/risk.py`.

**Value**, the weight of an item in the LV's score. It is never a price to show.

| Order | When | `value` | `value_basis` |
|---|---|---|---|
| 1 | step 3 found a price | `median` × quantity | `price` |
| 2 | no price (`none`) | the middle value of all items of order 1: a typical item | `assumed` |

`risk_summary.score` is the sum of value × score over the **normal items**, divided by the sum of their values.
`risk_summary.basis` counts the normal items per value basis, so a reader sees how much of the weighting is assumed.
Optional items get a `risk` but stay out of the LV's score, as they stay out of the totals.

Each item's `risk`: `score`, `confidence`, `price_risk`, `price_reason`, `description` (`complete`, `gaps`, `vague`
or empty), `description_risk`, `missing[]`, `description_reason`, `value`, `value_basis`.

**Confidence**, the number to show an estimator: 100 − `score` for an item with a price, 0 for an item without one
(`none`). `risk_summary.confidence` weighs it by value like the score, so an LV where step 3 found
few prices has a low confidence (P5: 47 since references have a price, 5 before). It is a place on a scale, not a measured chance of being right.

**Comments.** `lv comment <project> <item number> [--price N] [--text "..."] [--doubt]` saves the estimator's
comment on one item in `data/out/<project>.json` and recalculates every score; the descriptions checked before are
kept, no AI is asked. A price makes the item priced (price risk 0, confidence 100, value from that price); a doubt
on a found price gives it price risk 75; a note changes nothing. The latest comment with a price or a doubt decides.

Known limits: a value from related items inherits their prices, token prices included; on an LV where step 3 finds
little, most values are assumed (P4: 178 of 296 items had no candidate the judge accepted); the flags of the first
brainstorm (same item at two prices, outdated prices, quantity against plan) are not built.

## Plan check (optional)

When a construction plan (Bauplan) is given, `lv price … --plan <plan>` also compares the LV's quantities with it
and writes `data/out/<project>_plan.json`, plus the prototype's fixed overview: `<project>__<plan>_uebersicht.png`
(the plan with every located item numbered, and a legend) and `.xlsx` (every item with status, location and findings). Without `--plan` nothing changes. The LV is required;
the plan is optional.

| Part | Who | How |
|---|---|---|
| The LV's items | code | Step 0's reader, the same items as the price lookup (`lv/plan.py`, `positions`). The prototype's own GAEB reading by an LLM (`vlm/gaeb_llm.py`) is not used |
| Read the plan | AI (OpenAI vision model, `OPENAI_MODEL`) | `vlm/extract_plan.py`: countable components with counts and approximate x/y markers, and the rooms with their areas, read 3 times; the median count is used and counts that vary are flagged |
| Compare | code, then AI | `vlm/compare.py`: matches by plan codes (F1, T3) and dimensions first, then lets the model match the rest by majority vote; writes findings per item (quantity differs, not in the plan, in the plan but not in the LV, units not comparable) with markers and an open decision field |

The plan check starts right after step 0, since it needs only step 0's items, and runs in a background thread beside
steps 1 to 4 (`lv/plan.py`, `Background`). Its result is collected after the LV result is saved, so a failing plan
check loses nothing of it; the command then ends with an error that says so. A plan that is not
an image or PDF, a missing file, or a missing `OPENAI_API_KEY` / `OPENAI_MODEL` (in the root `.env` or in `vlm/.env`)
stops the command before anything runs; `--copy-only` and `--plan` cannot be combined.

At the end the plan check is **merged into `<project>.json`**, in English keys, so that one file holds everything:
every item gets `plan` (status, the plan's quantity, how it was matched, rooms, markers, finding ids), and the LV
gets `plan` (the plan file and its size, how many items were compared, all findings including those that no LV item
covers, and the names of the raw file and the overview). `<project>_plan.json` stays as the plan check's raw record,
with the prototype's German keys (`vlm/README.md`). Finding titles and descriptions stay German: the prototype
writes them.

Every item that can be located gets `plan` with markers (x/y relative to the plan image), so a view can show it on
the plan; items in m2 get frames around their rooms. Run on the Hörsaal H1 LV with its plan: 41 of 52 items located,
27 of the 30 compared items match, 4 findings (one is the plan's 3 exit signs not matched to the LV's exit-sign item).

Known limits: only items in pieces
(`St`) are compared by count; the plan check uses a second AI provider (OpenAI) next to Claude and Voyage.

## Tech stack

- Python 3.13, pip, `.venv` (Windows, PowerShell).
- GAEB reading and text search: standard library only. PDF text reading: `pypdf` (P3's prints) and `pymupdf`
  (PDF LVs); Excel: `openpyxl`.
- Plan check: an OpenAI vision model through the `openai` package; `pymupdf` renders PDF plans to images.
- Search by meaning: Voyage AI `voyage-4` through the `voyageai` package (Anthropic has no embedding model). No
  vector database: about 1,000 saved vectors are compared in memory.
- AI: Claude API through `langchain-anthropic`, used thinly: chat model, structured output, `.batch()` with a
  concurrency limit. No agents, chains, vector stores or LangGraph.
  - One model and one effort level per job (effort is how much the model thinks before it answers):

    | Job | Model | Effort |
    |---|---|---|
    | Step 2: propose the item type list, one call per LV | `claude-opus-5-5` | `medium` |
    | Step 2: assign each item a type, with its reason | `claude-sonnet-5-5` | `high` |
    | Step 3: judge the candidates | `claude-sonnet-5-5` | `xhigh` (at `high` it answers without thinking) |
    | Step 4: is each item's description complete? | `claude-sonnet-5-5` | `xhigh` |

    No AI reads a PDF: PDFs are read by code.
  - Model names and effort levels live in one settings place (`lv/ai.py`). No `temperature` parameter; structured output through the native
    JSON-schema method, not forced tool calls. Check both against the current `langchain-anthropic` docs when building.
- `pydantic` for the data shapes, `python-dotenv` for the API key, `pytest` for tests.

## Commands

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m lv ingest "<priced LV file>" --project P2     # flow A
.venv\Scripts\python.exe -m lv ingest "<prices file>" --project P3 --texts "<print 1.pdf>" "<print 2.pdf>"   # prices and texts in separate files
.venv\Scripts\python.exe -m lv price "<new LV file>" --project P4         # flow B
.venv\Scripts\python.exe -m lv price "<new LV file>" --project P4 --copy-only   # steps 0 and 1 and exact matches only, no AI
.venv\Scripts\python.exe -m lv comment P4 01.01.0002 --price 7500 --text "..."    # the estimator's comment; recalculates confidence
.venv\Scripts\python.exe -m lv price "<new LV file>" --project P4 --plan "<plan.pdf>"   # also compare with a construction plan
.venv\Scripts\python.exe -m lv.ai                                         # one real AI call, to check the key
.venv\Scripts\python.exe -m lv.samples                                    # write Excel and PDF test LVs to data/raw/generated/
.venv\Scripts\python.exe -m lv check --hide P2                            # accuracy check (not built yet)
.venv\Scripts\python.exe -m pytest
```

## Project structure

```
lv/
  __main__.py     command line: ingest, price, check
  model.py        line item, prediction and file shapes
  extract.py      step 0: one reader per format
  samples.py      Excel and PDF test LVs written from GAEB files
  match.py        step 1 group label; step 3 candidate search
  categorise.py   step 2: trade list, item type list, assignment, overview
  predict.py      step 3: judge input, verdicts to prediction, interpolation, statistics, totals
  risk.py         step 4: price risk, description check, value, the LV's risk score
  plan.py         plan check: step 0's items to the VLM prototype, which reads and compares the plan
  ai.py           model setup, API key and prompts
tests/            one small test file per module
data/raw/         the dataset, unchanged
data/library/     price library: one JSON per past project
data/out/         results: one JSON per priced LV
SPEC.md  CONTEXT.md
```

## Code style

Plain functions with type hints, data as pydantic models, no class or abstraction without a second user. Known
shortcuts get a `# ponytail:` comment naming the limit and the upgrade.

```python
def group_label(item: LineItem) -> str:
    """Same short text, long text and unit give the same label."""
    key = "\x1f".join(clean(t) for t in (item.short_text, item.long_text, item.unit))
    return hashlib.sha1(key.encode()).hexdigest()[:12]
```

## Testing strategy

`pytest`, tests in `tests/`, run against the real files in `data/raw/`. One small test per piece of real logic; no
mocks of the dataset. The tests need no network and no API key: in step 3 the judge is replaced by a fake that
returns fixed verdicts, and `tests/conftest.py` hides the key from every test, so no test can reach Claude. Real AI calls are checked by hand (`python -m lv.ai`, then `lv price` on P5) and by reading
the output.

## Boundaries

- **Always:** one row per item in extraction; every prediction points to real items in the price library; code does
  all arithmetic; the API key stays in `.env`, out of git; run the tests before a commit.
- **Ask first:** adding a dependency; changing the line item table; committing or pushing; sending data that is not
  anonymised to the AI service.
- **Never:** use internet prices or let the AI state a price; write a prediction into `unit_price`; merge rows
  during extraction; put a past project into the library twice.

## Success criteria

Step 0
- Item counts match the files: P1 159, P2 248, P4 296, P5 50.
- For each priced LV, the sum of `total_price` over normal items equals the total stated in the file.
- P4 has 24 optional items; P1, P2 and P5 have none.
- P3: every priced item in the `.D84` has a text from the PDF, joined by item number; unmatched items are listed.
- Excel (two column layouts) and PDF written from P1, P2, P4 and P5 read back to the same items as the GAEB file;
  P2's priced Excel file goes into the price library with the same prices.
- P6: all 27 PDFs read, 1,131 items, every one with a quantity.

Step 1
- P2: at least 27 groups with more than one item, covering at least 55 items. P4: at least 7 groups, 15 items.
- Two items that differ in one character of meaning (e.g. DN 150 vs DN 200) never share a label.

Step 2
- Every item of the new LV has exactly one item type from the LV's list or `Sonstiges`, and every type exactly one
  trade from the fixed list.
- P1 comes out at 40 item types or fewer.
- The overview never adds quantities across units.

Step 3
- Every prediction with a method other than `none` has at least one source, and every source `id` exists in the library.
- For every `adjusted` prediction, recomputing `calculation` gives `result`.
- No price is written into `unit_price`, and no price appears in what the judge is sent.
- With only P2 in the library, `lv price --copy-only` on P2's own `.X83` copies a price for every item whose label
  matches, and every copied price equals the unit price in the `.D86`.
- `lv price` for P4 writes `data/out/P4.json` with the number of items per method and the project total.

## Out of scope

Steps 4 and 5; plan check; Word; price adjustment by material or by date; unit conversion (week vs
day); item types for past projects; search by meaning; a database or web server.

## Open questions

1. **API key.** None is set up on this computer.
2. **Hand-over to the frontend.** Assumed: the frontend reads `data/out/<project>.json`. No web server.
3. **Trade list.** The test used 17 stand-in trade names. Is there an official list to use?
4. **GAEB 90 layout.** The record layout of `.D84`/`.D86` must be confirmed on the real files while building the reader.
5. **Cloud AI.** Is sending the (anonymised) item texts to the Claude API acceptable to the organisers?
6. **How strict is an exact match across file formats?** Today the label ignores letter case, spacing, line-break
   hyphens and the anonymiser's spelling of a hidden word. It does not ignore a short text cut at 70 characters or a
   word hidden in one file and readable in the other. Loosening it would copy more prices and could copy a wrong one.
