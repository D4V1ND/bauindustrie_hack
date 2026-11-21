# Spec: LV estimating backend, steps 0–3

Status: steps 0 and 1 are built, step 3 is being built, step 2 is not built. Terms in **bold** are defined in [CONTEXT.md](CONTEXT.md).

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
| Price library | P1, P2 (P3 once its PDF texts are read) |
| New LV | P4 |
| Accuracy check | hide one past project's prices, predict them from the other two, compare |

## Step 0: extraction

Reads one file and returns the line item table. It copies the file faithfully: one row per item, in file order,
nothing compared, nothing merged.

| Format | How | `read_by` | Order |
|---|---|---|---|
| GAEB XML (`.X83`) | code | `gaeb` | first |
| GAEB 90 (`.D83`, `.D84`, `.D86`) | code | `gaeb` | first |
| PDF | texts via an AI model | `ai` | second, needed for P3 |
| Excel, Word | not supported | | |

P3: prices come from `Angebot AN24-0305.D84` (code), texts from the PDF (AI), joined by item number. No price is ever
read by AI. Each past project enters the price library exactly once (P1 and P2: the `.D86` file).

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
| `read_by` | `gaeb` or `ai` | how the texts were read |

Left out on purpose: price components (labour/material/equipment), notes without a price.

## Step 1: exact match

Code only. Every line item gets a `group` label. Two items get the same label exactly when they are an
**exact match**: same short text, long text and unit, after removing differences without meaning (letter case,
extra spaces and line breaks, line-break hyphens such as "We- ge").

The label depends only on the item itself, so it links items inside one LV (for adding up quantities) and across
the price library (for copying a price).

## Step 2: trades and item types (new LV only)

Three levels above the line item: **trade** → **item type** → **group**.

1. **Propose the list.** One AI call reads all short texts and section titles of the LV and returns the list of
   item types, each with exactly one trade from a fixed trade list. Cap: about one type per four items, at most 100.
2. **Assign.** AI gives every item exactly one item type from that list, or `Sonstiges` if none fits.

Rules: surcharge items ("Zulage …") get the type of the thing they are a surcharge for. Types are for the overview,
not for prices. Quantities add up per unit and are never added across units.

Each item gains `item_type` and `trade`.

## Step 3: price lookup with evidence

A lookup, not an estimate. An item gets a price only when the price library holds the same item, or the same item
in a smaller and a larger size. Otherwise it gets the nearest past items as **references**, without a price, or
nothing. Low coverage is accepted; a wrong price is not.

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

### Search (code, standard library)

- Only library items with the same unit and the same surcharge status: a **surcharge item** ("Zulage", "Zuschlag",
  "Mehrpreis", "Aufpreis", "Mehrkosten") only meets other surcharge items.
- A **repeat item** ("wie vor, jedoch …", "wie Pos. …") is compared together with the text of the item it refers
  to: the last item before it in the same LV that is fully described. It also takes over that item's surcharge status.
- Ranking: similarity of the words and numbers in short text + long text (rare words weigh more). The 5 best with a
  score of at least 0.10 are the candidates.

### Judge (AI, `claude-sonnet-5-5`)

One call per item that has candidates. The judge reads the new item's text and the candidates' texts. It never
sees a price, a quantity-times-price or a total, and it never states one. Its rules are the estimator's own
(`JUDGE_RULES` in `lv/ai.py`): compare kind, material, scope, size, class and, for hourly rates, the kind of worker
or machine. It answers in a fixed shape:

| Field | Meaning |
|---|---|
| `judged[]` | one entry per candidate: `cand` (library item id), `verdict`, `difference` (a few words) |
| `verdict` | `same`, `size_only`, `type_only` or `different`; in doubt the stricter one |
| `dimension`, `cand_size`, `target_size` | only for `size_only`: what differs (e.g. `DN`) and both sizes as numbers |
| `reason` | one sentence for the item |

Verdicts for ids that were not offered are ignored. If the judge gives no answer for an item, the item gets `none`.

### From verdicts to the prediction (code)

- `similar`: the prices of all `same` candidates. A `size_only` candidate whose two sizes are equal counts as `same`.
- `adjusted`: the nearest smaller and the nearest larger `size_only` candidate, linear interpolation:
  `smaller price + (target size − smaller size) ÷ (larger size − smaller size) × (larger price − smaller price)`,
  rounded to the cent. Both candidates must name the same dimension and the same target size.
- `reference`: every `size_only` and `type_only` candidate is listed with its unit price. No price is proposed.

Each item gains a `prediction`:

| Field | Meaning |
|---|---|
| `method` | `copied`, `similar`, `adjusted`, `reference` or `none` |
| `min`, `max` | range of the past prices behind the prediction; empty for `reference` and `none` |
| `median` | the value used for totals. For `adjusted` it is the calculated result |
| `mean` | average of the same prices. For `adjusted` it is the calculated result |
| `count` | number of past prices behind the prediction; 0 for `reference` and `none` |
| `sources` | list of `{id, unit_price, verdict, difference}`: every past item used or shown. `verdict` is `exact` for `copied` |
| `rule`, `calculation`, `result` | only for `adjusted`, e.g. `linear interpolation by DN`, `80 + (125 - 100) / (150 - 100) x (120 - 80)`, `100.0` |
| `reason` | the judge's sentence, or a fixed sentence when code decided alone |

The result file `data/out/<project>.json` also holds a `summary`: per method the number of items and the sum of
`median × quantity`, and the project `total`. Sums count only **normal items** whose method is `copied`, `similar`
or `adjusted`. `lv price` prints the same numbers.

`lv price … --copy-only` stops after check 1: no search, no AI call, no API key needed. Without that option a
missing API key stops the command with a message that says what to do, and only if at least one item has candidates.

### What step 3 hands to step 4

Step 3 records facts and contains no thresholds. Step 4 will read `method`, `min`/`max`, `mean` vs `median`,
`count`, `sources[].verdict`, `sources[].difference` and `read_by` to decide what to flag.

### Known limits

- **Exact matches across file formats.** The dataset's anonymiser hid different words in each file, and GAEB 90
  files cut the short text at 70 characters. So the same item often has a different `group` label in a `.D86` and
  in an `.X83` file (P2 against itself: 160 of 248 items get the same label). Such items go to the judge instead.
- **P3 is not in the price library.** Its prices are read (`.D84`), but its texts sit in PDFs. Not built yet.
- **`lv check`** (hide one past project, predict it from the others, compare) is not built yet.
- A repeat item in P2's `.X83` names its reference in a separate field, not in its text. That field is not read.

## Tech stack

- Python 3.13, pip, `.venv` (Windows, PowerShell).
- GAEB reading and text search: standard library only.
- AI: Claude API through `langchain-anthropic`, used thinly: chat model, structured output, `.batch()` with a
  concurrency limit. No agents, chains, vector stores or LangGraph.
  - `claude-sonnet-5-5` for the step 3 judge: the variant that was measured.
  - Step 2 (not built yet): `claude-haiku-5-5` to assign item types, `claude-opus-5-5` for the two one-off calls
    (propose the item type list, read PDF texts).
  - Model names live in one settings place. No `temperature` parameter; structured output through the native
    JSON-schema method, not forced tool calls. Check both against the current `langchain-anthropic` docs when building.
- `pydantic` for the data shapes, `python-dotenv` for the API key, `pytest` for tests.

## Commands

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m lv ingest "<priced LV file>" --project P2     # flow A
.venv\Scripts\python.exe -m lv price "<new LV file>" --project P4         # flow B
.venv\Scripts\python.exe -m lv price "<new LV file>" --project P4 --copy-only   # exact matches only, no AI
.venv\Scripts\python.exe -m lv.ai                                         # one real AI call, to check the key
.venv\Scripts\python.exe -m lv check --hide P2                            # accuracy check (not built yet)
.venv\Scripts\python.exe -m pytest
```

## Project structure

```
lv/
  __main__.py     command line: ingest, price, check
  model.py        line item, prediction and file shapes
  extract.py      step 0: one reader per format
  match.py        step 1 group label; step 3 candidate search
  categorise.py   step 2 (not built yet)
  predict.py      step 3: judge input, verdicts to prediction, interpolation, statistics, totals
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
returns fixed verdicts. Real AI calls are checked by hand (`python -m lv.ai`, then `lv price` on P5) and by reading
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

Step 1
- P2: at least 27 groups with more than one item, covering at least 55 items. P4: at least 7 groups, 15 items.
- Two items that differ in one character of meaning (e.g. DN 150 vs DN 200) never share a label.

Step 2
- Every item of the new LV has exactly one item type from the LV's list, and every type exactly one trade.
- P1 comes out at 40 item types or fewer.

Step 3
- Every prediction with a method other than `none` has at least one source, and every source `id` exists in the library.
- For every `adjusted` prediction, recomputing `calculation` gives `result`.
- No price is written into `unit_price`, and no price appears in what the judge is sent.
- With only P2 in the library, `lv price --copy-only` on P2's own `.X83` copies a price for every item whose label
  matches, and every copied price equals the unit price in the `.D86`.
- `lv price` for P4 writes `data/out/P4.json` with the number of items per method and the project total.

## Out of scope

Steps 4 and 5; plan check; Excel and Word; P6; price adjustment by material or by date; unit conversion (week vs
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
