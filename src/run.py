"""Run-level orchestration entry point for Phase 1 scaffolding."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.ingest import create_snapshot, enforce_source_limit, load_pilot_sources, load_target_sources


def main() -> int:
    parser = argparse.ArgumentParser(description="Markets Recon POC runner")
    parser.add_argument("--sources", choices=("pilot", "target"), default="pilot")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ingest-only", action="store_true")
    args = parser.parse_args()

    sources = load_pilot_sources() if args.sources == "pilot" else load_target_sources()
    enforce_source_limit(sources)
    work_dir = Path("work") / args.run_id

    if args.ingest_only:
        for source in sources:
            create_snapshot(source, work_dir)
        return 0

    raise NotImplementedError("Phase 2 LLM analysis is not wired yet; use --ingest-only")


if __name__ == "__main__":
    raise SystemExit(main())
