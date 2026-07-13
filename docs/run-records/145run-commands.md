# 145-batch commands (second client list, 2026-07-13)

_Source of truth: `client-runs/runs-13072026-145rows/additional-data (with
local_file).csv` — 140 rows (client's 145 minus 2 exact GlobalX duplicates and
3 dropped rows: Victory Capital wrong-year 2025 content, Angel Oak insights
listing page, BNP Wealth cookie-wall HTML whose brochure-PDF row is kept), 38
local files under `manual-sources/`. Preflight: full sweep `preflight/`,
failed-link re-sweep `preflight-retry/`, manual-file confirmation
`preflight-confirm/` — every row fetch-safe and content-verified as of
2026-07-13. Splits: `splits/split-{1..14}.csv`, firm-whole, 14×10 rows._

Accepted groups (all four in `scout/group-notes.md`): Insight Investment
monthly+quarterly (split 4), Apollo two-site outlook (split 3), Coutts
two-domain outlook (split 4), Western Asset webcast summary+transcript
(split 6). Splits 3, 4, 6 carry the group flags; all other splits run bare.

All commands use the CODE-DEFAULT model matrix (2026-07-10): no
engine/model/effort flags.

## Split runs

Splits 3, 4, 6 (group flags):

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-13072026-145rows/splits/split-N.csv \
  --run-id 145b-splitN \
  --out-root client-runs/runs-13072026-145rows \
  --group-notes client-runs/runs-13072026-145rows/scout/group-notes.md \
  > client-runs/runs-13072026-145rows/145b-splitN.log 2>&1 &
```

All other splits — same command without the `--group-notes` line.

Operational rules: `nohup`, at most 2 splits in parallel, staggered a few
minutes; keep the Mac awake (`caffeinate -i`); ~8 min/source → a 10-row split
≈ 1–1.5 h; 14 splits two at a time ≈ 9–11 h. A dead split is relaunched with
the same command.

Split contents (firm-whole):

- split-1: American Century ×6, Deutsche Bank ×3, Alger
- split-2: Guggenheim ×3, Loomis Sayles ×3, State Street IM ×3, Amova
- split-3: TwentyFour ×3, Apollo ×2 (grouped), BlackRock iShares ×2,
  Charles Schwab ×2, Angel Oak
- split-4: Coutts ×2 (grouped), Diamond Hill ×2, Edmond de Rothschild ×2,
  GlobalX ×2, Insight Investment ×2 (grouped)
- split-5: LaSalle ×2, Lion Global ×2, Morgan Stanley ×2, Muzinich ×2, NYL ×2
- split-6: Polen ×2, Triodos ×2, Western Asset ×2 (grouped), World Gold
  Council ×2, Asset Management One, Aviva
- split-7: BIL, Barclays Private, BBH, Blackstone, BNP Paribas AM,
  BNP Wealth, Breckinridge, Brown Advisory, Bryn Mawr, Cantor Fitzgerald
- split-8: Carlyle, Carmignac, CCLA, CI Global, CIBC AM, Citi Wealth,
  Cohen & Steers, Commerce Trust, Commonfund, Decalia
- split-9: Dimensional, DPAM, DWS, Eastspring, Fidelity International,
  Fidelity Investments, Fiduciary Trust, Fiera Capital, Future Standard,
  Galaxy
- split-10: GCM Grosvenor, Generali, Guardian Capital, HSBC Private, IFM,
  Intech, J.P. Morgan, Janus Henderson, L&G, Lombard Odier
- split-11: M&G, Macquarie, Matthews Asia, Meketa, Natixis IM,
  Neuberger Berman, Nomura, Northern Trust, Ofi Invest, OTP Bank
- split-12: Pacific AM, Pathway, Patrizia, Payden & Rygel, Pictet AM,
  Probus Pleion, Putnam, Quintet, Royal London, S&P Global
- split-13: Schroders, Sellwood, Silvercrest, State Street,
  Standard Chartered, StepStone, Tikehau, Troy AM, U.S. Bank, UBP
- split-14: Van Lanschot Kempen, VanEck, Verus, Voya IM, Vontobel, Wasatch,
  W1M, Wells Fargo, Wilmington Trust, Zurich

## After each split — digest (1 LLM call per source)

```bash
.venv/bin/python -m src.summarize digest \
  --run client-runs/runs-13072026-145rows/145b-splitN \
  --out-dir client-runs/runs-13072026-145rows/digests/145b-splitN
```

## After all fourteen

Combine → datefill (report, review, apply) → reconcile, per
`docs/PIPELINE_RUNBOOK.md` §3. The cross-batch final (this batch + the
98-row batch) is ONE `src.reconcile --near-leaf` pass over both batches'
dated pre-reconcile concatenations (runbook §4).

## Notes

- 8 sources are `.txt` transcripts/notes: TwentyFour ×3 (split-3), Wasatch
  (split-14), Western Asset webcast (split-6), Brown Advisory podcast
  (split-7), Patrizia podcast (split-12), Polen webinar (split-6). Give
  their rows an extra look in review.
- Wells Fargo's manual PDF was rebuilt from a multipart HTTP capture
  (extracted `%PDF...%%EOF`); content verified (28 pages, 2026 Midyear
  Outlook).
- CI Global's PDF has a doubled-character text layer on page 1 (scrambled);
  the scrambled-page path is expected to fire.
