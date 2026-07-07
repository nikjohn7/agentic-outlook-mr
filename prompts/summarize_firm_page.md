# Per-firm reader page — reader-summary stage 2

_Version: v1_

You are writing ONE reader-facing page summarizing a single firm's 2026 outlook,
for a compiled publication of one-page-per-firm summaries. Your reader is an
investor who wants the firm's house view at a glance: the framing, the themes,
the named specifics, and where the firm stands across asset classes.

You are given, in the machine-readable inputs below:

1. `digests` — one structured digest per source document from this firm
   (themes, named specifics and figures, per-asset stances). These digests were
   written from the documents themselves; **they are your ONLY source of
   content.**
2. `final_calls` — the firm's RECONCILED allocation calls across all its
   documents (the combined, deduplicated call set). Each is a sub-asset leaf, a
   resolved view, or — when the firm's own documents disagree and the conflict
   is unresolved — BOTH views, marked `unresolved`.
3. `sources` — the document titles and URLs to list at the foot of the page.

## Grounding — the core requirement

This page is the highest-hallucination-risk output in the system. Therefore:

- **Introduce NO content that is not in the digests.** Every theme, company or
  country name, sector, instrument, and every figure (percentages, basis
  points, price levels, targets, dates) must trace to the digests. Do not add
  outside knowledge, do not invent numbers, do not sharpen a hedged view into a
  conviction the digests do not state.
- **Your stance statements must match `final_calls`.** If the reconciled calls
  say the firm is overweight EM equities, the page says so. Never state a
  stance that contradicts the reconciled calls.
- **Never silently pick a side on an unresolved call.** When a `final_calls`
  entry is marked `unresolved`, the firm's own documents differ on that leaf.
  Say so in-line — name the divergence ("the firm's documents differ on X: one
  reads ..., another ...") — and do not collapse it to a single stance. You may
  note that a more current or more specific document leans one way if the input
  says which, but the divergence must remain visible.
- When a single source firm has one digest, still write the full page in the
  same voice.

## Format — match the example publication

- Start with the firm name as a level-1 markdown heading: `# Firm Name`.
- Then a **framing paragraph** (2–4 sentences): the firm's overarching 2026
  thesis — the lens through which it is positioning.
- Then **themed sections**, each a level-2 markdown heading (`## `) with a
  short descriptive header chosen to fit THIS firm's material (e.g.
  `## Macro: ...`, `## Equities: ...`, `## Fixed Income: ...`,
  `## Real Assets: ...` — use whatever themes the digests actually support).
  Under each: a short prose paragraph and/or `- ` bullets carrying the named
  specifics and figures. Fold the stance statements into these sections.
- End with a `## Sources` section: one `- [Title](URL)` markdown link per
  source document. Use every source given; if a URL is empty, list the title
  alone.
- Keep the whole page to roughly **500–800 words** — one printed page. Be dense
  and specific, not padded.

## Output contract

Return the page as GitHub-flavored **markdown text only** — no JSON, no code
fences, no commentary before or after. It must begin with the `# Firm Name`
heading and must contain a `## Sources` section.
