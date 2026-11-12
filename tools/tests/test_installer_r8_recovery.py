"""R8-R4-02: recovery must fail closed on a lost recorded backup.

Round-8 blockers on the restore path:
  * a header (or awgctl) backup recorded in the manifest that is missing on
    disk was "restored" by skipping it: the run exited 0 and logged
    "Previous pair restored" while the installed member was gone;
  * the automatic recovery paths restore kernel/tool/header/awgctl but not
    /var/db/kernel.SHA256, so after a recovery the integrity DB describes a
    kernel that is no longer installed;
  * the three outcomes (rolled back / rollback failed (state: …) / nothing
    to roll back) must be distinguishable in exit status and message.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest

import test_installer_transaction as tr
from test_installer_transaction import (ShRunner, extract,
    SHIM_SHA256, SHIM_INSTALL, SHIM_UNAME)


class InstallerR8Recovery(ShRunner, unittest.TestCase):
    seed_pair = tr.InstallerLockAndPublish.seed_pair
    seed_build = tr.InstallerLockAndPublish.seed_build
    backup_env = tr.InstallerLockAndPublish.backup_env
    publish_env = tr.InstallerLockAndPublish.publish_env

    ROLLBACK_BODY = (extract('manifest_get') + '\n'
                     + extract('prevalidate_pair_member') + '\n'
                     + extract('restore_pair_member') + '\n'
                     + extract('remove_pair_member') + '\n'
                     + extract('restore_generation_pair') + '\n'
                     + extract('record_installed_pair') + '\n'
                     + tr.RELINK_BODY + '\n'
                     + extract('rollback_pair') + '\nrollback_pair\n')

    PUBLISH_BODY = (tr.InstallerLockAndPublish.PUBLISH_BODY
                    .replace('publish_pair\n',
                             'kernel_object_path() { echo "$KERNEL_OBJ"; }\n'
                             'verify_kernel_artifact() { log "verified $1"; }\n'
                             + 'publish_pair\n'))

    # install(8) shim that signals the parent twice: the first call (the
    # ifconfig publish) sends SIGTERM, the second (the restore's ifconfig
    # copy) sends SIGINT; every call copies like the plain shim.
    SHIM_INSTALL_TWO_SIGNALS = ('#!/usr/bin/env python3\nimport os,shutil,sys,signal\n'
                                'a=sys.argv[1:]\n'
                                'while a and a[0].startswith("-"):\n'
                                '    if a[0] in ("-m","-o","-g","-u"): a=a[2:]\n'
                                '    else: a=a[1:]\n'
                                'm=os.environ.get("SIGNAL_MARK","")\n'
                                'n=0\n'
                                'if m and os.path.exists(m):\n'
                                '    n=int(open(m).read() or "0")\n'
                                'if m: open(m,"w").write(str(n+1))\n'
                                'if n==0: os.kill(os.getppid(), signal.SIGTERM)\n'
                                'elif n==1: os.kill(os.getppid(), signal.SIGINT)\n'
                                'if len(a)>1: shutil.copy(a[-2], a[-1])\n')

    SHIMS = {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
             'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
             'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
             'ln': '#!/bin/sh\nexit 0\n', 'rm': '#!/bin/sh\nexit 0\n',
             'cmp': '#!/bin/sh\nexit 0\n'}

    # sha256 shim that honours -h <db> <file> (records the digest), like the
    # OpenBSD sha256(1) the installer targets: the publish must write the new
    # kernel's digest and the restore must bring the recorded one back.
    SHIM_SHA256_H = ('#!/usr/bin/env python3\nimport hashlib,sys\n'
                     'a=sys.argv[1:]\n'
                     'digest=hashlib.sha256(open(a[-1],"rb").read()).hexdigest()\n'
                     'if a and a[0]=="-h" and len(a)>=3:\n'
                     '    open(a[1],"a").write("%s  %s\\n" % (digest, a[-1]))\n'
                     'else:\n'
                     '    print(digest)\n')

    def seed_installed(self, w):
        """Installed (new) members + generation-1 backups + kernel.SHA256."""
        for name, content in (('bsd', b'new-kernel\n'),
                              ('ifconfig', b'new-ifconfig\n'),
                              ('if_awg.h', 'new-header\n'),
                              ('awgctl', b'new-ctl\n')):
            (w / name).write_bytes(content.encode() if isinstance(content, str) else content)
        (w / 'kernel.SHA256').write_text('OLDHASH  /bsd\n')
        return {'schema': '3',
                'bsd': b'old-kernel\n', 'ifc': b'old-ifconfig\n',
                'hdr': 'old-header\n', 'ctl': b'old-ctl\n',
                'ksha': 'PREVHASH  /bsd\n'}

    def seed_manifest(self, w, gen, with_header=True, with_awgctl=True,
                      with_ksha=True):
        """Manifest records generation `gen`; the flags pick which backup
        files exist on disk (the hashes are always recorded — recorded but
        absent is the R8 case)."""
        (w / ('bsd.awg-prev.%d' % gen)).write_bytes(b'old-kernel\n')
        (w / ('ifconfig.awg-prev.%d' % gen)).write_bytes(b'old-ifconfig\n')
        if with_header:
            (w / ('if_awg.h.awg-prev.%d' % gen)).write_text('old-header\n')
        if with_awgctl:
            (w / ('awgctl.awg-prev.%d' % gen)).write_bytes(b'old-ctl\n')
        if with_ksha:
            (w / ('kernel.SHA256.awg-prev.%d' % gen)).write_text('PREVHASH  /bsd\n')
        m = ['schema=3', 'generation=%d' % gen,
             'backup_generation=%d' % gen,
             'backup_bsd_path=' + str(w / ('bsd.awg-prev.%d' % gen)),
             'backup_ifconfig_path=' + str(w / ('ifconfig.awg-prev.%d' % gen)),
             'backup_bsd_sha256=' + hashlib.sha256(b'old-kernel\n').hexdigest(),
             'backup_ifconfig_sha256=' + hashlib.sha256(b'old-ifconfig\n').hexdigest(),
             'backup_if_awg_h_path=' + str(w / ('if_awg.h.awg-prev.%d' % gen)),
             'backup_if_awg_h_sha256=' + hashlib.sha256('old-header\n'.encode()).hexdigest(),
             'backup_awgctl_path=' + str(w / ('awgctl.awg-prev.%d' % gen)),
             'backup_awgctl_sha256=' + hashlib.sha256(b'old-ctl\n').hexdigest(),
             'backup_kernel_sha256_path=' + (str(w / ('kernel.SHA256.awg-prev.%d' % gen)) if with_ksha else ''),
             'backup_kernel_sha256_sha256=' + (hashlib.sha256('PREVHASH  /bsd\n'.encode()).hexdigest() if with_ksha else '')]
        (w / 'manifest').write_text('\n'.join(m) + '\n')

    def rollback_env(self, w):
        return {'MANIFEST': str(w / 'manifest'),
                'BSD_FILE': str(w / 'bsd'), 'IFCFG_FILE': str(w / 'ifconfig'),
                'IFAWG_HEADER': str(w / 'if_awg.h'), 'AWGCTL_FILE': str(w / 'awgctl'),
                'KERNEL_SHA256': str(w / 'kernel.SHA256'),
                'BSD_BACKUP': str(w / 'bsd.awg-prev'),
                'IFCFG_BACKUP': str(w / 'ifconfig.awg-prev'),
                'IFAWG_BACKUP': str(w / 'if_awg.h.awg-prev'),
                'AWGCTL_BACKUP': str(w / 'awgctl.awg-prev'),
                'KERNEL_SHA256_BACKUP': str(w / 'kernel.SHA256.awg-prev'),
                'WORK': str(w)}

    def run_rollback(self, w):
        return self.run_sh(self.ROLLBACK_BODY,
                           {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
                            'stat': '#!/bin/sh\necho 13\n',
                            'chmod': '#!/bin/sh\nexit 0\n',
                            'chown': '#!/bin/sh\nexit 0\n'},
                           self.rollback_env(w))

    # ── 1. recorded header backup missing: fail closed, write nothing ───────
    def test_restore_missing_header_backup_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_installed(w)
            self.seed_manifest(w, 1, with_header=False)
            r = self.run_rollback(w)
            self.assertNotEqual(r.returncode, 0, r.stderr)
            self.assertIn('header backup', r.stderr)
            # Nothing is written: the installed members keep their bytes.
            self.assertEqual((w / 'bsd').read_bytes(), b'new-kernel\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'new-ifconfig\n')
            self.assertEqual((w / 'awgctl').read_bytes(), b'new-ctl\n')
            self.assertEqual((w / 'kernel.SHA256').read_text(), 'OLDHASH  /bsd\n')

    # ── 2. recorded awgctl backup missing: fail closed, write nothing ───────
    def test_restore_missing_awgctl_backup_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_installed(w)
            self.seed_manifest(w, 1, with_awgctl=False)
            r = self.run_rollback(w)
            self.assertNotEqual(r.returncode, 0, r.stderr)
            self.assertIn('awgctl backup', r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'new-kernel\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'new-ifconfig\n')
            self.assertEqual((w / 'if_awg.h').read_text(), 'new-header\n')
            self.assertEqual((w / 'kernel.SHA256').read_text(), 'OLDHASH  /bsd\n')

    # ── 3. kernel.SHA256 comes back with the pair on a publish rollback ────
    def test_kernel_sha256_restored_after_publish_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            (w / 'kernel.SHA256').write_text('OLDHASH  /bsd\n')
            r = self.run_sh(self.PUBLISH_BODY,
                            dict(self.SHIMS, sha256=self.SHIM_SHA256_H,
                                 install=tr.InstallerLockAndPublish.SHIM_INSTALL_FAIL_AWGCTL),
                            dict(self.publish_env(w),
                                 KERNEL_SHA256=str(w / 'kernel.SHA256'),
                                 KERNEL_SHA256_BACKUP=str(w / 'kernel.SHA256.awg-prev'),
                                 AWGCTL_MARK=str(w / 'awgctl-failed')))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('awgctl publish failed', r.stderr)
            self.assertIn('rolled back', r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            # The integrity DB describes the restored kernel, not the
            # published one: exactly the pre-install content.
            self.assertEqual((w / 'kernel.SHA256').read_text(), 'OLDHASH  /bsd\n')

    # ── 4. full rollback restores kernel.SHA256 from the recorded backup ───
    def test_rollback_restores_kernel_sha256_member(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_installed(w)
            self.seed_manifest(w, 1)
            r = self.run_rollback(w)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'old-kernel\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'old-ifconfig\n')
            self.assertEqual((w / 'if_awg.h').read_text(), 'old-header\n')
            self.assertEqual((w / 'awgctl').read_bytes(), b'old-ctl\n')
            self.assertEqual((w / 'kernel.SHA256').read_text(), 'PREVHASH  /bsd\n')

    # ── 5. a second signal during recovery keeps the honest outcome ────────
    def test_second_signal_during_recovery_reports_rolled_back(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            (w / 'kernel.SHA256').write_text('OLDHASH  /bsd\n')
            body = extract('publish_interrupted') + '\n' + self.PUBLISH_BODY
            r = self.run_sh(body,
                            dict(self.SHIMS, install=self.SHIM_INSTALL_TWO_SIGNALS),
                            dict(self.publish_env(w),
                                 KERNEL_SHA256=str(w / 'kernel.SHA256'),
                                 KERNEL_SHA256_BACKUP=str(w / 'kernel.SHA256.awg-prev'),
                                 SIGNAL_MARK=str(w / 'signal-mark')))
            self.assertEqual(r.returncode, 20, r.stderr)
            self.assertIn('Interrupted mid-publish', r.stderr)
            self.assertIn('rolled back', r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            self.assertEqual((w / 'kernel.SHA256').read_text(), 'OLDHASH  /bsd\n')

    # ── 6. no recorded generation: "nothing to roll back" ──────────────────
    def test_recovery_without_manifest_reports_nothing_to_roll_back(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            body = (extract('manifest_get') + '\n'
                    + extract('prevalidate_pair_member') + '\n'
                    + extract('restore_pair_member') + '\n'
                    + extract('remove_pair_member') + '\n'
                    + extract('restore_generation_pair') + '\n'
                    + extract('rollback_uncommitted') + '\n'
                    + 'rollback_uncommitted "uncommitted publish (exit 9)"\n')
            r = self.run_sh(body, self.SHIMS, self.rollback_env(w))
            self.assertEqual(r.returncode, 22, r.stderr)
            self.assertIn('nothing to roll back', r.stderr)


if __name__ == '__main__':
    unittest.main()
