"""R5-R4-04: the patched kernel file must carry a known-good identity.

install.sh accepted any kern_pledge.c that merely contained the string
SIOCSAWG, so a one-line bogus file passed and a partial patch (only one
of the two ioctl cases) was installed. The gate now accepts exactly two
hashes — the stock release baseline and the exact patched file recorded
in tools/upstream/<release>.sha256 — and refuses everything else.

Negatives covered here: a bogus one-liner, a partial patch (SIOCSAWG
only), and an unknown release with no recorded baseline.
"""
import difflib
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'install.sh').read_text()

PRELUDE = '\n'.join([
    'log()  { echo "==> $*"; echo "==> $*" >> "$LOGFILE"; }',
    'die()  { echo "ERROR: $*" >&2; exit 1; }',
])
SHIM_SHA256 = ('#!/usr/bin/env python3\nimport hashlib,sys\n'
               'print(hashlib.sha256(open(sys.argv[-1],"rb").read()).hexdigest())\n')


def extract(name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\)\s*\{.*?^\}', SOURCE)
    assert match, 'install.sh needs a %s() function' % name
    return match.group()


BODY = (extract('install_pledge_hunk') + '\ninstall_pledge_hunk\n')

# A minimal stock kern_pledge.c and the AWG hunk the installer extracts
# from awg-openbsd.patch (generated with unified_diff so patch(1) accepts
# the context exactly).
STOCK = (
    '#include <sys/param.h>\n'
    '#include <sys/socket.h>\n'
    '\n'
    'int\n'
    'pledge_ioctl(int cmd)\n'
    '{\n'
    '\tswitch (cmd) {\n'
    '\tcase SIOCGIFADDR:\n'
    '\t\treturn 1;\n'
    '\t}\n'
    '}\n')
PATCHED = (
    '#include <sys/param.h>\n'
    '#include <sys/socket.h>\n'
    '#include <net/if_awg.h>\n'
    '\n'
    'int\n'
    'pledge_ioctl(int cmd)\n'
    '{\n'
    '\tswitch (cmd) {\n'
    '\tcase SIOCSAWG:\n'
    '\tcase SIOCGAWG:\n'
    '\tcase SIOCGIFADDR:\n'
    '\t\treturn 1;\n'
    '\t}\n'
    '}\n')
PARTIAL = STOCK.replace('\tcase SIOCGIFADDR:\n',
                        '\tcase SIOCSAWG:\n\tcase SIOCGIFADDR:\n')
BOGUS = 'int x; /* SIOCSAWG */\n'
HUNK = ''.join(difflib.unified_diff(
    STOCK.splitlines(keepends=True), PATCHED.splitlines(keepends=True),
    fromfile='sys/kern/kern_pledge.c', tofile='sys/kern/kern_pledge.c'))


class PledgeIdentity(unittest.TestCase):

    def fixture(self, w, release='8.0', base=STOCK, sha=None):
        (w / 'repo' / 'tools' / 'upstream').mkdir(parents=True)
        (w / 'repo' / 'src').mkdir(parents=True)
        (w / 'repo' / 'awg-openbsd.patch').write_text(HUNK)
        if sha is None:
            sha = ('%s  kern/kern_pledge.c\n' % hashlib.sha256(STOCK.encode()).hexdigest()
                   + '%s  kern/kern_pledge.c.awg\n' % hashlib.sha256(PATCHED.encode()).hexdigest())
        (w / 'repo' / 'tools' / 'upstream' / (release + '.sha256')).write_text(sha)
        (w / 'repo' / 'src' / 'kern_pledge.c.orig').write_text(STOCK)
        (w / 'src' / 'kern').mkdir(parents=True)
        (w / 'src' / 'kern' / 'kern_pledge.c').write_text(base)
        (w / 'work').mkdir()

    def run_hunk(self, w, shims=None):
        env = dict(os.environ, LOGFILE=str(w / 'LOGFILE'),
                   REPODIR=str(w / 'repo'), SRCDIR=str(w / 'src'), WORK=str(w / 'work'),
                   PATH=str(w) + ':' + os.environ['PATH'])
        for name, text in (shims or {}).items():
            p = w / name
            p.write_text(text)
            p.chmod(0o700)
        script = w / 'run.sh'
        script.write_text(PRELUDE + '\n' + BODY + '\n')
        return subprocess.run(['sh', str(script)], env=env,
                              capture_output=True, text=True)

    def test_stock_baseline_is_patched(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.fixture(w, base=STOCK)
            r = self.run_hunk(w, {'uname': '#!/bin/sh\necho 8.0\n',
                                  'sha256': SHIM_SHA256})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Patching', r.stdout)
            self.assertEqual((w / 'src' / 'kern' / 'kern_pledge.c').read_text(), PATCHED)

    def test_already_patched_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.fixture(w, base=PATCHED)
            r = self.run_hunk(w, {'uname': '#!/bin/sh\necho 8.0\n',
                                  'sha256': SHIM_SHA256})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('already carries', r.stdout)

    def test_bogus_one_liner_is_refused(self):
        """R5-R4-04: a file that merely contains SIOCSAWG is not the patch."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.fixture(w, base=BOGUS)
            r = self.run_hunk(w, {'uname': '#!/bin/sh\necho 8.0\n',
                                  'sha256': SHIM_SHA256})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not a known kern_pledge.c generation', r.stderr)
            self.assertEqual((w / 'src' / 'kern' / 'kern_pledge.c').read_text(), BOGUS)

    def test_partial_patch_is_refused(self):
        """Only SIOCSAWG (no SIOCGAWG) must not count as the patched file."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.fixture(w, base=PARTIAL)
            r = self.run_hunk(w, {'uname': '#!/bin/sh\necho 8.0\n',
                                  'sha256': SHIM_SHA256})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not a known kern_pledge.c generation', r.stderr)

    def test_unknown_release_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            # 7.9 records only the header digest: no kern/kern_pledge.c baseline.
            self.fixture(w, release='7.9', base=STOCK,
                         sha='%s  net/if_awg.h\n' % hashlib.sha256(PATCHED.encode()).hexdigest())
            r = self.run_hunk(w, {'uname': '#!/bin/sh\necho 7.9\n',
                                  'sha256': SHIM_SHA256})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('no 7.9 baseline recorded', r.stderr)


if __name__ == '__main__':
    unittest.main()
