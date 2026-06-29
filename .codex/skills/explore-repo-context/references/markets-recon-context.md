# Markets Recon Repo Context

Use this reference only for this project. It identifies the current authoritative files to read before implementation decisions.

## Read First

Read these files in order:

1. `CLAUDE.md` - project mission, constraints, workflow target, and definition of done.
2. `WORKBOOK_SCHEMA.md` - canonical workbook reference; prefer this over older notes if conflicts appear.
3. `POC_CONTEXT.md` - POC purpose, business problem, scope, output requirements, and open questions.
4. `allocator-pro-poc-spec.html` - client-facing spec and commitments.
5. CSV headers and small samples from `excel-file/`:
   - `Asset Class List - Locked.csv`
   - `Target Ingestion List.csv`
   - `Target Output.csv`

## Current Authorities

- The workbook CSVs in `excel-file/` are the primary source of truth.
- `Asset Class List - Locked.csv` is the locked taxonomy. Use labels exactly.
- `WORKBOOK_SCHEMA.md` is the canonical written schema reference.
- `Target Output.csv` defines output shape only; it is not a benchmark set.
- Older notes that conflict with the workbook should lose unless the user says otherwise.

## Project Constraints To Preserve

- Do not copy or assume the old `initial-test` architecture.
- Do not start by creating a production API, database schema, or front end.
- Do not invent taxonomy labels.
- Use `uncertain` when evidence is weak rather than forcing a neutral call.
- Include citation commentary and a page or source locator where applicable.
- Compute confidence from explicit documented checks, not LLM self-confidence.
- Process no more than 20 input items per run.
- Produce one reviewable output file per run.

## Useful Local Facts

- The target source list contains 37 sources, so the run cap implies at least two runs.
- Output is keyed on `Sub-Asset Class`; there is no topic column in the locked taxonomy.
- `Asset Class Category`, `Asset Class`, and `Canva Groupings` are deterministic lookups once `Sub-Asset Class` is selected.
- `View` codes are `O`, `N`, `U`, and `UNCERTAIN`, pending client sign-off.
