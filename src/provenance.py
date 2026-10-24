"""What produced a run: code commit, working-tree state and content hashes.

Written into every run manifest so a frozen output states exactly which code,
prompts and taxonomy made it. It records; it never blocks a run. Comparing two
runs (or a run against a validated control) is a matter of diffing these
lines. Git is optional: outside a repository the commit reads `unknown`.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The inputs that decide a run's calls: pipeline code, every prompt, the
# locked taxonomy.
TRACKED_GLOBS = ("src/*.py", "prompts/*.md", "excel-file/Asset Class List - Locked.csv")
# Dirty paths listed in the manifest before it summarizes the rest.
MAX_DIRTY_LISTED = 10


def _git(args: list[str], root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def tracked_files(root: Path = PROJECT_ROOT) -> list[Path]:
    files: set[Path] = set()
    for pattern in TRACKED_GLOBS:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def file_hashes(root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Relative path -> SHA-256 of every tracked input."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tracked_files(root)
    }


def implementation_hash(hashes: dict[str, str]) -> str:
    """One digest over every tracked input (path and content), so two runs
    can be compared at a glance."""
    digest = hashlib.sha256()
    for name in sorted(hashes):
        digest.update(f"{name}\0{hashes[name]}\n".encode("utf-8"))
    return digest.hexdigest()


def collect(root: Path = PROJECT_ROOT) -> dict[str, object]:
    hashes = file_hashes(root)
    commit = (_git(["rev-parse", "HEAD"], root) or "").strip()
    status = _git(["status", "--porcelain", "--", "src", "prompts", "excel-file"], root)
    # Porcelain lines are `XY path`; the status columns may be spaces.
    dirty = [line[3:] for line in (status or "").splitlines() if line.strip()]
    return {
        "commit": commit or "unknown",
        "dirty": dirty if status is not None else None,
        "implementation": implementation_hash(hashes),
        "files": hashes,
    }


def manifest_lines(provenance: dict[str, object]) -> list[str]:
    """The manifest's `## Provenance` section."""
    dirty = provenance.get("dirty")
    if dirty is None:
        tree = "unknown"
    elif not dirty:
        tree = "clean"
    else:
        listed = ", ".join(dirty[:MAX_DIRTY_LISTED])
        more = f" (+{len(dirty) - MAX_DIRTY_LISTED} more)" if len(dirty) > MAX_DIRTY_LISTED else ""
        tree = f"uncommitted changes in {len(dirty)} file(s): {listed}{more}"
    lines = [
        "## Provenance",
        f"- code commit: {provenance['commit']}",
        f"- working tree (src, prompts, taxonomy): {tree}",
        f"- implementation hash: {provenance['implementation']}",
    ]
    files = provenance.get("files") or {}
    lines += [f"- {name}: {digest[:12]}" for name, digest in files.items() if not name.startswith("src/")]
    return lines + [""]
