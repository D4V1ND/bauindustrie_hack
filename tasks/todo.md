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
  - Real call checked on 2026-11-21 with the key in `.env`: `.venv\Scripts\python.exe -m lv.ai` returned the answer
    in the requested shape.
  - Acceptance:
    - One real call returns an answer in the requested shape.
    - A missing key stops the program with a message that says what to do.
    - No `temperature` is sent, and structured output uses the native method, checked against the current docs.
  - Verify: run the one-call check by hand; tests for the no-key message pass.
  - Depends on: 1.
  - Files: `lv/ai.py`, `tests/test_ai.py`, `requirements.txt`.

- [x] **Task 6: Propose the item type list** (small)
  - Real run on P5 (`claude-opus-5-5`, one call, 14 s): 20 types in 11 trades, close to the list of the first test.
  - Changed: the cap is never below 20. With "one type per four items" alone, P5 (50 items) got 13 types and no
    type for its 7 hourly-rate items. P1 still gets at most 40.
  - The model also reads the first 200 characters of each long text, as in the first test, not only short texts.
  - Does: one call per new LV that returns the list of item types, each with exactly one trade from the fixed list.
  - Acceptance:
    - The list respects the cap: about one type per four items, at most 100. P1 gives 40 types or fewer.
    - Type names are unique, and every trade is one from the fixed list.
    - No type is named after surcharges ("Zulage").
  - Verify: run on P5 and read the list.
  - Depends on: 2, 5.
  - Files: `lv/categorise.py`, `lv/ai.py`, `tests/test_categorise.py`.

- [x] **Task 7: Assign item types and print the overview** (medium)
  - Real run on P5 (45 s for steps 2 and 3 together): 50 items -> 20 item types -> 11 trades, 0 items forced to
    `Sonstiges`. Step 3 on the same run: 2 `similar`, 13 `reference`, 35 `none` (library: P1 and P2).
  - Items went to `claude-haiku-5-5` 25 per call (changed to Sonnet in task 12). The overview counts normal items only; optional items are named
    in one line below it. With `--copy-only` step 2 is skipped as well.
  - Real run on P4 (once, 219 calls, no failed call): 296 items -> 73 item types -> 16 trades, no item outside the
    list, none forced to `Sonstiges`. The 24 optional items are all hourly rates. Step 3 on the same run: 1 `copied`
    (32.74), 1 `similar` (an optional hourly rate, so not in the total), 0 `adjusted`, 69 `reference`, 225 `none`.
  - Seen in that run and fixed afterwards: the model proposed a type "Erschwerniszulage Tiefbau" (7 items). A type
    with "zulage" anywhere in its name is now dropped. `data/out/P4.json` is from before this fix.
  - Seen on P5 and not changed: the judge called P1's site-setup lump sum the `same` item as P5's (5,700.00). A lump
    sum depends on the project; the judging rules have no rule for that yet.
  - Does: assigns every item of the new LV one type from the list, in batches, writes `item_type` and `trade` into the
    result file, and makes `lv price` print the overview: trade → item type → quantities per unit.
  - Acceptance:
    - Every item has exactly one type from the list or `Sonstiges`; an answer outside the list is asked again once.
    - Quantities in the overview are added per unit and never across units.
    - `lv price` on P4 and on P5 finishes and writes the full result file.
  - Verify: tests pass for the validation and the per-unit sums; run on P5 and read the overview for sense.
  - Depends on: 4, 6.
  - Files: `lv/categorise.py`, `lv/__main__.py`, `tests/test_categorise.py`.

- [x] **Task 12: Reason per item type, Sonnet, effort levels, one decision per group** (small)
  - Does: the assignment also returns one short reason per item and saves it as `item_type_reason`; it runs on
    `claude-sonnet-5-5`; every AI job names its effort level next to its model (propose `medium`, assign `high`,
    judge `high`); identical items are asked about once and the others get a copy.
  - Acceptance (all with the fake):
    - The reason is saved per item. An item that falls back to `Sonstiges` after the second try gets a reason
      written by code that says so. A valid type with an empty reason is still assigned.
    - A reason never changes the type.
    - Identical items are asked once and all get the same type, trade and reason. Two repeat items with the same
      words that follow different items are asked separately.
    - The request carries the effort level together with the fixed answer shape (checked offline).
  - Real run on P5: 50 items asked, 0 copied (P5 has no identical items); assignment took 11 s in 2 calls on
    Sonnet. Every item has a reason, none over 20 words, none written by code; 20 types in 10 trades.
    The Haiku run was not timed on its own: the whole P5 run took 45 s then and 57 s now.
  - On hold: the P4 run. It will also show whether the fix that drops surcharge-named types holds on real data.
  - Verify: tests pass; `lv price` on P5 and read `item_type_reason` in `data/out/P5.json`.
  - Depends on: 7.
  - Files: `lv/categorise.py`, `lv/ai.py`, `lv/model.py`, `lv/__main__.py`, `tests/`.

### Checkpoint: steps 0–2 complete
- [x] All tests pass.
- [x] `lv price` runs on P4 and P5 end to end.
- [ ] You have read the printed overview for P5.
- [x] Committed locally after each task, as you asked for this build. Nothing is pushed.

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

- [x] **Task 10: Judge and verdicts to prediction** (medium)
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

- [x] **Task 11: `lv price` with the judge** (small)
  - Built and tested with a fake judge. Real runs on P5 and P4 are done: numbers under task 7.
  - Found while building: once `.env` held a key, one test that ran `lv price` reached the real judge (2 test runs,
    28 calls each, P5 against P2). `tests/conftest.py` now hides the key from every test.
  - Items that would go to the judge with P1 and P2 in the library: 32 of 50 for P5, 206 of 296 for P4.
  - Does: makes `lv price` call the real judge for the items that have candidates, and only for those.
  - Acceptance:
    - A missing API key stops the command with a message that says what to do, and only when at least one item
      has candidates. With `--copy-only`, or when nothing needs judging, no key is needed.
    - The command prints the number of items per method and the total, and writes `data/out/<project>.json`.
    - One real run on P5 returns verdicts in the fixed shape.
  - Verify: tests pass; with the key in `.env`, run `lv price` on P5 and read the result file.
  - Depends on: 10.
  - Files: `lv/__main__.py`, `tests/test_predict.py`.

- [x] **Task 13: Pack the judge's questions by item type, one judgement per set of identical items** (medium)
  - Does: identical items of the new LV are searched and judged once and the result is copied to the others with
    `judged_item`; items with candidates go to the judge in packs of one item type (at most 15), with one hint
    line; an item whose part of a pack answer does not fit is asked again on its own. The five outcomes, the
    judging rules and the search stay as they were (one sentence about the hint line was added to the rules).
  - Acceptance (all with the fake judge):
    - Identical items are judged once and all carry the result plus the id of the judged item.
    - Packs follow item types and never exceed 15; the hint line appears once per pack.
    - A pack answer missing one item leads to one single-item question. A verdict naming a candidate of another
      item, an invented id, a missing or a doubled candidate rejects that item's part of the answer.
    - What still fails after the single question counts as unanswered. Without item types the calls are per item.
  - Agreement check with real calls (library P1 and P2; same items judged one by one and packed):

    | | P5 | P4 |
    |---|---|---|
    | Judge calls before this change | 32 | 206 |
    | Judge calls packed | 18 (17 packs + 1 single question) | 62 (no item had to be asked again) |
    | Items judged / copied back from an identical item | 32 / 0 | 198 / 9 |
    | Verdicts compared | 117 | 865 |
    | Same verdict | 105 | 834 |
    | Looser in packs (`same` or `size_only` instead of `type_only` or `different`) | 0 | 0 |
    | Stricter in packs (`type_only` instead of `size_only`) | 0 | 2 |
    | `different` one by one, `type_only` packed | 9 | 20 |
    | `type_only` one by one, `different` packed | 3 | 9 |
    | Judge time one by one / packed | 33 s / 34 s | 161 s / 119 s |

    The rule "packed must nowhere be looser" holds on both. In packs the judge more often calls a candidate
    `type_only` where it said `different` one by one; that adds references (P4: 63 against 51), never a price.
  - Also seen in these runs:
    - P4's fresh step 2 run has no item type named after a surcharge; the 7 items that sat under
      "Erschwerniszulage Tiefbau" now sit under the thing they belong to.
    - The same P4 run proposed no type for hourly rates, so the 25 items of the section "Stundenlohnarbeiten" went
      to `Sonstiges` (with reasons that say so). The earlier run had two such types. The list differs from run to run.
    - The judge is not fully repeatable: P5's "Asphaltbeton 0/11 mm, Stärke 4 cm" was judged `same` as a P2 surface
      course in both of these runs (77.34 per m2) and not in the two runs before.
  - `data/out/P5.json` and `data/out/P4.json` are the packed results of this check.
  - Verify: tests pass; the check script and its lists of differences are outside the repo (one-off).
  - Depends on: 7, 10.
  - Files: `lv/predict.py`, `lv/match.py`, `lv/model.py`, `lv/ai.py`, `lv/__main__.py`, `tests/test_predict.py`.

- [x] **Task 14: P3's texts from its PDF prints, P3 into the price library** (medium)
  - Does: reads item texts by code (`pypdf`, no AI) from PDF prints of an LV whose prices file has no texts, joins
    them to the priced items by item number, and makes `lv ingest <prices file> --project P3 --texts <pdf> <pdf>`
    write `data/library/P3.json`. Items read this way get `read_by` `pdf`.
  - Acceptance (against the real files):
    - 274 priced items, every one with a short text; quantity x unit price equals the total for all.
    - Prices, quantities and units are exactly those of the `.D84`; nothing is taken from a PDF.
    - A handful of items word for word as in the PDF, among them a long text that runs over three pages.
    - Items of the `.D84` without any text in the PDFs are listed and kept. A text whose PDF quantity or unit
      disagrees with the prices file is not used and is listed.
  - Found:
    - Both PDFs hold all 274 item numbers of the `.D84`, and quantity and unit agree for every item in both.
    - 168 items have a long text, 106 have none. All 106 were checked: the PDF shows the short text and then the
      quantity, nothing else. 89 are repeat items ("wie vor, jedoch ..."), 16 hourly rates, 1 fitting. No miss is
      left. The throwaway prototype had 165 with a long text; it read the PDF in plain mode.
    - Plain text extraction scrambles the tender print (quantity, long text, then item number, and lines with
      anonymised words pushed to the page end). Layout mode gives the reading order. The reader uses layout mode.
    - The bid print has no text for one item (3.2.160); its text comes from the tender print.
    - The `.D84` has no section records; section titles come from the PDFs.
  - Library: 446 priced line items before (P1 197, P2 249), 720 after (P3 274).
  - Real runs, library P1 + P2 against P1 + P2 + P3 (the "before" numbers are the packed runs of task 13):

    | | P5 before | P5 after | P4 before | P4 after |
    |---|---|---|---|---|
    | `copied` | 0 | 0 | 1 | 1 |
    | `similar` | 3 | 3 | 1 | 4 |
    | `adjusted` | 0 | 0 | 0 | 0 |
    | `reference` | 10 | 25 | 63 | 95 |
    | `none` | 37 | 22 | 231 | 196 |
    | total | 13,913.60 | 8,385.00 | 32.74 | 22,532.74 |

    P5's total fell because one borderline item (Asphaltbeton, 6,960.60) was judged `same` in the run before and
    not in this one; that is the judge varying between runs, not P3.
  - Predictions with a price that rest on a P3 price (none of them on a token price):
    - P5 04.0002 "Gehobener [PERSON_020]", 20 h: `similar`, 71.60 per h from P3/7.1.50 "BGr. IV, Gehobener
      [PERSON_020]" = 1,432.00.
    - P4 01.01.0002, 02.01.0002 and 03.01.0002 "Baustelle räumen", 1 psch each: `similar`, 7,500.00 from P3/1.1.30
      "Baustelle räumen" = 22,500.00 together. They are one judgement: the three items are identical, the second
      and third carry `judged_item` P4/01.01.0002. A lump sum again (see task 7).
  - P3's token prices show up only in references so far: 33 reference sources on P5 and P4 carry a P3 unit price
    below 2. No rule was added.
  - Verify: tests pass; `lv ingest` for P3 as above and open `data/library/P3.json`.
  - Depends on: 3.
  - Files: `lv/extract.py`, `lv/model.py`, `lv/__main__.py`, `tests/test_extract_pdf.py`, `requirements.txt`.

- [x] **Task 15: Search by meaning (Voyage embeddings), merged with the word search by rank** (medium)
  - Does: embeds every library item and every new item once with Voyage AI `voyage-4` (short text + first 500
    characters of the long text, without anonymiser tokens), saves the vectors in `data/embeddings/voyage-4.json`,
    and merges the word ranking and the meaning ranking by reciprocal rank fusion (best 20 of each, top 5 to the
    judge). Without `VOYAGE_API_KEY`, `lv price` searches by words as before and says so.
  - Why: word search misses other words for the same thing. P4's DN 80 gate valve ("Handbetätigung") ranked 8th by
    words against P1's "Absperrschieber DN 80, PN 16 … mit Handrad"; by meaning it ranked 1st, and the judge called it
    `same` (258.48 per piece; the words-only run had no price).
  - Found:
    - Voyage without a payment method allows 3 calls and 10,000 tokens a minute: embedding the 720 library items took
      9.4 min, P4 7.2 min, once. The code waits a minute when it hits the limit.
    - Meaning scores sit in a narrow band (best per P5 item 64–93%) and blur numbers: "FBS Pass- und Gelenkstück
      DN 800" is closest to "Bogen DN 80" (78%). A cut-off cannot separate right from wrong, so the judge reads every
      candidate.
    - P5, real run with the step 2 item types kept (21 judge calls, 53 s):

      | | words only, 5 (task 14 run) | RRF top 5 |
      |---|---|---|
      | `similar` | 3 | 8 |
      | `adjusted` | 0 | 1 |
      | `reference` | 25 | 26 |
      | `none` | 22 | 15 |
      | total | 8,385.00 | 13,220.35 |

      New prices, read by hand (P5 has no answer key): TOK-Band, Facharbeiter, Bagger bis 1 m3 plausible;
      Vorarbeiter (Polier) from P1's "Polier- oder Obermonteur"; Transporter `same` although the judge called it
      "ambiguous"; LKW bis 12 t interpolated with a 15 t tipper that also has all-wheel drive.
    - P4, the same way (80 judge calls, 3 min):

      | | words only, 5 (task 14 run) | RRF top 5 |
      |---|---|---|
      | `copied` | 1 | 1 |
      | `similar` | 4 | 3 |
      | `reference` | 95 | 114 |
      | `none` | 196 | 178 |
      | total | 22,532.74 | 276.74 |

      Every price that was lost still had its candidate in the top 5; the judge decided more strictly this time.
      "Baustelle räumen" (3 identical items, 7,500 each from P3) became `type_only` ("scope wording differs"). The
      DN 80 gate valve became `type_only` ("no material or coating stated") although a single call earlier said
      `same`. New: "Betrieb der Pumpen" 6.80 per h from P3's "Pumpe 10 l/s pro h"; Bauzaun H 2,0 m judged `same`
      with P3's "Ortsfester Schutzzaun", but at P3's token price of 0.10 per m.
  - So the judge, not the shortlist, now decides most outcomes, and on borderline items it does not give the same
    verdict twice. P3's token prices reach `same` verdicts through meaning search; no rule for them yet.
  - Not measured yet: whether more prices also means more wrong prices. The hidden-project test answers it.
  - View: `data/views/step3_match.html` (one P4 item end to end; every P5 item with its top 5 and verdicts).
  - Files: `lv/match.py`, `lv/ai.py`, `lv/predict.py`, `lv/__main__.py`, `tests/test_match.py`, `tests/conftest.py`,
    `requirements.txt`, `.gitignore`.

- [x] **Task 16: Keep the judge's whole answer and thinking; the result format for the frontend** (small)
  - Does: every prediction keeps `judged`, all candidates with the judge's verdict and difference (`different`
    included), and every source carries the library item's `short_text`. `lv price` writes
    `data/out/<project>_judge.json`: per judge call the items, the question as sent, the summarised thinking and
    the answer. `docs/result-format.md` describes both files for the frontend, with JSON Schemas generated from the
    code (`docs/result.schema.json`, `docs/judge-log.schema.json`).
  - Found: with structured output at effort `high`, Sonnet 5.5 answers without thinking (0 reasoning tokens in all
    21 P5 calls and in a single test call). Plain text at `high` thinks; structured output at `xhigh` thinks
    (270 reasoning tokens) and at `max` (1,683). So every verdict so far was made without thinking. `thinking` stays
    empty until the judge's effort is raised; that is the estimator's call, since `high` is the measured setting.
  - P5 again with `lv price` end to end (step 2 asked again): 9 of 50 priced, 28 `reference`, 13 `none`, the same
    items priced as in task 15's run. But LKW bis 12 t changed from `adjusted` 101.01 (7 t and 15 t tippers) to
    `similar` at 1.06 per h: P3/2.5.30 "Lkw-Kipper (4-Achser) mit Allradantrieb, 12 t NL" is the right item at one of
    P3's token prices. With P4's Bauzaun (0.10 per m), two token prices now reach a price. A rule for them is needed.
  - Judge moved to effort `xhigh` (your call). P5 end to end: thinking saved for 19 of 20 calls (median 1,190
    characters), 20 calls in 94 s, none asked again. 5 of 50 priced instead of 9: TOK-Band, Vorarbeiter (Polier),
    Transporter and LKW bis 12 t (the 1.06 token price) became `reference`, the four that read as doubtful. Kept:
    Baustelleneinrichtung, Asphalt schneiden, Gehobener Facharbeiter, Facharbeiter, Bagger bis 1 m3.
  - Verify: tests pass; `lv price` on P5 writes `P5.json` and `P5_judge.json`.
  - Files: `lv/model.py`, `lv/predict.py`, `lv/ai.py`, `lv/__main__.py`, `tests/test_ai.py`, `tests/test_predict.py`,
    `docs/result-format.md`, `docs/result.schema.json`, `docs/judge-log.schema.json`.

- [x] **Task 17: Keep the best 3 candidates, each with a why** (small)
  - Does: the judge gives every candidate one sentence, `why`: for `same` why it is the same thing, otherwise why it
    is not. The prediction keeps the best 3 candidates in `judged`, best first (what the price rests on, then by
    verdict). A `reference` lists at most 3 sources.
  - Decided against a search shortlist of 3: in the P5 run at `xhigh`, one of the five prices (Bagger bis 1 m3) had its
    match at place 5 of the shortlist. So the judge still reads 5 and the cut to 3 comes after it.
  - P5 end to end: 17 judge calls, 140 s. Every item has 3 candidates in `judged`, every one with a `why`. 6 priced,
    33 `reference`, 11 `none`; the sixth price is Vorarbeiter (Polier), now matched to P3's Bauvorarbeiter at 82.60
    (it was `reference` in the run before: the judge varying, not this change).
  - Files: `lv/ai.py` (judging rules), `lv/predict.py`, `lv/model.py`, `tests/test_predict.py`, docs and views.

- [x] **Task 18: Step 4, a risk score per item and one for the LV** (medium)
  - Does: `lv/risk.py`. Price risk by code from the step 3 method; description risk from one AI check per item
    (complete, gaps or vague, with what is missing); score = 0.6 × price risk + 0.4 × description risk. Each item gets
    a value for weighting: its price × quantity, else the middle price of its related items × quantity, else the
    value of a typical item. The LV's score is the value-weighted mean over the normal items. `lv price` writes
    `risk` per item and `risk_summary`, and prints the score with the five items that weigh most.
  - Decided with you: one percent per item from price and description; the more an item is worth, the more it
    moves the LV's score; items without a price are weighted by related items' prices, then as a typical item. The
    AI does not rate importance, since that would be a price guess.
  - P5, real run (2 description calls, 40 s): LV score 51 of 100 over a value of 167,595.45. Value basis:
    5 by price, 33 by related items, 12 assumed. Descriptions: 30 complete, 20 with gaps,
    0 vague. Examples: "Asphalt schneiden" 19 (similar, complete); "Transporter" 65 (reference; vehicle size
    and operator not stated); "FBS Pass- und Gelenkstück DN 800" 80 (nothing found; length or angle not stated).
  - Limits: the five heaviest items are all weighted by related items' prices, not by a found price; values inherit
    token prices; all numbers of the scale are starting values.
  - P4, all steps in one `lv price` run: 9 min 29 s end to end. Opus item types 40 s, assignment 31 s (12 calls),
    judge 373 s (67 calls, then 15 asked again), description check 119 s (12 calls); reading, search and saving
    about 6 s. Step 3: 1 copied, 8 similar, 1 adjusted, 121 reference, 165 none, total 23,604.14; thinking saved for
    77 of 82 calls. Step 4: LV score 52 of 100 over a value of 582,810.29; value basis 9 by price, 109 by related
    items, 154 assumed; descriptions 172 complete, 115 with gaps, 9 vague.
  - P4 against its run at `high`: "Baustelle räumen" (3 identical lump sums, 7,500 each) is `similar` again, the
    judge varying once more. Bauzaun (the 0.10 token price) and the pump operation became `reference`. New prices:
    two plate load tests (100 per test), bitumen spraying (1.60 per m2), asphalt cutting (adjusted, 15.68 per m) and a
    longitudinal crossing surcharge at 0.50 per m, one of P3's near-zero prices.
  - Speed: the description check needs only texts and item types, so it could run beside the judge (about 2 min
    less on P4). Not built.
  - Files: `lv/risk.py`, `lv/model.py`, `lv/ai.py`, `lv/__main__.py`, `tests/test_risk.py`, docs and views.

- [x] **Task 19: Optional plan check, linking the VLM prototype (PR #2) to the pipeline** (small)
  - Does: `lv price … --plan <plan>` also reads a construction plan with the prototype's OpenAI vision model
    (`vlm/extract_plan.py`, 3 readings), compares it with the LV in `vlm/compare.py` and writes
    `data/out/<project>_plan.json`. The LV comes from step 0's reader through `lv/plan.py`, not from
    `vlm/gaeb_llm.py`. A bad plan file, `--copy-only`, or a missing OpenAI key stops the command before anything runs;
    the plan check runs after the LV result is saved.
  - Merged first: PR #1 (steps 0 to 4) and PR #2 (the VLM prototype); the one conflict, `.gitignore`, keeps both lists.
  - Checked: tests for the item adapter, for the prototype's comparison on step 0 items without any AI call
    (a matching count, a differing count with its marker, an item in m2 left unchecked), and for the early stops; by
    hand, a real image without an OpenAI key stops with the prototype's message and writes nothing.
  - Not checked: a real plan run. This repository has no plan file and no OpenAI key.
  - Files: `lv/plan.py`, `lv/__main__.py`, `tests/test_plan.py`, `requirements.txt`, `SPEC.md`, `docs/result-format.md`,
    `vlm/README.md`.

- [x] **Task 20: The plan check runs beside steps 1 to 4** (small)
  - Does: with `--plan`, the plan check starts in a background thread right after step 0 and runs while steps 1 to 4
    go on; `lv price` collects it after saving the LV result. A failing plan check ends the command with an error
    that says the LV result is saved. Progress lines are now whole lines ("asking …" and "… done in N s"), so the
    plan check's own lines cannot break into them.
  - Why: the plan check needs only step 0's items, and it is the slow part when a plan is given.
  - Checked: tests for the background runner, for the plan check running in its own thread and writing its file,
    and for a failing plan check keeping the LV result. Not measured: the time saved, which needs a real plan run.

- [x] **Task 21: Keep the plan link working with PR #5, and write the plan overview** (small)
  - PR #5 (coordinates per item, rooms, a fixed overview per plan) changed `extract_runs` to return the readings and
    the rooms as a pair. `lv/plan.py` now takes both and passes the rooms on; without this, `lv price --plan` would
    crash in the plan check, and the tests would not have shown it. A new test guards the pair.
  - `lv price … --plan` also writes the prototype's overview: `<project>__<plan>_uebersicht.png` and `.xlsx`.
    `requirements.txt`: `pillow`, `openpyxl`.
  - Real run, Hörsaal H1 with its plan: LV pipeline 2 min 3 s, plan check 5 min 33 s in parallel, 5 min 37 s end to end.
    41 of 52 items located on the plan (8 by plan code, 20 by AI matching, 13 by room). 27 of 30 compared items
    match; seats (240) and podium stairs (2) are now matched. 4 findings: aisle steps not counted, the podium in m2
    against 1 piece, and one pair that is a single thing: the plan's 3 exit signs (NA) against the LV's exit-sign item.

- [x] **Task 22: One structured result: judge_call link, and the plan check merged into <project>.json** (small)
  - `prediction.judge_call`: the `call` number in `<project>_judge.json` that decided the item, the last call that
    asked about it. Set by `lv price` from the judge log.
  - With `--plan`, `lv/plan.py` `merge` writes the plan check into the LV in English keys: `plan` on each item (status,
    plan quantity, how matched, rooms, markers, finding ids) and `plan` on the LV (plan file and size, all findings
    including those no item covers, raw file and overview names). The LV file is saved again after the merge;
    `<project>_plan.json` stays as the raw record.
  - Existing files filled in without AI calls: H1 (judge_call on 52 of 52 items, plan on all 52, 4 findings), P5 (50
    of 50), P4 (264 of 296; the other 32 were copied or had no candidates).
  - Tests: judge_call points at the last call that asked about the item, the retried items at their own call; the
    merge (ok, differs with its finding and marker, an m2 item not checked, a plan component no item covers); the
    command writes the merged file.

### Checkpoint: step 3
- [x] All tests pass without network and without a key.
- [x] `lv price --copy-only` on P2's own `.X83` copies only correct prices.
- [x] The key is in `.env`; `.venv\Scripts\python.exe -m lv.ai` and `lv price` on P5 and P4 were run.
- [ ] You have read `data/out/P5.json` and `data/out/P4.json`.

## Waiting

- [x] **P3 in the price library.** Done in task 14.
- [ ] **`lv check --hide <project>`**: the accuracy check as a command.
