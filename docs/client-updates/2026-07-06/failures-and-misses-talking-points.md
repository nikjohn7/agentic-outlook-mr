# Talking points — failures & misses (internal, for Nikhil)

_Plain-language answers for when Kyle asks "what's in the failures file?" or
"why didn't you find call X?" Not client-facing as a document, but every
sentence here is safe to say or paste._

## The one-liner that frames everything

> "A failure is not a mistake we shipped — it's a call the system drafted and
> then refused to stand behind, with the reason logged. Most failures are
> housekeeping (duplicates from reading two documents together); the rest are
> the safety checks doing their job."

## What's in the failures files

**Second test (108 rows) — four buckets:**

1. **Duplicates — ~62 rows, the majority.** When a firm's two documents are
   read together (T. Rowe Price's monthly + UK view; Wellington's quarterly +
   monthly), both documents often state the same view. We keep one copy and
   log the other as a duplicate. These aren't errors — they're evidence the
   two documents genuinely agree.
2. **Dial-graphic rows — 20 rows.** Two firms publish their views as dial
   graphics on web pages. The first pass rejected these because the strict
   text check couldn't see inside a picture. We then had the independent
   reviewer verify each one against the page image: nearly all were confirmed
   correct. Three were new calls (now in the results file); the rest turned
   out to duplicate calls already captured from the firm's paired document.
   Net effect: the safety check was too strict, we fixed it, and no correct
   call was lost.
3. **Conflicts resolved — 13 rows.** The firm's two documents disagreed
   (e.g., an older monthly dial vs a newer quarterly view). The system kept
   the more current or more specific reading and logged the losing one here,
   so an analyst can audit the choice.
4. **Not verified — 10 rows.** The system drafted a call but could not
   confirm the exact supporting quote on the page (usually hard-to-read page
   layouts). Rather than trust an unverifiable quote, it rejects the call.
   This is deliberate: we'd rather miss a call than fabricate evidence.

**Pilot (18 rows):** same buckets, smaller numbers — mostly duplicates from
the paired Schroders and J.P. Morgan documents, a few unverifiable quotes,
and one item whose category doesn't exist in the agreed asset-class list.

## Why the results "miss" some reference calls

> "When our output doesn't show a reference call, it's almost never that the
> system read the document wrong. It's one of four understandable reasons."

1. **The material isn't behind the shared link (the BlackRock story).** The
   reference calls for BlackRock draw on the firm's wider publications —
   bond, credit, and multi-asset dial pages. The link we were given is an
   equity-only document. The system reads exactly what's behind the link, so
   10 of its 13 BlackRock "misses" cite content that simply isn't in that
   document. Every call the system did make from that document checked out.
   → This is the "source scope" question on the questions sheet: should a
   source ever mean more than the one link?
   (The pilot had the same situation with PIMCO — the link initially pointed
   at a 2-page summary of a much longer outlook. Once the full document was
   used, the misses disappeared.)
2. **Same view, neighbouring shelf.** Sometimes the system records the same
   view under an adjacent category — "US Duration" where the reference says
   "US Treasuries", or a green-bond category where the reference says
   "Europe Fixed Income". The call and direction are right; the label
   differs. → This is the category-naming question on the questions sheet.
3. **Judgment-depth calls.** Some reference calls require an analyst-style
   leap (a macro comment turned into an allocation call). The system makes
   only clearly-grounded single-step inferences, and marks even those for
   review. Deeper leaps are deliberately out of scope until confirmed.
   → Also on the questions sheet.
4. **A small number of genuine gaps — and what we did about them.** Two worth
   knowing by name:
   - *BlackRock mega-cap tech:* the document argues both "Asian chipmakers
     attractive" and "expensive US mega-cap tech vulnerable". The system
     caught the first and missed the second. We've since added a rule: when a
     document rotates out of X into Y, record both sides.
   - *"Reduced back to neutral" (two EM-debt rows):* the system recorded the
     direction of the move (underweight) instead of where the firm ended up
     (neutral). Rule added: land on the resulting stance.
   Both fixes are in and will be validated on the next run.

## Pilot-specific: if Kyle compares the pilot CSVs

- **Aberdeen looks weakest in the pilot — most of that is category rollup,
  not misreading.** The system folded country-level views (Taiwan, Korea,
  Malaysia) into one "Asia Equities" call. That behaviour has been fixed:
  in the second test, named countries land on their own categories (you can
  see Taiwan and South Korea rows in the second-test results).
- **J.P. Morgan's numbers reflect breadth, not errors** — the system read the
  full paired dial grid and produced more calls than the reference list
  enumerates; the directions agree where both made a call.

## Two proof points to keep in your pocket

- Every kept call's supporting quote was re-verified against the source
  after the run: **142/142 passed in the second test, 106/106 in the pilot.**
- Where the system and the reference disagreed on direction in the second
  test (5 categories), independent re-checking found: 1 was our reference's
  transcription error, and most of the rest are convention differences (e.g.
  table vs text) — the kind of thing the questions sheet is meant to settle.
