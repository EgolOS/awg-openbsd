"""Installer gates are isolated verbatim; never invoke installation on the host."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallerSource(unittest.TestCase):
    def check_source(self, stale=False, patched=False):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            tree = w / 'ifconfig'; tree.mkdir()
            good = b'/* deterministic source revision fixture */\n'
            (tree / 'ifconfig.c').write_bytes(b'stale source' if stale else good)
            if patched:
                (tree / 'ifconfig.c.orig').write_bytes(good)
                (tree / 'ifconfig.c').write_bytes(b'patched awgversion')
            digest = hashlib.sha256(good).hexdigest()
            manifest = w / 'baseline.sha256'
            manifest.write_text(digest + '  sbin/ifconfig/ifconfig.c\n')
            source = (ROOT / 'install.sh').read_text()
            match = re.search(r'(?ms)^verify_ifconfig_source\(\) \{.*?^\}', source)
            self.assertIsNotNone(match, 'installer needs a fail-closed source identity gate')
            shim = w / 'sha256'
            shim.write_text('#!/usr/bin/env python3\nimport hashlib,sys\nprint(hashlib.sha256(open(sys.argv[-1],"rb").read()).hexdigest())\n')
            shim.chmod(0o700)
            script = 'set -eu\ndie() { echo "$*" >&2; exit 1; }\n' + match.group() + '\nverify_ifconfig_source "$1" "$2"\n'
            env = dict(os.environ, PATH=str(w)+':'+os.environ['PATH'])
            return subprocess.run(['sh', '-c', script, '_', str(tree), str(manifest)],
                                  env=env, capture_output=True, text=True)

    def test_stale_source_rejected(self):
        r = self.check_source(stale=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('source identity', r.stderr)

    def test_recorded_source_accepted(self):
        self.assertEqual(self.check_source().returncode, 0)

    def test_patched_tree_checks_pristine_original(self):
        self.assertEqual(self.check_source(patched=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
