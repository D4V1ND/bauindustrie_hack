# Implementation plan: LV estimating backend, steps 0–2

Covers extraction (step 0), exact match (step 1) and trades and item types (step 2) from [SPEC.md](../SPEC.md).
The tasks with acceptance criteria are in [todo.md](todo.md).

**Not in this plan:** step 3 (price prediction). Its matching rule is being tightened and re-measured in a separate
session. The step 3 plan is written only after that rule is approved.

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

## Deferred to the step 3 plan

- Price prediction, the accuracy check command (`lv check`) and the project total.
- Reading P3 (prices from the `.D84`, texts from a PDF). P3 is only used as a price source, and which PDF holds the
  full texts was found in the other session.

## Open questions

1. **API key.** Needed from task 5 on.
2. **Saving AI answers between runs.** Proposed: a `data/cache/` folder, so a second run is instant and the demo shows
   the same result every time. Not confirmed yet; task 5 includes it only if you agree.
3. **Trade list.** Keep the 17 stand-in names, or is there an official list?
4. **Branch.** Proposed earlier: `backend/lv-pipeline`. Not created yet.
5. **`data/library/` and `data/out/` in git?** Proposed: yes, so the frontend can build against real files.
