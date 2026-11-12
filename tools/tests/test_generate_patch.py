"""R4-03: a clean tracked checkout must regenerate the pledge hunk.

The patch generator diffs src/kern_pledge.c against src/kern_pledge.c.orig.
When *.orig was gitignored the baseline vanished from a fresh clone, diff
failed, awk succeeded, and the pipeline hid the failure: awg-openbsd.patch
was regenerated WITHOUT the kern_pledge.c hunk and --check then passed.

These tests export HEAD with `git archive` (the same tree a fresh clone
gets), run generate-patch.sh in the export, and assert the pledge cases
survive. They fail on the pre-fix tree and pass after the .gitignore
exception + fatal diff/awk.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git_archive(dest: Path) -> None:
    # AWG_SOURCE_REPO lets the suite run from inside a previous export
    # (which has no .git) and still archive the real repository HEAD.
    repo = Path(os.environ.get('AWG_SOURCE_REPO') or ROOT)
    with open(dest / 'head.tar', 'wb') as tar:
        proc = subprocess.run(['git', 'archive', 'HEAD'], cwd=repo,
                              stdout=tar, stderr=subprocess.PIPE, text=True)
    assert proc.returncode == 0, proc.stderr
    proc = subprocess.run(['tar', '-x', '-C', str(dest), '-f', str(dest / 'head.tar')],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    os.unlink(dest / 'head.tar')


def run_gen(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['sh', str(tree / 'generate-patch.sh'), *args],
                          cwd=tree, capture_output=True, text=True)


def pledge_section(patch_text: str) -> str:
    """The diff section whose --- header names sys/kern/kern_pledge.c."""
    out, inside = [], False
    for line in patch_text.splitlines():
        if line.startswith('--- '):
            inside = line.split('\t')[0].strip() == '--- sys/kern/kern_pledge.c'
        if inside:
            out.append(line)
    return '\n'.join(out)


@unittest.skipIf(shutil.which('git') is None, 'git required for the archive export')
class GeneratePatchCleanExport(unittest.TestCase):
    def test_clean_export_carries_pledge_hunk(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            git_archive(tree)
            r = run_gen(tree)
            self.assertEqual(r.returncode, 0, r.stderr)
            patch = (tree / 'awg-openbsd.patch').read_text()
            section = pledge_section(patch)
            self.assertIn('case SIOCSAWG', section,
                          'regenerated patch lost the SIOCSAWG case in kern_pledge.c')
            self.assertIn('case SIOCGAWG', section,
                          'regenerated patch lost the SIOCGAWG case in kern_pledge.c')
            self.assertIn('net/if_awg.h', section,
                          'kern_pledge.c hunk must include the if_awg.h header')

    def test_check_passes_on_clean_export(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            git_archive(tree)
            r = run_gen(tree, '--check')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('up to date', r.stdout)

    def test_check_nonzero_when_baseline_absent(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            git_archive(tree)
            os.unlink(tree / 'src' / 'kern_pledge.c.orig')
            r = run_gen(tree, '--check')
            self.assertNotEqual(r.returncode, 0,
                                '--check must fail when the baseline is absent')
            self.assertIn('kern_pledge.c.orig', r.stderr + r.stdout)

    def test_regenerate_nonzero_when_baseline_absent(self):
        """Plain regeneration must not silently emit a patch without the hunk."""
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            git_archive(tree)
            os.unlink(tree / 'src' / 'kern_pledge.c.orig')
            r = run_gen(tree)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('kern_pledge.c.orig', r.stderr)


if __name__ == '__main__':
    unittest.main()
