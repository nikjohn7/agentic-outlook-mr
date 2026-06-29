# Workbook Schema

Status date: 2026-06-27

This file documents the Markets Recon workbook that drives the POC. The workbook
arrived as three CSVs (one per sheet) in `excel-file/`. This is the canonical
reference for taxonomy, target sources, and output shape. Where older notes
conflict with this file, prefer this file (and the CSVs it describes).

## Sheet / CSV inventory

| CSV (`excel-file/`)              | Sheet role                         | Data rows |
| -------------------------------- | ---------------------------------- | --------- |
| `Asset Class List - Locked.csv`  | Canonical taxonomy (source of truth) | 396       |
| `Target Ingestion List.csv`      | Source list to process             | 37        |
| `Target Output.csv`              | Output shape — one worked example  | 1         |

"Locked" in the taxonomy sheet name signals it is the authoritative, frozen
taxonomy. Use its labels **exactly** — no remapping, no invented labels.

## 1. Taxonomy — `Asset Class List - Locked.csv`

Columns:

| Column                | Meaning                                                        | Example                                  |
| --------------------- | ------------------------------------------------------------- | ---------------------------------------- |
| `Number`              | Row id, 1–396                                                 | `396`                                    |
| `Sub-Asset Class`     | The leaf label a call is made against                         | `US Treasuries`                          |
| `Asset Class Category`| Mid-level grouping                                            | `Fixed Income - Sovereigns North America`|
| `Asset Class`         | Top-level class                                               | `Fixed Income`                           |
| `Canva Groupings`     | Parallel presentation grouping (not a level of the tree)      | `Fixed Income - Rates & Sovereigns`      |

Structure: **Asset Class → Asset Class Category → Sub-Asset Class**, with
`Canva Groupings` as a separate presentation cut.

- **396** sub-asset classes (all distinct).
- **31** asset-class categories.
- **4** top-level asset classes:

  | Asset Class   | Sub-asset count |
  | ------------- | --------------- |
  | Alternatives  | 147             |
  | Equities      | 119             |
  | Fixed Income  | 115             |
  | Currencies    | 15              |

- **12** Canva Groupings: Alts - Hedge Funds; Alts - Private Equity & Credit;
  Alts - Real Assets; Alts - Real Assets Geo; Asset Allocation; Commodities;
  Currencies; Equities - Geography; Equities - Sectors, Factors, Thematics;
  Fixed Income - Credit & Specialty; Fixed Income - General;
  Fixed Income - Rates & Sovereigns.

**No "Topic" column exists.** Earlier notes (`POC_CONTEXT.md`, `CLAUDE.md`,
client spec) treat "topic" as a first-class taxonomy field. The locked taxonomy
and the output sheet have no such field. See Reconciliation below.

## 2. Target sources — `Target Ingestion List.csv`

Columns: `Id, Firm, Title, Published At, Source Link` (a 6th column repeats the
source link).

- **37** source rows, **18** distinct firms (Aberdeen, AEW, AllianceBernstein,
  CI Global, Columbia Threadneedle, Eastspring, Federated Hermes, Guggenheim,
  Impax, Invesco, J.P. Morgan, Janus Henderson, KKR, Lord Abbett, Manulife,
  PGIM, State Street, T. Rowe Price).
- Source-type mix: ~**13** PDF URLs, ~**24** HTML pages.
- **37 > the 20-items-per-run cap → at least 2 runs required.**

Edge cases to handle in ingestion:

- **11** rows have a blank `Id`.
- One AllianceBernstein link is `read://https_...?url=...` (reader-mode prefix).
- One CI Global link is a `seismic.com` URL with `PLUSSIGN` / `___` substituted
  for `+` / other characters — needs decoding before fetch.

## 3. Output shape — `Target Output.csv`

This sheet contains **one** worked example row. It defines the **output schema**
and call-code convention only — it is **not** a benchmark/ground-truth set to
reproduce (confirmed with the user). A fuller analyst-reviewed ground-truth set
should still be requested from the client.

Columns:

| Column                 | Meaning                                              |
| ---------------------- | --------------------------------------------------- |
| `Firm`                 | Source firm                                         |
| `Date`                 | Source publication date                             |
| `Source`               | Source title                                        |
| `URL`                  | Source URL                                          |
| `Sub-Asset Class`      | Taxonomy leaf the call is made against              |
| `Asset Class Category` | Deterministic lookup from taxonomy                  |
| `Canva Groupings`      | Deterministic lookup from taxonomy                  |
| `Asset Class`          | Deterministic lookup from taxonomy                  |
| `View`                 | Call code (see legend)                              |
| `Full Commentary`      | Evidence / citation text supporting the call        |

**Deterministic join:** once `Sub-Asset Class` is chosen, the
`Asset Class Category`, `Asset Class`, and `Canva Groupings` columns are a pure
lookup against the locked taxonomy. The model only decides the **sub-asset class**
and the **View**; the other three taxonomy columns are filled by lookup.

**`View` legend** (single-letter codes; pending client sign-off):

| Code        | Call        | One-hot (UW, N, OW) |
| ----------- | ----------- | ------------------- |
| `O`         | overweight  | 0, 0, 1             |
| `N`         | neutral     | 0, 1, 0             |
| `U`         | underweight | 1, 0, 0             |
| `UNCERTAIN` | uncertain   | 0, 0, 0             |

The example row uses `View = O` for `Taiwan Equities`. Its source/URL do not
match any ingestion row, confirming the sheet is illustrative shape, not data.

## Reconciliation / open decisions

- **No Topic field.** Drop "topic" as a required output column. The output is
  keyed on sub-asset class, not topic. Treat topic (if used at all) as optional
  internal context, not an output field.
- **`Target Output` is format-only.** Not a benchmark. Request a real
  analyst-reviewed ground-truth set from the client for evaluation.
- **View legend** (O/N/U/UNCERTAIN) is to be confirmed with the client.
- **Run batching.** 37 sources must be split across ≥2 runs (≤20 each).
