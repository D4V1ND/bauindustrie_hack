# Data: where it is saved

## Source
- **Original:** Google Drive folder "Challenge A Dataset", shared by the hackathon organisers ("makeathon"):
  https://drive.google.com/drive/folders/1wS4UMVghBL8T5opmZ2UBmBu0kr6U8zIB
- **Local copy:** `data/raw/Challenge A Dataset/`, 44 files, 25.7 MB, downloaded as a zip on 2026-11-20 and unzipped unchanged.
- **Not in git:** `data/raw/` is gitignored and stays local. Tests that need it skip or fail without it, and the
  `Dockerfile` copies it in, so build the image from a checkout that has it.
- **Generated test LVs:** `data/raw/generated/`, written by `python -m lv.samples` from the GAEB files of P1, P2,
  P4 and P5: `<project>_a.xlsx` (columns OZ, Kurztext, Langtext, Menge, Einheit, EP, GP, Art),
  `<project>_b.xlsx` (Pos., Bezeichnung, Menge, ME, Einheitspreis, Gesamtpreis, Positionsart; long text in the row
  below) and `<project>.pdf` (P6's print layout, no prices). P2's Excel files carry prices. Not real files: real
  Excel LVs may name their columns differently.

## Projects

6 projects (P1–P6). Only 3 of them have prices, and each of those 3 also comes as an unpriced version
(useful as an answer key: hide the prices, predict, compare).

| # | Project | Prices? | Files |
|---|---|---|---|
| P1 | 8209.003 mechanical/plant engineering | ✅ €537k | GAEB + PDF, unpriced + priced |
| P2 | Civil works and water pipe, 2023 | ✅ €897k | GAEB + PDF, unpriced + priced |
| P3 | AN24-0305 water main "Fernleitung – Weinsbach" (road + water pipe) | ✅ €989k | GAEB + PDF, unpriced + priced |
| P4 | 501-24-01 road and water pipe | ❌ | GAEB |
| P5 | Stream culvert | ❌ | GAEB + PDF |
| P6 | Training centre + office building | ❌ | 27 PDFs (one per trade) |

Earlier counts listed 8. Two were duplicates and are merged:
- Old "P7" ("2024-001", `Leistungsverzeichnis.pdf` + `LV-Schachtbauwerk.pdf`) is the **unpriced version of P3**: same
  item numbers, texts and quantities, e.g. 3.2.20 "GGG DN 250 verlegen". `LV-Schachtbauwerk.pdf` is its manholes section.
- Old "P8" (`HEM2301-2_Gesamt-LV.pdf`) is the **tender PDF of P1**: contains "8209.003" and 118 of P1's 159 item texts.

## Files per project (all in `data/raw/Challenge A Dataset/`)

| # | Files |
|---|---|
| P1 | `8209.003_Leistungsverzeichnis_leer.X83`, `8209.003_Leistungsverzeichnis_leer.D83` (unpriced, same LV in 2 formats), `HEM2301-2_Gesamt-LV.pdf` (unpriced tender PDF + tender forms), `[ORT_024] LV24069.D86` (priced) |
| P2 | `[NUMMER_001]_leer.X83`, `Leistungsverzeichnis_leer.pdf` (unpriced), `StBALV23040.D86` (priced, texts + prices), `StBALV23040.D84` (prices only), `Abgabe LV.pdf` (priced bid + tender forms) |
| P3 | `Leistungsverzeichnis.pdf` (unpriced), `LV-Schachtbauwerk.pdf` (unpriced, manholes section only), `Angebot AN24-0305.D84` (priced, texts mostly missing), `Angebot AN24-0305.pdf` (priced, full texts), `LV-Positionen.docx` (items grouped by topic) |
| P4 | `501-24-01_GAEB-XML_sas.x83` |
| P5 | `GAEB-[PROJEKT_004].X83`, `LV-Ausdruck-[PROJEKT_004].pdf` |
| P6 | `LV_00_Roh_und_Erdauarbeiten.pdf` … `LV_23_Sanitaertrennwaende.pdf` (27 files) |

## Which file belongs to which project (alphabetical, as in the Drive folder)

| File | Project | What it is | Format | Size |
|---|---|---|---|---|
| `501-24-01_GAEB-XML_sas.x83` | P4 | unpriced LV | GAEB XML 3.1 | 1,561 KB |
| `8209.003_Leistungsverzeichnis_leer.D83` | P1 | unpriced LV (same as .X83) | GAEB 90 | 343 KB |
| `8209.003_Leistungsverzeichnis_leer.X83` | P1 | unpriced LV | GAEB XML 3.2 | 518 KB |
| `[NUMMER_001]_leer.X83` | P2 | unpriced LV | GAEB XML 3.2 | 1,009 KB |
| `[ORT_024] LV24069.D86` | P1 | **priced** (contract award) | GAEB 90 | 381 KB |
| `Abgabe LV.pdf` | P2 | **priced** bid + tender forms | PDF | 1,086 KB |
| `Angebot AN24-0305.D84` | P3 | **priced** bid (texts mostly missing) | GAEB 90 | 98 KB |
| `Angebot AN24-0305.pdf` | P3 | **priced** bid (full texts) | PDF | 337 KB |
| `GAEB-[PROJEKT_004].X83` | P5 | unpriced LV | GAEB XML 3.2 | 97 KB |
| `HEM2301-2_Gesamt-LV.pdf` | P1 | unpriced tender PDF + tender forms | PDF | 2,323 KB |
| `Leistungsverzeichnis.pdf` | P3 | unpriced LV | PDF | 717 KB |
| `Leistungsverzeichnis_leer.pdf` | P2 | unpriced LV | PDF | 501 KB |
| `LV-Ausdruck-[PROJEKT_004].pdf` | P5 | unpriced LV (printout of the .X83) | PDF | 489 KB |
| `LV-Positionen.docx` | P3 | items grouped by topic (not an LV) | Word | 15 KB |
| `LV-Schachtbauwerk.pdf` | P3 | unpriced, manholes section only | PDF | 361 KB |
| `LV_00_Roh_und_Erdauarbeiten.pdf` | P6 | unpriced LV, trade: Roh und Erdauarbeiten | PDF | 1,004 KB |
| `LV_01_Fertigteiltreppen.pdf` | P6 | unpriced LV, trade: Fertigteiltreppen | PDF | 427 KB |
| `LV_02_PVC_Fussbodenbelag.pdf` | P6 | unpriced LV, trade: PVC Fussbodenbelag | PDF | 506 KB |
| `LV_03_Malerarbeiten.pdf` | P6 | unpriced LV, trade: Malerarbeiten | PDF | 623 KB |
| `LV_04_Porenbetonfassade.pdf` | P6 | unpriced LV, trade: Porenbetonfassade | PDF | 455 KB |
| `LV_05_[FIRMA_096].pdf` | P6 | unpriced LV, trade: Mobile Trennwand (file name anonymised; section "Schreinerarbeiten") | PDF | 407 KB |
| `LV_06_Kautschukboden.pdf` | P6 | unpriced LV, trade: Kautschukboden | PDF | 531 KB |
| `LV_07_Teppichboden.pdf` | P6 | unpriced LV, trade: Teppichboden | PDF | 461 KB |
| `LV_08_Fliesen_und_Natursteinarbeiten.pdf` | P6 | unpriced LV, trade: Fliesen und Natursteinarbeiten | PDF | 602 KB |
| `LV_09_Porenbetondach.pdf` | P6 | unpriced LV, trade: Porenbetondach | PDF | 444 KB |
| `LV_10_Stahlbetonfertigeile_Stuetzen_Binder.pdf` | P6 | unpriced LV, trade: Stahlbetonfertigeile Stuetzen Binder | PDF | 465 KB |
| `LV_11_Stahlbetonfertigeile_Sandwichsockel.pdf` | P6 | unpriced LV, trade: Stahlbetonfertigeile Sandwichsockel | PDF | 446 KB |
| `LV_12_Dachabdichtung.pdf` | P6 | unpriced LV, trade: Dachabdichtung | PDF | 504 KB |
| `LV_13_ELT.pdf` | P6 | unpriced LV, trade: ELT | PDF | 901 KB |
| `LV_14_Sanitaeranlage.pdf` | P6 | unpriced LV, trade: Sanitaeranlage | PDF | 927 KB |
| `LV_14a_Betonkernaktivierung.pdf` | P6 | unpriced LV, trade: Betonkernaktivierung | PDF | 541 KB |
| `LV_14b_Dachentwaesserung.pdf` | P6 | unpriced LV, trade: Dachentwaesserung | PDF | 514 KB |
| `LV_14c_Industriebodenheizung.pdf` | P6 | unpriced LV, trade: Industriebodenheizung | PDF | 466 KB |
| `LV_15_Abdichtung_Aussenwaende.pdf` | P6 | unpriced LV, trade: Abdichtung Aussenwaende | PDF | 416 KB |
| `LV_15a_Abdichtung_Bodenplatte_Buero.pdf` | P6 | unpriced LV, trade: Abdichtung Bodenplatte Buero | PDF | 415 KB |
| `LV_17_Trockenbau.pdf` | P6 | unpriced LV, trade: Trockenbau | PDF | 545 KB |
| `LV_18_Estrich.pdf` | P6 | unpriced LV, trade: Estrich | PDF | 527 KB |
| `LV_19_Sektionaltore_Stahltueren.pdf` | P6 | unpriced LV, trade: Sektionaltore Stahltueren | PDF | 557 KB |
| `LV_20_Bodenbelagsarbeiten.pdf` | P6 | unpriced LV, trade: Bodenbelagsarbeiten | PDF | 602 KB |
| `LV_21_Natursteinarbeiten.pdf` | P6 | unpriced LV, trade: Natursteinarbeiten | PDF | 575 KB |
| `LV_22_Schreinerarbeiten.pdf` | P6 | unpriced LV, trade: Schreinerarbeiten | PDF | 548 KB |
| `LV_23_Sanitaertrennwaende.pdf` | P6 | unpriced LV, trade: Sanitaertrennwaende | PDF | 453 KB |
| `StBALV23040.D84` | P2 | **priced** (bid, prices only, no texts) | GAEB 90 | 45 KB |
| `StBALV23040.D86` | P2 | **priced** (contract award, texts + prices) | GAEB 90 | 369 KB |

## What's inside an LV

An LV is a numbered list of line items (*Positionen*), grouped into sections. Each item is a product, a piece of work
or a lump sum, and each has the same fields.

### Fields of each line item

| Field | German | Meaning | Example (P1 `[ORT_024] LV24069.D86`, item 05.01.36) |
|---|---|---|---|
| Item number | **OZ** (*Ordnungszahl*) | Position in the structure: section.subsection.item | `05.01.36` |
| Short text | **Kurztext** | One-line name of the item | PER000115-Flansch DN 150, PN 10 (stainless steel flange, 150 mm, pressure class 10; "Edelstahl" was anonymised to PER000115) |
| Long text | **Langtext** | Full specification: material, size, standard, what's included, how it's measured and billed | Glatter Flansch, DN 150, PN 10 sonst wie vor, als Zulage zu vorbeschriebener Rohrleitung. (plain flange, otherwise as before, as a surcharge on the pipe described above) |
| Quantity | **Menge** | How much is needed | 175 |
| Unit | **ME** (*Mengeneinheit*) | St (pieces), m, m², m³, kg, h (hours), psch (lump sum) | St |
| Unit price | **EP** (*Einheitspreis*) | Price per unit. Empty in an unpriced LV: that's what the estimator fills in | 175,40 € |
| Total price | **GP** (*Gesamtpreis*) | Quantity × unit price. Empty in an unpriced LV | 30.695,00 € |
| Price components | **Preisanteile** | Unit price split into labour / material / equipment / other. Only in P1, and there everything sits in "labour" | Löhne 175,40 · Stoffe 0,00 · Geräte 0,00 · Sonstiges 0,00 |

### Around the items

| Part | German | What it is |
|---|---|---|
| Header | **Kopf** | Project name and number, client, dates, currency |
| Sections | **Titel / Bereich** | Grouping, e.g. `05` Rohrleitungen (pipes) → `05.01` Rohrleitungen aus nichtrostendem Stahl (stainless steel pipes) |
| Section total | **Summe** | Sum of a section. Only in priced LVs |
| Notes | **Vorbemerkungen / Hinweistext** | Text without a price: site conditions, rules that apply to all items |

### Three kinds of items

All three have the same fields; they differ in what is measured and paid (the unit).

- **Product (supply and install):** "Flansch DN 150", 175 pieces (P1, item 05.01.36).
- **Work:** "Boden Graben … lösen" (dig a trench), 30 m³ (P2, item 04.01.0002).
- **Lump sum or hours:** "Baustelleneinrichtung" (site setup), 1 lump sum (P1, item 01.01.1); "Stundenlohn" (daywork), billed per hour.
