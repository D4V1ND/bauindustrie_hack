# Tasks: steps 0–2

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

## Waiting

- [ ] **Step 3 plan.** Written only after you say the tightened matching rule is good enough.
