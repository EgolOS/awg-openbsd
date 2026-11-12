"""R6-N5: the staging path must be real-script-startup correct.

Round-6 blocker: IFAWG_STAGED was expanded before $WORK existed, so every run
wrote a root-level /if_awg.h. These tests execute the *real* install.sh (not
extracted functions) against a sandboxed root with PATH shims, covering:
clean environment, absent system header, deliberately incompatible old header,
stale stage file present, and --kernel-only staging.
"""
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / 'install.sh'
SHIMS = Path(__file__).resolve().parent / 'shims'
REPO_HEADER = (ROOT / 'src' / 'if_awg.h').read_text()
UPSTREAM_MANIFEST = (ROOT / 'tools' / 'upstream' / '8.0.sha256').read_text()


def upstream_digest(relpath):
    """Digest recorded in tools/upstream/8.0.sha256 for one relative path."""
    for line in UPSTREAM_MANIFEST.splitlines():
        if line.startswith('#') or not line.strip():
            continue
        digest, _, path = line.partition('  ')
        if path.strip() == relpath:
            return digest.strip()
    raise KeyError(relpath)


class InstallerR6Staging(unittest.TestCase):
    """Real-script startup with a sandboxed root; the host never touches /bsd."""

    def sandbox(self, w, with_sys_header=True, with_stale_root=False):
        (w / 'ifcfg').mkdir(exist_ok=True)
        (w / 'ifcfg' / 'ifconfig.c').write_bytes(b'pristine\n')
        (w / 'bsd').write_bytes(b'kernel-bytes\n')
        (w / 'ifconfig').write_bytes(b'ifconfig-bytes\n')
        (w / 'obj').mkdir()
        (w / 'db').mkdir()
        (w / 'usr' / 'include' / 'net').mkdir(parents=True)
        (w / 'usr' / 'local' / 'sbin').mkdir(parents=True)
        (w / 'usr' / 'share' / 'man' / 'man8').mkdir(parents=True)
        (w / 'etc').mkdir()
        # Repo source tree for isolated builds (REPODIR override)
        (w / 'src').mkdir(exist_ok=True)
        (w / 'sbin' / 'awgctl').mkdir(parents=True, exist_ok=True)
        (w / 'sbin' / 'ifconfig').mkdir(parents=True, exist_ok=True)
        (w / 'tools' / 'upstream').mkdir(parents=True, exist_ok=True)
        # Copy real repo sources needed by install.sh
        import shutil
        shutil.copy2(ROOT / 'src' / 'if_awg.h', w / 'src' / 'if_awg.h')
        shutil.copy2(ROOT / 'src' / 'if_awg.c', w / 'src' / 'if_awg.c')
        shutil.copy2(ROOT / 'src' / 'awg_noise.h', w / 'src' / 'awg_noise.h')
        shutil.copy2(ROOT / 'src' / 'awg_noise.c', w / 'src' / 'awg_noise.c')
        shutil.copy2(ROOT / 'src' / 'AWG.MP', w / 'src' / 'AWG.MP')
        shutil.copy2(ROOT / 'src' / 'kern_pledge.c.orig', w / 'src' / 'kern_pledge.c.orig')
        shutil.copy2(ROOT / 'sbin' / 'awgctl' / 'Makefile', w / 'sbin' / 'awgctl' / 'Makefile')
        shutil.copy2(ROOT / 'sbin' / 'awgctl' / 'awgctl.c', w / 'sbin' / 'awgctl' / 'awgctl.c')
        shutil.copy2(ROOT / 'sbin' / 'awgctl' / 'awgctl.8', w / 'sbin' / 'awgctl' / 'awgctl.8')
        shutil.copy2(ROOT / 'sbin' / 'ifconfig' / 'ifconfig_awg.patch', w / 'sbin' / 'ifconfig' / 'ifconfig_awg.patch')
        shutil.copy2(ROOT / 'sbin' / 'awg-quick', w / 'sbin' / 'awg-quick')
        shutil.copy2(ROOT / 'tools' / 'verify-release.sh', w / 'tools' / 'verify-release.sh')
        shutil.copy2(ROOT / 'awg-openbsd.patch', w / 'awg-openbsd.patch')
        # Copy upstream digest file
        upstream_file = ROOT / 'tools' / 'upstream' / '8.0.sha256'
        if upstream_file.exists():
            shutil.copy2(upstream_file, w / 'tools' / 'upstream' / '8.0.sha256')
        if with_sys_header:
            # A deliberately incompatible *old* header: AWG_KEY_LEN 16 where
            # the shipped header has 32, so the compiled marker names it.
            (w / 'usr' / 'include' / 'net' / 'if_awg.h').write_text(
                '#define AWG_KEY_LEN 16\t/* legacy header */\n')
        if with_stale_root:
            (w / 'rootfs').mkdir()
            (w / 'rootfs' / 'if_awg.h').write_text('/* STALE ROOT STAGE */\n')
        # tree_trust answers owner/mode from a table; everything is root-owned
        # 755 in the sandbox, with a kernel-sized %z for the built bsd.
        (w / 'table').write_text('*\troot\tdrwxr-xr-x\t10485760\n')
        # sha256 answers for the release files the installer compares against
        # the recorded baseline (the sandbox copies are fixtures, not the real
        # 8.0 blobs): ifconfig.c (stock) and kern_pledge.c (already patched).
        (w / 'sha256.table').write_text(
            '%s\t%s\n%s\t%s\n' % (
                w / 'ifcfg' / 'ifconfig.c', upstream_digest('sbin/ifconfig/ifconfig.c'),
                w / 'sys' / 'kern' / 'kern_pledge.c', upstream_digest('kern/kern_pledge.c.awg')))
        # CDN sources: a signed src.tar.gz carrying sbin/ifconfig/ifconfig.c.
        (w / 'etc' / 'signify').mkdir()
        (w / 'etc' / 'signify' / 'openbsd-80-base.pub').write_text('untrusted\tcomment\n')
        with tarfile.open(w / 'src.tar', 'w') as tf:
            tf.add(w / 'ifcfg' / 'ifconfig.c', arcname='sbin/ifconfig/ifconfig.c')
        digest = hashlib_sha256(w / 'src.tar')
        (w / 'SHA256.sig').write_text('src.tar.gz\t%s\nsys.tar.gz\t%s\n' % (digest, digest))

    def env(self, w, mode_env=None):
        e = {'AWG_LOCK_FILE': str(w / 'lock'),
             'BSD_FILE': str(w / 'bsd'),
             'IFCFG_FILE': str(w / 'ifconfig'),
             'IFAWG_HEADER': str(w / 'usr' / 'include' / 'net' / 'if_awg.h'),
             'AWGCTL_FILE': str(w / 'usr' / 'local' / 'sbin' / 'awgctl'),
             'AWG_QUICK_FILE': str(w / 'usr' / 'local' / 'sbin' / 'awg-quick'),
             'AWG_CONF_DIR': str(w / 'etc' / 'amnezia' / 'amneziawg'),
             'AWGCTL_MAN_DIR': str(w / 'usr' / 'share' / 'man' / 'man8'),
             'INSTALL_DB': str(w / 'db'),
             'MANIFEST': str(w / 'db' / 'manifest'),
             'BSD_BACKUP_BASE': str(w / 'bsd.awg-prev'),
             'IFCFG_BACKUP_BASE': str(w / 'ifconfig.awg-prev'),
             'IFAWG_BACKUP_BASE': str(w / 'usr' / 'include' / 'net' / 'if_awg.h.awg-prev'),
             'AWGCTL_BACKUP_BASE': str(w / 'usr' / 'local' / 'sbin' / 'awgctl.awg-prev'),
             'KERNEL_SHA256': str(w / 'kernel.SHA256'),
             'BSD_STAGING': str(w / 'bsd.staging'),
             'SHA256_TABLE': str(w / 'sha256.table'),
             'SRCDIR': str(w / 'sys'),
             'IFCFG_SRC': str(w / 'ifcfg'),
             'STAT_TABLE': str(w / 'table'),
             'OBJDIR': str(w / 'obj'),
             'BSDOBJDIR': str(w / 'obj'),
             'RELINK_KERNEL_DIR': str(w / 'usr' / 'share' / 'relink' / 'kernel'),
             'KERNEL_IDENT': 'AWG.MP',
             'CMDFILE': str(w / 'cmdfile'),
             'SRC_TARBALL': str(w / 'src.tar'),
             'SRC_SIG': str(w / 'SHA256.sig'),
             'SIGNIFY_KEY': str(w / 'etc' / 'signify' / 'openbsd-80-base.pub'),
             'REPODIR': str(w),
             'PATH': str(SHIMS) + ':' + os.environ['PATH'],
             'TMPDIR': str(w)}
        if mode_env:
            e.update(mode_env)
        return e

    def run_install(self, w, args, env):
        (w / 'cmdfile').write_text('')
        return subprocess.run(['sh', str(INSTALL)] + args, env=env,
                              capture_output=True, text=True, cwd=str(w))

    def tree(self, w):
        """A kernel tree matching the running release (sys/conf/newvers.sh)."""
        (w / 'sys').mkdir(exist_ok=True)
        (w / 'sys' / 'net').mkdir(exist_ok=True)
        (w / 'sys' / 'net' / 'if_wg.c').write_text('/* wg */\n')
        (w / 'sys' / 'dev' / 'rasops').mkdir(parents=True, exist_ok=True)
        (w / 'sys' / 'dev' / 'rasops' / 'files.rasops').write_text('OpenBSD\n')
        (w / 'sys' / 'kern').mkdir(exist_ok=True)
        (w / 'sys' / 'kern' / 'kern_pledge.c').write_text(
            '#include "net/if_awg.h"\n\tcase SIOCSAWG:\n\tcase SIOCGAWG:\n')
        (w / 'sys' / 'arch' / 'amd64' / 'conf').mkdir(parents=True, exist_ok=True)
        (w / 'sys' / 'conf').mkdir(exist_ok=True)
        (w / 'sys' / 'conf' / 'newvers.sh').write_text('osr="8.0"\n')
        (w / 'sys' / 'conf' / 'files').write_text(
            'pseudo-device wg: ifnet\nfile net/if_wg.c\t\t\twg\n'
            'file net/wg_cookie.c\t\t\twg\nfile crypto/blake2s.c\t\twg\n'
            'file crypto/curve25519.c\t\twg\nfile crypto/chachapoly.c\t\twg\n')
        (w / 'ifcfg').mkdir(exist_ok=True)
        (w / 'ifcfg' / 'ifconfig.c').write_text('pristine\n')

    def staged_include(self, w):
        """The -I directory the compiler was given (from the recorded build).

        The make shim compiles a probe with that -I and fails when the
        directory has no net/if_awg.h, so a produced probe.s is the proof the
        compiler resolved the staged header (the -I directory itself is
        removed with $WORK when the run exits).
        """
        cmd = (w / 'cmdfile').read_text()
        m = re.search(r'cc [^\n]*?-I(?P<inc>[^\s]+) -S obj/probe\.c', cmd)
        assert m is not None, 'the ifconfig build must receive -I: %r' % cmd
        self.assertTrue((w / 'ifcfg' / 'obj' / 'probe.s').exists(),
                        'the probe must compile against -I%s' % m.group('inc'))
        return m.group('inc')

    def staged_path(self, stdout):
        """The stage the run reports, from the real script's own log line."""
        m = re.search(r'Staged driver header in (\S+)', stdout)
        assert m is not None, 'the run must stage the header: %s' % stdout
        return m.group(1)

    def assert_staged_in_work(self, w, stdout):
        """Assert the stage sits in the run's mktemp $WORK with a private mode.

        $WORK is the mktemp directory (a fresh awg-install* under /tmp or
        /usr/src); a root-level /if_awg.h has no such prefix. The mode is read
        from the recorded mkdir/chmod because the run removes $WORK on exit.
        """
        staged = self.staged_path(stdout)
        m = re.match(r'(?P<work>.*/awg-install[^/]*)/stage/net/if_awg\.h$', staged)
        assert m is not None, \
            'the stage must be $WORK/stage/net/if_awg.h: %s' % staged
        work = m.group('work')
        self.assertNotEqual(work, '/', 'the stage must not be a root-level file')
        cmd = (w / 'cmdfile').read_text()
        self.assertIn('mkdir -m 700 -p %s/net' % os.path.join(work, 'stage'), cmd,
                      'the stage directory must be created with a private mode')
        self.assertIn('chmod 600 %s' % staged, cmd,
                      'the staged header must be private')
        return work

    def compiled_marker(self, w):
        """The AWG_KEY_LEN baked into the compiled probe: which header won.

        The make shim compiles a probe that includes <net/if_awg.h> with the
        real compiler and the -I the installer passes, so the emitted assembly
        carries the value from the header the compiler actually opened
        (32 = staged, 16 = the incompatible installed one).
        """
        asm = (w / 'ifcfg' / 'obj' / 'probe.s').read_text()
        ref = re.search(r'awg_marker:\s*\n\s*\.quad\s+(\S+)', asm)
        assert ref is not None, 'the probe must emit awg_marker: %r' % asm[:400]
        lab = re.search(re.escape(ref.group(1)) + r':\s*\n\s*\.string\s+"([^"]*)"', asm)
        assert lab is not None, 'the marker label is missing: %r' % asm[:400]
        return lab.group(1)

    def test_clean_environment_stages_under_work(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.sandbox(w)
            self.tree(w)
            r = self.run_install(w, ['--tools-only'], self.env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Lock acquired', r.stdout)
            # The stage is inside the run's mktemp $WORK, in the net/ layout,
            # with a private mode.
            work = self.assert_staged_in_work(w, r.stdout)
            # The builder received -I on that directory and the compiler
            # resolved the staged header, not the installed one.
            inc = self.staged_include(w)
            self.assertEqual(os.path.realpath(inc),
                             os.path.realpath(os.path.join(work, 'stage')))
            self.assertNotEqual(inc, '/', 'the stage must not be a root-level file')
            self.assertEqual(self.compiled_marker(w), '32')
            # The published header is the repo header, not the old system one.
            self.assertEqual(
                (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text(),
                REPO_HEADER)
            # No root-level /if_awg.h is created in the sandbox root.
            self.assertFalse((w / 'if_awg.h').exists())
            self.assertFalse((w / 'rootfs' / 'if_awg.h').exists())

    def test_absent_system_header_published_from_stage(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.sandbox(w, with_sys_header=False)
            self.tree(w)
            r = self.run_install(w, ['--tools-only'], self.env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            # With no system header at all, the -I stage is the only candidate
            # and the build still succeeds with it.
            self.assertEqual(self.compiled_marker(w), '32')
            self.assertEqual(
                (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text(),
                REPO_HEADER)

    def test_stale_root_stage_is_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.sandbox(w, with_sys_header=True, with_stale_root=True)
            self.tree(w)
            # The sandbox root has a stale /if_awg.h; the run must stage into
            # $WORK and publish that, leaving the stale root file untouched.
            r = self.run_install(w, ['--tools-only'], self.env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(
                (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text(),
                REPO_HEADER)
            self.assertIn('make "CPPFLAGS=-I', (w / 'cmdfile').read_text())
            # The compiler read the staged header (32), not the incompatible
            # installed one (16), and not the stale root file.
            self.assertEqual(self.compiled_marker(w), '32')
            self.assertEqual((w / 'rootfs' / 'if_awg.h').read_text(),
                             '/* STALE ROOT STAGE */\n')
            self.assertNotEqual(
                (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text(),
                '/* STALE ROOT STAGE */\n')

    def test_kernel_only_stages_header(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.sandbox(w, with_sys_header=True, with_stale_root=True)
            self.tree(w)
            # kernel_publish installs the built kernel; the artifact must pass
            # verify_kernel_artifact (ELF magic via dd, size via stat, nm).
            r = self.run_install(w, ['--kernel-only'], self.env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Lock acquired', r.stdout)
            # The kernel-only path never rebuilds ifconfig, so it must stage
            # the header itself, inside $WORK, and publish that.
            self.assert_staged_in_work(w, r.stdout)
            self.assertEqual(
                (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text(),
                REPO_HEADER)
            self.assertFalse((w / 'if_awg.h').exists())
            self.assertEqual((w / 'rootfs' / 'if_awg.h').read_text(),
                             '/* STALE ROOT STAGE */\n')

    def test_all_mode_stages_header_and_installs_awgctl_manpage(self):
        """R4-14: --all publishes the staged header and installs awgctl.8."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.sandbox(w, with_sys_header=True, with_stale_root=True)
            self.tree(w)
            r = self.run_install(w, ['--all'], self.env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assert_staged_in_work(w, r.stdout)
            self.assertEqual(
                (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text(),
                REPO_HEADER)
            # The management tool and its manpage ship together.
            self.assertTrue((w / 'usr' / 'local' / 'sbin' / 'awgctl').exists(),
                            'awgctl must be installed with --all')
            self.assertTrue((w / 'usr' / 'share' / 'man' / 'man8' / 'awgctl.8').exists(),
                            'awgctl.8 must be installed with awgctl')
            self.assertFalse((w / 'if_awg.h').exists())

    # AWG-014 negative control: the builder owns $WORK after
    # hand_tree_to_builder, so the staged header is writable between the
    # compile and the publish. The published bytes must be the mutated stage,
    # which is what the trust boundary (root installs the builder's output)
    # promises — nothing re-hashes the stage before publishing it.
    BUILDER_MAKE_MUTATING = ('#!/bin/sh\n'
                             'printf \'make %s\\n\' "$*" >> "$CMDFILE"\n'
                             'case "$*" in\n'
                             '  *-V*.OBJDIR*) echo "$PWD/obj"; exit 0 ;;\n'
                             'esac\n'
                             'mkdir -p obj\n'
                             'inc=""\n'
                             'for a in "$@"; do\n'
                             '    case "$a" in\n'
                             '        CPPFLAGS=-I*) inc="${a#CPPFLAGS=-I}" ;;\n'
                             '        CPPFLAGS=*)   inc="${a#CPPFLAGS=}" ;;\n'
                             '    esac\n'
                             'done\n'
                             'if [ -n "$inc" ]; then\n'
                             '    printf \'#define AWG_STR(x) #x\\n#define AWG_XSTR(x) AWG_STR(x)\\n#include <net/if_awg.h>\\nconst char *awg_marker = AWG_XSTR(AWG_KEY_LEN);\\nint main(void) { return 0; }\\n\' > obj/probe.c\n'
                             '    cc -DIFDESCRSIZE=256 -I"$inc" -S obj/probe.c -o obj/probe.s 2>> "$CMDFILE" || exit 1\n'
                             '    printf \'/* BUILDER-MUTATION-AFTER-COMPILE */\\n\' >> "$inc/net/if_awg.h"\n'
                             'fi\n'
                             'printf \'#!/bin/sh\\n\' > obj/ifconfig\n'
                             '/bin/chmod 755 obj/ifconfig\n'
                             'exit 0\n')

    def test_builder_mutation_of_staged_header_is_published(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            shims = w / 'builder-shims'
            shims.mkdir()
            (shims / 'make').write_text(self.BUILDER_MAKE_MUTATING)
            (shims / 'make').chmod(0o755)
            self.sandbox(w)
            self.tree(w)
            env = self.env(w)
            env['PATH'] = str(shims) + ':' + env['PATH']
            r = self.run_install(w, ['--tools-only'], env)
            self.assertEqual(r.returncode, 0, r.stderr)
            # The build ran as the builder with -I on the private stage, and
            # the compiler resolved that stage (32, not the legacy 16).
            self.assertIn('su -m _awgbuild -c make', (w / 'cmdfile').read_text())
            self.assertEqual(self.compiled_marker(w), '32')
            published = (w / 'usr' / 'include' / 'net' / 'if_awg.h').read_text()
            self.assertNotEqual(published, REPO_HEADER)
            self.assertEqual(published,
                             REPO_HEADER + '/* BUILDER-MUTATION-AFTER-COMPILE */\n')


def hashlib_sha256(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    unittest.main()