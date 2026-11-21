# bauindustrie_hack

## The 3 features

| Feature | What it does | Merges old # | Client priority |
|---|---|---|---|
| **A. Quantity summary** | Groups matching line items in the new LV and adds them up per unit ("40 chairs, 1,200 m² floor"). Exports the groups per trade or supplier ("4 chairs from you"). | 1, 2, 3 | **First.** "Summarising is totally important." |
| **B. Price estimate** | Gives each line item a price from the old LVs: copy it, interpolate by size (10 cm → 20 cm), or adjust by material (stainless steel ≈ 3× steel). From those it builds the **project total**, the **top ~20 cost drivers** and a **go/no-go** against the company's size limits. | 4, 6, 7 | End goal ("Mastermind") |
| **C. Plan check** | Compares LV quantities with what the 2D plan shows and flags mismatches (10 windows vs 20). | 5 | Wanted, but hardest. For the demo, plan data can be made up. |

## Backend API

The frontend talks to one small HTTP API, `lv/api.py`, built with FastAPI. It uploads an LV (and optionally a
construction plan), follows the run, reads the result, and saves the estimator's comments.

### Architecture

```mermaid
flowchart LR
    FE[Frontend] -- "HTTP: JSON and file uploads" --> API["API<br/>lv/api.py (FastAPI)"]
    API -- "1 saves the uploads" --> UP[("data/uploads/(project)/<br/>LV file, plan")]
    API -- "2 starts one process per run" --> RUN["lv price<br/>steps 0–4, plan check in parallel"]
    RUN -- "asks" --> AI["Claude: item types, judge, descriptions<br/>Voyage: search by meaning<br/>OpenAI: plan reading"]
    RUN -- "3 writes" --> OUT[("data/out/<br/>(project).json · _judge.json · _plan.json<br/>overview PNG/XLSX · .log")]
    API -- "4 reads, saves comments" --> OUT
```

- **One run is one process.** `POST /projects` saves the files, starts `python -m lv price …` (the same command as
  on the command line) and answers at once with `202`. The frontend then polls `GET /projects/{project}/status`
  until the run is no longer `running`. The run's printed lines are its log, shown in the status.
- **The files are the database.** The result is `data/out/<project>.json` (format:
  [docs/result-format.md](docs/result-format.md), JSON Schema: [docs/result.schema.json](docs/result.schema.json)).
  There is no database server.
- **With a plan,** the plan check runs beside steps 0–4 and is merged into the same `<project>.json` at the end
  (`plan` on each item and on the LV).
- **Comments need no AI.** A comment is saved into `<project>.json` and every confidence is recalculated at once.

### Run it

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
```

```powershell
.venv\Scripts\python -m uvicorn lv.api:app --port 8000
```

- **Try it in the browser:** http://localhost:8000/docs lists every endpoint, with a "Try it out" button.
- **Generate frontend types:** the machine-readable description is at http://localhost:8000/openapi.json, e.g.
  `npx openapi-typescript http://localhost:8000/openapi.json -o api.d.ts`.
- **Keys** go in the root `.env`:
  - `ANTHROPIC_API_KEY`: steps 2–4;
  - `VOYAGE_API_KEY`: optional, search by meaning;
  - `OPENAI_API_KEY` and `OPENAI_MODEL`: only for the plan check.
- **Passcode:** on the server, set the environment variable `LV_PASSCODE`. Every call then needs it as the header
  `X-Passcode: <passcode>`; without it the answer is `401`. Where a header cannot be sent (an `<img>` or SVG
  `<image>` of the plan), add `?passcode=<passcode>` to the address. Not set (on your own computer), the API is open.
  The dashboard and the chat panel ask for it once and keep it in the browser. `/docs`, `/openapi.json` and the chat
  panel page `/assistant` hold no data and stay open.
- **Price library:** the past LVs must already be in `data/library/` (`python -m lv ingest …`). Adding to the library
  has no endpoint.

### Endpoints

| Method | Path | What it does | Answer |
|---|---|---|---|
| `POST` | `/projects` | Upload an LV (and a plan) and start a run | `202` + [Status](#status) |
| `GET` | `/projects` | All projects, A to Z, with their key numbers | list of [Project](#project) |
| `GET` | `/projects/{project}/status` | Is the run done? Its last log lines | [Status](#status) |
| `GET` | `/projects/{project}` | **The result**: every line item with prediction, risk, comments and plan check | the LV ([docs/result-format.md](docs/result-format.md)) |
| `GET` | `/projects/{project}/judge` | Every judge call of the last run | list of [JudgeCall](#judgecall) |
| `GET` | `/projects/{project}/judge/{call}` | One judge call (an item's `prediction.judge_call` names it) | [JudgeCall](#judgecall) |
| `GET` | `/projects/{project}/files/{name}` | A plan file named in the result: `plan.file` (the plan image), `plan.raw_file`, `plan.overview_files` | the file |
| `POST` | `/projects/{project}/items/{oz}/comments` | Save the estimator's comment on one item | [Commented](#commented) |

`{project}` is the short project ID (e.g. `H1`). `{oz}` is the item number (e.g. `04.0050`).

### A typical session

**1. Upload.** A multipart form with these fields:

| Field | Required | What |
|---|---|---|
| `project` | yes | short ID: letters, digits and `-`, at most 40 (e.g. `H1`) |
| `lv` | yes | the new LV: `.x83` (GAEB XML) or `.d83`, `.d84`, `.d86` (GAEB 90) |
| `plan` | no | construction plan: `.png`, `.jpg`, `.jpeg` or `.pdf` |
| `copy_only` | no | `true`: only copy exact matches. No AI, no cost, a few seconds, no plan allowed. For trying out the frontend |

```js
const form = new FormData();
form.append("project", "H1");
form.append("lv", lvFile);
form.append("plan", planFile); // optional
const started = await fetch("http://localhost:8000/projects", { method: "POST", body: form });
// 202 {"project": "H1", "status": "running", "has_result": false, "error": "", "log": []}
```

**2. Poll** `GET /projects/H1/status` every 2–3 seconds while `status` is `running`, and show `log` as progress.
A full run asks AI models, so it takes minutes, not seconds: a few minutes for the 52 items of the Hörsaal H1 with
a plan.

**3. Load the result:** `GET /projects/H1`. One JSON with everything. The fields are described in
[docs/result-format.md](docs/result-format.md) and on the page "LV Result File".

**4. Show the judge's reasoning** for an item: read `item.prediction.judge_call` (e.g. `7`), then
`GET /projects/H1/judge/7`. It returns the question, the judge's thinking and its answer. `null` means no judge
was asked for this item.

**5. Draw the plan check** (only when `lv.plan` is not `null`):
- Load the image from `GET /projects/H1/files/{lv.plan.file}` (on the server: add `?passcode=…`, since an image
  cannot send a header).
- Every marker in `item.plan.markers` and `lv.plan.findings[].markers` has `x` and `y` from 0 to 1, measured from
  the top left. Pixel position = `x × width`, `y × height` of the shown image.
- A plan uploaded as PDF stays a PDF: upload PNG or JPG when the frontend should draw on it.

**6. Save a comment** on an item:

```js
await fetch("http://localhost:8000/projects/H1/items/04.0050/comments", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ price: 48.0, text: "Quote from supplier" }), // or { doubt: true, text: "…" }
});
```

The answer holds the updated item and the LV's new `risk_summary`, so the view can update without loading the
whole result again.

### Shapes

#### Status

| Field | Type | Meaning |
|---|---|---|
| `project` | string | the project ID |
| `status` | `running`, `done`, `failed` | `done` also for a result from before the server started |
| `has_result` | boolean | `<project>.json` exists. During a re-run this is still the previous result |
| `error` | string | when `failed`: the run's last line, the reason it stopped (e.g. a missing key) |
| `log` | list of strings | the run's last 40 printed lines |

```json
{
  "project": "P5", "status": "done", "has_result": true, "error": "",
  "log": ["price library: 720 line items with a price from 3 project(s)", "…",
          "confidence: 0 of 100 (an item with a price: 100 - risk; an item without one: 0)",
          "50 line items -> …\\data\\out\\P5.json"]
}
```

#### Project

| Field | Type | Meaning |
|---|---|---|
| `project` | string | the project ID |
| `status` | `running`, `done`, `failed` | as in Status |
| `items` | integer or null | line items; `null` while a first run has no result yet |
| `total` | number or null | the price estimate: normal items with a price, median × quantity |
| `confidence` | integer or null | 0 to 100, the LV's confidence weighted by value |
| `plan_findings` | integer or null | findings of the plan check; `null` when no plan was checked |

```json
[{"project": "H1", "status": "done", "items": 52, "total": 5700.0, "confidence": 1, "plan_findings": 4},
 {"project": "P5", "status": "done", "items": 50, "total": 10952.0, "confidence": 5, "plan_findings": null}]
```

#### Comment (the request body)

| Field | Type | Meaning |
|---|---|---|
| `text` | string | the comment |
| `price` | number > 0 or null | the estimator's own unit price: the item gets confidence 100 |
| `doubt` | boolean | the found price does not fit: lowers the item's confidence |

Give at least one of `text`, `price` or `doubt`. The latest comment with a price or a doubt decides.

#### Commented

```json
{
  "item": {"id": "P5/01.0004", "oz": "01.0004", "…": "…",
           "comments": [{"text": "Quote from supplier", "price": 48.0, "effect": "note"}],
           "risk": {"score": 0, "confidence": 100, "price_risk": 0,
                    "price_reason": "The price was entered by the estimator.", "…": "…"}},
  "risk_summary": {"score": 98, "confidence": 2, "value": 2400.0, "basis": {"price": 1, "reference": 0, "assumed": 49}}
}
```

#### JudgeCall

| Field | Type | Meaning |
|---|---|---|
| `call` | integer | the number an item's `prediction.judge_call` points at |
| `items` | list of strings | the line item ids asked about together in this call |
| `question` | string | the question as sent to the judge |
| `thinking` | string | the judge's summarised thinking |
| `answer` | object or null | the judge's answer: a verdict and a `why` per candidate; `null` when no usable answer came back |

### Errors

Every error answer is JSON with a `detail` field, e.g. `{"detail": "No project H9."}`.

| Code | When |
|---|---|
| `401` | `LV_PASSCODE` is set on the server and the passcode is missing or wrong: ask for it, then call again |
| `404` | unknown project, item, judge call or file; a project without a result yet |
| `409` | the project is running: a second upload, or a comment during the run |
| `422` | a bad project ID, a wrong file type, a plan together with `copy_only`, a comment with no text, price or doubt, a price of 0 or less. For a malformed request, `detail` is a list of what is wrong |

A run that stops shows `status: "failed"` with the reason in `error`. If only the plan check fails, the LV result
of steps 0–4 is already saved, so `has_result` is `true`.

### Good to know

- **Cost:** each full run pays for AI calls (Claude, Voyage, OpenAI), roughly $1–2 for the Hörsaal H1 with a plan.
  Use `copy_only` while building the frontend.
- **A re-run replaces the result.** Until it finishes, `GET /projects/{project}` returns the previous result.
- **Runs are tracked in the server's memory.** Restart the server only when no run is going. After a restart, a
  project with a result file shows `done`.
- **One shared passcode, no personal logins,** and every origin may call the API (CORS). Fine for the hackathon:
  without the passcode nobody can read results (they hold the company's past prices) or start paid runs. Beyond the
  hackathon, add a login per person.
## Assistant

A chat assistant for the right-hand panel. It answers questions about the open tender and the past projects from
the company's own data, writes comments on line items when asked, and drafts Bieterfragen. Try it at
http://localhost:8000/assistant (a plain panel with tabs, `lv/static/assistant.html`).

| File | What it does |
|---|---|
| `lv/index.py` | The search index: one card per line item, three vectors per card (short text, comments, long text) |
| `lv/assistant.py` | The tools, the Claude loop, tabs, memory and Bieterfragen |
| `lv/assistant_prompt.md` | The assistant's instructions: how the tool works, what the estimators told us, rules it may not break |

### How a position is found

Each line item becomes a **card**. The search part is three texts, each embedded on its own with Voyage
(`voyage-4`): **short** (section path and short text, weight 0.5), **comments** (weight 0.3) and **long** (the
first 1,500 characters of the long text, weight 0.2). A card without comments shares their weight between the other
two. A question is embedded as a query and compared with all three; that ranking is merged with a word search
(TF-IDF, so exact numbers like "DN 250" still win) by reciprocal rank fusion. The context part of a card
(project, item number, unit, quantity, price or prediction, comments) is what the model reads to answer.

Vectors are saved per text in `data/embeddings/voyage-4-rag.json`, so every text is embedded only once. A new
comment re-embeds only that comment. Without `VOYAGE_API_KEY`, or while vectors are missing, the search runs by
words only.

### When it is indexed

- `python -m lv ingest ...` and `python -m lv price ...` index the LV as their **last step**. A run's status stays
  `running` until that is done. A `--copy-only` run builds a word-only index (it promises no AI calls).
- **While a tender's run is going, the assistant does not answer about it.** It replies with `state: "waiting"`
  ("Bitte warte, bis die Extraktion abgeschlossen ist"), asks no model and saves nothing. The panel polls
  `GET /assistant/status` and unlocks the input when the state is `ready`.
- A result without an index (from before this feature) or with an outdated one is indexed in the background the
  first time the assistant is asked; the state is `indexing` until then.
- `python -m lv index [P1 H1 ...]` builds the indexes again by hand and embeds what is missing.
- **Voyage free tier** (no payment method): 3 calls and 10,000 tokens a minute. The indexer waits for that by
  itself: the 720 past line items take about 10 minutes once, a new tender of a few hundred items a few minutes.
  With a payment method, set `VOYAGE_RPM` and `VOYAGE_TPM` in `.env` higher.

### What the model may do

| Tool | What it does | Guard in code |
|---|---|---|
| `search_positions` | finds cards in the open tender, the library or both | read only |
| `get_position`, `project_overview`, `list_projects` | details, totals, cost drivers | read only; only the tab's tender and the library |
| `save_comment`, `edit_comment` | comment on an item of the open tender (`author: "assistant"`) | only the tab's tender, never the library, never while it runs |
| `draft_bidder_question`, `update_bidder_question`, `list_bidder_questions` | drafts in `data/out/<project>_bidder_questions.json` | drafts only, nothing is ever sent |
| `remember`, `forget` | facts in `data/assistant/memory.json`, shown to the model in every tab | |

The model is `claude-opus-5-5` at effort `medium` (`ASSISTANT_MODEL` in `lv/ai.py`), with adaptive thinking and
server-side refusal fallback. It needs `ANTHROPIC_API_KEY` in `.env`. The address of the API is read only from
`.env` (`ANTHROPIC_BASE_URL`), never from the shell.

### Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/assistant/status?project=H1` | `ready`, `extracting`, `indexing`, `missing` or `failed`, with a message to show |
| `GET` | `/assistant/tabs?project=H1` | the chat tabs, oldest first |
| `POST` | `/assistant/tabs` | new tab: `{"project": "H1" or null, "title": "..."}` (null: only the library) |
| `GET` | `/assistant/tabs/{id}` | one tab with its messages (`shown`) |
| `PATCH` | `/assistant/tabs/{id}` | rename: `{"title": "..."}` |
| `DELETE` | `/assistant/tabs/{id}` | delete a tab |
| `POST` | `/assistant/tabs/{id}/messages` | ask: `{"text": "..."}` → `{state, text, actions, positions, tab}` |
| `GET`, `POST` | `/assistant/memory` | the stored facts; add one: `{"text": "...", "project": null}` |
| `DELETE` | `/assistant/memory/{id}` | forget one |
| `GET` | `/projects/{project}/bidder-questions` | the drafted Bieterfragen |
| `PATCH` | `/projects/{project}/bidder-questions/{id}` | change a draft or its `status` (`draft`, `ready`, `discarded`) |

`state` of an answer: `answered`, `waiting` (the tender is still extracting or indexing), `refused` or `error`.
`actions` lists what the assistant changed (e.g. "Kommentar an 03.0040 gespeichert"), so the view can reload that
item; `positions` lists the cards it looked at.
