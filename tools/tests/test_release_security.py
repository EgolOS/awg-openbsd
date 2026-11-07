"""Offline regressions using real signify signatures, never release credentials."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SIGNIFY = shutil.which('signify') or shutil.which('signify-openbsd')


class ReleaseSecurity(unittest.TestCase):
    def test_ci_never_schedules_persistent_self_hosted_runner(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertNotIn('runs-on: [self-hosted', workflow)
        self.assertIn('contents: read', workflow)

    @unittest.skipUnless(SIGNIFY, 'signify not installed')
    def test_signed_archive_checks_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            env = dict(os.environ, LC_ALL='C')
            def command(*args):
                return subprocess.run(args, cwd=work, env=env, capture_output=True)
            def require(*args):
                r = command(*args)
                self.assertEqual(r.returncode, 0, r.stderr.decode())
            require(SIGNIFY, '-G', '-n', '-p', 'key.pub', '-s', 'key.sec')
            archive = work / 'sys.tar.gz'
            archive.write_bytes(b'deterministic non-secret archive fixture\n')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            # Absent unrelated archives must not make selected-file verification fail.
            (work / 'SHA256').write_text(f'SHA256 (sys.tar.gz) = {digest}\nSHA256 (src.tar.gz) = {"0" * 64}\n')
            require(SIGNIFY, '-S', '-e', '-s', 'key.sec', '-m', 'SHA256', '-x', 'SHA256.sig')
            helper = ROOT / 'tools/verify-release.sh'
            def verify():
                return command('sh', str(helper), str(work / 'key.pub'), str(work / 'SHA256.sig'), str(archive))
            self.assertEqual(verify().returncode, 0, 'valid signed selected file must pass')
            archive.write_bytes(b'wrong checksum')
            self.assertNotEqual(verify().returncode, 0)
            archive.write_bytes(b'deterministic non-secret archive fixture\n')
            signed = (work / 'SHA256.sig').read_bytes()
            (work / 'SHA256.sig').write_bytes(b'invalid signature\n')
            self.assertNotEqual(verify().returncode, 0)
            (work / 'SHA256.sig').unlink()
            self.assertNotEqual(verify().returncode, 0)
            (work / 'SHA256.sig').write_bytes(signed)
            require(SIGNIFY, '-G', '-n', '-p', 'wrong.pub', '-s', 'wrong.sec')
            (work / 'key.pub').write_bytes((work / 'wrong.pub').read_bytes())
            self.assertNotEqual(verify().returncode, 0)


if __name__ == '__main__':
    unittest.main()
