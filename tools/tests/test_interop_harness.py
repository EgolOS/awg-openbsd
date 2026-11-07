"""A failed sender must never become an interop PASS."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InteropHarness(unittest.TestCase):
    def test_requires_explicit_disposable_lab_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            remote = Path(d) / 'remote'
            touched = Path(d) / 'touched'
            remote.write_text('#!/bin/sh\n: > "$TOUCHED"\nexit 1\n')
            remote.chmod(0o700)
            env = dict(os.environ, A_SSH=str(remote), B_SSH=str(remote), TOUCHED=str(touched))
            env.pop('AWG_LAB_DISPOSABLE', None)
            r = subprocess.run(['sh', str(ROOT / 'tools/lab/interop-test.sh')], env=env,
                               capture_output=True, text=True, timeout=10)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(touched.exists(), 'must refuse before any remote command')

    def run_broken_transfer(self, sender_status):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            remote = work / 'remote'
            remote.write_text('''#!/bin/sh
command=$(cat)
case "$command" in
 *'pfctl -si'*) echo 'Status: Disabled' ;;
 *'mktemp -d'*) echo /var/tmp/awg-interop.fixture ;;
 *'ifconfig awg0 >/dev/null'*) exit 1 ;;
 *'last handshake'*) echo 1 ;;
 *'nc -N'*) exit "${SEND_STATUS:-7}" ;;
 *'wc -c'*) echo 0 ;;
 *'sha256 -q'*) echo wrong ;;
 *'sed -n'*'awgpubkey'*) echo dummy-public-key ;;
 *'sed -n'*'tx:'*) echo 100 ;;
 *) : ;;
esac
''')
            remote.chmod(0o700)
            (work / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            (work / 'sleep').chmod(0o700)
            env = dict(os.environ, PATH=str(work) + ':' + os.environ['PATH'],
                       A_SSH=str(remote), B_SSH=str(remote), AWG_LAB_DISPOSABLE='1', SEND_STATUS=sender_status)
            r = subprocess.run(['sh', str(ROOT / 'tools/lab/interop-test.sh')], env=env,
                               capture_output=True, text=True, timeout=10)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertNotIn('RESULT: interop passed', r.stdout)
            self.assertIn('FAIL:', r.stdout)

    def test_sender_failure_is_reported(self):
        self.run_broken_transfer('7')

    def test_receiver_corruption_is_reported(self):
        self.run_broken_transfer('0')


if __name__ == '__main__':
    unittest.main()
