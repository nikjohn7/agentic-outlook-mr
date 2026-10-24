from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from src import provenance


def _tree(root: Path) -> None:
    (root / "src").mkdir()
    (root / "prompts").mkdir()
    (root / "excel-file").mkdir()
    (root / "src" / "run.py").write_text("print('run')\n", encoding="utf-8")
    (root / "prompts" / "analyze_chunk.md").write_text("Extract calls.\n", encoding="utf-8")
    (root / "excel-file" / "Asset Class List - Locked.csv").write_text("a,b\n", encoding="utf-8")
    (root / "excel-file" / "Target Output.csv").write_text("x\n", encoding="utf-8")
    (root / "prompts" / "REGISTRY.md").write_text("catalog\n", encoding="utf-8")
    (root / "notes.txt").write_text("not tracked\n", encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=root, check=True, capture_output=True,
    )


class ProvenanceTest(unittest.TestCase):
    def test_hashes_cover_code_prompts_and_taxonomy_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _tree(root)
            hashes = provenance.file_hashes(root)
        self.assertEqual(
            ["excel-file/Asset Class List - Locked.csv", "prompts/analyze_chunk.md", "src/run.py"],
            sorted(hashes),
        )

    def test_implementation_hash_follows_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _tree(root)
            before = provenance.implementation_hash(provenance.file_hashes(root))
            (root / "notes.txt").write_text("changed\n", encoding="utf-8")
            self.assertEqual(before, provenance.implementation_hash(provenance.file_hashes(root)))
            (root / "prompts" / "analyze_chunk.md").write_text("Extract more.\n", encoding="utf-8")
            self.assertNotEqual(before, provenance.implementation_hash(provenance.file_hashes(root)))

    def test_outside_git_the_commit_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _tree(root)
            info = provenance.collect(root)
        self.assertEqual("unknown", info["commit"])
        lines = provenance.manifest_lines(info)
        self.assertIn("- working tree (src, prompts, taxonomy): unknown", lines)

    def test_records_commit_and_uncommitted_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _tree(root)
            _git(root, "init", "-q")
            _git(root, "add", ".")
            _git(root, "commit", "-q", "-m", "init")
            clean = provenance.collect(root)
            (root / "prompts" / "analyze_chunk.md").write_text("Edited.\n", encoding="utf-8")
            (root / "notes.txt").write_text("edited\n", encoding="utf-8")  # not a run input
            # Neither the prompt catalog nor another workbook sheet is a run input.
            (root / "prompts" / "REGISTRY.md").write_text("catalog edit\n", encoding="utf-8")
            (root / "excel-file" / "Target Output.csv").write_text("y\n", encoding="utf-8")
            dirty = provenance.collect(root)
        self.assertRegex(clean["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual([], clean["dirty"])
        self.assertEqual(["prompts/analyze_chunk.md"], dirty["dirty"])
        lines = provenance.manifest_lines(dirty)
        self.assertIn(
            "- working tree (src, prompts, taxonomy): uncommitted changes in 1 file(s): "
            "prompts/analyze_chunk.md",
            lines,
        )
        self.assertIn("- working tree (src, prompts, taxonomy): clean", provenance.manifest_lines(clean))
        # Prompt and taxonomy hashes are listed; code is covered by the commit.
        self.assertTrue(any(line.startswith("- prompts/analyze_chunk.md: ") for line in lines))
        self.assertFalse(any(line.startswith("- src/") for line in lines))


if __name__ == "__main__":
    unittest.main()
