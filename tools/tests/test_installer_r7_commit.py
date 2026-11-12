"""R7 commit-record failures must not leave the published kernel installed."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import test_installer_transaction as tr
from test_installer_transaction import (ShRunner,
    SHIM_SHA256, SHIM_INSTALL, SHIM_UNAME)


class R7Commit(ShRunner, unittest.TestCase):
    seed_pair = tr.InstallerLockAndPublish.seed_pair
    seed_build = tr.InstallerLockAndPublish.seed_build
    backup_env = tr.InstallerLockAndPublish.backup_env
    publish_env = tr.InstallerLockAndPublish.publish_env

    def check_commit_failure(self, rollback_fails, fail_at='rename'):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            body = tr.InstallerLockAndPublish.PUBLISH_BODY.replace('publish_pair\n',
                'kernel_object_path() { echo "$KERNEL_OBJ"; }\n'
                'verify_kernel_artifact() { return 0; }\npublish_pair\n')
            mv = ('#!/bin/sh\ncase "$1 $2" in *manifest.tmp.*)\n'
                  'if /bin/grep -q new-kernel "$BSD_FILE"; then echo forced-record-failure >&2; exit 1; fi ;;\nesac\n'
                  'exec /bin/mv "$@"\n')
            if fail_at == 'directory-fsync':
                mv = '#!/bin/sh\nexec /bin/mv "$@"\n'
                body = body.replace('"$FSYNC_HELPER" "$MANIFEST" "$(dirname "$MANIFEST")"',
                    '(echo forced-record-failure >&2; false)')
            if fail_at == 'unexpected-exit':
                body = body.replace('publish_pair\n',
                    'publish_awgctl() { echo forced-record-failure >&2; exit 7; }\npublish_pair\n')
                body = (tr.extract('finish') + '\n'
                    'verify_bsd_final() { return 0; }\nrelease_lock() { return 0; }\n'
                    'trap finish EXIT\n' + body)
            install = SHIM_INSTALL
            if rollback_fails:
                install = SHIM_INSTALL.replace('elif a: shutil.copy(a[-2], a[-1])',
                    'elif a:\n'
                    '    if ".awg-prev." in a[-2] and a[-1]==os.environ["BSD_FILE"]: sys.exit(1)\n'
                    '    shutil.copy(a[-2], a[-1])')
            r = self.run_sh(body, {'sha256': SHIM_SHA256, 'install': install,
                'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
                'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
                'ln': '#!/bin/sh\nexit 0\n', 'mv': mv}, self.publish_env(w))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('forced-record-failure', r.stderr)
            if rollback_fails:
                self.assertEqual(r.returncode, 21, r.stderr)
                self.assertIn('rollback failed', r.stderr.lower())
                self.assertEqual((w / 'bsd').read_bytes(), b'new-kernel\n')
            else:
                self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n', r.stderr)
                self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
                self.assertEqual((w / 'if_awg.h').read_bytes(), b'header\n')
                self.assertEqual((w / 'awgctl').read_bytes(), b'ctl-bytes\n')
                self.assertEqual(r.returncode, 20, r.stderr)
                self.assertIn('rolled back', r.stderr.lower())
                self.assertNotIn(hashlib.sha256(b'new-kernel\n').hexdigest(),
                                 (w / 'manifest').read_text())

    def test_record_failure_rolls_back(self):
        self.check_commit_failure(False)

    def test_record_failure_reports_failed_rollback(self):
        self.check_commit_failure(True)

    def test_directory_fsync_failure_after_manifest_rename_rolls_back(self):
        self.check_commit_failure(False, 'directory-fsync')

    def test_unexpected_exit_after_kernel_rolls_back_in_exit_trap(self):
        self.check_commit_failure(False, 'unexpected-exit')


if __name__ == '__main__':
    unittest.main()
