# Implementation plan: LV estimating backend, steps 0–3

Covers extraction (step 0), exact match (step 1), trades and item types (step 2) and the price lookup with evidence
(step 3) from [SPEC.md](../SPEC.md). The tasks with acceptance criteria are in [todo.md](todo.md).

**Order changed:** step 3 (tasks 8–11) was built before step 2 (tasks 6 and 7), because it does not need trades or
item types. Its plan is the section "Step 3" at the end of this file. All tasks 1–11 are built now.

## What exists after this plan

```
.venv\Scripts\python.exe -m lv ingest "<priced LV>" --project P2   ->  data/library/P2.json
.venv\Scripts\python.exe -m lv price  "<new LV>"    --project P4   ->  data/out/P4.json
```

`data/out/P4.json` holds every line item of the new LV with its `group`, `item_type` and `trade`, and the command
prints the overview (trade → item type → quantities per unit). No prices yet.

## Decisions

- **Order follows the data flow, riskiest reader early.** Everything needs the line item table, so the shapes and the
  two GAEB readers come first. The GAEB 90 reader is the least certain piece (fixed-width records, layout read off the
  files), so it is task 3, not last.
- **Each task ends in something runnable.** After task 2 a new LV can be read into a file; after task 4 it carries
  group labels; after task 7 it carries item types.
- **Tasks 1–4 need no AI and no API key.** They can be built and tested before the key exists.
- **Tests run on the real dataset files**, as the spec says. No mocked LVs.
- **One place for model names and prompts** (`lv/ai.py`), so the model can be swapped after a test run.
- **Trade list is one constant.** The 17 stand-in names from the test are used until an official list is chosen.

## Dependency order

```
1 skeleton + shapes
   └─ 2 GAEB XML reader ── writes data/out/
        ├─ 3 GAEB 90 reader ── writes data/library/
        └─ 4 group label (step 1)
             └─ 5 AI setup ── needs the API key
                  └─ 6 propose item types (step 2, pass 1)
                       └─ 7 assign item types + overview (step 2, pass 2)
```

Tasks 3 and 4 do not depend on each other.

## What was checked in the files before planning

GAEB 90 files are 80-character lines; the first two characters name the record:

| Record | Content | Seen in |
|---|---|---|
| `11` / `12` | section start with number / section title | all `.D86` |
| `21` | item: item number, kind flags, quantity (3 decimals, no point), unit | `.D86`, P3 `.D84` |
| `23` | unit price and total price (2 decimals, no point) | all priced files |
| `25` / `26` | short text / long text lines | `.D86` |
| `32` | section sum | all priced files |

Text is in a DOS code page (umlauts read correctly as code page 850).

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| P1's `.D86` has 197 item records, the `.X83` of the same project has 159 items (P2: 249 vs 248) | A wrong item count in the price library | Task 3 starts by matching both files by item number and naming what the extra records are, before any price is trusted |
| GAEB 90 field positions are read off the files, not from the standard | Wrong quantities or prices, silently | Two cross-checks in task 3: P1's `.D83` must equal P1's `.X83`, and the sum of item totals must equal the section sums in the file |
| No API key on this computer | Tasks 5–7 blocked | Tasks 1–4 don't need it; get the key while they are built |
| LangChain habits the current Claude models reject (`temperature`, forced tool calls) | AI calls fail | Task 5 checks the current `langchain-anthropic` docs first and proves one real call |
| AI assigns a type that is not in the list | Broken overview | Task 7 validates every answer against the list and re-asks once; leftovers become `Sonstiges` |

## Checkpoints

- **After task 3:** library files for P1 and P2 and result files for P4 and P5 exist. You look at one of each.
- **After task 4:** group counts match the numbers measured in the spec.
- **After task 7:** `lv price` runs on P4 and P5 end to end. You read the printed overview before anything is committed.

## Step 3: price lookup with evidence (tasks 8–11)

What exists after these tasks:

```
.venv\Scripts\python.exe -m lv price "<new LV>" --project P4              ->  data/out/P4.json, with predictions
.venv\Scripts\python.exe -m lv price "<new LV>" --project P4 --copy-only  ->  the same, exact matches only, no AI
```

### Decisions

- **A lookup, not an estimate.** Measured on this dataset: a loose "similar" rule priced 17–30% of items and was
  typically 80–120% off. The strict rule prices about 1% and stays within 25%. Low coverage is accepted.
- **Code decides, the AI only compares texts.** The judge gives one verdict per candidate and never sees a price.
  Which method an item gets, every statistic and the interpolation are code.
- **The judge is a function that is handed in.** `predict` takes the judge as an argument, so the tests hand in a
  fake with fixed verdicts and need no network and no key.
- **One judge call per item**, sent in parallel with `.batch()`. One item's failed call does not lose the others.
- **Surcharge status and the text a repeat item refers to are worked out on the fly** and not saved in the line
  item table, so the table does not change beyond `group`, `prediction` and `summary`.
- **`--copy-only`** makes the first check runnable on its own, without a key.

### Dependency order

```
3 GAEB 90 reader ─┐
4 group label ────┼─ 8 shapes + copied prices + total ─┐
                  └─ 9 candidate search ───────────────┼─ 10 judge + verdicts to prediction ── 11 lv price with the judge
5 AI setup ────────────────────────────────────────────┘
```

### Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The same item is worded differently in a `.D86` and an `.X83` (anonymiser, short text cut at 70 characters) | Fewer copied prices than expected, more judge calls | Measured in task 8 and reported; the label is not loosened without your decision (open question 6 in the spec) |
| The judge names two sizes that do not belong together | A wrong interpolated price | Interpolation only when both candidates name the same dimension and the same target size |
| The judge answers for an id that was not offered, or not at all | A price without a real source | Unknown ids are ignored; no answer gives `none` |
| No API key | Tasks 10 and 11 cannot be checked with a real call | Everything is tested with a fake judge; the one real run is left to you |

## Still deferred

- The accuracy check command (`lv check`).
- Reading P3 (prices from the `.D84` work; texts from a PDF do not exist yet). P3 is only used as a price source,
  and which PDF holds the full texts was found in the other session.

## Open questions

1. **API key.** Needed from task 5 on.
2. **Saving AI answers between runs.** Proposed: a `data/cache/` folder, so a second run is instant and the demo shows
   the same result every time. Not confirmed yet; task 5 includes it only if you agree.
3. **Trade list.** Keep the 17 stand-in names, or is there an official list?
4. **Branch.** Proposed earlier: `backend/lv-pipeline`. Not created yet.
5. **`data/library/` and `data/out/` in git?** Proposed: yes, so the frontend can build against real files.
