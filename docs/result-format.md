# Result format of `lv price`

What the backend writes for one new LV (Leistungsverzeichnis, bill of quantities), for the frontend to show.
The exact shapes are in two JSON Schemas generated from the code:

| File (in `data/out/`) | What it holds | Schema |
|---|---|---|
| `<project>.json`, e.g. `P5.json` | the LV: every line item with its trade, item type and price lookup, plus a summary | [`result.schema.json`](result.schema.json) |
| `<project>_judge.json`, e.g. `P5_judge.json` | every call to the judge (an AI model): what it read, its thinking, its answer | [`judge-log.schema.json`](judge-log.schema.json) |

Regenerate both schemas after a change to `lv/model.py` or the judge's answer shape (see the end of this file).

## `<project>.json`

### Top level

| Field | Type | Meaning |
|---|---|---|
| `project` | string | short project ID, e.g. `"P5"` |
| `name`, `date`, `source_file` | string, date or null, string | from the GAEB file |
| `items` | array of line items | in LV order |
| `item_types` | array of `{name, trade, description}` | step 2: the LV's own list of item types |
| `summary` | object | step 3: items and money per method, and the project total (see below) |
| `risk_summary` | object | step 4: the LV's risk score and what it rests on (see below) |
| `plan` | object or null | plan check, only when a plan was given: the plan, all findings, the files behind them (see below) |
| `priced` | boolean | whether the file itself had prices; `false` for a new LV |

### A line item (`items[]`)

| Field | Type | Meaning |
|---|---|---|
| `id` | string | project + `/` + item number, unique everywhere, e.g. `"P5/04.0004"` |
| `oz` | string | item number (Ordnungszahl) as printed in the LV |
| `section` | array of strings | section titles, outermost first |
| `short_text`, `long_text` | string | the item's texts. Some may contain anonymiser tokens such as `[PERSON_020]` |
| `quantity`, `unit` | number or null, string | e.g. `5`, `"h"` |
| `unit_price`, `total_price` | null in a new LV | never filled by the tool. **Do not show these as the predicted price** |
| `kind` | `"normal"` or `"optional"` | optional items (Bedarfspositionen) are not added to totals |
| `group` | string | items with the same `group` are word-for-word the same item |
| `item_type`, `trade`, `item_type_reason` | string | step 2: the item's type, its trade, one sentence why |
| `prediction` | object | step 3: what the price lookup found (below) |
| `risk` | object | step 4: the item's risk score and its two parts (below) |
| `comments` | array of `{text, price, effect}` | the estimator's comments, oldest first, written by `lv comment` (below); empty until then |
| `plan` | object or null | plan check, only when a plan was given: what the plan says about this item (below) |

### `prediction`

| Field | Type | Meaning |
|---|---|---|
| `method` | `"copied"`, `"similar"`, `"adjusted"`, `"reference"`, `"none"` | how the result came about (table below) |
| `median` | number or null | **the price to show** per unit; set only for `copied`, `similar`, `adjusted` |
| `min`, `max` | number or null | range of the past prices behind it. For `none`: only a span, the lowest and highest price in `judged`, not a price |
| `mean`, `count` | number or null, integer | average of those prices, and how many there are |
| `sources` | array of sources | the past items the price rests on (`copied`, `similar`, `adjusted`), or the related items the suggested price comes from (`reference`) |
| `judged` | array of sources | the 3 best candidates the judge looked at, best first, each with its `why`. The judge reads 5; the file keeps 3 |
| `reason` | string | one sentence from the judge, or a fixed sentence when code decided alone |
| `rule`, `calculation`, `result` | string, string, number | only for `adjusted`: e.g. `"linear interpolation by DN"`, `"80 + (125 - 100) / (150 - 100) x (120 - 80)"`, `100.0` |
| `judged_item` | string or null | set when this item is identical to another item of the LV: the `id` of the item that was actually judged |
| `judge_call` | integer or null | **link to the judge log**: the `call` number in `<project>_judge.json` that decided this item; null when the judge was not asked (an exact copy, or no candidates) |

| `method` | What it means | Show |
|---|---|---|
| `copied` | the library holds the same item word for word | the price, as exact |
| `similar` | the judge called a library item the same thing | the price, with its source(s) |
| `adjusted` | two sizes around the target were found; the price is interpolated | the price, with both sources and the calculation |
| `reference` | related library items (`size_only`, `type_only`), but none the same | `median` as the suggested price: the middle price of the sources, shown as "related, not the same" |
| `none` | every candidate `different`, or nothing found | no price, confidence 0; `min`–`max` as a span from the 3 items in `judged`, when set |

### A source (`sources[]` and `judged[]`)

| Field | Type | Meaning |
|---|---|---|
| `id` | string | the library item, e.g. `"P3/7.1.160"`; the part before `/` is the past project |
| `short_text` | string | its short text |
| `unit_price` | number | its price in that past project |
| `verdict` | `"exact"`, `"same"`, `"size_only"`, `"type_only"`, `"different"` | `exact`: copied without a judge. The others are the judge's verdicts |
| `difference` | string | a few words from the judge on what differs; empty when nothing does |
| `why` | string | one sentence from the judge: for `same`, why this is the match; for every other verdict, why it is not |

### `summary`

```json
{"methods": {"copied": {"items": 0, "total": 0.0}, "similar": {"items": 5, "total": 10952.0},
             "adjusted": {"items": 0, "total": 0.0}, "reference": {"items": 33, "total": 142315.46},
             "none": {"items": 12, "total": 0.0}},
 "total": 153267.46}
```

`items` counts every item; `total` adds up `median × quantity` over the normal items with a price.

### Example: one item from P5 (judge at effort xhigh)

```json
{
  "id": "P5/04.0006",
  "oz": "04.0006",
  "section": [
    "04 Stundenlohnarbeiten (Regie)"
  ],
  "short_text": "Bagger bis 1 m3",
  "long_text": "Bagger bis 1 m3",
  "quantity": 10.0,
  "unit": "h",
  "unit_price": null,
  "total_price": null,
  "kind": "normal",
  "read_by": "gaeb",
  "group": "b355a78a8cf4",
  "item_type": "Sonstiges",
  "trade": "Sonstiges",
  "item_type_reason": "No valid answer came back from the model after two tries, so code set this item to Sonstiges.",
  "prediction": {
    "method": "similar",
    "min": 113.5,
    "max": 113.5,
    "median": 113.5,
    "mean": 113.5,
    "count": 1,
    "sources": [
      {
        "id": "P3/7.1.100",
        "short_text": "Hydraulikbagger 1,0 m³",
        "unit_price": 113.5,
        "verdict": "same",
        "difference": "",
        "why": "A 1,0 m³ hydraulic excavator matches an excavator up to 1 m³."
      }
    ],
    "rule": null,
    "calculation": null,
    "result": null,
    "reason": "Only the 1,0 m³ Hydraulikbagger matches; the others are larger special excavators or other machines.",
    "judged": [
      {
        "id": "P3/7.1.100",
        "short_text": "Hydraulikbagger 1,0 m³",
        "unit_price": 113.5,
        "verdict": "same",
        "difference": "",
        "why": "A 1,0 m³ hydraulic excavator matches an excavator up to 1 m³."
      },
      {
        "id": "P3/2.5.20",
        "short_text": "wie vor, jedoch Bagger zum Verfüllen einschließlich Anbauverdichtungsgerät",
        "unit_price": 1.13,
        "verdict": "type_only",
        "difference": "25 t backfill excavator with compactor vs excavator up to 1 m³",
        "why": "A 25 t excavator with an attachment compactor is not an excavator up to 1 m³."
      },
      {
        "id": "P3/2.5.10",
        "short_text": "Bagger (25 to) mit Humusschaufel (b = 2,0 m)",
        "unit_price": 1.13,
        "verdict": "type_only",
        "difference": "25 t excavator with Humusschaufel, operator, archaeology vs excavator up to 1 m³",
        "why": "Size is given in tonnes, not m³, and scope includes a special bucket and operator."
      }
    ],
    "judged_item": null
  }
}
```

Shown to an estimator: **Bagger bis 1 m³, 10 h: €113.50 per h (similar), €1,135.00 in total**, source P3 "Hydraulikbagger 1,0 m³". The match is the first entry in `judged`; the other two say why they are not it.
Behind a "why" control: the judge's reason and the five candidates with their verdicts.

### `risk` (step 4)

Scores run from 0 (safe) to 100. They are places on a scale, not probabilities.

| Field | Type | Meaning |
|---|---|---|
| `score` | integer | **the item's risk**: 0.6 × `price_risk` + 0.4 × `description_risk`; the price risk alone when the description was not checked or the estimator entered the price |
| `confidence` | integer | **the item's confidence**: 100 − `score` when the item has a price (found by step 3 or entered by the estimator), else 0 |
| `price_risk` | integer | from the step 3 method: copied 5, similar 25 (40 when its past prices lie more than 10% apart), adjusted 45, reference 75, none 100. A comment with a price makes it 0; a doubt on a found price makes it 75 |
| `price_reason` | string | one fixed sentence saying why |
| `description` | `"complete"`, `"gaps"`, `"vague"` or null | does the text state what an estimator needs to price the item? null: not checked |
| `description_risk` | integer or null | complete 10, gaps 50, vague 90 |
| `missing` | array of strings | what the text does not state, at most 3 short phrases |
| `description_reason` | string | one sentence from the model |
| `value` | number or null | roughly what the item is worth. **A weight, never a price to show** |
| `value_basis` | `"price"`, `"reference"`, `"assumed"` | found or entered price × quantity, or the value of a typical item; `reference` only in results from before 21 Nov 2026 |

Example, P5 "Transporter", 5 h:

```json
{
  "score": 65,
  "confidence": 35,
  "price_risk": 75,
  "price_reason": "Only related past items were found, none the same: the price is their middle price.",
  "description": "gaps",
  "description_risk": 50,
  "missing": [
    "vehicle size or payload",
    "operator included"
  ],
  "description_reason": "A transporter hourly rate needs a vehicle size or payload, and it is unclear whether the driver is included.",
  "value": 456.0,
  "value_basis": "price"
}
```

### `risk_summary` (step 4)

```json
{"score": 51, "confidence": 47, "value": 167595.46, "basis": {"price": 38, "reference": 0, "assumed": 12}}
```

`score` is the items' scores weighted by `value`, over the normal items. `confidence` is the items' confidence
weighted the same way: here 5, because only 5 of 50 items have a price. `basis` counts those items per value basis:
here 12 of 50 values are assumed, so show `basis` next to the score.

### Comments: the estimator's input (`lv comment`)

The estimator can comment on one item. The command saves the comment in the result file and recalculates every
item's risk and confidence and the LV's `risk_summary`. No AI is asked and no API key is needed.

```powershell
.venv\Scripts\python.exe -m lv comment P5 02.0005 --price 12 --text "Angebot Erdbauer"   # own unit price
.venv\Scripts\python.exe -m lv comment P5 02.0001 --doubt --text "Asphaltschneiden ist teurer geworden"
.venv\Scripts\python.exe -m lv comment P5 02.0014 --text "Rückfrage beim Planer"         # a note only
```

| Comment | Effect on the item |
|---|---|
| with `price` | counts as priced: `price_risk` 0, `confidence` 100, `value` = price × quantity. The description no longer counts |
| `effect: "doubt"` | a found price drops to `price_risk` 75 (P5 `02.0001`: confidence 81 → 51). An item without a price stays at 0 |
| `effect: "note"` | nothing changes |

The latest comment with a price or a doubt decides; notes after it change nothing. On P5, a price of €12 per m3 for
the biggest open item (`02.0005`, 270 m3) lifts the LV's confidence from 5 to 9. The comment's `price` is the
estimator's own number, so show it as such; the tool still never writes `unit_price`.

## `<project>_judge.json`

An array, one entry per judge call. One call judges up to 15 items of the same item type.

```json
{
  "call": 17,
  "items": [
    "P5/04.0003",
    "P5/04.0004",
    "P5/04.0005",
    "P5/04.0006",
    "P5/04.0007"
  ],
  "question": "HINT: the 5 new items below were sorted under \"Sonstiges\" (trade: Sonstiges) by an earlier step. Decide by the texts only.\n\n### P5/04.0003 | unit h | qty 20\nNEW: Facharbeiter — Facharbeiter\n- P3/7.1.60: BGr.V, Baufacharbeiter — \n- P3/7.1.40: BGr.III, Spezialba …",
  "thinking": "I'm weighing the candidate groupings for the P5/04.0003 Facharbeiter item in unit h, starting with BGr. V, which I'm checking against the BRTV Bau Berufsgruppen to see whether it really covers skilled construction workers.\n\nI'm checking which Berufsgruppe in t …",
  "answer": {
    "items": [
      {
        "id": "P5/04.0006",
        "judged": [
          {
            "cand": "P3/2.5.20",
            "verdict": "type_only",
            "dimension": null,
            "cand_size": null,
            "target_size": null,
            "difference": "25 t backfill excavator with compactor vs excavator up to 1 m³",
            "why": "A 25 t excavator with an attachment compactor is not an excavator up to 1 m³."
          },
          {
            "cand": "P3/2.5.10",
            "verdict": "type_only",
            "dimension": null,
            "cand_size": null,
            "target_size": null,
            "difference": "25 t excavator with Humusschaufel, operator, archaeology vs excavator up to 1 m³",
            "why": "Size is given in tonnes, not m³, and scope includes a special bucket and operator."
          },
          {
            "cand": "P3/2.5.30",
            "verdict": "different",
            "dimension": null,
            "cand_size": null,
            "target_size": null,
            "difference": "Lkw-Kipper, not an excavator",
            "why": "A tipper truck is a different machine from an excavator."
          },
          {
            "cand": "P3/7.1.180",
            "verdict": "different",
            "dimension": null,
            "cand_size": null,
            "target_size": null,
            "difference": "compressor with hammer, not an excavator",
            "why": "A compressor with a hammer is a different machine from an excavator."
          },
          {
            "cand": "P3/7.1.100",
            "verdict": "same",
            "dimension": null,
            "cand_size": null,
            "target_size": null,
            "difference": "",
            "why": "A 1,0 m³ hydraulic excavator matches an excavator up to 1 m³."
          }
        ],
        "reason": "Only the 1,0 m³ Hydraulikbagger matches; the others are larger special excavators or other machines."
      },
      "… the other items of this call …"
    ]
  }
}
```

To find an item's call, read its `prediction.judge_call`: the entry with that `call` number. An item may appear in
two calls when its first answer did not fit and it was asked again on its own; `judge_call` names the later one,
which counted.

## The plan check inside `<project>.json` (only with a plan)

`lv price … --plan` merges the plan check into the result file at the end, in English keys.

### `plan` on an item

| Field | Meaning |
|---|---|
| `status` | `ok`, `ok_unstable` (counts varied between readings), `differs`, `not_in_plan`, `units_differ`, `not_checked` (only items in pieces are compared by count) |
| `quantity`, `unit` | what the plan shows: a count, or for items in m2 the area of their rooms (information only) |
| `matched_by` | `plan_code` (e.g. F1), `dimensions`, `ai_match`, `room`, or empty |
| `rooms` | for items in m2: the rooms they were placed in |
| `markers` | where to show the item on the plan: `page`, `x`, `y` relative to the image (0 to 1, from the top left), `shape` `point` or `frame` (then `width`, `height`), `label` |
| `findings` | ids of its findings in the LV's `plan.findings` |

Example, Hörsaal H1, "Fensterelement Aluminium … (F1–F4)":

```json
{
  "status": "ok",
  "quantity": 4.0,
  "unit": "St",
  "matched_by": "plan_code",
  "rooms": [],
  "markers": [
    {
      "page": 1,
      "x": 0.099,
      "y": 0.345,
      "shape": "point",
      "width": null,
      "height": null,
      "label": "F1–F4"
    },
    {
      "page": 1,
      "x": 0.099,
      "y": 0.442,
      "shape": "point",
      "width": null,
      "height": null,
      "label": "F1–F4"
    },
    "… 2 more …"
  ],
  "findings": []
}
```

### `plan` on the LV

| Field | Meaning |
|---|---|
| `file`, `width_px`, `height_px` | the plan image; pixel = `x × width_px`, `y × height_px` |
| `checked` | items whose quantity was compared |
| `findings[]` | `id`, `oz` (null for something on the plan that no LV item covers), `kind` (`quantity_differs`, `not_in_plan`, `missing_in_lv`, `units_differ`, `unstable_count`), `severity` (`error`, `risk`, `note`), `title` and `description` (German, written by the plan check), `lv_quantity`, `lv_unit`, `plan_quantity`, `plan_unit`, `confidence`, `decision` (`open`), `markers` |
| `raw_file` | `<project>_plan.json`, the plan check's own result |
| `overview_files` | the PNG and Excel overview |

A finding no LV item covers, from H1:

```json
{
  "id": "b-004",
  "oz": null,
  "kind": "missing_in_lv",
  "severity": "risk",
  "title": "Im Plan vorhanden, im LV keine passende Position: NA",
  "plan_quantity": 3.0,
  "plan_unit": "St",
  "confidence": "high",
  "decision": "open"
}
```

## `<project>_plan.json` (only with a plan): the raw record

Written by `lv price … --plan <plan>`. The shape is the VLM prototype's (`vlm/README.md`), with German keys:

| Field | Meaning |
|---|---|
| `eingaben` | the LV file and the plan (`id`, `datei`, `breite_px`, `hoehe_px`) |
| `positionen[]` | the LV's items: `oz`, `gruppe`, `kurztext`, `menge`, `einheit`, plus `pruefung` (`ok`, `ok_instabil`, `abweichung`, `nicht_im_plan`, `einheit_nicht_vergleichbar`, `nicht_geprueft`), `befund_ids`, and `plan`: `null` when the item cannot be located, else `menge`, `einheit`, `zuordnung` (`Plankürzel`, `KI-Zuordnung` or `Raum`), `marker[]` and, for items in m2, `raeume` |
| `befunde[]` | findings: `id`, `oz` (null for a plan component missing in the LV), `art`, `schwere` (`fehler`, `risiko`, `hinweis`), `titel`, `beschreibung`, `lv` and `plan` quantities, `marker[]` with `x`, `y` relative to the plan image (0 to 1, from the top left; `breite` and `hoehe` for a frame), and `entscheidung` (`status: "offen"`, for the estimator) |
| `zusammenfassung` | number of findings per `schwere`, and how many items were compared |

Join it to `<project>.json` on `oz`. Show an item's `plan.marker` (or a finding's `marker`) on the plan image when it is
selected: pixel = `x × breite_px`, `y × hoehe_px`.

Next to it, `<project>__<plan>_uebersicht.png` and `.xlsx` hold the same result as a fixed overview: the plan with
every located item numbered and a legend, and a spreadsheet of all items.

## Rules for showing results

- The predicted price is `prediction.median`, and only when `method` is `copied`, `similar` or `adjusted`. Show
  `min`–`max` and `count` next to it when `count` > 1.
- Every price must show its source(s): `id`, `short_text`, `unit_price`. Traceability is a hard requirement.
- `none` has no price. Show its `min`–`max` as a span next to the 3 items of `judged`, never as a price, and keep it
  out of every total.
- `judged` is ordered best first. If its first entry has the verdict `same`, that is the match and its `why` says why;
  every other entry's `why` says why it is not the match.
- Totals come from `summary`; do not add up items yourself.
- Show `risk.score` with its reasons: `price_reason`, `description_reason` and `missing`. A list sorted by
  `risk.score × risk.value` puts the items that matter most first.
- Never show `risk.value` as a price, and show `risk_summary.basis` next to `risk_summary.score`.

## Known limits

- **Token prices.** P3's bid has prices below €2 such as €0.10 per m. They can reach `similar` (P4's
  "Bauzaun" was priced at €0.10/m this way). Flag a `unit_price` below 2 in a source; the rule is not settled.
- **`thinking` can be empty** for a call: the judge (effort `xhigh`) decides itself whether to think, and skipped it
  in 1 of 20 P5 calls. The judge's `reason` and each `difference` are always there.
- **The judge varies between runs** on borderline items: the same candidate can be `same` in one run and
  `type_only` in the next.

## Regenerating the schemas

```powershell
.venv\Scripts\python.exe -c "import json; from lv.model import LV; open('docs/result.schema.json','w',encoding='utf-8').write(json.dumps(LV.model_json_schema(mode='serialization'), ensure_ascii=False, indent=1) + '\n')"
```

`judge-log.schema.json` wraps the judge's answer shape (`JudgeAnswer` in `lv/predict.py`); regenerate it the same
way when that shape changes.
