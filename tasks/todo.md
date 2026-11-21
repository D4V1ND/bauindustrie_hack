# Tasks: steps 0–3

Plan and decisions: [plan.md](plan.md). Spec: [SPEC.md](../SPEC.md).
Test command for every task: `.venv\Scripts\python.exe -m pytest`

## Phase 1: read LV files (step 0)

- [x] **Task 1: Skeleton and line item shapes** (small)
  - Does: creates the `lv` package, the command line with `ingest` and `price` (no behaviour yet), the LV and line
    item shapes from the spec, `requirements.txt` and a `.gitignore` covering `.env` and `.venv`.
  - Acceptance:
    - An LV with one line item can be written to JSON and read back unchanged.
    - A line item with a `kind` or `read_by` outside the allowed values is rejected.
    - `python -m lv --help` lists both commands.
  - Verify: tests pass; run `.venv\Scripts\python.exe -m lv --help`.
  - Depends on: nothing.
  - Files: `lv/__main__.py`, `lv/model.py`, `tests/test_model.py`, `requirements.txt`, `.gitignore`.

- [x] **Task 2: GAEB XML reader** (medium)
  - Does: reads an `.X83` file into the line item table (header, sections, texts, quantity, cleaned unit, kind) and
    makes `lv price` write `data/out/<project>.json`.
  - Acceptance:
    - Item counts: P1 159, P2 248, P4 296, P5 50, in file order, every `id` unique.
    - P4 has 24 optional items; P1, P2 and P5 have none.
    - Units come out in one spelling each: no `Stk.`, `Std.` or `m²` left; `St`, `h`, `m2` instead.
  - Verify: tests pass; run `lv price` on P5 and open `data/out/P5.json`.
  - Depends on: 1.
  - Files: `lv/extract.py`, `lv/__main__.py`, `tests/test_extract_xml.py`.

- [x] **Task 3: GAEB 90 reader** (medium, highest risk)
  - Does: reads `.D83`, `.D84` and `.D86` files into the same table, including unit and total prices, and makes
    `lv ingest` write `data/library/<project>.json`.
  - Acceptance:
    - P1's `.D83` gives the same item numbers, short texts, quantities and units as P1's `.X83`.
    - For P1 and P2 `.D86`: the sum of `total_price` over normal items equals the sums stated in the file.
    - The extra item records in the `.D86` files (P1: 197 against 159, P2: 249 against 248) are identified, and a test
      states the rule that keeps or drops them.
  - Found in task 2: P2's `.X83` writes item numbers without leading zeros (`1.1.2`). Check how the `.D86` writes
    the same item before matching the two files by item number.
  - Found in task 3:
    - The extra item records are change orders (section "Nachträge"): 38 in P1 (unit price, but no quantity, unit
      or total) and 1 in P2. They are kept. P2's `.D86` writes item numbers as `01.01.0002`.
    - P2's item 08.01.0003 appears as `TE.L0.0009` in the `.D86`: the dataset's anonymiser replaced its number.
    - The anonymiser hid different words in each file format (`PER000115-Rohr` in GAEB 90 is `Edelstahl-Rohr` or
      `[PERSON_115]-Rohr` in XML). Texts of the same LV are therefore not letter-identical across formats.
    - `Std` without a dot is "piece x day" in P2 (its own unit list says so), not hours. Only `Std.` becomes `h`.
    - P3's `.D84` is read too (274 items with prices, texts missing). P2's `.D84` has prices only and is refused.
  - Verify: tests pass; run `lv ingest` on P1 and P2 and open one library file.
  - Depends on: 2.
  - Files: `lv/extract.py`, `lv/__main__.py`, `tests/test_extract_gaeb90.py`.

### Checkpoint: extraction
- [ ] All tests pass.
- [ ] `data/library/P1.json`, `data/library/P2.json`, `data/out/P4.json`, `data/out/P5.json` exist.
- [ ] You have looked at one library file and one result file.

## Phase 2: link identical items (step 1)

- [x] **Task 4: Group label** (small)
  - Does: gives every line item a `group` label from its cleaned short text, long text and unit, in both commands.
  - Acceptance:
    - P2: at least 27 groups with more than one item, covering at least 55 items. P4: at least 7 groups, 15 items.
    - Same label despite letter case, extra spaces, line breaks and a line-break hyphen (`We- ge` = `Wege`).
    - Different label when one character of meaning differs (`DN 150` vs `DN 200`) or the unit differs.
  - Verify: tests pass.
  - Depends on: 2.
  - Files: `lv/match.py`, `lv/__main__.py`, `tests/test_match.py`.

### Checkpoint: steps 0 and 1
- [ ] All tests pass. No API key was needed so far.
- [ ] Review with you before the AI tasks start.

## Phase 3: trades and item types (step 2)

- [x] **Task 5: AI setup** (small; needs the API key)
  - Does: sets up the two Claude models through `langchain-anthropic` in one place, loads the key from `.env`, and
    provides one helper that returns an answer in a fixed, validated shape. Saves answers between runs if you agreed
    to that (open question 2).
  - Built without a key: only the judge model (`claude-sonnet-5-5`) is set up, because tasks 6 and 7 are not built
    yet. Answers are not saved between runs (open question 2 is still open).
  - **Still to do by you:** put the key into `.env`, then run `.venv\Scripts\python.exe -m lv.ai` (one real call).
  - Acceptance:
    - One real call returns an answer in the requested shape. (not checked yet: no key)
    - A missing key stops the program with a message that says what to do.
    - No `temperature` is sent, and structured output uses the native method, checked against the current docs.
  - Verify: run the one-call check by hand; tests for the no-key message pass.
  - Depends on: 1.
  - Files: `lv/ai.py`, `tests/test_ai.py`, `requirements.txt`.

- [ ] **Task 6: Propose the item type list** (small)
  - Does: one call per new LV that returns the list of item types, each with exactly one trade from the fixed list.
  - Acceptance:
    - The list respects the cap: about one type per four items, at most 100. P1 gives 40 types or fewer.
    - Type names are unique, and every trade is one from the fixed list.
    - No type is named after surcharges ("Zulage").
  - Verify: run on P5 and read the list.
  - Depends on: 2, 5.
  - Files: `lv/categorise.py`, `lv/ai.py`, `tests/test_categorise.py`.

- [ ] **Task 7: Assign item types and print the overview** (medium)
  - Does: assigns every item of the new LV one type from the list, in batches, writes `item_type` and `trade` into the
    result file, and makes `lv price` print the overview: trade → item type → quantities per unit.
  - Acceptance:
    - Every item has exactly one type from the list or `Sonstiges`; an answer outside the list is asked again once.
    - Quantities in the overview are added per unit and never across units.
    - `lv price` on P4 and on P5 finishes and writes the full result file.
  - Verify: tests pass for the validation and the per-unit sums; run on P5 and read the overview for sense.
  - Depends on: 4, 6.
  - Files: `lv/categorise.py`, `lv/__main__.py`, `tests/test_categorise.py`.

### Checkpoint: steps 0–2 complete
- [ ] All tests pass.
- [ ] `lv price` runs on P4 and P5 end to end.
- [ ] You have read the printed overview for P5.
- [ ] Nothing is committed until you say so.

## Phase 4: price lookup with evidence (step 3)

Built before tasks 6 and 7: step 3 does not need trades or item types.

- [x] **Task 8: Prediction shapes, copied prices and the total** (medium)
  - Measured: 160 of 248 items (65%) of P2's `.X83` get a copied price from P2's `.D86`. 156 of them are exactly
    the item's own price. The other 4 are two pairs of identical items that P2 itself priced differently
    (19.60 and 21.00; 236.47 and 610.86 for 318 m and 18 m of the same pipe): range and median show both prices.
    The 88 items without a copy are worded differently in the two files (see the spec, known limits).
  - Does: adds the `prediction` and `summary` shapes, copies prices from library items with the same `group` label,
    adds up the total, and makes `lv price` load `data/library/`, print the result and write it. With `--copy-only`
    the command stops here: no search, no AI.
  - Acceptance:
    - `copied`: min, max, median, mean and count come from all library items with the same label; every source is
      listed with `verdict` `exact`. Library items without a unit price are never a source.
    - The total is the sum of `median × quantity` over normal items with method `copied`, `similar` or `adjusted`.
      Optional items and the methods `reference` and `none` add nothing.
    - `unit_price` of the new LV's items stays empty.
    - With only P2 in the library, pricing P2's own `.X83` with `--copy-only`: every copied price equals the unit
      price of the same item in the `.D86`. The share of copied items is measured and reported.
  - Verify: tests pass; run `lv ingest` on P2's `.D86`, then `lv price --copy-only` on `[NUMMER_001]_leer.X83`.
  - Depends on: 3, 4.
  - Files: `lv/model.py`, `lv/predict.py`, `lv/__main__.py`, `tests/test_predict.py`.

- [x] **Task 9: Candidate search** (medium)
  - Does: for an item without an exact match, finds up to 5 candidates in the price library by text similarity,
    with the standard library only.
  - Acceptance:
    - A candidate always has the same unit and the same surcharge status as the new item.
    - A repeat item ("wie vor, jedoch …") is searched together with the text of the item it refers to, and takes
      over that item's surcharge status. The reference never crosses from one LV into another.
    - At most 5 candidates, best first, none below the minimum score of 0.10.
  - Verify: tests pass.
  - Depends on: 4.
  - Files: `lv/match.py`, `tests/test_match.py`.

- [ ] **Task 10: Judge and verdicts to prediction** (medium)
  - Does: writes the judge's input (texts only), holds the estimator's judging rules as the prompt, and turns the
    verdicts into `similar`, `adjusted`, `reference` or `none` in code.
  - Acceptance (all with a fake judge):
    - The judge's input holds the new item's and the candidates' texts and no price.
    - `similar` when at least one candidate is `same`; statistics over all `same` candidates.
    - `adjusted` when there is a smaller and a larger `size_only` candidate: `80 + (125 − 100) ÷ (150 − 100) ×
      (120 − 80) = 100`, with `rule`, `calculation` and `result` saved; recomputing `calculation` gives `result`.
    - `reference` for `type_only` candidates or `size_only` candidates on one side only: sources listed, no price.
    - `none` when all candidates are `different`, when there is no candidate, or when the judge gave no answer.
    - A verdict for an id that was not offered is ignored.
  - Verify: tests pass.
  - Depends on: 5, 8, 9.
  - Files: `lv/predict.py`, `lv/ai.py`, `tests/test_predict.py`.

- [ ] **Task 11: `lv price` with the judge** (small)
  - Does: makes `lv price` call the real judge for the items that have candidates, and only for those.
  - Acceptance:
    - A missing API key stops the command with a message that says what to do, and only when at least one item
      has candidates. With `--copy-only`, or when nothing needs judging, no key is needed.
    - The command prints the number of items per method and the total, and writes `data/out/<project>.json`.
    - One real run on P5 returns verdicts in the fixed shape. (needs the API key: to be run by you)
  - Verify: tests pass; with the key in `.env`, run `lv price` on P5 and read the result file.
  - Depends on: 10.
  - Files: `lv/__main__.py`, `tests/test_predict.py`.

### Checkpoint: step 3
- [ ] All tests pass without network and without a key.
- [ ] `lv price --copy-only` on P2's own `.X83` copies only correct prices.
- [ ] You have put the key into `.env`, run `.venv\Scripts\python.exe -m lv.ai` and `lv price` on P5, and read the result.

## Waiting

- [ ] **Tasks 6 and 7** (trades and item types).
- [ ] **P3 in the price library.** Prices are read from the `.D84`; the texts sit in PDFs.
- [ ] **`lv check --hide <project>`**: the accuracy check as a command.
