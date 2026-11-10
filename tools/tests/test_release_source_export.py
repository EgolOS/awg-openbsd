"""E1-SOURCE: the published release must build awgctl from a tracked-only export.

The v0.2.1-rc1 publication lost sbin/awgctl/{Makefile,awgctl.8,awgctl.c}
because .gitignore carried an unanchored `awgctl` pattern: `git add -A`
skipped the ignored source directory while `make check` still compiled the
untracked on-disk copies, so the gate passed on a warmed tree. This
regression exports the committed HEAD with `git archive` (tracked files
only, no build-warmed working tree) and proves `make awgctl` compiles from
that export, plus a negative control proving the build can fail.

The Linux host build (gcc -DAWGCTL_LINUX) is a smoke build of the source;
it is not the OpenBSD native ABI/runtime.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORT_FILES = ['sbin/awgctl/Makefile', 'sbin/awgctl/awgctl.8', 'sbin/awgctl/awgctl.c']


def repo_dir() -> Path:
    """Repo to archive. AWG_SOURCE_REPO lets a gate run the suite from
    inside a previous export (which has no .git) against the real repo."""
    return Path(os.environ.get('AWG_SOURCE_REPO') or ROOT)


def git_archive(dest: Path) -> None:
    """Export the committed HEAD (not the dirty worktree) into dest."""
    with open(dest / 'head.tar', 'wb') as tar:
        subprocess.run(
            ['git', '-C', str(repo_dir()), 'archive', '--format=tar', 'HEAD'],
            check=True, stdout=tar,
        )
    subprocess.run(
        ['tar', '-xf', str(dest / 'head.tar'), '-C', str(dest)],
        check=True,
    )
    (dest / 'head.tar').unlink()


class TrackedExportBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.export = Path(cls._tmp.name)
        git_archive(cls.export)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_export_contains_awgctl_source_man_and_makefile(self):
        for rel in EXPORT_FILES:
            self.assertTrue((self.export / rel).is_file(),
                            f'tracked export is missing {rel}')

    def test_make_awgctl_compiles_from_tracked_export(self):
        r = subprocess.run(['make', 'awgctl'], cwd=self.export,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         f'make awgctl failed in clean export:\n{r.stderr}')
        self.assertTrue((self.export / 'awgctl').is_file(),
                        'make awgctl produced no binary in the export')

    def test_negative_control_missing_source_fails_make(self):
        """Remove the source from the export; the real make must fail."""
        with tempfile.TemporaryDirectory() as d:
            export = Path(d)
            git_archive(export)
            (export / 'sbin/awgctl/awgctl.c').unlink()
            r = subprocess.run(['make', 'awgctl'], cwd=export,
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0,
                                'make awgctl must fail without awgctl.c')


if __name__ == '__main__':
    unittest.main()
