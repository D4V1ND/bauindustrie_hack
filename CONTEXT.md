# LV Estimating

Helps a construction contractor's estimator summarise and price a tender, using only the company's own past projects.

## Language

### Documents

**LV** (*Leistungsverzeichnis*):
The client's numbered list of everything to be built or supplied in one project, grouped into sections.
_Avoid_: Bill of quantities, tender list, BoQ

**Priced LV**:
An LV from a past project in which the estimator's unit prices are filled in.
_Avoid_: Old LV, historical LV, reference LV

**New LV**:
An LV from a current tender with quantities but no prices yet.
_Avoid_: Empty LV, blank LV, unpriced LV

**Section** (*Titel*):
A named group of line items inside an LV, possibly nested (e.g. "05 Rohrleitungen" → "05.01 …").
_Avoid_: Chapter, group, category

### Line items

**Line item** (*Position*):
One numbered entry of an LV: a product, a piece of work or a lump sum, with a quantity and a unit.
_Avoid_: Position, row, entry

**Item number** (*Ordnungszahl, OZ*):
The line item's number as printed in the LV, showing its place in the sections (e.g. `05.01.36`).
_Avoid_: Position number, ID

**Short text** (*Kurztext*):
The one-line name of a line item.

**Long text** (*Langtext*):
The full specification of a line item: material, size, standard, what is included.

**Unit price** (*Einheitspreis, EP*):
The price for one unit of a line item, set by the estimator.

**Total price** (*Gesamtpreis, GP*):
Quantity × unit price of one line item.

**Optional item** (*Bedarfsposition*):
A line item the client may or may not call for; it is priced but does not count in the project total.
_Avoid_: Contingency item, provisional item

**Normal item** (*Normalposition*):
A line item that will be carried out and counts in the project total.

### Pricing

**Past project**:
A finished tender whose priced LV is available as a source of prices.
_Avoid_: Old project, historical project, reference project

**Price library**:
All line items of all past projects' priced LVs taken together, each still knowing which project it came from.
_Avoid_: Database, price database, price list, catalogue

**Price prediction**:
What the tool records for a new line item's empty unit price: how it looked the item up, the past line items it found, and, only when the match is strict, their prices with range, median and mean. The estimator sets the final price.
_Avoid_: Estimate, suggested price, quote

**Reference**:
A past line item shown next to a new line item as evidence, without a price being proposed from it, because it differs in something that changes the price.
_Avoid_: Weak match, low-confidence price

**Copied price**:
A price prediction taken unchanged from an exact match in the price library.

**Adjusted price**:
A price prediction calculated from similar matches by a named rule (e.g. interpolated between a smaller and a larger size), stored with the rule, its inputs and the reason.
_Avoid_: Interpolated price, estimated price

### Levels of an LV overview

**Trade** (*Gewerk*):
The branch of construction work an item type belongs to, taken from one fixed list (e.g. earthworks, pipework).
_Avoid_: Category, department, discipline

**Item type**:
The general kind of thing or work a line item is, regardless of size, material or colour (e.g. flange, site fence); each belongs to exactly one trade.
_Avoid_: Sub-category, product, style, kind

### Matching

**Surcharge item** (*Zulage*):
A line item that is an add-on price on top of another line item (e.g. "Zulage für Flansch"). It is only compared with other surcharge items.
_Avoid_: Extra, add-on

**Repeat item** (*"wie vor, jedoch …"*):
A line item whose text only says how it differs from an earlier line item. It is read together with the text of the item it refers to.
_Avoid_: Reference item (a **reference** is something else)

**Exact match**:
Two line items whose short text, long text and unit are identical, ignoring letter case, spacing, line-break hyphens and the spelling of an anonymised word.
_Avoid_: Duplicate, 1:1 match, identical item

**Group**:
All line items that are exact matches of each other; their quantities may be added up. It is the narrowest level, below item type.
_Avoid_: Cluster, bundle, category, sub-sub-category

**Candidate**:
A line item from the price library that has the same unit and the same surcharge status as a new line item and a similar text, offered to the judge as a possible price source.
_Avoid_: Hit, result, neighbour

**Judge**:
The AI model that compares the text of a new line item with the texts of its candidates. It never sees or states a price.
_Avoid_: Matcher, classifier

**Verdict**:
The judge's answer for one candidate: `same` (everything that changes the price matches), `size_only` (exactly one numeric size differs), `type_only` (same kind of thing, but material, scope or more than one attribute differs) or `different`.

**Similar match**:
A candidate with the verdict `same`: the same item in kind, material, scope, size and class, though its text is not identical. Its price may be used.
_Avoid_: Fuzzy match, near match
