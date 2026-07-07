# Roadmap — client decisions, v1 wave, v1.2 / v2 backlog

_Created 2026-07-07. Source: the client's written answers (received 2026-07-06)
to the questions sheet, plus the follow-up discussion with Nikhil. This file is
the durable record of what was decided, what ships before the 37-source run,
and what is explicitly deferred — so v1.2/v2 work can be picked up later
without re-deriving the reasoning._

## Client decisions (binding for v1)

1. **Source links are final.** The 37-source list will contain ultimate
   destinations (PDF or final HTML). No manual link-resolution step needed.
2. **Grouped sources cannot be pre-declared by the client.** He relies on the
   system to find same-firm relationships. His suggested design (two-pass:
   run all, then cross-reference same-firm overlap of asset class + call +
   reasoning, with a dual-confidence audit trail) is adopted as the **v1.2
   firm-reconcile stage** (below). For v1 the system ships a lighter
   three-layer version (see "v1 pre-37 wave").
   Materially: the 37-source list is only ~14 distinct firms — Aberdeen ×7,
   Invesco ×6, State Street ×5, Columbia Threadneedle ×3, Impax ×2, PGIM ×2 —
   so same-firm overlap handling is a hard prerequisite for the 37-run.
3. **Call legend confirmed:** `O` / `N` / `U` / `UNCERTAIN`.
4. **Forecast-number changes:** no benchmark exists; direction of a value
   change is context-dependent. Client explicitly says do NOT build
   wider-context machinery for this if it risks accuracy. Current conservative
   materiality gate stays as-is.
5. **Stated vs implied:** stated views always win — even against a challenging
   inference. Implied calls must always be analyzed and confirmed, never
   silently dropped. When an implied call challenges a stated one, the system
   flags it as a *recommendation* with its reasoning (including when it
   suggests changing a previously made call); it does not replace the stated
   row in v1. The confidence-based override path (implied may override a
   low-confidence stated view) is **v1.2**.
6. **Specificity: calls stay specific for now** (named country → its own
   leaf). Cross-firm volume-based broad-vs-specific review is **v1.2**; the
   output already carries the broader taxonomy columns (`Asset Class
   Category` / `Asset Class` / `Canva Groupings`) a human can aggregate on.
7. **Dials/charts:** clear visual/table evidence of a call is recorded as a
   call and can be high confidence *regardless of commentary tone* — unless
   the commentary explicitly addresses the same leaf. Commentary about a
   narrower sub-asset (e.g. gold mining vs gold) does not override the chart.
   Full dial-dashboard policy (every dial → row? which levels?) is **v2**.
8. **Source scope: the link only.** Reference calls never draw on firm
   material beyond the provided link; at most, other same-firm sources
   *within the provided source set* may be considered (which the v1 scout +
   cross-check and the v1.2 reconcile stage cover by construction).
9. **Combined two-document rows:** keep each document's title and date,
   pipe-separated (`|`) in `Source` and `Date`. Titles/URLs join every member;
   the `Date` join skips blanks (see decision 11 — dates are often blank), so a
   grouped row never shows a stray `| ` around an empty date.
10. **Reference capture** (page number + exact phrase for PDF; exact
    phrase(s) for HTML) and **confidence thresholds**: confirmed as-is;
    client defers threshold policy to us.
11. **Dates come from the document only (2026-07-07).** The client stated the
    dates in the source CSV are unreliable and must not be used. The `Date` on
    every output row is extracted from the document itself, as strict
    `DD/MM/YYYY`, or left blank — never taken from the source list. HTML uses
    the stated publication date; PDF accepts only a full worded date near the
    top of page 1 (a bare month-year → blank; numeric forms and file metadata
    are not used). This supersedes the earlier document-date *fallback* (which
    only filled a blank CSV date); the CSV date is now always discarded in
    ingestion. Scout and the group resolver still read the CSV date at metadata
    time (pre-ingest) — out of scope; the change is that ingestion outputs never
    carry it.

## v1 pre-37 wave (in flight)

Three independent instruction sets, one agent session each (files in `tmp/`,
gitignored):

1. `tmp/instructions-1-checker-context.md` — checker receives the source's
   rolling memory; implied-call verification rule (stated-beats-implied,
   challenges logged as flagged recommendations); dial-vs-commentary
   convention line.
2. `tmp/instructions-2-scout-groups.md` — pre-run companion scout: a light
   metadata-only agent pass that proposes read-together groups among
   same-firm sources and emits a `--group-notes`-compatible file. Feeds the
   existing group-ledger machinery; zero pipeline changes.
3. `tmp/instructions-3-firm-crosscheck.md` — bare-bones post-run firm
   cross-check: deterministic join on (firm, sub-asset leaf) across run
   outputs; same-view overlaps auto-marked; conflicting groups reviewed by
   one cheap agent pass into a "similar" report with a needs-human flag.
   Purely additive report; never modifies run outputs.
4. `tmp/instructions-4-reader-summaries.md` — the reader-summaries feature
   (see "Reader summaries" section below): per-source digest, deterministic
   reconciled-calls stopgap over the crosscheck verdicts, per-firm page
   synthesis, `python-docx` binder, smoke run on the frozen second test.
   Fully unattended — `needs_human` conflicts surface as in-page divergence
   notes, nothing blocks.

Layering rationale: the scout catches genuine companions *before* the run so
in-run assembly/arbitration resolves overlap with full context (as it did for
the T. Rowe Price and Wellington pairs in the second test); the cross-check is
the post-hoc safety net for everything the scout conservatively left
ungrouped, and it works across runs (the 20-items-per-run cap means the 37
sources span at least two runs).

## Production batches (context, 2026-07-07)

The client sends the ~37-source list first, then a second CSV of roughly 70
more sources. **The final outputs of both batches are combined into one
deliverable.** Consequences: the post-run firm cross-check (and later the
v1.2 firm-reconcile) must run across ALL batch outputs, not per batch —
`src.crosscheck --outputs` accepting multiple output files is the designed
entry point; same-firm sources may straddle batches as well as runs; and the
one-page reader summaries (below) are generated per run but bound into a
single Word document at the combine step.

## Reader summaries (client request, 2026-07-07)

The client compiles a reader-facing document of one-page firm summaries
(example: `tmp/pdfs/Recons - 2026 Investment Outlook Summaries - Markets
Recon - Jan 2026 1.pdf`) and asked for a Word-document output of a one-page
summary per document analysed, to feed that compilation. Assessment:
**add-on, not a new build**, but the example's editorial density (themed
sections, named specifics, figures) exceeds what the call-extraction
artifacts retain, so:

- **One page per FIRM — confirmed 2026-07-07.** Two-stage design:
  - **Stage 1, per-source digest (runs with each run):** one LLM call per
    source — reads the native document (checker-style visual read),
    grounded by that source's kept calls (quote-verified) and `memory.md`,
    emits a structured digest (themes, named specifics, figures, stance per
    asset class, title/URL). The only stage that reads documents.
  - **Stage 2, per-firm synthesis (once, at the batch-combine step):** one
    LLM call per firm — receives all that firm's stage-1 digests plus the
    firm's RECONCILED final calls (post-crosscheck, never raw per-run
    rows, so the narrative cannot contradict the combined CSV), writes the
    one-page narrative (framing paragraph, themed sections, Sources list
    linking every document). Reads digests only — cheap even for Aberdeen
    ×7. Single-source firms still pass through stage 2 for a consistent
    voice.
  - Rationale for two stages over one-call-per-firm: a firm's sources can
    straddle runs and batches, so document reading must happen
    incrementally per run while firm-page writing happens once at the end;
    also avoids stuffing 7 PDFs into one degraded-attention call.
- **Word assembly is deterministic**: `python-docx` merge of all per-firm
  pages into one file (page break per firm), done once at the
  batch-combine step. POC delivers clean structure (title, headings,
  bullets, hyperlinks); client branding is applied by their team, not us.
- Honesty note: a reader-facing narrative is the highest hallucination-risk
  artifact in the system. v1 mitigation is grounding (calls + memory in the
  prompt, instruction to state only what the document says) + analyst skim;
  a verification pass over summaries is the v1.2 item below.

## v1.2 backlog

1. **Full post-run firm-reconcile stage with dual-confidence audit trail**
   (the client's own design — the priority v1.2 item). Replaces/extends the
   bare-bones cross-check: after all runs complete, cross-reference same-firm
   rows on asset class + call + reasoning; dedupe with provenance; for
   conflicts, run a second confidence process and surface BOTH the original
   per-run confidence and the reconcile confidence, so either the system can
   make a final call or the row is escalated to a human. Design notes: reuse
   `src/eval.py` join normalization; arbiter-style comparison of the two
   rows' commentary; output a reconciled master file + audit columns
   (first-pass confidence, reconcile verdict, reconcile confidence,
   needs_human). Seed: `src/crosscheck.py` (v1 wave, instruction set 3).
2. **Stated-view override path.** Allow a high-confidence implied call to
   override a *low-confidence* stated view, with the override reason and the
   suggested change recorded (client answer 5's second half). Requires the
   v1 stated-beats-implied plumbing (instruction set 1) as its base; the
   override rule must be deterministic (band thresholds), not an LLM choice.
3. **Cross-firm volume review over taxonomy columns** (client answer 6):
   given all firms' outputs, compare specific-leaf coverage against broader
   groupings (e.g. Taiwan Equities ×1 firm vs Asia Equities ×10 firms) and
   emit a broad-vs-specific advisory column ("fallback" column) so a human
   can decide whether to block calls together. Analytics layer over existing
   output columns; no row-schema change expected.
4. **Same-firm cross-source call linking as discovery** (replacing analyst
   group-notes): agent looks across same-firm sources for potential
   call-linkage — whether calls in one document support, extend, or should be
   read together with another's — and can propose new calls from the linkage.
   Seed: the v1 scout (instruction set 2) does the metadata-level version.
5. **Implied-call cross-file verification, deepened:** verify an implied call
   against the whole file (and grouped companions) to see whether phrases
   elsewhere link to or support it — possibly a checker extension, possibly a
   separate agent (decide by how much it bloats the checker call). Seed: the
   v1 checker-sees-memory change (instruction set 1).
6. **Near-leaf / fuzzy matching in the cross-check** (v1 joins on exact
   firm+leaf only): catch "US Duration" vs "US Treasuries"-style adjacent
   overlaps across same-firm sources.
7. **Summary verification pass:** spot-check each one-page reader summary's
   claims (names, figures, quotes) against the source document,
   checker-style, before it ships — the reader summaries are the
   highest-hallucination-risk output and v1 relies on grounding + analyst
   skim only.

## v2 backlog

1. **Full dial-dashboard policy** (client answer 7): for full dial grids,
   decide whether every dial becomes a row, which levels are recorded
   (main vs sub-asset rows), and the priority sequence when chart and
   commentary both speak — including detecting that commentary is about a
   sub-sub-asset (gold mining, not gold) and therefore does not override the
   chart. The v1 wave ships only the single convention line; the systematic
   priority-sequence analysis is here.
2. **Forecast-delta wider-context method** (client answer 4): a wider-based
   context consumption method to interpret whether a value change is
   positive/negative — explicitly deferred by the client, and only to be
   attempted if it does not compromise accuracy.

## Not doing (decided against)

- Frequency-weighted specificity inside a single document (region-only when a
  country is mentioned once) — superseded by client answer 6: calls stay
  specific; broad-vs-specific is handled by the v1.2 volume review, not by
  suppressing specific calls at extraction time.
- Auto-grouping all same-firm sources at ingest (option A of the grouping
  design): a 7-document group ledger is heavy, order-sensitive, and cannot
  span the 20-items-per-run cap. Rejected in favor of scout (conservative
  pairs only) + post-run reconcile.
