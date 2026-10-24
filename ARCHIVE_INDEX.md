# Workspace archive index

Cleanup completed 2026-10-23 (Australia/Melbourne), following explicit approval
of audit batches A, B and C. Paths in manifests are relative to
`/Users/nikhil/Documents/nik-coding/marketsRecon`, including the `poc/` prefix.

Archive location:

`/Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/`

This location is outside the active workspace but on the same machine. No
independent backup or complete pipeline reproducibility was established.

## Packages

| Package | Regular files | Empty and nonempty directories | Original file bytes | Compressed bytes |
| --- | ---: | ---: | ---: | ---: |
| `145-preflight.tar.gz` | 701 | 191 | 401,081,505 | 350,720,342 |
| `visual-inspection.tar.gz` | 1,816 | 212 | 671,993,297 | 643,606,316 |

SHA-256:

```text
531d5ed4c2b9cc6912723e4f32c4bae97c739e20f90c6ef246690a8021c27f18  145-preflight.tar.gz
9133b1aa1103adc582bef275b9152047fee85f6594bae48877fd4a732c2b17da  visual-inspection.tar.gz
```

Each package has a sibling `<package-name>.manifest.json` containing every
file's original path, SHA-256, size, timestamps and mode, plus directory
membership and metadata. Archive entries retain file modes and modification
times. No extended attributes were found on these targets.
`archive-verification.json` records full extraction verification;
`duplicate-removals.json` records batch A's exact replacement mappings.

## Original locations

`145-preflight.tar.gz` contains these complete historical preflight trees:

- `poc/client-runs/runs-13072026-145rows/preflight/` (564 files)
- `poc/client-runs/runs-13072026-145rows/preflight-retry/` (23 files)
- `poc/client-runs/runs-13072026-145rows/preflight-confirm/` (114 files)

These contain the initial sweep, failed-link retry and manual-copy confirmation:
reports, results, native downloads, printed captures, snapshots and chunk/ingest
metadata. Production `work/`, manual sources, source CSVs, scout decisions,
run outputs, retries, datefill records and logs remain in the repository.
References in historical command sheets and logs to the three paths above now
refer to archived material. Restore the corresponding tree to resolve its
original workspace-relative chunk paths. Archive contents were not rewritten.

`visual-inspection.tar.gz` contains:

- `poc/tmp/pdfs/` (1,809 files after the five approved duplicate removals)
- `poc/tmp/allspring-date-pages/` (2 files)
- `poc/tmp/date-check/` (1 file)
- `poc/tmp/insight-p6-8/` (4 files)

These preserve page renderings, quote/date checks, crops, extracted text and
five PDF page extracts. The six implementation/handoff Markdown files directly
under `poc/tmp/` remained in place at this cleanup (see Verification). A
complete source-to-render mapping was not
established; the visual evidence was archived rather than discarded.

## Batch A: exact duplicates removed

The five listed files were SHA-256-identical to the retained counterpart at
cleanup time. Counterparts were then included in the verified visual archive.
To recover a removed name, extract its counterpart and copy it to that name.

| Removed path | Counterpart in `visual-inspection.tar.gz` | Bytes |
| --- | --- | ---: |
| `poc/tmp/pdfs/fidelity-p26-28.png` | `poc/tmp/pdfs/fidelity-26.png` | 87,354 |
| `poc/tmp/pdfs/pimco-p11-11.png` | `poc/tmp/pdfs/pimco-page-11.png` | 333,480 |
| `poc/tmp/pdfs/quote-check/page-05.png` | `poc/tmp/pdfs/quote-check/janus-page-05.png` | 524,249 |
| `poc/tmp/pdfs/ab-p11-12/pages.txt` | `poc/tmp/pdfs/ab-p11-12/p11-12.txt` | 10,947 |
| `poc/tmp/pdfs/jpm-p1-4/pages.txt` | `poc/tmp/pdfs/jpm-p1-4/pages-1-4.txt` | 29,752 |

## Verification and scope

- Rechecked audited counts/sizes for all seven target trees, 745 available
  prior per-file metadata records, all five duplicate hashes and timestamps,
  and target modification dates. No approved target showed a new modification.
- Checked for open files in the target trees before archival and removal.
- Extracted both archives into separate temporary verification directories;
  checked exact membership, all 2,517 file hashes/sizes and all 403 directories.
- Rechecked source membership, hashes, timestamps and file identities before
  removal; removed only explicit manifest paths and the five named duplicates.
- Removed 2,522 original files (1,074,060,584 logical bytes; 1,079,828,480
  allocated file bytes) and 403 now-empty directories.
- Archives and receipts occupy 995,213,025 logical bytes and 1,008,238,592
  allocated bytes outside the workspace. After the index, net allocated-file
  reduction is approximately 68 MiB; filesystem snapshots/background activity
  can affect volume-level free space. Temporary verification copies were removed.
- Verified all 2,392 protected project files remained byte-identical,
  including source code, tests, original inputs, human decisions, the July
  master, current client exports, firm pages, binder and the six `tmp/` notes.
- Verified 261 CSV local-file references retained their pre-cleanup state.
  Eleven already-unresolved references in the retry2 `as-provided` CSV remain
  historical evidence; its corrected working CSV still resolves.
- Git HEAD stayed unchanged during execution. No staging, commits, pipeline
  runs, model calls, regeneration or dependency installation was performed.
  `ARCHIVE_INDEX.md` is the only project file added by this cleanup.

## Inspect or restore

List archived paths without restoring them:

```sh
tar -tzf /Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/145-preflight.tar.gz
tar -tzf /Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/visual-inspection.tar.gz
```

Check archive hashes against the values above:

```sh
shasum -a 256 /Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/145-preflight.tar.gz /Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/visual-inspection.tar.gz
```

Prefer extraction into a new empty directory for inspection. To restore a
package to its original location after checking for concurrent work, use
`-k` to refuse overwriting existing files:

```sh
tar -xkzf /Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/145-preflight.tar.gz -C /Users/nikhil/Documents/nik-coding/marketsRecon
tar -xkzf /Users/nikhil/Documents/nik-archives/marketsRecon/2026-10-23/visual-inspection.tar.gz -C /Users/nikhil/Documents/nik-coding/marketsRecon
```

For a partial restore, append the exact original path from the manifest to
the corresponding command. Do not rerun the pipeline merely to recover these
historical artifacts. The original pilot and 98-batch `work/` folders and the
roadmap's client-summary example PDF were already absent before cleanup;
these archives do not recover those missing items.

## Later deletion outside this archive (2026-10-24)

The six implementation/handoff Markdown files directly under `poc/tmp/`,
protected above, were deleted on 2026-10-24 once their builds were committed.
This deletion is outside the verified cleanup: no archive, receipt or hashes
were taken. Four names are recoverable from the ROADMAP references removed in
commit `c4a84d3`: `tmp/instructions-1-checker-context.md`,
`tmp/instructions-2-scout-groups.md`, `tmp/instructions-3-firm-crosscheck.md`
and `tmp/instructions-4-reader-summaries.md`; the other two names were not
recorded. Their content is superseded by the committed builds and their
ROADMAP / `prompts/REGISTRY.md` entries.
