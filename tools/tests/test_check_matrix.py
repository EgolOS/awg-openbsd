#!/usr/bin/env python3
"""Behavioural regressions for tools/check_matrix_totals.py.

Each test copies the real TRACEABILITY.md / SECURITY-AUDIT.md into a temporary
repo root, mutates exactly one thing, and asserts the validator fails (or passes
for the untouched baseline). These run under `make check-tests`.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class MatrixValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="matrix-test-"))
        (self.tmp / "tools").mkdir()
        (self.tmp / "docs").mkdir()
        shutil.copy(REPO_ROOT / "tools" / "check_matrix_totals.py",
                    self.tmp / "tools" / "check_matrix_totals.py")
        shutil.copy(REPO_ROOT / "docs" / "TRACEABILITY.md",
                    self.tmp / "docs" / "TRACEABILITY.md")
        shutil.copy(REPO_ROOT / "docs" / "SECURITY-AUDIT.md",
                    self.tmp / "docs" / "SECURITY-AUDIT.md")
        shutil.copy(REPO_ROOT / "docs" / "RELEASE-NOTES.md",
                    self.tmp / "docs" / "RELEASE-NOTES.md")
        (self.tmp / "src").mkdir()
        for name in ("if_awg.c", "if_awg.h"):
            shutil.copy(REPO_ROOT / "src" / name, self.tmp / "src" / name)
        self.matrix = self.tmp / "docs" / "TRACEABILITY.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_check(self):
        proc = subprocess.run(
            [sys.executable, str(self.tmp / "tools" / "check_matrix_totals.py")],
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def edit(self, old, new, count=1):
        text = self.matrix.read_text()
        self.assertIn(old, text, f"fixture text not found: {old!r}")
        self.matrix.write_text(text.replace(old, new, count))

    def test_baseline_passes(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("VALIDATION PASSED", out)

    def test_duplicate_id_fails(self):
        self.edit("| AWG-041 | high | open", "| AWG-040 | high | open")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("duplicate requirement ID AWG-040", out)

    def test_missing_id_fails(self):
        text = self.matrix.read_text()
        line = [ln for ln in text.splitlines() if ln.startswith("| U-7 |")][0]
        self.matrix.write_text(text.replace(line + "\n", ""))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("missing source IDs U-7", out)

    def test_missing_summary_fails(self):
        self.edit("## Summary Counts", "## Counts Removed")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("missing '## Summary Counts' section", out)

    def test_stale_count_fails(self):
        # Derive the current value instead of hardcoding it, so this regression
        # cannot go stale when rows are reclassified.
        text = self.matrix.read_text()
        match = re.search(r"^\| proven \| (\d+) \|$", text, re.M)
        if match is None:
            self.fail("Summary Counts row for 'proven' not found")
        current = match.group(1)
        stale = str(int(current) + 100)
        self.edit(f"| proven | {current} |", f"| proven | {stale} |")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("Summary Counts is stale", out)
        self.assertIn(f"proven: summary={stale}", out)

    def test_fixture_evidence_cannot_be_proven(self):
        # R8-2: the same host parser adapter the matrix calls fixture-only at U-5
        # cannot be flipped to proven for a sibling row.
        self.edit(
            "host parser adapter fixture (production parser text + bash `ifconfig` stub), "
            "no shell/OpenBSD execution | fixture-only |",
            "host parser adapter fixture (production parser text + bash `ifconfig` stub), "
            "no shell/OpenBSD execution | proven |",
        )
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("fixture evidence is 'fixture-only', never 'proven'", out)

    def test_malformed_row_fails(self):
        self.edit(
            "| U-1 | Private key/PSK/HPK never on argv or in stderr (AWG-005) |",
            "| U-1 | Private key/PSK/HPK never on argv or in stderr (AWG-005)",
        )
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("malformed requirement row", out)

    def test_invalid_status_fails(self):
        text = self.matrix.read_text()
        row = [ln for ln in text.splitlines() if ln.startswith("| G-12 |")][0]
        self.matrix.write_text(text.replace(row, row.replace("| untested |", "| maybe |")))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("invalid status 'maybe'", out)

    def test_proven_without_artifact_fails(self):
        text = self.matrix.read_text()
        row = [ln for ln in text.splitlines() if ln.startswith("| G-11 |")][0]
        self.matrix.write_text(text.replace(row, row.replace(
            "`make check-patch` at this candidate: \"generated patch: current\"; generator recorded at `7cc433b`",
            "verified by inspection")))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("is 'proven' but cites no verifiable artifact", out)

    def test_awg_mirror_mismatch_fails(self):
        text = self.matrix.read_text()
        row = [ln for ln in text.splitlines() if ln.startswith("| AWG-002 |")][0]
        self.matrix.write_text(text.replace(row, row.replace("| untested |", "| proven |")))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("weakest mapped requirement K-1", out)


    def git_init(self):
        subprocess.run(["git", "init"], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.tmp,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.tmp,
                       capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.tmp,
                       capture_output=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp,
                              capture_output=True, text=True).stdout.strip()

    def test_anchor_off_topic_fails(self):
        # R9-3: L-3 once pointed at the Linux-host bullet (59) instead of recovery (60).
        self.edit(
            "| L-3 | Recovery is bounded snapshot; does not cover peer list, routes, "
            "IPv6 flags | Documented limitation | RELEASE-NOTES.md:60 — limitation, "
            "no capability claim | not applicable |",
            "| L-3 | Recovery is bounded snapshot; does not cover peer list, routes, "
            "IPv6 flags | Documented limitation | RELEASE-NOTES.md:59 — limitation, "
            "no capability claim | not applicable |",
        )
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("the anchor cites the wrong text", out)
        self.assertIn("L-3 anchors RELEASE-NOTES.md:59", out)

    def test_anchor_out_of_range_fails(self):
        self.edit("RELEASE-NOTES.md:62 — limitation", "RELEASE-NOTES.md:999 — limitation")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("out of range", out)

    def test_evidence_index_commit_without_artifact_fails(self):
        # R9-2: the index named commits that do not contain the artifact.
        sha = self.git_init()
        self.edit(
            "| `make-check.log` | `docs/evidence/2026-11-07/make-check.log` | `0e557e1` |",
            f"| `make-check.log` | `docs/evidence/2026-11-07/make-check.log` | `{sha}` |",
        )
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("is not in that commit", out)

    def test_evidence_index_commit_with_artifact_passes(self):
        evidence = self.tmp / "docs" / "evidence" / "2026-11-07"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "make-check.log").write_text("make check passed\n")
        sha = self.git_init()
        self.edit(
            "| `make-check.log` | `docs/evidence/2026-11-07/make-check.log` | `0e557e1` |",
            f"| `make-check.log` | `docs/evidence/2026-11-07/make-check.log` | `{sha}` |",
        )
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("VALIDATION PASSED", out)

if __name__ == "__main__":
    unittest.main(verbosity=2)
