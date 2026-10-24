"""Run-level orchestration: ingest -> LLM analyze -> validate/score -> assemble."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path

from src.assemble import (
    FailureRecord,
    assemble_candidates,
    pdf_chunk_pages,
    validation_key,
    write_run_outputs,
)
from src.confidence import evidence_context, evidence_passes
from src.ingest import (
    IngestedSource,
    Chunk,
    SourceRecord,
    create_snapshot,
    enforce_source_limit,
    load_pilot_sources,
    load_target_sources,
)
from src.llm import (
    CODEX_MODELS,
    DEFAULT_CODEX_MODEL,
    ENGINE_CONFIGS,
    LLMParseError,
    call as llm_call,
    call_parsed,
    parse_arbitration,
    parse_groups,
    parse_quote_visual_verification,
    parse_verdicts,
    resolve_codex_model,
)
from src import sharedevidence
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import load_taxonomy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYZE_PROMPT = PROJECT_ROOT / "prompts" / "analyze_chunk.md"
BRAIN_PROMPT = PROJECT_ROOT / "prompts" / "brain.md"
CONVENTIONS_FILE = PROJECT_ROOT / "prompts" / "conventions.md"
CHECK_PROMPT = PROJECT_ROOT / "prompts" / "check_candidates.md"
ARBITER_PROMPT = PROJECT_ROOT / "prompts" / "arbitrate_conflict.md"
GROUPS_PROMPT = PROJECT_ROOT / "prompts" / "resolve_groups.md"
QUOTE_VISUAL_PROMPT = PROJECT_ROOT / "prompts" / "verify_quote_visual.md"

# The chunk call can fail two ways worth catching per-chunk (so one bad chunk
# never sinks the whole run): unparseable/contract-breaking output after the
# repair retries, and a non-zero engine exit.
CHUNK_CALL_ERRORS = (LLMParseError, RuntimeError)


def run_pipeline(
    *,
    sources: list,
    run_id: str,
    engine: str,
    model: str | None = None,
    effort: str | None = None,
    checker_engine: str = "claude",
    checker_model: str | None = "claude-opus-5-5",
    checker_effort: str = "high",
    checker_context: bool = False,
    arbiter_engine: str = "claude",
    arbiter_model: str | None = "claude-haiku-5-5",
    arbiter_effort: str = "high",
    group_notes_text: str | None = None,
    group_notes_path: str | None = None,
    grouper_engine: str = "codex",
    grouper_model: str | None = "gpt-6-luna",
    grouper_effort: str = "high",
    quote_visual_engine: str = "codex",
    quote_visual_model: str | None = "gpt-6-luna",
    quote_visual_effort: str = "high",
    evidence_gate: bool = True,
    evidence_engine: str = sharedevidence.DEFAULT_ENGINE,
    evidence_model: str | None = sharedevidence.DEFAULT_MODEL,
    evidence_effort: str = sharedevidence.DEFAULT_EFFORT,
    runner=None,
    analyze_prompt: str | Path = ANALYZE_PROMPT,
    brain_text: str | None = None,
    conventions_text: str | None = None,
    out_root: str | Path | None = None,
):
    """Ingest each source, analyze every chunk, then check, score, and assemble.

    Up to four LLM steps, each with its own engine/model/effort: analyze (the
    extraction workhorse), checker (second-reader verdicts per source batch),
    arbiter (conflict resolution, only called when views collide), and the
    group resolver (only when analyst group notes are supplied - translates
    free-text pairing notes into an explicit, saved grouping plan once, before
    any analysis).
    """
    enforce_source_limit(sources)
    # A direct run_pipeline caller (e.g. a test) may leave a codex step's model
    # None; fill the default so run_config always states the model. The CLI has
    # already resolved these via resolve_engine_settings.
    if checker_engine == "codex" and checker_model is None:
        checker_model = DEFAULT_CODEX_MODEL
    if arbiter_engine == "codex" and arbiter_model is None:
        arbiter_model = DEFAULT_CODEX_MODEL
    if grouper_engine == "codex" and grouper_model is None:
        grouper_model = DEFAULT_CODEX_MODEL
    if quote_visual_engine == "codex" and quote_visual_model is None:
        quote_visual_model = DEFAULT_CODEX_MODEL
    if evidence_engine == "codex" and evidence_model is None:
        evidence_model = DEFAULT_CODEX_MODEL
    taxonomy = load_taxonomy()
    taxonomy_block = taxonomy.grouped_block()
    brain = brain_text if brain_text is not None else _brain_text()
    conventions = conventions_text if conventions_text is not None else _conventions_text()
    # Default paths (out_root absent) are exactly today's: work/<id> and runs/<id>.
    # An out_root reroots both under it: <out_root>/work/<id> and <out_root>/<id> -
    # so a production batch keeps all its artifacts out of the test/pilot trees.
    if out_root is not None:
        base = Path(out_root)
        work_dir = base / "work" / run_id
        run_dir = base / run_id
    else:
        work_dir = Path("work") / run_id
        run_dir = Path("runs") / run_id

    group_map: dict[str, str] = {}
    grouping: dict[str, object] | None = None
    if group_notes_text:
        groups, group_warnings = _resolve_groups(
            sources,
            group_notes_text,
            engine=grouper_engine,
            model=grouper_model,
            effort=grouper_effort,
            runner=runner,
        )
        group_map = {
            source_id: group["group_id"] for group in groups for source_id in group["source_ids"]
        }
        grouping = {
            "notes_path": group_notes_path or "(inline)",
            "groups": groups,
            "warnings": group_warnings,
        }
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "groups.json").write_text(json.dumps(grouping, indent=2), encoding="utf-8")

    all_candidates: list[CandidateCall] = []
    verdicts: dict[int, CheckVerdict] = {}
    chunk_failures: list[FailureRecord] = []
    snapshots: dict[tuple[str, str], str] = {}
    page_counts: dict[str, int] = {}
    scrambled_pages: dict[str, set[int]] = {}
    ocr_pages: dict[str, set[int]] = {}
    visual_pages: dict[str, set[int]] = {}
    native_source_paths: dict[str, Path] = {}
    source_infos: dict[str, SourceInfo] = {}
    source_summaries: list[dict[str, object]] = []
    group_ledgers: dict[str, str] = {}

    for source in sources:
        source_infos[source.source_id] = SourceInfo(
            source_id=source.source_id,
            firm=source.firm,
            date=source.date,
            source=source.source,
            url=source.url,
            pdf_path=str(source.pdf_path) if source.pdf_path else "",
        )
        try:
            ingested = create_snapshot(source, work_dir)
        except Exception as exc:
            message = _exception_summary(exc)
            chunk_failures.append(
                FailureRecord.from_chunk(
                    "ingest_error", message, source.source_id, "ingest"
                )
            )
            source_summaries.append(
                {
                    "source_id": source.source_id,
                    "source_type": source.source_type,
                    "page_count": None,
                    "chunk_count": 0,
                    "candidates": 0,
                    "visual_heavy": False,
                    "printed_pdf": False,
                    "scrambled_pages": [],
                    "ocr_pages": [],
                    "ocr_note": "",
                    "ingest_error": message,
                }
            )
            continue
        snapshot_text = ingested.snapshot_text_path.read_text(encoding="utf-8")
        pdf_text = (
            ingested.pdf_snapshot_text_path.read_text(encoding="utf-8")
            if ingested.pdf_snapshot_text_path is not None
            else ""
        )
        pdf_page_spans = (
            _pdf_page_spans(
                pdf_text,
                _pdf_page_texts(ingested.pdf_native_path),
                frozenset(ingested.pdf_ocr_pages),
            )
            if ingested.pdf_native_path is not None
            else None
        )
        pdf_chunk_texts: dict[str, str] = {}
        for chunk in ingested.chunks:
            chunk_pages = pdf_chunk_pages(chunk.chunk_id)
            if not chunk_pages:
                snapshots[(source.source_id, chunk.chunk_id)] = snapshot_text
                continue
            # A paired-PDF chunk validates against the PDF only, keyed per chunk
            # (assemble.validation_key): its text, pages, page count and path.
            # The text is only the chunk's own pages - the pages the model was
            # given - so a quote found only on another page cannot pass.
            key = (source.source_id, chunk.chunk_id)
            pdf_chunk_texts[chunk.chunk_id] = _pdf_chunk_text(
                pdf_text, pdf_page_spans, chunk_pages
            )
            snapshots[key] = pdf_chunk_texts[chunk.chunk_id]
            page_counts[key] = ingested.pdf_page_count
            native_source_paths[key] = Path(ingested.pdf_native_path)
            visual_pages[key] = set(chunk_pages)
            if ingested.pdf_scrambled_pages:
                scrambled_pages[key] = set(ingested.pdf_scrambled_pages)
            if ingested.pdf_ocr_pages:
                ocr_pages[key] = set(ingested.pdf_ocr_pages)
        if ingested.page_count is not None:
            page_counts[source.source_id] = ingested.page_count
        native_source_paths[source.source_id] = Path(ingested.native_source_path)
        if ingested.scrambled_pages:
            scrambled_pages[source.source_id] = set(ingested.scrambled_pages)
        if ingested.ocr_pages:
            ocr_pages[source.source_id] = set(ingested.ocr_pages)
        source_visual_pages: set[int] = set()
        if (ingested.printed_pdf or ingested.visual_heavy) and ingested.page_count:
            source_visual_pages = set(range(1, ingested.page_count + 1))
            visual_pages[source.source_id] = source_visual_pages
        source_infos[source.source_id] = SourceInfo(
            source_id=source.source_id,
            firm=source.firm,
            # ingested.source, not source: create_snapshot fills a blank date
            # from the document itself (see ingest's document-date fallback).
            date=ingested.source.date,
            source=source.source,
            url=source.url,
            pdf_path=str(source.pdf_path) if source.pdf_path else "",
        )

        group_id = group_map.get(source.source_id)
        candidates, failures = analyze_source(
            ingested,
            work_dir,
            taxonomy_block=taxonomy_block,
            brain_text=brain,
            conventions_text=conventions,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            analyze_prompt=analyze_prompt,
            initial_memory=group_ledgers.get(group_id, "") if group_id else "",
        )
        # The source's rolling memory is complete once every chunk is analyzed;
        # it seeds the group ledger (grouped sources) and is handed to the
        # checker as whole-file context for this same source.
        source_memory = (work_dir / source.source_id / "memory.md").read_text(encoding="utf-8")
        if group_id:
            group_ledgers[group_id] = (
                "\n## Companion document already analyzed in this grouped source set\n"
                "Report this document's own stances normally - corroboration and\n"
                "conflicts across the set are resolved downstream; mark `conflict:\n"
                "true` where this document disagrees with the companion.\n\n"
                f"{source_memory}\n"
            )
        offset = len(all_candidates)
        all_candidates.extend(candidates)
        chunk_failures.extend(failures)
        if candidates:
            visual_unverified = _visual_unverified_candidate_indexes(
                candidates,
                snapshot_text=snapshot_text,
                visual_pages=source_visual_pages,
            )
            pdf_context = None
            if ingested.pdf_native_path is not None:
                visual_unverified |= _pdf_visual_unverified_candidate_indexes(
                    candidates, chunk_texts=pdf_chunk_texts, visual_pages=visual_pages
                )
                pdf_context = (
                    pdf_chunk_texts,
                    frozenset(ingested.pdf_scrambled_pages),
                    frozenset(ingested.pdf_ocr_pages),
                )
            source_verdicts, checker_failure = _check_candidates(
                source,
                candidates,
                memory_text=source_memory,
                conventions=conventions,
                engine=checker_engine,
                model=checker_model,
                effort=checker_effort,
                runner=runner,
                # A paired PDF is the page image the checker can open; only
                # `pdf:` candidates are routed to it (pdf_context).
                native_source_path=ingested.pdf_native_path or ingested.native_source_path,
                visual_unverified=visual_unverified,
                checker_context=checker_context,
                snapshot_text=snapshot_text,
                scrambled_pages=frozenset(scrambled_pages.get(source.source_id, set())),
                ocr_pages=frozenset(ocr_pages.get(source.source_id, set())),
                pdf_context=pdf_context,
            )
            verdicts.update(
                {offset + local_index: verdict for local_index, verdict in source_verdicts.items()}
            )
            if checker_failure is not None:
                chunk_failures.append(checker_failure)
        source_summaries.append(
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "page_count": ingested.page_count,
                "chunk_count": len(ingested.chunks),
                "candidates": len(candidates),
                "visual_heavy": ingested.visual_heavy,
                "printed_pdf": ingested.printed_pdf,
                "scrambled_pages": list(ingested.scrambled_pages),
                "ocr_pages": list(ingested.ocr_pages),
                "ocr_note": ingested.ocr_note,
                "pdf_chunk_count": sum(
                    bool(pdf_chunk_pages(chunk.chunk_id)) for chunk in ingested.chunks
                ),
                "flagged_pages": list(ingested.flagged_pages),
            }
        )

    arbiter = _make_arbiter(
        conventions,
        engine=arbiter_engine,
        model=arbiter_model,
        effort=arbiter_effort,
        runner=runner,
    )
    quote_visual_stats = {
        "attempted": 0,
        "present_verbatim": 0,
        "present_paraphrase": 0,
        "absent": 0,
        "malformed": 0,
    }
    quote_visual_verifier = _make_quote_visual_verifier(
        engine=quote_visual_engine,
        model=quote_visual_model,
        effort=quote_visual_effort,
        runner=runner,
        stats=quote_visual_stats,
    )
    # Shared-evidence gate (src/sharedevidence.py): one passage cited for
    # several leaves keeps only the most compelling call(s). Judgment-tier model.
    shared_evidence_judge = (
        sharedevidence.make_judge(
            engine=evidence_engine, model=evidence_model, effort=evidence_effort, runner=runner
        )
        if evidence_gate
        else None
    )
    result = assemble_candidates(
        all_candidates,
        sources=source_infos,
        taxonomy=taxonomy,
        snapshots=snapshots,
        page_counts=page_counts,
        scrambled_pages=scrambled_pages,
        ocr_pages=ocr_pages,
        visual_pages=visual_pages,
        verdicts=verdicts,
        arbiter=arbiter,
        group_map=group_map or None,
        quote_visual_verifier=quote_visual_verifier,
        native_source_paths=native_source_paths,
        shared_evidence_judge=shared_evidence_judge,
    )
    run_config = {
        "engine": engine,
        "model": model,
        "effort": effort,
        "checker": f"{checker_engine}/{checker_model}/{checker_effort}",
        "checker context": "on" if checker_context else "off",
        "arbiter": f"{arbiter_engine}/{arbiter_model}/{arbiter_effort}",
        "quote visual verifier": (
            f"{quote_visual_engine}/{quote_visual_model}/{quote_visual_effort}"
        ),
        "shared-evidence gate": (
            f"{evidence_engine}/{evidence_model}/{evidence_effort}" if evidence_gate else "off"
        ),
    }
    if group_notes_text:
        run_config["grouper"] = f"{grouper_engine}/{grouper_model}/{grouper_effort}"
        run_config["group notes"] = group_notes_path or "(inline)"
    write_run_outputs(
        result,
        run_dir,
        sources=source_infos,
        source_summaries=source_summaries,
        chunk_failures=chunk_failures,
        run_config=run_config,
        grouping=grouping,
        quote_visual_stats=quote_visual_stats,
    )
    return result, chunk_failures, run_dir


def _exception_summary(exc: Exception) -> str:
    summary = f"{type(exc).__name__}: {exc}".strip()
    return summary[:300]


def analyze_source(
    ingested: IngestedSource,
    work_dir: str | Path,
    *,
    taxonomy_block: str,
    brain_text: str,
    conventions_text: str = "",
    engine: str,
    model: str | None = None,
    effort: str | None = None,
    runner=None,
    analyze_prompt: str | Path = ANALYZE_PROMPT,
    initial_memory: str = "",
) -> tuple[list[CandidateCall], list[FailureRecord]]:
    """Analyze every chunk of one source with rolling memory between chunks.

    initial_memory seeds the rolling memory before the first chunk - used to
    carry a grouped companion document's ledger into this document's read.
    """
    source = ingested.source
    memory_path = Path(work_dir) / source.source_id / "memory.md"
    memory_path.write_text(_memory_header(source) + initial_memory, encoding="utf-8")

    candidates: list[CandidateCall] = []
    failures: list[FailureRecord] = []
    for chunk in ingested.chunks:
        template_vars = {
            "taxonomy": taxonomy_block,
            "brain_examples": brain_text,
            "conventions": conventions_text,
            "memory": memory_path.read_text(encoding="utf-8"),
            "chunk_content": _chunk_content(ingested, chunk),
        }
        inputs = {"source_id": source.source_id, "chunk_id": chunk.chunk_id}
        try:
            call_result = llm_call(
                analyze_prompt,
                inputs,
                engine=engine,
                model=model,
                effort=effort,
                runner=runner,
                template_vars=template_vars,
            )
        except CHUNK_CALL_ERRORS as exc:
            reason = "json_parse_error" if isinstance(exc, LLMParseError) else "engine_error"
            failures.append(
                FailureRecord.from_chunk(reason, str(exc)[:300], source.source_id, chunk.chunk_id)
            )
            _append_memory(memory_path, chunk.chunk_id, f"({reason}; chunk skipped)", [])
            continue

        # The chunk id routes validation (Markdown vs paired PDF), so it is the
        # chunk actually read, never the id the model echoed.
        chunk_candidates = [
            replace(candidate, chunk_id=chunk.chunk_id) for candidate in call_result.candidates
        ]
        candidates.extend(chunk_candidates)
        _append_memory(memory_path, chunk.chunk_id, call_result.summary, chunk_candidates)

    return candidates, failures


def _check_candidates(
    source,
    candidates: list[CandidateCall],
    *,
    memory_text: str = "",
    conventions: str = "",
    engine: str,
    model: str | None,
    effort: str | None,
    runner=None,
    native_source_path: str | Path | None = None,
    visual_unverified: set[int] | None = None,
    checker_context: bool = False,
    snapshot_text: str = "",
    scrambled_pages: frozenset[int] = frozenset(),
    ocr_pages: frozenset[int] = frozenset(),
    pdf_context: tuple[dict[str, str], frozenset[int], frozenset[int]] | None = None,
) -> tuple[dict[int, CheckVerdict], FailureRecord | None]:
    """One second-reader call for all of a source's candidates.

    Returns {local candidate index: verdict}. A failed checker call returns no
    verdicts plus a failure record - candidates then proceed capped and
    flagged for review, never silently promoted.

    memory_text is the source's complete rolling memory (the same ledger built
    during analyze). It is injected as whole-file context so the checker can see
    whether a candidate is corroborated or contradicted elsewhere in the file -
    but it never relaxes the per-candidate evidence bar (the prompt says so).

    When checker_context is on, each candidate is deterministically routed
    (src/confidence.evidence_context) to either a text context window
    (`evidence_context`) or a visual fallback (`context_unreliable: true`,
    reusing native_source_path + the cited page). Routing never fails a run or
    blocks a candidate.

    pdf_context is ({`pdf:` chunk id: that chunk's PDF page text}, scrambled
    pages, OCR pages) for a Markdown source
    with a paired PDF: `pdf:` candidates are routed on it with the PDF page
    image, Markdown candidates on the Markdown text with no page image.
    """
    native_pdf = _native_pdf_path(native_source_path)
    inputs = {
        "source_id": source.source_id,
        "firm": source.firm,
        "source_title": source.source,
        "native_source_path": str(native_source_path) if native_source_path else "",
        "candidates": [
            {
                "index": index,
                "sub_asset_raw": candidate.sub_asset_raw,
                "sub_asset_class": candidate.sub_asset_class,
                "view": candidate.view,
                "call_language": candidate.call_language,
                "evidence_kind": candidate.evidence_kind,
                "evidence_quote": candidate.evidence_quote,
                "locator": candidate.locator,
                "reasoning": candidate.reasoning,
                "basis": candidate.basis,
                "text_unverifiable_visual": index in (visual_unverified or set()),
                **_candidate_context_fields(
                    candidate,
                    enabled=checker_context,
                    snapshot_text=snapshot_text,
                    scrambled_pages=scrambled_pages,
                    ocr_pages=ocr_pages,
                    native_pdf_available=native_pdf is not None,
                    pdf_context=pdf_context,
                ),
            }
            for index, candidate in enumerate(candidates)
        ],
    }
    try:
        result = call_parsed(
            CHECK_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            template_vars={"conventions": conventions, "memory": memory_text},
            parser=parse_verdicts,
        )
    except CHUNK_CALL_ERRORS as exc:
        failure = FailureRecord.from_chunk(
            "checker_error", str(exc)[:300], source.source_id, "checker"
        )
        return {}, failure
    verdict_map = {
        verdict.index: verdict
        for verdict in result.payload
        if 0 <= verdict.index < len(candidates)
    }
    return verdict_map, None


def _visual_unverified_candidate_indexes(
    candidates: list[CandidateCall],
    *,
    snapshot_text: str,
    visual_pages: set[int],
) -> set[int]:
    if not visual_pages:
        return set()
    indexes: set[int] = set()
    pages = frozenset(visual_pages)
    for index, candidate in enumerate(candidates):
        check = evidence_passes(candidate, snapshot_text, visual_pages=pages)
        if check.visual_unverified_by_text:
            indexes.add(index)
    return indexes


def _pdf_visual_unverified_candidate_indexes(
    candidates: list[CandidateCall],
    *,
    chunk_texts: dict[str, str],
    visual_pages: dict,
) -> set[int]:
    """Indexes of `pdf:` candidates whose table/visual evidence only the page
    image can verify (their chunk's pages, against that chunk's PDF text)."""
    indexes: set[int] = set()
    for index, candidate in enumerate(candidates):
        if not pdf_chunk_pages(candidate.chunk_id):
            continue
        pages = set(visual_pages.get(validation_key(candidate), ()))
        if _visual_unverified_candidate_indexes(
            [candidate],
            snapshot_text=chunk_texts.get(candidate.chunk_id, ""),
            visual_pages=pages,
        ):
            indexes.add(index)
    return indexes


def _pdf_page_texts(pdf_path: str | Path | None) -> list[str] | None:
    """Per-page pdfplumber text of a paired PDF (the same extraction ingest
    joins into the PDF snapshot), or None when the file cannot be read."""
    if pdf_path is None:
        return None
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            return [page.extract_text() or "" for page in pdf.pages]
    except Exception:
        return None


_PDF_PAGE_JOIN = "\n\n"  # ingest._extract_pdf_text joins page texts with this


def _pdf_page_spans(
    pdf_text: str,
    page_texts: list[str] | None,
    ocr_pages: frozenset[int],
) -> dict[int, tuple[int, int]] | None:
    """Map page number -> (start, end) offsets of that page in the PDF snapshot.

    The snapshot is the page texts joined by _PDF_PAGE_JOIN, except that an
    OCR page may carry its OCR text instead; a run of consecutive OCR pages is
    therefore bounded by its non-OCR neighbours and every page in the run gets
    the run's whole span. None when the snapshot does not line up with the
    page texts (callers then fall back to the whole PDF text).
    """
    if not page_texts:
        return None
    spans: dict[int, tuple[int, int]] = {}
    cursor = 0
    pending: list[int] = []
    pending_start = 0
    for page_number, page_text in enumerate(page_texts, start=1):
        if page_number in ocr_pages:
            if not pending:
                pending_start = cursor
            pending.append(page_number)
            continue
        if pending:
            boundary = pdf_text.find(_PDF_PAGE_JOIN + page_text, pending_start)
            if boundary < 0:
                return None
            spans.update((page, (pending_start, boundary)) for page in pending)
            pending = []
            cursor = boundary + len(_PDF_PAGE_JOIN)
        if not pdf_text.startswith(page_text, cursor):
            return None
        spans[page_number] = (cursor, cursor + len(page_text))
        cursor += len(page_text) + len(_PDF_PAGE_JOIN)
    if pending:
        spans.update((page, (pending_start, len(pdf_text))) for page in pending)
    elif cursor - len(_PDF_PAGE_JOIN) != len(pdf_text):
        return None
    return spans


def _pdf_chunk_text(
    pdf_text: str,
    page_spans: dict[int, tuple[int, int]] | None,
    chunk_pages: tuple[int, ...],
) -> str:
    """The PDF snapshot text of a `pdf:` chunk's own pages. Falls back to the
    whole PDF text only when the page boundaries cannot be recovered."""
    if page_spans is None or any(page not in page_spans for page in chunk_pages):
        return pdf_text
    start = min(page_spans[page][0] for page in chunk_pages)
    end = max(page_spans[page][1] for page in chunk_pages)
    return pdf_text[start:end]


def _candidate_context_fields(
    candidate: CandidateCall,
    *,
    pdf_context: tuple[dict[str, str], frozenset[int], frozenset[int]] | None,
    native_pdf_available: bool,
    **fields,
) -> dict[str, object]:
    """_context_fields on the candidate's own route when a paired PDF is in play."""
    if pdf_context is None:
        return _context_fields(candidate, native_pdf_available=native_pdf_available, **fields)
    if pdf_chunk_pages(candidate.chunk_id):
        chunk_texts, scrambled, ocr = pdf_context
        fields.update(
            snapshot_text=chunk_texts.get(candidate.chunk_id, ""),
            scrambled_pages=scrambled,
            ocr_pages=ocr,
        )
        return _context_fields(candidate, native_pdf_available=native_pdf_available, **fields)
    return _context_fields(candidate, native_pdf_available=False, **fields)


def _native_pdf_path(native_source_path: str | Path | None) -> Path | None:
    """The native source path when it is a PDF a checker can open a page image
    from, else None (a visual fallback needs a page image)."""
    if native_source_path is None:
        return None
    path = Path(native_source_path)
    return path if path.suffix.lower() == ".pdf" else None


def _context_fields(
    candidate: CandidateCall,
    *,
    enabled: bool,
    snapshot_text: str,
    scrambled_pages: frozenset[int],
    ocr_pages: frozenset[int],
    native_pdf_available: bool,
) -> dict[str, object]:
    """The candidate's evidence-context fields for the checker input.

    Empty (no fields) unless --checker-context is on. A CLEAN route adds
    `evidence_context` (the text window); a DEGRADED route adds
    `context_unreliable: true` - but only when there is a PDF page image to open
    (native PDF present and a cited page), otherwise nothing is attached and the
    candidate is judged exactly as today. Never raises."""
    if not enabled:
        return {}
    context = evidence_context(
        candidate,
        snapshot_text,
        scrambled_pages=scrambled_pages,
        ocr_pages=ocr_pages,
    )
    if context.route == "clean" and context.text:
        return {"evidence_context": context.text}
    if context.route == "degraded" and native_pdf_available and context.cited_page is not None:
        return {"context_unreliable": True}
    return {}


def _make_arbiter(
    conventions_text: str,
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner=None,
):
    """Build the conflict arbiter callable handed to assemble_candidates.

    The arbiter sees the conflicting candidates' evidence (not their
    deterministic confidence, which must not anchor it) plus the house
    conventions, and must name a winner or return null (-> unresolved).
    Per-candidate source_id is included because a conflict group can span the
    documents of an analyst-grouped source set.
    """

    def arbiter(group) -> tuple[int | None, str]:
        first = group[0].candidate
        inputs = {
            "source_id": first.source_id,
            "sub_asset_class": first.sub_asset_class,
            "candidates": [
                {
                    "index": index,
                    "source_id": item.candidate.source_id,
                    "view": item.candidate.view,
                    "call_language": item.candidate.call_language,
                    "evidence_kind": item.candidate.evidence_kind,
                    "evidence_quote": item.candidate.evidence_quote,
                    "locator": item.candidate.locator,
                    "reasoning": item.candidate.reasoning,
                }
                for index, item in enumerate(group)
            ],
        }
        try:
            result = call_parsed(
                ARBITER_PROMPT,
                inputs,
                engine=engine,
                model=model,
                effort=effort,
                runner=runner,
                template_vars={"conventions": conventions_text},
                parser=parse_arbitration,
            )
        except CHUNK_CALL_ERRORS as exc:
            return None, f"arbiter error: {str(exc)[:200]}"
        return result.payload

    return arbiter


def _make_quote_visual_verifier(
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner=None,
    stats: dict[str, int] | None = None,
):
    """Build the tier-3 categorical quote verifier.

    The verifier is called only after deterministic tiers 1-2b fail. It fails
    closed: non-PDF sources, malformed JSON, parser failures, and engine errors
    all return a non-keeping judgment rather than crashing assembly.
    """

    def verifier(candidate: CandidateCall, source: SourceInfo, native_source_path: Path | None) -> str:
        if stats is not None:
            stats["attempted"] = stats.get("attempted", 0) + 1
        if native_source_path is None or native_source_path.suffix.lower() != ".pdf":
            if stats is not None:
                stats["absent"] = stats.get("absent", 0) + 1
            return "absent"
        inputs = {
            "source_id": source.source_id,
            "firm": source.firm,
            "source_title": source.source,
            "native_source_path": str(native_source_path),
            "locator": candidate.locator,
            "evidence_quote": candidate.evidence_quote,
            "sub_asset_class": candidate.sub_asset_class,
            "view": candidate.view,
        }
        try:
            result = call_parsed(
                QUOTE_VISUAL_PROMPT,
                inputs,
                engine=engine,
                model=model,
                effort=effort,
                runner=runner,
                parser=parse_quote_visual_verification,
            )
        except CHUNK_CALL_ERRORS:
            if stats is not None:
                stats["malformed"] = stats.get("malformed", 0) + 1
            return "malformed"
        judgment = result.payload
        if stats is not None:
            stats[judgment] = stats.get(judgment, 0) + 1
        return judgment

    return verifier


def _resolve_groups(
    sources,
    notes_text: str,
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner=None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Resolve free-text analyst group notes into an explicit grouping plan.

    The LLM only translates note lines into source_ids from this run; every
    deterministic guard (unknown ids, overlapping groups, notes resolving to
    fewer than two sources) lands in warnings so the manifest shows exactly
    what was and wasn't grouped. A failed resolver call degrades to an
    ungrouped run, never a crash.
    """
    inputs = {
        "sources": [
            {
                "source_id": source.source_id,
                "firm": source.firm,
                "title": source.source,
                "date": source.date,
            }
            for source in sources
        ]
    }
    try:
        result = call_parsed(
            GROUPS_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            template_vars={"group_notes": notes_text},
            parser=parse_groups,
        )
    except CHUNK_CALL_ERRORS as exc:
        return [], [f"group-notes resolution failed; run proceeds ungrouped: {str(exc)[:200]}"]

    raw_groups, unmatched = result.payload
    known = {source.source_id for source in sources}
    warnings = [f"unmatched note: {line}" for line in unmatched]
    groups: list[dict[str, object]] = []
    grouped_ids: set[str] = set()
    for member_ids, note in raw_groups:
        unknown = [member for member in member_ids if member not in known]
        overlap = [member for member in member_ids if member in grouped_ids]
        members = [
            member for member in member_ids if member in known and member not in grouped_ids
        ]
        if unknown:
            warnings.append(f"dropped unknown source ids {unknown} from note: {note}")
        if overlap:
            warnings.append(f"source(s) {overlap} already grouped; dropped from note: {note}")
        if len(members) < 2:
            warnings.append(f"note did not resolve to two run sources; ignored: {note}")
            continue
        grouped_ids.update(members)
        groups.append(
            {"group_id": f"group-{len(groups) + 1}", "source_ids": members, "note": note}
        )
    return groups, warnings


def _chunk_content(ingested: IngestedSource, chunk: Chunk) -> str:
    """Render the chunk the model must read: native PDF pages, or HTML text."""
    paired_pdf = bool(pdf_chunk_pages(chunk.chunk_id))
    if ingested.source.source_type == "pdf" or ingested.printed_pdf or paired_pdf:
        pages = chunk.locator.replace("p.", "")
        capture_note = (
            f"(This PDF is a print-to-PDF capture of the web page at "
            f"{ingested.source.resolved_url}; use its page numbers as locators.)\n\n"
            if ingested.printed_pdf
            else ""
        )
        if paired_pdf:
            capture_note = (
                "(These pages were flagged because the Markdown conversion of this "
                "document may have lost their graphics: report what the charts, "
                "grids and tables show. Calls already taken from the text are "
                "merged downstream.)\n\n"
            )
        return (
            f"This chunk is pages {pages} of the PDF file at:\n"
            f"`{chunk.source_path}`\n\n"
            f"{capture_note}"
            "Open and read those pages as rendered pages - view them so you see "
            "tables, positioning/view grids, arrows, and dial gauges as printed, not "
            "only the extracted text. Cite what you actually see on the page."
        )
    text = _html_chunk_text(chunk)
    origin = ingested.source.resolved_url or ingested.native_source_path.name
    conversion_note = (
        "This text is a Markdown conversion of the document, rendered to plain "
        "text: charts, positioning grids, dots, arrows and other graphics may be "
        "missing, and multi-column pages may be interleaved. Never infer a view "
        "from a visual you cannot see in the text.\n"
        if ingested.source.source_type == "md"
        else ""
    )
    return (
        f"This chunk is the extracted text below "
        f"(locator {chunk.locator}, from {origin}).\n"
        f"{conversion_note}"
        "If the text references a chart, figure, or infographic you cannot see, "
        "say so in your summary rather than guessing its contents.\n\n"
        "-----\n"
        f"{text}\n"
        "-----"
    )


def _html_chunk_text(chunk: Chunk) -> str:
    text = Path(chunk.source_path).read_text(encoding="utf-8")
    match = re.fullmatch(r"char:(\d+)-(\d+)", chunk.chunk_id)
    if match:
        return text[int(match.group(1)) : int(match.group(2))]
    return text


def _memory_header(source: SourceRecord) -> str:
    return f"# {source.firm} - {source.source}  ({source.source_id})\n"


def _append_memory(
    memory_path: Path,
    chunk_id: str,
    summary: str,
    candidates: list[CandidateCall],
) -> None:
    ledger = (
        "; ".join(f"{c.sub_asset_class}={c.view}[{c.locator}]" for c in candidates)
        if candidates
        else "none"
    )
    block = f"\n## Chunk {chunk_id}\nSummary: {summary}\nCandidates: {ledger}\n"
    with memory_path.open("a", encoding="utf-8") as handle:
        handle.write(block)


def _brain_text() -> str:
    if BRAIN_PROMPT.exists():
        return BRAIN_PROMPT.read_text(encoding="utf-8").strip()
    return "No calibration examples are available for this run."


def _conventions_text() -> str:
    if CONVENTIONS_FILE.exists():
        return CONVENTIONS_FILE.read_text(encoding="utf-8").strip()
    return "No house conventions are available for this run."


def load_sources(spec: str) -> list[SourceRecord]:
    """Resolve a ``--sources`` value to source records.

    ``pilot`` and ``target`` load the built-in CSVs; any other value is a path
    to a source CSV in the pilot column family (canonical firm/date/source/url +
    optional local_file, header aliases accepted - see ``ingest._COLUMN_ALIASES``)
    - so adding a second test set needs no code change. The <=20 source limit is
    enforced by the caller for every case.
    """
    if spec == "pilot":
        return load_pilot_sources()
    if spec == "target":
        return load_target_sources()
    return load_pilot_sources(spec)


def resolve_engine_settings(engine: str, model: str | None, effort: str | None) -> tuple[str, str]:
    """Validate and resolve the per-run model/effort so every run states them.

    claude: model is required (alias like ``fable``/``opus``/``sonnet`` or a
    full name) so a run never silently inherits the CLI's settings default.
    codex: model defaults to ``DEFAULT_CODEX_MODEL`` when omitted and must be a
    member of the codex allowlist otherwise. effort is required for both engines
    and must be a level the engine accepts (the codex floor is ``low`` - no
    ``minimal``).
    """
    if engine == "codex":
        model = resolve_codex_model(model)  # None → default; off-list → error
    elif not model:
        raise ValueError("--model is required with --engine claude (e.g. fable, opus, sonnet)")

    efforts = ENGINE_CONFIGS[engine].efforts
    if not effort:
        raise ValueError(f"--effort is required; {engine} accepts: {', '.join(efforts)}")
    if effort not in efforts:
        raise ValueError(f"unknown {engine} effort {effort!r}; accepts: {', '.join(efforts)}")
    return model, effort


def build_parser() -> argparse.ArgumentParser:
    """The run CLI's argument parser. Extracted so the resolved no-flags defaults
    (the model matrix) are testable without invoking the pipeline."""
    parser = argparse.ArgumentParser(description="Markets Recon POC runner")
    parser.add_argument(
        "--sources",
        default="pilot",
        help="'pilot', 'target', or a path to a source CSV (canonical columns "
        "firm/date/source/url + optional local_file, with header aliases like "
        "Entity Name/Title/External link accepted). A .pdf URL is downloaded and "
        "read as a PDF; any other URL takes the HTML path. <=20 sources.",
    )
    parser.add_argument("--run-id", required=True)
    # Defaults below are the model matrix (revamp 2026-10-23); a bare
    # `python -m src.run --run-id X --sources ...` resolves to it fully.
    parser.add_argument("--engine", choices=("claude", "codex"), default="codex")
    parser.add_argument(
        "--model",
        default="gpt-6.1-sol",
        help="model for the analyze run: with --engine codex one of "
        f"{', '.join(CODEX_MODELS)} (default gpt-6.1-sol); required with --engine claude",
    )
    parser.add_argument(
        "--effort",
        default="high",
        help="reasoning effort for the analyze run (default high): claude "
        "low|medium|high|xhigh|max; codex low|medium|high|xhigh",
    )
    parser.add_argument(
        "--checker-engine",
        choices=("claude", "codex"),
        default="claude",
        help="engine for the second-reader checker step (default claude)",
    )
    parser.add_argument(
        "--checker-model",
        default="claude-opus-5-5",
        help="model for the checker step (default claude-opus-5-5; required with --checker-engine claude)",
    )
    parser.add_argument(
        "--checker-effort",
        default="high",
        help="reasoning effort for the checker step (default high)",
    )
    parser.add_argument(
        "--checker-context",
        action="store_true",
        help="attach a deterministically routed evidence-context window to each "
        "checked candidate: the quote's surrounding paragraphs on a clean page, "
        "or a visual-fallback flag on a scrambled/OCR/subsequence page (opt-in; "
        "off by default pending A/B validation)",
    )
    parser.add_argument(
        "--arbiter-engine",
        choices=("claude", "codex"),
        default="claude",
        help="engine for the conflict-arbiter step (default claude)",
    )
    parser.add_argument(
        "--arbiter-model",
        default="claude-haiku-5-5",
        help="model for the arbiter step (default claude-haiku-5-5; required with --arbiter-engine claude)",
    )
    parser.add_argument(
        "--arbiter-effort",
        default="high",
        help="reasoning effort for the arbiter step (default high)",
    )
    parser.add_argument(
        "--group-notes",
        help="path to a free-text analyst notes file naming which sources to "
        "combine; resolved once into work/<run-id>/groups.json before analysis",
    )
    parser.add_argument(
        "--grouper-engine",
        choices=("claude", "codex"),
        default="codex",
        help="engine for the group-notes resolver step (default codex)",
    )
    parser.add_argument(
        "--grouper-model",
        default="gpt-6-luna",
        help="model for the group-notes resolver (default gpt-6-luna; required with --grouper-engine claude)",
    )
    parser.add_argument(
        "--grouper-effort",
        default="high",
        help="reasoning effort for the group-notes resolver (default high)",
    )
    parser.add_argument(
        "--quote-visual-engine",
        choices=("claude", "codex"),
        default="codex",
        help="engine for tier-3 visual quote verification (default codex)",
    )
    parser.add_argument(
        "--quote-visual-model",
        default="gpt-6-luna",
        help="model for tier-3 visual quote verification (default gpt-6-luna)",
    )
    parser.add_argument(
        "--quote-visual-effort",
        default="high",
        help="reasoning effort for tier-3 visual quote verification (default high)",
    )
    parser.add_argument(
        "--evidence-engine",
        choices=("claude", "codex"),
        default=sharedevidence.DEFAULT_ENGINE,
        help=f"engine for the shared-evidence gate (default {sharedevidence.DEFAULT_ENGINE})",
    )
    parser.add_argument(
        "--evidence-model",
        default=sharedevidence.DEFAULT_MODEL,
        help=f"model for the shared-evidence gate (default {sharedevidence.DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--evidence-effort",
        default=sharedevidence.DEFAULT_EFFORT,
        help=f"reasoning effort for the shared-evidence gate (default {sharedevidence.DEFAULT_EFFORT})",
    )
    parser.add_argument(
        "--no-evidence-gate",
        action="store_true",
        help="skip the shared-evidence gate (one passage cited for several assets "
        "then keeps every call, as before 2026-10)",
    )
    parser.add_argument(
        "--out-root",
        help="optional root for this run's artifacts: writes to <out-root>/<run-id>/ "
        "and <out-root>/work/<run-id>/ instead of runs/<run-id> and work/<run-id> "
        "(used to keep a production batch out of the test/pilot trees). Default keeps "
        "today's paths.",
    )
    parser.add_argument("--ingest-only", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    sources = load_sources(args.sources)
    enforce_source_limit(sources)

    if args.ingest_only:
        work_dir = (
            Path(args.out_root) / "work" / args.run_id
            if args.out_root
            else Path("work") / args.run_id
        )
        for source in sources:
            create_snapshot(source, work_dir)
        return 0

    group_notes_text = None
    grouper_model, grouper_effort = args.grouper_model, args.grouper_effort
    try:
        model, effort = resolve_engine_settings(args.engine, args.model, args.effort)
        checker_model, checker_effort = resolve_engine_settings(
            args.checker_engine, args.checker_model, args.checker_effort
        )
        arbiter_model, arbiter_effort = resolve_engine_settings(
            args.arbiter_engine, args.arbiter_model, args.arbiter_effort
        )
        if args.group_notes:
            group_notes_text = Path(args.group_notes).read_text(encoding="utf-8")
            grouper_model, grouper_effort = resolve_engine_settings(
                args.grouper_engine, args.grouper_model, args.grouper_effort
            )
        quote_visual_model, quote_visual_effort = resolve_engine_settings(
            args.quote_visual_engine, args.quote_visual_model, args.quote_visual_effort
        )
        evidence_model, evidence_effort = resolve_engine_settings(
            args.evidence_engine, args.evidence_model, args.evidence_effort
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    grouper_line = (
        f" | grouper={args.grouper_engine}/{grouper_model}/{grouper_effort}"
        if group_notes_text
        else ""
    )
    print(
        f"run {args.run_id}: engine={args.engine} model={model} effort={effort} | "
        f"checker={args.checker_engine}/{checker_model}/{checker_effort}"
        f"{'+context' if args.checker_context else ''} | "
        f"arbiter={args.arbiter_engine}/{arbiter_model}/{arbiter_effort} | "
        f"quote_visual={args.quote_visual_engine}/{quote_visual_model}/{quote_visual_effort} | "
        f"evidence_gate={'off' if args.no_evidence_gate else f'{args.evidence_engine}/{evidence_model}/{evidence_effort}'}"
        f"{grouper_line}"
    )
    result, chunk_failures, run_dir = run_pipeline(
        sources=sources,
        run_id=args.run_id,
        engine=args.engine,
        model=model,
        effort=effort,
        checker_engine=args.checker_engine,
        checker_model=checker_model,
        checker_effort=checker_effort,
        checker_context=args.checker_context,
        arbiter_engine=args.arbiter_engine,
        arbiter_model=arbiter_model,
        arbiter_effort=arbiter_effort,
        group_notes_text=group_notes_text,
        group_notes_path=args.group_notes,
        grouper_engine=args.grouper_engine,
        grouper_model=grouper_model,
        grouper_effort=grouper_effort,
        quote_visual_engine=args.quote_visual_engine,
        quote_visual_model=quote_visual_model,
        quote_visual_effort=quote_visual_effort,
        evidence_gate=not args.no_evidence_gate,
        evidence_engine=args.evidence_engine,
        evidence_model=evidence_model,
        evidence_effort=evidence_effort,
        out_root=args.out_root,
    )
    kept = len(result.output_rows)
    failed = len(result.failures) + len(chunk_failures)
    print(f"run {args.run_id}: {kept} calls kept, {failed} failed -> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
