"""E2: the recorded upstream digest must match the vendored stock sources.

tools/upstream-digest-check.py ties three artifacts together: the recorded
manifest (tools/upstream/<release>.sha256), the vendored stock
`kern_pledge.c.orig` + patched `kern_pledge.c`, and the `sys/kern/kern_pledge.c`
hunk inside awg-openbsd.patch. A baseline move that refreshes only one of the
three is silent upstream drift; the gate must fail on it.

These tests run the check at HEAD (passes) and with each artifact drifted one
line (fails), so a regression that weakens the check is caught.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "upstream-digest-check.py"

# Minimal repo layout the check reads (see VENDORED in the tool).
PARTS = ("tools/upstream/8.0.sha256", "src/kern_pledge.c.orig",
         "src/kern_pledge.c", "awg-openbsd.patch")


def mini_repo(dest: Path) -> Path:
    for rel in PARTS:
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    return dest


def run_check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--repo", str(repo)],
                          capture_output=True, text=True)


class UpstreamDigestCheck(unittest.TestCase):
    def test_passes_at_head(self):
        proc = subprocess.run([sys.executable, str(TOOL)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("upstream digest check: OpenBSD 8.0", proc.stdout)

    def test_stock_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            repo = mini_repo(Path(d))
            orig = repo / "src/kern_pledge.c.orig"
            text = orig.read_text()
            orig.write_text(text.replace("case SIOCATMARK:", "case SIOCATMARK2:", 1))
            proc = run_check(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("digest:kern/kern_pledge.c", proc.stdout)

    def test_patched_copy_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            repo = mini_repo(Path(d))
            patched = repo / "src/kern_pledge.c"
            patched.write_text(patched.read_text().replace(
                "case SIOCGAWG:", "case SIOCGAWG3:", 1))
            proc = run_check(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("digest:kern/kern_pledge.c.awg", proc.stdout)

    def test_manifest_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            repo = mini_repo(Path(d))
            man = repo / "tools/upstream/8.0.sha256"
            lines = man.read_text().splitlines()
            for i, line in enumerate(lines):
                if line.split()[-1] == "kern/kern_pledge.c":
                    digest, rel = line.split(None, 1)
                    flipped = ("0" if digest[0] != "0" else "1") + digest[1:]
                    lines[i] = f"{flipped}  {rel}"
                    break
            else:
                self.fail("manifest has no kern/kern_pledge.c entry")
            man.write_text("\n".join(lines) + "\n")
            proc = run_check(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_patch_artifact_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            repo = mini_repo(Path(d))
            patch_file = repo / "awg-openbsd.patch"
            text = patch_file.read_text()
            sec = text.find("--- sys/kern/kern_pledge.c")
            self.assertGreater(sec, 0)
            idx = text.find("case SIOCSAWG:", sec)
            self.assertGreater(idx, sec)
            patch_file.write_text(text[:idx] + "case SIOCSAWG9:" + text[idx + len("case SIOCSAWG:"):])
            proc = run_check(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("hunk:awg-openbsd.patch", proc.stdout)


if __name__ == "__main__":
    unittest.main()
