"""R9: the native installer findings (F2-F6, F8, F9) as executed regressions.

Every test here runs the shipped shell functions from install.sh (extracted
verbatim, PATH-shimmed) — none of them restates the logic in Python, so a
repaired or re-broken function changes the result. The cases are written as
green / broken / green triples where the finding was a red gate: a green run
proves the fix, the broken leg proves the check can still fail, and the third
leg proves the state is recoverable.

Covered:
  F2  stock /usr/obj (build:wobj 0770) is not weakened; config's object
      directory is made writable for the builder, and an unwritable root fails
      closed with an actionable message.
  F3  a symlinked /usr/src does not abort the run (mkdir -p on a link).
  F4  a redirected, not-yet-existing source parent is created before the
      archive is extracted.
  F3+F4 trust: the extraction target is checked by *resolved* path, so a link
      planted by another account cannot redirect root's extraction.
  F5  an interrupted publish restores the complete pre-run manifest (every
      installed_* key) as well as the five live members.
  F6  a corrupt backup names the generation that failed, not "generation 0".
  F8  after a legitimate KARL relink the EXIT trap trusts the verified
      /var/db/kernel.SHA256 and exits 0, while corruption still exits 3.
  F9  the kernel relink objects are published with the kernel, survive an
      upgrade, are restored by a rollback, and are left consistent when a
      publish is interrupted.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_installer_transaction as tr
from test_installer_transaction import (ShRunner, extract, RELINK_BODY,
                                        KERNEL_OVERRIDES, SHIM_INSTALL,
                                        SHIM_SHA256, SHIM_UNAME)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RelinkFixtures(ShRunner):
    """Sandboxes that carry a kernel build and a published relink set."""

    seed_pair = tr.InstallerLockAndPublish.seed_pair
    seed_build = tr.InstallerLockAndPublish.seed_build
    backup_env = tr.InstallerLockAndPublish.backup_env
    publish_env = tr.InstallerLockAndPublish.publish_env

    def relink_target(self, w):
        return w / 'relink' / 'AWG.MP'

    def seed_relink(self, w, names=('old_a.o', 'old_b.o')):
        """A relink directory as a previous install (or stock make) left it."""
        d = self.relink_target(w)
        d.mkdir(parents=True, exist_ok=True)
        (d / 'Makefile').write_text('newbsd:\n\t@echo newbsd\n')
        for name in names:
            (d / name).write_bytes(b'previous-object\n')
        return d

    def seed_build_objects(self, w, names=('if_awg.o', 'awg_noise.o'),
                           content=b'built-object\n'):
        for name in names:
            (w / 'build' / name).write_bytes(content)

    def publish_body(self, extra=''):
        return (tr.InstallerLockAndPublish.PUBLISH_BODY + '\n' + extra
                if extra else tr.InstallerLockAndPublish.PUBLISH_BODY)

    def publish(self, w, install=SHIM_INSTALL, body=None, extra_env=None):
        shims = {'sha256': SHIM_SHA256, 'install': install,
                 'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
                 'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
                 'ln': '#!/bin/sh\nexit 0\n', 'cmp': '#!/bin/sh\nexit 0\n'}
        env = self.publish_env(w)
        env.update(extra_env or {})
        return self.run_sh(body or self.publish_body(), shims, env)


# ── F8: final verification must accept a verified relink ─────────────────────

class KarlFinalVerification(ShRunner, unittest.TestCase):
    """F8: `finish()` (the EXIT trap) forced rc=3 on every KARL host."""

    FINISH_BODY = (extract('manifest_get') + '\n' + extract('kernel_db_sha') + '\n'
                   + extract('verify_bsd_final') + '\n'
                   + 'PUBLISH_STARTED=no\n'
                   + 'release_lock() { return 0; }\n'
                   + extract('finish') + '\ntrap finish EXIT\nexit 0\n')

    def run_finish(self, w, bsd_bytes, manifest_text, db_text):
        (w / 'bsd').write_bytes(bsd_bytes)
        (w / 'manifest').write_text(manifest_text)
        (w / 'kernel.SHA256').write_text(db_text)
        return self.run_sh(self.FINISH_BODY,
                           {'sha256': SHIM_SHA256, 'stat': '#!/bin/sh\necho 13\n'},
                           {'BSD_FILE': str(w / 'bsd'),
                            'MANIFEST': str(w / 'manifest'),
                            'KERNEL_SHA256': str(w / 'kernel.SHA256')})

    def test_green_broken_green_after_relink(self):
        """Relinked kernel: green, corruption: rc 3, repaired: green."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            staged = b'staged-kernel\n'
            relinked = b'relinked-kernel\n'
            manifest = ('generation=3\ninstalled_bsd_sha256=%s\ninstalled_bsd_size=%d\n'
                        % (sha(staged), len(staged)))

            # 1. green: /bsd is the relinked kernel and the DB records it.
            r = self.run_finish(w, relinked, manifest,
                                'SHA256 (%s) = %s\n' % (w / 'bsd', sha(relinked)))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn('WARNING', r.stderr)

            # 2. broken: /bsd is not what the DB says — corruption, not a relink.
            r = self.run_finish(w, b'corrupted\n', manifest,
                                'SHA256 (%s) = %s\n' % (w / 'bsd', sha(relinked)))
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            self.assertIn('does not match', r.stderr)

            # 3. green again: the kernel is restored to the recorded bytes.
            r = self.run_finish(w, relinked, manifest,
                                'SHA256 (%s) = %s\n' % (w / 'bsd', sha(relinked)))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_db_without_a_bsd_entry_fails_closed(self):
        """A DB that cannot vouch for /bsd must not be treated as "no relink"."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.run_finish(w, b'kernel\n', 'installed_bsd_sha256=%s\n' % sha(b'kernel\n'),
                                'SHA256 (/some/other/file) = deadbeef\n')
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            self.assertIn('records no SHA256', r.stderr)

    def test_db_for_another_path_is_not_accepted(self):
        """Exact path match: an entry for a different file proves nothing."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.run_finish(w, b'kernel\n', 'installed_bsd_sha256=%s\n' % sha(b'kernel\n'),
                                'SHA256 (/bsd) = %s\n' % sha(b'kernel\n'))
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)

    def test_manifest_still_governs_when_the_db_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(b'kernel\n')
            (w / 'manifest').write_text('installed_bsd_sha256=%s\n' % sha(b'kernel\n'))
            r = self.run_sh(self.FINISH_BODY,
                            {'sha256': SHIM_SHA256, 'stat': '#!/bin/sh\necho 13\n'},
                            {'BSD_FILE': str(w / 'bsd'),
                             'MANIFEST': str(w / 'manifest'),
                             'KERNEL_SHA256': str(w / 'kernel.SHA256')})
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


# ── F5: an interrupted publish restores the whole previous manifest ──────────

class InterruptedPublishManifest(RelinkFixtures, unittest.TestCase):
    """F5: recovery restored the five members but dropped installed_* keys."""

    def pre_run_manifest(self, w):
        """A complete manifest from generation 1 that is live before the run."""
        (w / 'manifest').write_text(
            'schema=3\n'
            'generation=1\n'
            'release_tag=v0.2.1-rc2\n'
            'uname_v=OpenBSD 8.0 (AWG.MP) #4: boot\n'
            'backup_generation=1\n'
            'backup_bsd_path=%s\n' % (w / 'bsd.awg-prev.1') +
            'backup_ifconfig_path=%s\n' % (w / 'ifconfig.awg-prev.1') +
            'backup_bsd_sha256=%s\n' % sha(b'kernel-bytes\n') +
            'backup_ifconfig_sha256=%s\n' % sha(b'ifconfig-bytes\n') +
            'installed_bsd_sha256=%s\n' % sha(b'kernel-bytes\n') +
            'installed_bsd_size=13\n' +
            'installed_ifconfig_sha256=%s\n' % sha(b'ifconfig-bytes\n') +
            'installed_ifconfig_size=15\n'
            'installed_awgctl_sha256=%s\n' % sha(b'ctl-bytes\n') +
            'installed_at=2026-11-09T00:00:00Z\n'
            'relink_state=present\n'
            'relink_backup_path=\n')

    def test_interrupted_publish_restores_complete_manifest(self):
        """Green: the pre-run manifest comes back byte-complete."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            self.seed_relink(w)
            self.pre_run_manifest(w)
            before = (w / 'manifest').read_text()

            # The awgctl install is the last member: the kernel, header and
            # relink objects are already published when the run breaks.
            r = self.publish(w, install=tr.InstallerLockAndPublish.SHIM_INSTALL_FAIL_AWGCTL,
                             extra_env={'AWGCTL_MARK': str(w / 'awgctl-failed')})
            self.assertEqual(r.returncode, 20, r.stderr)
            after = (w / 'manifest').read_text()
            # The members that were live before the run are live again …
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            self.assertEqual((w / 'awgctl').read_bytes(), b'ctl-bytes\n')
            # … and so is every key of the manifest that described them.
            for key in ('generation=1', 'release_tag=v0.2.1-rc2',
                        'installed_bsd_sha256=' + sha(b'kernel-bytes\n'),
                        'installed_ifconfig_sha256=' + sha(b'ifconfig-bytes\n'),
                        'installed_awgctl_sha256=' + sha(b'ctl-bytes\n'),
                        'installed_at=2026-11-09T00:00:00Z'):
                self.assertIn(key, after)
                self.assertIn(key, before)
            self.assertNotIn(sha(b'new-kernel\n'), after)
            self.assertFalse(list(w.glob('manifest.tmp.*')))

    def test_broken_snapshot_is_repaired_from_the_restored_pair(self):
        """Broken leg: a manifest that lost its installed_* keys (the old
        snapshot ordering) is rewritten to describe the pair that is live."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            # Exactly what the pre-fix code snapshotted: backup keys only.
            (w / 'manifest').write_text(
                'schema=3\ngeneration=2\n'
                'backup_bsd_path=%s\n' % (w / 'bsd.awg-prev.2') +
                'backup_ifconfig_path=%s\n' % (w / 'ifconfig.awg-prev.2') +
                'backup_bsd_sha256=%s\n' % sha(b'kernel-bytes\n') +
                'backup_ifconfig_sha256=%s\n' % sha(b'ifconfig-bytes\n'))
            r = self.publish(w, install=tr.InstallerLockAndPublish.SHIM_INSTALL_FAIL_AWGCTL,
                             extra_env={'AWGCTL_MARK': str(w / 'awgctl-failed')})
            self.assertEqual(r.returncode, 20, r.stderr)
            manifest = (w / 'manifest').read_text()
            self.assertIn('installed_bsd_sha256=' + sha(b'kernel-bytes\n'), manifest)
            self.assertIn('installed_ifconfig_sha256=' + sha(b'ifconfig-bytes\n'), manifest)
            self.assertIn('installed_awgctl_sha256=' + sha(b'ctl-bytes\n'), manifest)

    def test_interrupted_before_publish_leaves_manifest_intact(self):
        """Green: a run that dies during the build phase (before any publish)
        must not leave a manifest that describes nothing."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            self.pre_run_manifest(w)
            body = (extract('manifest_get') + '\n' + extract('manifest_apply_kv') + '\n'
                    + extract('next_generation') + '\n' + extract('backup_member') + '\n'
                    + extract('release_tag') + '\n' + extract('write_backup_pair') + '\n'
                    + 'snapshot_precommit_manifest() { :; }\n'
                    + 'BUILD_USER=root\n'
                    + 'write_backup_pair\n')
            r = self.run_sh(body,
                            {'sha256': SHIM_SHA256, 'stat': '#!/bin/sh\necho 13\n',
                             'uname': SHIM_UNAME},
                            self.backup_env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            manifest = (w / 'manifest').read_text()
            self.assertIn('generation=2', manifest)
            self.assertIn('installed_bsd_sha256=' + sha(b'kernel-bytes\n'), manifest)
            self.assertIn('installed_ifconfig_sha256=' + sha(b'ifconfig-bytes\n'), manifest)


# ── F6: the corrupt-backup diagnostic must name the generation ───────────────

class BackupGenerationDiagnostic(RelinkFixtures, unittest.TestCase):
    def test_corrupt_backup_names_the_real_generation(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(b'new-kernel\n')
            (w / 'ifconfig').write_bytes(b'new-ifconfig\n')
            (w / 'bsd.awg-prev.5').write_bytes(b'truncated\n')
            (w / 'ifconfig.awg-prev.5').write_bytes(b'ifconfig-bytes\n')
            (w / 'manifest').write_text(
                'generation=5\nbackup_generation=5\n'
                'backup_bsd_path=%s\n' % (w / 'bsd.awg-prev.5') +
                'backup_ifconfig_path=%s\n' % (w / 'ifconfig.awg-prev.5') +
                'backup_bsd_sha256=%s\n' % sha(b'kernel-bytes\n') +
                'backup_ifconfig_sha256=%s\n' % sha(b'ifconfig-bytes\n') +
                'installed_bsd_sha256=%s\n' % sha(b'new-kernel\n'))
            env = self.backup_env(w)
            env['RELINK_KERNEL_DIR'] = str(w / 'relink')
            env['KERNEL_IDENT'] = 'AWG.MP'
            r = self.run_sh(tr.InstallerLockAndPublish.ROLLBACK_TRIPLE_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
                             'stat': '#!/bin/sh\necho 13\n',
                             'chmod': '#!/bin/sh\nexit 0\n',
                             'chown': '#!/bin/sh\nexit 0\n'}, env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('generation 5 rollback failed', r.stderr)
            self.assertNotIn('generation 0', r.stderr)


# ── F9: the relink objects travel with the kernel ────────────────────────────

class RelinkLifecycle(RelinkFixtures, unittest.TestCase):
    def test_publish_installs_relink_objects_and_records_them(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            r = self.publish(w)
            self.assertEqual(r.returncode, 0, r.stderr)
            target = self.relink_target(w)
            self.assertTrue((target / 'Makefile').exists(), r.stdout)
            self.assertTrue((target / 'if_awg.o').exists())
            self.assertTrue((target / 'awg_noise.o').exists())
            self.assertTrue((target / 'makegap.sh').exists())
            self.assertTrue((target / 'ld.script').exists())
            manifest = (w / 'manifest').read_text()
            self.assertIn('relink_state=present', manifest)
            self.assertIn('relink_objects=2', manifest)
            self.assertIn('relink_path=' + str(target), manifest)

    def test_broken_object_set_is_refused_and_the_pair_rolls_back(self):
        """Broken leg: no compile Makefile in the object directory."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            self.seed_relink(w)
            (w / 'build' / 'Makefile').unlink()
            r = self.publish(w)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('KARL relink objects publish failed', r.stderr)
            self.assertIn('no compile Makefile', r.stderr)
            # The kernel came back with the rest of the pair.
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            # …and the previous relink set was never touched.
            self.assertTrue((self.relink_target(w) / 'old_a.o').exists())

    def test_broken_makefile_without_newbsd_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            (w / 'build' / 'Makefile').write_text('all:\n\t@echo all\n')
            r = self.publish(w)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('has no newbsd target', r.stderr)

    def test_upgrade_keeps_the_previous_relink_set_and_prunes_older(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            self.seed_relink(w, names=('old_a.o',))
            r = self.publish(w)
            self.assertEqual(r.returncode, 0, r.stderr)
            target = self.relink_target(w)
            prev1 = Path(str(target) + '.awg-prev.1')
            self.assertTrue(prev1.is_dir(), 'the replaced set must be kept for rollback')
            self.assertTrue((prev1 / 'old_a.o').exists())
            self.assertTrue((target / 'if_awg.o').exists())

            # A second run publishes generation 2 and prunes generation 1's set.
            self.seed_build_objects(w, content=b'second-build\n')
            r = self.publish(w)
            self.assertEqual(r.returncode, 0, r.stderr)
            manifest = (w / 'manifest').read_text()
            self.assertIn('generation=2', manifest)
            prev2 = Path(str(target) + '.awg-prev.2')
            self.assertTrue(prev2.is_dir())
            self.assertEqual((prev2 / 'if_awg.o').read_bytes(), b'obj-if_awg\n')
            self.assertFalse(prev1.exists(), 'only the newest relink backup is kept')
            self.assertEqual((target / 'if_awg.o').read_bytes(), b'second-build\n')

    def test_rollback_restores_the_matching_relink_set(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(b'new-kernel\n')
            (w / 'ifconfig').write_bytes(b'new-ifconfig\n')
            (w / 'bsd.awg-prev.2').write_bytes(b'old-kernel\n')
            (w / 'ifconfig.awg-prev.2').write_bytes(b'old-ifconfig\n')
            target = self.relink_target(w)
            target.mkdir(parents=True)
            (target / 'new_build.o').write_bytes(b'new\n')
            backup = Path(str(target) + '.awg-prev.2')
            backup.mkdir()
            (backup / 'old_build.o').write_bytes(b'old\n')
            (w / 'manifest').write_text(
                'generation=2\nbackup_generation=2\n'
                'backup_bsd_path=%s\n' % (w / 'bsd.awg-prev.2') +
                'backup_ifconfig_path=%s\n' % (w / 'ifconfig.awg-prev.2') +
                'backup_bsd_sha256=%s\n' % sha(b'old-kernel\n') +
                'backup_ifconfig_sha256=%s\n' % sha(b'old-ifconfig\n') +
                'installed_bsd_sha256=%s\n' % sha(b'new-kernel\n') +
                'relink_state=present\n'
                'relink_backup_path=%s\n' % backup)
            env = self.backup_env(w)
            env['RELINK_KERNEL_DIR'] = str(w / 'relink')
            env['KERNEL_IDENT'] = 'AWG.MP'
            r = self.run_sh(tr.InstallerLockAndPublish.ROLLBACK_TRIPLE_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
                             'stat': '#!/bin/sh\necho 13\n',
                             'chmod': '#!/bin/sh\nexit 0\n',
                             'chown': '#!/bin/sh\nexit 0\n'}, env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'old-kernel\n')
            self.assertTrue((target / 'old_build.o').exists(),
                            'the kernel that came back needs its own objects')
            self.assertFalse((target / 'new_build.o').exists())
            manifest = (w / 'manifest').read_text()
            self.assertIn('installed_bsd_sha256=' + sha(b'old-kernel\n'), manifest)
            self.assertIn('relink_state=present', manifest)

    def test_rollback_removes_objects_the_restored_generation_never_had(self):
        """First install rolled back: no relink objects may be left behind."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(b'new-kernel\n')
            (w / 'ifconfig').write_bytes(b'new-ifconfig\n')
            (w / 'bsd.awg-prev.1').write_bytes(b'old-kernel\n')
            (w / 'ifconfig.awg-prev.1').write_bytes(b'old-ifconfig\n')
            target = self.relink_target(w)
            target.mkdir(parents=True)
            (target / 'if_awg.o').write_bytes(b'new\n')
            (w / 'manifest').write_text(
                'generation=1\nbackup_generation=1\n'
                'backup_bsd_path=%s\n' % (w / 'bsd.awg-prev.1') +
                'backup_ifconfig_path=%s\n' % (w / 'ifconfig.awg-prev.1') +
                'backup_bsd_sha256=%s\n' % sha(b'old-kernel\n') +
                'backup_ifconfig_sha256=%s\n' % sha(b'old-ifconfig\n') +
                'installed_bsd_sha256=%s\n' % sha(b'new-kernel\n') +
                'relink_state=missing\n'
                'relink_backup_path=\n')
            env = self.backup_env(w)
            env['RELINK_KERNEL_DIR'] = str(w / 'relink')
            env['KERNEL_IDENT'] = 'AWG.MP'
            r = self.run_sh(tr.InstallerLockAndPublish.ROLLBACK_TRIPLE_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
                             'stat': '#!/bin/sh\necho 13\n',
                             'chmod': '#!/bin/sh\nexit 0\n',
                             'chown': '#!/bin/sh\nexit 0\n'}, env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(target.exists(),
                             'stale objects would relink the rolled-away kernel')
            self.assertIn('relink_state=missing', (w / 'manifest').read_text())

    def test_interrupted_publish_leaves_relink_set_consistent(self):
        """The relink set and the restored kernel must agree after a break."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            target = self.seed_relink(w, names=('old_a.o',))
            r = self.publish(w, install=tr.InstallerLockAndPublish.SHIM_INSTALL_FAIL_AWGCTL,
                             extra_env={'AWGCTL_MARK': str(w / 'awgctl-failed')})
            self.assertEqual(r.returncode, 20, r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertTrue((target / 'old_a.o').exists(),
                            'the restored kernel needs the previous objects')
            self.assertFalse((target / 'if_awg.o').exists())
            manifest = (w / 'manifest').read_text()
            self.assertIn('relink_state=present', manifest)
            self.assertIn('relink_objects=1', manifest)

    def test_pending_journal_without_a_backup_keeps_the_live_set(self):
        """Interruption between the two renames: nothing to swap back."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            target = self.seed_relink(w, names=('old_a.o',))
            (w / 'manifest').write_text(
                'generation=2\nrelink_state=pending\n'
                'relink_backup_path=%s\n' % (Path(str(target) + '.awg-prev.2')))
            body = (extract('manifest_get') + '\n' + RELINK_BODY + '\n'
                    + 'restore_relink_generation\n')
            env = {'MANIFEST': str(w / 'manifest'),
                   'RELINK_KERNEL_DIR': str(w / 'relink'),
                   'KERNEL_IDENT': 'AWG.MP', 'WORK': str(w)}
            r = self.run_sh(body, {}, env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((target / 'old_a.o').exists())
            self.assertIn('still holds the previous objects', r.logfile)

    def test_interrupt_before_the_relink_publish_keeps_the_live_set(self):
        """Found natively: aborting before publish_relink_objects ran deleted the
        live set, because the restore read the *previous* generation's manifest
        (present + a backup path that had already been consumed)."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            target = self.seed_relink(w, names=('old_a.o', 'old_b.o'))
            (w / 'manifest').write_text(
                'generation=12\nrelink_state=present\n'
                'relink_backup_path=%s\n' % (Path(str(target) + '.awg-prev.11')))
            body = (extract('manifest_get') + '\n' + RELINK_BODY + '\n'
                    + 'restore_relink_generation interrupt\n')
            env = {'MANIFEST': str(w / 'manifest'),
                   'RELINK_KERNEL_DIR': str(w / 'relink'),
                   'KERNEL_IDENT': 'AWG.MP', 'WORK': str(w),
                   'RELINK_PUBLISH_STATE': 'none', 'RELINK_BACKUP': ''}
            r = self.run_sh(body, {}, env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((target / 'old_a.o').exists(),
                            'a run that never touched the objects must not delete them')
            self.assertTrue((target / 'old_b.o').exists())
            self.assertIn('not touched by this run', r.logfile)

    def test_interrupt_after_the_swap_restores_the_previous_objects(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            target = self.seed_relink(w, names=('new_marker.o',))
            prev = Path(str(target) + '.awg-prev.12')
            prev.mkdir()
            (prev / 'old_a.o').write_bytes(b'previous-object\n')
            (w / 'manifest').write_text('generation=12\nrelink_state=pending\n')
            body = (extract('manifest_get') + '\n' + RELINK_BODY + '\n'
                    + 'restore_relink_generation interrupt\n')
            env = {'MANIFEST': str(w / 'manifest'),
                   'RELINK_KERNEL_DIR': str(w / 'relink'),
                   'KERNEL_IDENT': 'AWG.MP', 'WORK': str(w),
                   'RELINK_PUBLISH_STATE': 'swapped', 'RELINK_BACKUP': str(prev)}
            r = self.run_sh(body, {}, env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((target / 'old_a.o').exists())
            self.assertFalse((target / 'new_marker.o').exists())
            self.assertIn('Restored the relink objects of the previous kernel', r.logfile)

    def test_relinked_kernel_is_described_without_rewriting_the_manifest(self):
        """A booted KARL host has /bsd != installed_bsd_sha256 by design; the
        integrity DB vouches for it, so the manifest must not be rewritten."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(b'relinked\n')
            (w / 'ifconfig').write_bytes(b'ifconfig\n')
            (w / 'kernel.SHA256').write_text(
                'SHA256 (%s) = %s\n' % (w / 'bsd', sha(b'relinked\n')))
            (w / 'manifest').write_text(
                'generation=11\nrelink_state=present\n'
                'installed_bsd_sha256=%s\n' % sha(b'staged\n') +
                'installed_ifconfig_sha256=%s\n' % sha(b'ifconfig\n'))
            body = (extract('manifest_get') + '\n' + extract('kernel_db_sha') + '\n'
                    + extract('manifest_describes_live_pair')
                    + '\nmanifest_describes_live_pair\n')
            env = {'MANIFEST': str(w / 'manifest'), 'BSD_FILE': str(w / 'bsd'),
                   'IFCFG_FILE': str(w / 'ifconfig'),
                   'KERNEL_SHA256': str(w / 'kernel.SHA256')}
            r = self.run_sh(body, {'sha256': SHIM_SHA256}, env)
            self.assertEqual(r.returncode, 0, r.stderr)

            # Without the relink record the same bytes are not explained.
            (w / 'manifest').write_text(
                'generation=11\nrelink_state=missing\n'
                'installed_bsd_sha256=%s\n' % sha(b'staged\n') +
                'installed_ifconfig_sha256=%s\n' % sha(b'ifconfig\n'))
            r = self.run_sh(body, {'sha256': SHIM_SHA256}, env)
            self.assertNotEqual(r.returncode, 0)

    def test_skip_flag_records_the_reason_and_reports_it(self):
        """Explicit opt-out: no objects, but the manifest says why."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            r = self.publish(w, extra_env={'RELINK_SKIP': 'yes'})
            self.assertEqual(r.returncode, 0, r.stderr)
            manifest = (w / 'manifest').read_text()
            self.assertIn('relink_state=missing', manifest)
            self.assertFalse(self.relink_target(w).exists())


# ── F2: the builder's object directory ───────────────────────────────────────

class BuilderObjdir(ShRunner, unittest.TestCase):
    """F2: stock /usr/obj is build:wobj 0770; the builder cannot write it."""

    BODY = (extract('resolve_build_user') + '\n' + extract('builder_run') + '\n'
            + extract('builder_can_write') + '\n' + extract('prepare_objdir') + '\n'
            + 'prepare_objdir\n')

    # su shim that models the account boundary: the builder may not write the
    # shared object root until it is in the group that owns it (the group
    # route), or until root has chowned the subtree (the prefix route).
    SHIM_SU_RUN = ('#!/bin/sh\n'
                   'last=""; cmd=""\n'
                   'while [ $# -gt 0 ]; do\n'
                   '  case "$1" in -m) shift 2 ;; -c) shift; cmd=$1; shift ;; *) shift ;; esac\n'
                   'done\n'
                   '[ -f "$BUILDER_GRANT" ] && exit 0\n'
                   'if [ -n "$WOBJ_ROOT" ] && [ "$PWD" = "$WOBJ_ROOT" ]; then exit 1; fi\n'
                   'case "$PWD" in\n'
                   '  */sys/*) [ -f "$SUBTREE_GRANT" ] && exit 0; exit 1 ;;\n'
                   'esac\n'
                   '[ -n "$cmd" ] && exec sh -c "$cmd"\n'
                   'exit 1\n')
    SHIM_CHOWN = ('#!/bin/sh\nprintf \'chown %s\\n\' "$*" >> "$CMDFILE"\n'
                  '[ -n "$SUBTREE_GRANT" ] && : > "$SUBTREE_GRANT"\n'
                  'exit 0\n')
    # usermod -G <group> <user>: the group route. NOGROUP=1 models a host where
    # it does not help (the account stays outside the group).
    SHIM_USERMOD = ('#!/bin/sh\nprintf \'usermod %s\\n\' "$*" >> "$CMDFILE"\n'
                    '[ "$NOGROUP" = 1 ] && exit 0\n'
                    '[ -n "$BUILDER_GRANT" ] && : > "$BUILDER_GRANT"\n'
                    'exit 0\n')
    SHIM_ID = ('#!/bin/sh\n'
               'case "$1" in -gn) echo _awgbuild ;; *) echo 0 ;; esac\n')
    SHIM_STAT_GROUP = '#!/bin/sh\ncase "$2" in %Sg) echo wobj ;; *) echo 13 ;; esac\n'
    SHIM_MKDIR_REC = '#!/bin/sh\nprintf \'mkdir %s\\n\' "$*" >> "$CMDFILE"\n'

    def run_prepare(self, w, root, uid='0', really_mkdir=False, writable_root=False,
                    nogroup=False):
        shims = {'id': self.SHIM_ID,
                 'su': self.SHIM_SU_RUN,
                 'chown': self.SHIM_CHOWN,
                 'usermod': self.SHIM_USERMOD,
                 'stat': self.SHIM_STAT_GROUP,
                 'mkdir': self.SHIM_MKDIR_REC}
        if really_mkdir:
            shims['mkdir'] = ('#!/bin/sh\n'
                              'printf \'mkdir %s\\n\' "$*" >> "$CMDFILE"\n'
                              'last=""; rec=no\n'
                              'for a in "$@"; do\n'
                              '  case "$a" in -p) rec=yes ;; -m) ;; *) last="$a" ;; esac\n'
                              'done\n'
                              'if [ "$rec" = yes ]; then /bin/mkdir -p "$last"; else /bin/mkdir "$last"; fi\n')
        return self.run_sh(self.BODY, shims,
                           {'AWG_BUILD_USER': '_awgbuild', 'BUILD_USER': '_awgbuild',
                            'BSDOBJDIR': str(root), 'SRCDIR': str(w / 'sys'),
                            'WORK': str(w), 'INSTALL_DB': str(w / 'db'),
                            'WOBJ_ROOT': '' if writable_root else str(root),
                            'NOGROUP': '1' if nogroup else '0',
                            'BUILDER_GRANT': str(w / 'builder-grant'),
                            'SUBTREE_GRANT': str(w / 'subtree-grant')})

    def test_root_builder_skips_the_ownership_work(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            root = w / 'usr' / 'obj'
            root.mkdir(parents=True)
            r = self.run_sh(self.BODY, {'id': '#!/bin/sh\necho 0\n'},
                            {'AWG_BUILD_USER': '_awgbuild', 'BUILD_USER': 'root',
                             'ALLOW_ROOT_BUILD': 'yes', 'BSDOBJDIR': str(root),
                             'WORK': str(w)})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('object directory ownership is not enforced', r.logfile)

    def test_writable_object_root_is_used_as_is(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            root = w / 'usr' / 'obj'
            root.mkdir(parents=True)
            r = self.run_prepare(w, root, writable_root=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('is writable by _awgbuild', r.logfile)
            self.assertNotIn('mkdir', r.cmdlog)

    def test_unwritable_root_puts_the_builder_in_the_owning_group(self):
        """Stock layout: /usr/obj is 0770 build:wobj, so the builder cannot even
        traverse it. The native remedy is group membership, not a mode change."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            root = w / 'usr' / 'obj'
            root.mkdir(parents=True)
            os.chmod(root, 0o770)
            r = self.run_prepare(w, root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('usermod -G wobj _awgbuild', r.cmdlog)
            self.assertIn('is writable by _awgbuild now', r.logfile)
            # No subtree is created and no mode is changed.
            self.assertNotIn('mkdir', r.cmdlog)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o770)

    def test_group_route_unavailable_falls_back_to_a_builder_owned_subtree(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            root = w / 'usr' / 'obj'
            root.mkdir(parents=True)
            os.chmod(root, 0o755)
            r = self.run_prepare(w, root, really_mkdir=True, nogroup=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            target = root / 'sys' / 'arch' / 'amd64' / 'compile'
            self.assertTrue(target.is_dir(), r.cmdlog)
            self.assertIn('chown -R _awgbuild %s' % (root / 'sys'), r.cmdlog)
            self.assertIn('Created', r.logfile)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o755)

    def test_unwritable_root_still_unwritable_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            root = w / 'usr' / 'obj'
            root.mkdir(parents=True)
            os.chmod(root, 0o555)
            try:
                # Neither remedy works (no group, nothing created): the run must
                # stop with an actionable message, not compile into nowhere.
                r = self.run_prepare(w, root, nogroup=True)
            finally:
                os.chmod(root, 0o755)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('BSDOBJDIR', r.stderr)
            self.assertIn('mkdir -p %s' % (root / 'sys' / 'arch' / 'amd64' / 'compile'),
                          r.cmdlog)

    def test_the_shared_object_root_mode_is_never_weakened(self):
        body = extract('prepare_objdir')
        self.assertNotIn('chmod', body,
                         'the installer must not widen /usr/obj permissions')
        self.assertIn('"$root/sys"', body,
                      'only the sys/ subtree may be handed to the builder')
        self.assertIn('useradd -s /sbin/nologin', extract('usage'),
                      'usage must still document the builder account')
        self.assertIn('BSDOBJDIR', extract('usage'))


# ── F3/F4: source fetch vs a symlinked or redirected /usr/src ───────────────

class SourceFetch(ShRunner, unittest.TestCase):
    """F3: `mkdir -p /usr/src` aborted on a symlink. F4: tar -C needed a
    parent that a redirect does not have yet."""

    # tar shim: extract the "archive" into the -C directory as a minimal tree.
    SHIM_TAR = ('#!/usr/bin/env python3\nimport os, sys\n'
                'a = sys.argv[1:]\n'
                'd = "."\n'
                'if "-C" in a:\n'
                '    d = a[a.index("-C") + 1]\n'
                'for rel, text in (("sys/net/if_wg.c", "/* wg */\\n"),\n'
                '                  ("sys/dev/rasops/files.rasops", "OpenBSD\\n"),\n'
                '                  ("sys/conf/newvers.sh", \'osr="8.0"\\n\')):\n'
                '    p = os.path.join(d, rel)\n'
                '    os.makedirs(os.path.dirname(p), exist_ok=True)\n'
                '    open(p, "w").write(text)\n'
                'open(os.environ["CMDFILE"], "a").write("tar " + " ".join(a) + "\\n")\n')
    SHIM_FTP = ('#!/bin/sh\nprintf \'ftp %s\\n\' "$*" >> "$CMDFILE"\n'
                'out=""; prev=""\n'
                'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                '[ -n "$out" ] && echo archive > "$out"\n'
                'exit 0\n')
    VERIFY_STUB = 'verify_release_file() { log "verified $1"; }\n'
    BODY = (extract('tree_trust') + '\n' + extract('tree_trust_pending') + '\n'
            + extract('check_pending_trust') + '\n' + extract('fetch_sys_sources')
            + '\n' + VERIFY_STUB + 'fetch_sys_sources\n')

    def env(self, w, srcdir):
        return {'SRCDIR': str(srcdir), 'WORK': str(w),
                'CDN': 'https://cdn.invalid/pub/OpenBSD', 'CMDFILE': str(w / 'cmd'),
                'REPODIR': str(w), 'STAT_TABLE': str(w / 'table'),
                'AWG_BUILD_USER': '_awgbuild', 'BUILD_USER': '_awgbuild'}

    def table(self, w, rows):
        (w / 'table').write_text(rows + '*\troot\tdrwxr-xr-x\n')

    def run_fetch(self, w, srcdir, table_extra=''):
        (w / 'log').write_text('')
        (w / 'cmd').write_text('')
        self.table(w, table_extra)
        return self.run_sh(self.BODY,
                           {'uname': SHIM_UNAME, 'tar': self.SHIM_TAR,
                            'ftp': self.SHIM_FTP, 'stat': tr.SHIM_STAT,
                            'mkdir': self.SHIM_MKDIR},
                           self.env(w, srcdir))

    # mkdir shim that records the call and really creates -p targets.
    SHIM_MKDIR = ('#!/bin/sh\n'
                  'printf \'mkdir %s\\n\' "$*" >> "$CMDFILE"\n'
                  'last=""; rec=no\n'
                  'for a in "$@"; do\n'
                  '  case "$a" in -p) rec=yes ;; -m) ;; *) last="$a" ;; esac\n'
                  'done\n'
                  '[ -n "$last" ] || exit 1\n'
                  'if [ "$rec" = yes ]; then /bin/mkdir -p "$last"; else /bin/mkdir "$last"; fi\n')

    def test_symlinked_usr_src_is_followed_not_recreated(self):
        """F3: /usr/src is a symlink; the run must not try to mkdir it."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            real = w / 'build' / 'src'
            real.mkdir(parents=True)
            (w / 'usr').mkdir()
            (w / 'usr' / 'src').symlink_to(real)
            srcdir = w / 'usr' / 'src' / 'sys'
            r = self.run_fetch(w, srcdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            # The parent exists (through the link): no mkdir at all…
            self.assertNotIn('mkdir', (w / 'cmd').read_text())
            # …and the archive is extracted through the link.
            self.assertTrue((real / 'sys' / 'net' / 'if_wg.c').exists())
            self.assertIn('is a symlink to %s' % real, r.logfile)

    def test_missing_redirected_parent_is_created_first(self):
        """F4: SRCDIR outside /usr/src with no parent yet."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            srcdir = w / 'build' / 'src' / 'sys'
            r = self.run_fetch(w, srcdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            parent = w / 'build' / 'src'
            self.assertIn('mkdir -p %s' % parent, (w / 'cmd').read_text())
            self.assertTrue((srcdir / 'net' / 'if_wg.c').exists())
            self.assertIn('tar -C %s -xzf' % parent, (w / 'cmd').read_text())

    def test_untrusted_extraction_target_is_refused_before_tar(self):
        """F3/F4: trust is checked on the resolved path, before root writes."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            real = w / 'build' / 'src'
            real.mkdir(parents=True)
            (w / 'usr').mkdir()
            (w / 'usr' / 'src').symlink_to(real)
            srcdir = w / 'usr' / 'src' / 'sys'
            r = self.run_fetch(w, srcdir,
                               table_extra='%s\troot\tdrwxrwxr-x\n' % real)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('untrusted path', r.stderr)
            self.assertNotIn('tar', (w / 'cmd').read_text(),
                             'nothing may be extracted into an untrusted target')
            self.assertNotIn('ftp', (w / 'cmd').read_text())

    def test_present_sources_are_not_re_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            srcdir = w / 'build' / 'src' / 'sys'
            (srcdir / 'net').mkdir(parents=True)
            (srcdir / 'net' / 'if_wg.c').write_text('/* wg */\n')
            (srcdir / 'dev' / 'rasops').mkdir(parents=True)
            (srcdir / 'dev' / 'rasops' / 'files.rasops').write_text('OpenBSD\n')
            (srcdir / 'conf').mkdir()
            (srcdir / 'conf' / 'newvers.sh').write_text('osr="8.0"\n')
            r = self.run_fetch(w, srcdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('already present', r.logfile)
            self.assertNotIn('tar', (w / 'cmd').read_text())


# ── Help rendering regression (F1 footgun in usage heredoc) ───────────────────

INSTALLER = tr.ROOT / 'install.sh'

# The recording sha256 shim. It records argc plus every argv it is called with,
# answers a read-only `-q <file>` the way BSD sha256(1) does, and exits 0 for
# everything else — so a help render that executes the heredoc substitution
# leaves the destructive `-h <kernel>` argv in the log instead of a hash, and
# the watched members stay byte-identical only because the shim never writes.
SHIM_SHA256_RECORD = (
    '#!/usr/bin/env python3\n'
    'import hashlib, os, sys\n'
    'a = sys.argv[1:]\n'
    'with open(os.environ["SHA256_LOG"], "a") as f:\n'
    '    f.write("argc=%d\\n" % len(a))\n'
    '    for x in a:\n'
    '        f.write("arg=%s\\n" % x)\n'
    'if len(a) == 2 and a[0] == "-q":\n'
    '    print(hashlib.sha256(open(a[1], "rb").read()).hexdigest())\n'
    'sys.exit(0)\n')

# BSD stat(1) emulation: the shipped finish() trap calls `stat -f %z <kernel>`
# before it exits, and GNU stat spells that differently. Same shim the rest of
# the suite uses for the BSD-only `-f` formats.
SHIM_STAT_BSD = (
    '#!/usr/bin/env python3\n'
    'import os, sys\n'
    'a = sys.argv[1:]\n'
    'while a and a[0].startswith("-"):\n'
    '    a = a[1:]\n'
    'fmt = a[0] if a else ""\n'
    'p = a[1] if len(a) > 1 else "."\n'
    'st = os.stat(p)\n'
    'print(st.st_size if fmt == "%z" else int(st.st_mtime))\n')


def fingerprint(path):
    """sha256 + size of one file, so a same-size rewrite cannot hide."""
    data = Path(path).read_bytes()
    return (hashlib.sha256(data).hexdigest(), len(data))


class HelpSwitchRun:
    """One `install.sh <switch>` execution and everything it left behind."""

    def __init__(self, argv_seen, proc, rootfs, shimlog, before, after):
        self.argv_seen = argv_seen      # recorded argv that reached install.sh
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.rootfs = rootfs
        self.shimlog = shimlog
        self.before = before            # fingerprints of the watched members
        self.after = after
        self.lock_artefacts = sorted(
            p.name for p in (rootfs / 'var' / 'db' / 'awg-install').iterdir()
            if p.name.startswith('lock'))

    @property
    def shim_calls(self):
        """One entry per sha256 invocation: the argv list the shim was given."""
        calls, cur = [], None
        for line in self.shimlog.splitlines():
            if line.startswith('argc='):
                cur = []
                calls.append(cur)
            elif line.startswith('arg=') and cur is not None:
                cur.append(line[len('arg='):])
        return calls

    def describe(self):
        """Probe-style one-liner used in failure messages and evidence."""
        return ('argv reaching install.sh: %r | rc=%s | shim calls: %r | '
                'locked: %r | stderr: %r'
                % (self.argv_seen, self.returncode, self.shim_calls,
                   self.lock_artefacts, self.stderr[:400]))


class HelpRenderingInert(ShRunner, unittest.TestCase):
    """Both -h and --help must render usage without executing command substitutions.

    The original defect: usage() used an unquoted heredoc (cat <<EOF) containing
    an unescaped backtick command substitution `sha256 -h /bsd`. When --help or -h
    was invoked, the shell executed that substitution, overwriting /bsd with a
    hash line (the F1 destructive action).

    These cases execute the *shipped* install.sh — not an extracted copy of
    usage() — one switch per run, against a seeded sandbox root that holds the
    three members the warning is about (the kernel, the integrity DB and the
    install manifest). A recording sha256 shim on PATH is the tripwire: an inert
    render never calls it, so the recorded argv must be empty and every watched
    member must be byte-identical before and after.
    """

    # The switch each run passes to install.sh, and the lock/DB names the script
    # itself uses (INSTALL_DB/manifest, AWG_LOCK_FILE, BSD_FILE, KERNEL_SHA256).
    WATCHED = ('bsd', 'var/db/kernel.SHA256', 'var/db/awg-install/manifest')

    def seed_rootfs(self, w):
        """A contained root with /bsd, /var/db/kernel.SHA256 and the manifest."""
        rootfs = w / 'rootfs'
        db = rootfs / 'var' / 'db'
        (db / 'awg-install').mkdir(parents=True)
        kernel = rootfs / 'bsd'
        kernel.write_bytes(b'OPENBSD-KERNEL-STUB\n' * 4096)
        ksum = hashlib.sha256(kernel.read_bytes()).hexdigest()
        (db / 'kernel.SHA256').write_text('SHA256 (%s) = %s\n' % (kernel, ksum))
        (db / 'awg-install' / 'manifest').write_text(
            'schema=3\ngeneration=41\nuname_v=AWG.MP#41\n'
            'release_tag=r10-help\ninstalled_bsd_sha256=%s\n'
            'installed_bsd_size=%d\nrelink_state=present\n'
            % (ksum, kernel.stat().st_size))
        return rootfs

    def run_installer_switch(self, switch):
        """Execute `install.sh <switch>` once, in its own sandbox, shimmed.

        The switch is handed to the script exactly as a user would type it: a
        launcher records the argv it received (so the assertion is about the
        argv that reached install.sh, not about this helper's bookkeeping) and
        then execs the shipped installer with it.
        """
        self.assertEqual(fingerprint(INSTALLER)[0],
                         hashlib.sha256(tr.SOURCE.encode()).hexdigest(),
                         'the tests must execute the shipped install.sh bytes')
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            rootfs = self.seed_rootfs(w)
            shimdir = w / 'shim'
            shimdir.mkdir()
            for name, text in (('sha256', SHIM_SHA256_RECORD),
                               ('stat', SHIM_STAT_BSD)):
                shim = shimdir / name
                shim.write_text(text)
                shim.chmod(0o700)
            shimlog = w / 'sha256-calls.log'
            argvlog = w / 'argv.log'
            tmp = w / 'tmp'
            tmp.mkdir()
            launcher = shimdir / 'launch'
            launcher.write_text(
                '#!/bin/sh\n'
                'printf "arg=%s\\n" "$@" > "$ARGV_LOG"\n'
                'exec sh "$INSTALLER" "$@"\n')
            launcher.chmod(0o700)
            watched = [rootfs / rel for rel in self.WATCHED]
            before = [fingerprint(p) for p in watched]
            instdb = rootfs / 'var' / 'db' / 'awg-install'
            env = dict(os.environ,
                       INSTALL_DB=str(instdb),
                       AWG_LOCK_FILE=str(instdb / 'lock'),
                       BSD_FILE=str(rootfs / 'bsd'),
                       KERNEL_SHA256=str(rootfs / 'var' / 'db' / 'kernel.SHA256'),
                       TMPDIR=str(tmp),
                       PATH=str(shimdir) + ':' + os.environ['PATH'],
                       SHA256_LOG=str(shimlog),
                       ARGV_LOG=str(argvlog),
                       INSTALLER=str(INSTALLER))
            # `mktemp /tmp/awg-install-log.XXXXXX` is hardcoded in the script
            # (no override), so the one path outside the sandbox is its own log;
            # clean up exactly the files this run created.
            stray_before = set(Path('/tmp').glob('awg-install-log.*'))
            proc = subprocess.run(['sh', str(launcher), switch], env=env,
                                  capture_output=True, text=True, cwd=str(w))
            for path in set(Path('/tmp').glob('awg-install-log.*')) - stray_before:
                path.unlink()
            after = [fingerprint(p) for p in watched]
            argv_seen = [ln[len('arg='):] for ln in argvlog.read_text().splitlines()
                         if ln.startswith('arg=')] if argvlog.exists() else []
            shimlog_text = shimlog.read_text() if shimlog.exists() else ''
            return HelpSwitchRun(argv_seen, proc, rootfs, shimlog_text,
                                 before, after)

    def assert_help_is_inert(self, switch):
        """The shared contract every help switch must satisfy, run per switch."""
        r = self.run_installer_switch(switch)
        # 1. the shipped dispatch ran and rendered usage for THIS switch.
        self.assertEqual(r.argv_seen, [switch],
                         'the switch must reach install.sh unchanged: '
                         + r.describe())
        self.assertEqual(r.returncode, 0, r.describe())
        self.assertIn('Usage: %s' % INSTALLER, r.stdout,
                      'usage() of the shipped installer must have run: '
                      + r.describe())
        # 2. the warning is text, not a substitution result.
        self.assertIn('`sha256 -h /bsd`', r.stdout,
                      'the warning must appear with its backticks intact: '
                      + r.describe())
        # 3. the destructive write form never appears in any recorded argv.
        for call in r.shim_calls:
            self.assertNotIn('-h', call,
                             'a help render executed `sha256 -h …`, which writes '
                             'into its second argument (/bsd on a real host): '
                             + r.describe())
            self.assertEqual(call[:1], ['-q'],
                             'the only sha256 call a help render may make is the '
                             'EXIT-trap read `sha256 -q <kernel>`: ' + r.describe())
        # 4. no lock was taken (help exits before acquire_lock).
        self.assertEqual(r.lock_artefacts, [], r.describe())
        # 5. kernel, integrity DB and manifest are byte-identical.
        self.assertEqual(r.before, r.after,
                         'help rendering rewrote a published member: '
                         + r.describe())
        self.assertEqual(r.stderr, '', r.describe())
        return r

    def test_help_short_flag_no_command_substitution(self):
        """`install.sh -h` must render usage and leave /bsd untouched."""
        r = self.assert_help_is_inert('-h')
        # The shipped script installs `trap finish EXIT` before argument
        # parsing, so the only sha256 call a help render can legitimately make
        # is finish()'s read-only integrity read of the kernel — never a write.
        self.assertEqual(r.shim_calls, [['-q', str(r.rootfs / 'bsd')]],
                         r.describe())

    def test_help_long_flag_no_command_substitution(self):
        """`install.sh --help` must render usage and leave /bsd untouched."""
        r = self.assert_help_is_inert('--help')
        self.assertEqual(r.shim_calls, [['-q', str(r.rootfs / 'bsd')]],
                         r.describe())

    def test_help_switches_reach_the_script_as_distinct_argv(self):
        """The two help cases must not be the same run twice.

        Both switches are executed here in one case so the distinction is a
        property of the runs themselves: same sandbox layout, same shim, but
        the argv recorded at install.sh's entry differs.
        """
        short = self.run_installer_switch('-h')
        long = self.run_installer_switch('--help')
        self.assertEqual(short.argv_seen, ['-h'], short.describe())
        self.assertEqual(long.argv_seen, ['--help'], long.describe())
        self.assertNotEqual(short.argv_seen, long.argv_seen, short.describe())
        for r in (short, long):
            for call in r.shim_calls:
                self.assertNotIn('-h', call, r.describe())

    def test_usage_contains_no_other_command_substitutions(self):
        """Audit: usage() heredoc must not contain any other command substitutions."""
        usage_text = extract('usage')
        # Count backticks - should be exactly 2 (the escaped pair for the warning)
        backtick_count = usage_text.count('`')
        self.assertEqual(backtick_count, 2,
                         f'Found {backtick_count} backticks in usage(); '
                         'only the escaped warning pair is allowed')
        # No $() command substitutions
        self.assertNotIn('$(', usage_text,
                         'usage() must not contain $(...) command substitutions')
        # The heredoc delimiter must be unquoted EOF (allows $0 expansion)
        # but the content must have escaped backticks
        self.assertIn(r'\`sha256 -h /bsd\`', usage_text,
                      'warning backticks must be escaped in source')


if __name__ == '__main__':
    unittest.main()
