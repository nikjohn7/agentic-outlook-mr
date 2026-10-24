# October quality validation - 2026-10-25

**PASS for the three focused acceptance controls, with review items below.**
Ready for the user to review and launch the revised splits. No full split,
combine, reconciliation, final export, digest, firm page or binder was launched.
All final cases used identical code/prompt content and the original model matrix.

## Final cases and artifacts

All paths are relative to the repository root (the POC checkout); batch root
is `client-runs/runs-23102026-16rows/`. **Local-only evidence:** `client-runs/`
is gitignored, so the run folders, audits, logs and renders linked below exist
on the operator's machine and are not in the repository; only the
implementation fingerprint (`quality-v2-validation/implementation-currency-final-sha256.json`)
is force-added so the launcher's drift report works from a clean checkout. Each run has an adjacent `.log`, preserved
`work/<run-id>/`, CSV/XLSX client output and failures, and `pagecheck-audit.csv`.

| Case / manifest | Candidates = kept + failed | Chunks | Review rows | Acceptance audit |
| --- | --- | --- | --- | --- |
| [Lombard `oct26md-qv3-lombard-a2`](../../client-runs/runs-23102026-16rows/oct26md-qv3-lombard-a2/manifest.md) | 10 = 5 + 5 | 11/11 | 1 | [PASS](../../client-runs/runs-23102026-16rows/oct26md-qv3-lombard-a2/quality-audit.json) |
| [Neuberger `oct26md-qv3-neuberger-a2`](../../client-runs/runs-23102026-16rows/oct26md-qv3-neuberger-a2/manifest.md) | 99 = 49 + 50 | 6/6 | 6 | [PASS](../../client-runs/runs-23102026-16rows/oct26md-qv3-neuberger-a2/quality-audit.json) |
| [Amova `oct26md-qv3-amova-a2`](../../client-runs/runs-23102026-16rows/oct26md-qv3-amova-a2/manifest.md) | 19 = 19 + 0 | 12/12 | 6 | [PASS](../../client-runs/runs-23102026-16rows/oct26md-qv3-amova-a2/quality-audit.json) |

All three processes completed with Python/pipeline exit 0, zero chunk failures,
complete source/chunk coverage, reconciled counts, required fields and locked
hierarchy, consistent evidence origins and PDF markers, and CSV/XLSX agreement.
Internal/client kept counts agree in these single-source cases. The local
[artifact audit](../../client-runs/runs-23102026-16rows/quality-v2-validation/audit.py)
checks structure, not model judgment. Semantic findings follow from personally
inspected source pages/prose and review of retained rows **and losses**.

### Lombard: PASS, with small-weight reading review

Personally inspected rendered physical p.6 Figure 5 (trend), p.7 Figure 7
(valuation), p.8 Figure 9 (allocation), and supporting p.6-12 prose. Figures 5/7
are inputs, not adopted positions. Figure 9's outlined neutral weights versus
red current markers support Duration U, Credit O, DM Equities U, EM Equities U,
Commodities U. The [high-resolution detail](../../client-runs/runs-23102026-16rows/quality-v2-validation/lombard-fig9-detail.png)
confirms the small below-neutral DM gap. The checker leaves that row Medium /
review; commodities is also Medium because the visual evidence was judged thin.

The five kept calls are stated allocations (origin both 1 / PDF 4). Four failures
are unmapped cyclical/inflation/volatility exposures, and one is a same-view
merge whose evidence remains on Duration. No trend/valuation-only candidates
or their 17 old conflict groups survive. This demonstrates live extractor
suppression; a live non-call checker failure was not needed in this case.

[Before/after losses](../../client-runs/runs-23102026-16rows/quality-v2-validation/lombard-final-comparison.json):
17 old kept rows -> 5, with 12 losses inspected. Japanese/Korean bond opportunity
prose explicitly describes valuation inputs; exclusion is based on that evidence,
not on requiring country signs to equal broad Duration U. Chinese-bond trend
readings likewise do not establish positioning. Residential investment is a
GDP/rate-sensitivity discussion, not a property allocation. The All Roads/HFRX
section compares product benefits and hypothetical historical portfolios; the
p.4 phrase "can play a central role" does not establish a broad hedge-fund
allocation. A genuine recommendation to allocate to a hedge/product remains
eligible under the general rules. Duration's exact Markdown evidence has a PDF
pagecheck `not_found`; its separate marked PDF allocation passage remains p.8.

### Neuberger: PASS for positioning-grid preservation

Personally inspected grids p.4-7 and the TIPS debate p.9. **All 42 rendered-grid
endpoints match the final calls and carry PDF markers**:
[42-cell control](../../client-runs/runs-23102026-16rows/quality-v2-validation/neuberger-rendered-grid-control.json).
Representative controls: TIPS N, Global/EM equities N, US large cap O,
Europe U, Japan/China O, cash and US government/agency MBS U, global IG N,
US IG U, ABS/CMBS O, named 10-year sovereigns N, USD/JPY/GBP U, EUR O, CHF N.
Arrows describe the journey, not a second position. No analytical chart on p.9
became an allocation call.

The live arbiter kept the true house-grid TIPS N over prose U, broad EM N over
narrower AI-exposed-EM O, and credit N from its current endpoint over a trim U.
Its notes explicitly applied eligible house-stance precedence. Failures: 43
same-view merges, 5 arbitrated-out candidates, one checker sign rejection and
one BB-high-yield taxonomy non-match. Origins: both 22 / PDF 20 / Markdown 7;
pagecheck: 42 PDF-read passages, 28 added pages, 22 `not_found` Markdown passages.
There are no quote, source-ID lookup or engine failures.

[Comparison with smoke and original split](../../client-runs/runs-23102026-16rows/quality-v2-validation/neuberger-final-comparison.json):
49 kept versus smoke 54 / original source-specific split 51. All 42 grid calls
survive. Technology/Cyclicals earnings visibility was not proposed as a stance;
Copper/Gold support prose and the AI-exposed-EM thematic mapping are **recall /
scope review items**, not proven non-calls. The old Long-Dated US Treasuries U
maps this time to generic Duration - Long U on the same preference; source scope
should be reviewed. Original split Global IG U becomes N, agreeing with the
broad grid while domestic IG U is separately retained. Non-US Duration U becomes
O on relative-conviction prose, but remains Medium/review: comparative conviction
may not mean absolute overweight; the narrower non-US developed-bond grid is N.
Also review broad Small/Mid-Cap N alongside specific US SMIDs N for granularity.
Complementary intermediate O / long U still trigger the existing sibling flag.

### Amova: PASS for eligible inference, basis and review routing

Reviewed p.1-10 prose and rendered forecast tables p.13-15. All 19 kept calls
are **inferred, Medium (68-74)**, retaining US/Japan equity O, sovereign-duration
and FX/commodity views without inventing explicit OW/N/UW language. Japan's
fiscal-credibility condition remains quoted. US Treasuries N is a genuine
rise-then-retreat yield path; Copper UNCERTAIN is conditional demand/risk
language, not a committed two-phase path.

China's two-speed macro passage produces no equity N. The CSI500 diversification
passage produces China Equities O, **72/review**, with the checker explicitly
questioning onshore-versus-offshore scope on the broad leaf. It is not declared
an unqualified all-China overweight. EUR N also remains review: opposite moves
against USD and JPY are not necessarily a temporal neutral path. Other flagged
judgments are JGB direction without a current yield baseline, European sovereign
conditional pressure, possible DAX upside, and Copper uncertainty. Normal
ambiguity follows review routing; it is not treated as a clear non-call fail.

Both GBP N and AUD N survive the actual shared sentence naming sterling and the
Australian dollar, with a successful live shared-evidence verdict. JPY O / USD U
retain the 158-to-151 forecast path; Oil U retains high-80s to mid-70s wording;
Gold O retains qualified support. These are prose inferences. The appendix's
September 2026 rows are forecast distributions, not identified current market
levels; December 2026 -> September 2027 is future-to-future. No current baseline
or forecast-delta magnitude was invented. Materiality thresholds are unchanged.

[Baseline comparison](../../client-runs/runs-23102026-16rows/quality-v2-validation/amova-final-comparison.json):
24 old kept -> 19, with six losses (broad equity/duration/technology risks,
French spread recap, broad-credit yield attractiveness, Quality speculation)
and GBP N newly retained. China N -> O and EUR O -> N remain explicitly scoped /
flagged judgments. Old JPY/USD `forecast_delta` tags become inferred, preserving
their directions with the correct confidence cap. All 12 chunks were read;
all kept evidence is Markdown, since the paired PDF adds analytical charts and
forecast tables rather than new positioning. Zero PDF-origin rows is not a defect.

## Implementation, settings and reproducible provenance

The actual checkout contained the prerequisites: source/chunk binding `43e325f`
and eligibility `2ef9c2b`. Starting HEAD was `2ef9c2b`; concurrent unrelated docs
commit `c4a84d336cb4194654c1b2eac08848e0bf055be0` became the release HEAD. This task
performed no Git staging, commit, reset or clean. All final integration changes were
uncommitted when validated. The current shared checkout was explicitly confirmed by the user.

Final [source/prompt/taxonomy hashes](../../client-runs/runs-23102026-16rows/quality-v2-validation/implementation-currency-final-sha256.json)
SHA-256: `b292037f7aa7bfefbdfc7972ff03cb89e3e2933801948e74a8f9a04c6047868d`.
The [reproducible diff against 7e7e397](../../client-runs/runs-23102026-16rows/quality-v2-validation/implementation-currency-final.patch)
includes actual code, prompts and tests; SHA-256
`5250c6c65b42eb4bec0f27056d52cf749de26393ada2e941ddc1642091b4210a`.
Both matched the checkout after all final runs. HEAD alone is not the fingerprint.

Verified together: adopted allocations versus analytical inputs; fatal/audited
checker non-calls before merging/arbitration; stance-only published precedence;
asset-specific single-step inferences with correct basis/caps; and machine-bound
source/chunk IDs. Ambiguous evidence retains review routing. No firm-specific
suppression, answer-key rows/counts, taxonomy changes or parent-sign rule was added.

Scoped integration fixes: clearer PDF instruction and failure explanation;
stance-only crosscheck wording; conventions v1.5 / extractor v1.8 separating
forecast eligibility from basis; GBP/AUD ordinary-name recognition in cross-leaf
dedup; and per-process `-c features.hooks=false` for headless Codex. The latter
fixes a demonstrated global Stop hook replacing **11 real extracted candidates
with empty JSON** ([minimal trace](../../client-runs/runs-23102026-16rows/quality-v2-validation/hook-interference-evidence.json)).
It leaves global configuration/model/effort/sandbox unchanged; the official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents the setting. An initial live basis failure and a second currency-name
loss were corrected and revalidated; they were not counted as passes.

Concurrent edits by another session (pairing validation, complete PDF cited-range
checks, commentary alignment, verified-page counts, optional-CLI defaults and
related docs/tests and `.gitignore` commentary) were preserved, inspected and included in the final tested
fingerprint. They were not reverted or claimed as this task's authorship.

The matrix is unchanged: analyze codex/gpt-6.1-sol/high; checker
claude/claude-opus-5-5/high; arbiter claude/claude-haiku-5-5/high; visual verifier
codex/gpt-6-luna/high; shared-evidence claude/claude-opus-5-5/medium. Checker context
off, evidence gate on, no groups, inventory thresholds unchanged. Exact commands
use defaults, from `poc/` under Bash `set -eu` / `set -o pipefail`:

```bash
B=client-runs/runs-23102026-16rows
# CASE=lombard|neuberger|amova; RUN=oct26md-qv3-${CASE}-a2
.venv/bin/python -u -m src.run \
  --sources "$B/quality-v2-validation/$CASE.csv" \
  --run-id "$RUN" --out-root "$B" 2>&1 | tee "$B/$RUN.log"
# Local artifact audit, repeated for each completed final RUN:
PYTHONPATH=. .venv/bin/python "$B/quality-v2-validation/audit.py" "$RUN"
```

Each attempt checked output/work/log targets unused before launching; at most
two live validations ran together. Exact selected rows/paths/hashes are in
[sources.json](../../client-runs/runs-23102026-16rows/quality-v2-validation/sources.json),
selected with CSV parsing from splits 3/1/2 while retaining every input field.
All attempts, including completed failures and terminated partials, remain in
[attempts.json](../../client-runs/runs-23102026-16rows/quality-v2-validation/attempts.json).
Two superseded Neuberger attempts were intentionally stopped (143), not called
complete; every final acceptance run finished. Initial sandbox/runtime failures
and hook corruption are preserved. Following auto-review's source-transmission
rejection, the user explicitly approved all three documents/PDFs through the
configured Codex/Claude CLIs; no source/destination approval remains outstanding.

## Checks, preservation and launch decision

`.venv/bin/python -m unittest discover -s tests`: **Ran 553, OK (skipped=1)** on the
validated checkout (565, OK (skipped=1) on the PR head after the #6 merge, the provenance change
and the review fixes)
([final log](../../client-runs/runs-23102026-16rows/quality-v2-validation/unittest-currency-final.log)).
Synthetic eligibility verdicts prove routing, not model judgment; the source
findings above are separate live semantic evidence. Fresh no-LLM preflight on
all 15 inputs: 15 OK, 0 failed/suspect, under `quality-v2-validation/preflight-release-nollm/`.
It independently retained State Street Sector Market Perspectives `06/10/2026`
from Markdown. URLs and all other dates remain blank; no datefill, and L&G's
Q4 2024 edition stays excluded. All 32 intake source hashes still match.

Original baseline outputs/logs remain SHA-256 identical. Verified comparison
counts: internal 153/55/85; client 140/55/85; client review 38/14/38. These are
not targets. Their logs/manifests do not embed a commit: `7e7e397` remains reported
past provenance, not something proved by today's HEAD. Existing trial, smoke,
source and work paths were not overwritten/renamed/deleted. The July master
was not accessed for writing. Temporary test scratch
`/private/tmp/marketsrecon-quality-tests-20261025.log` was copied to the retained
`unittest-initial.log`, removed, and verified absent. Rendered pages/text are
retained as review evidence. Preparatory notes remain in
`quality-v2-validation/preparatory-review-history.md`.

**No execution blocker remains, with one caveat.** The validated fingerprint
predates the merged #6 review fixes to `src/assemble.py`, `src/pagecheck.py`,
`src/run.py` and `src/scout.py` (page-reference parsing for the `pdf:` chunk
guard and page check, lazy PDF page re-read, engine-aware scout model) and the
PR review clarifications to `conventions.md`, `analyze_chunk.md`,
`check_candidates.md`, `arbitrate_conflict.md` and `crosscheck_conflicts.md`
(wording that resolves internal contradictions; the prompts are read by the
run, so model behaviour may shift). The launcher reports this drift without
blocking, and each split's manifest records its own provenance. Re-running one
focused control (Neuberger, the largest) on the current code before the splits
is recommended. Review flags, prose recall and scope items
above remain analyst work; these controls do not establish benchmark truth for
all 15 sources. Launch/status commands are in
[the current plan](oct2026-md-run-commands.md) and
[the guarded helper](oct2026-quality-v3-rerun.sh): splits 2 and 3 first, at most
two at once, wait for both successful completion checks, then recommended split 1.
Shell syntax/CLI options and unused paths/sessions are checked without launching.
The exit-status probe verified Python/caffeinate 7 versus tee 0 is captured
correctly. The helper checks completed manifests/files, not merely Python exit
0. Drift from the final fingerprint is reported as a warning (2026-10-25: changed
from a hard stop); each run's manifest now records its own provenance. Future combine inputs are **only** the three
`oct26md-qv3-splitN/output.csv` files and matching failures after split review,
in new revision-specific destinations. Keeping old split 1 creates a documented
mixed configuration with its old eligibility/source-ID limitations.
