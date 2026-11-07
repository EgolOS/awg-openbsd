"""Transactional installer gates (AWG-014, AWG-029) run verbatim with shims.

Like test_installer_source.py this parses install.sh and executes the extracted
shell functions with a small PATH shim harness, so the host never touches a real
/bsd. Nothing here needs network. The functions are taken verbatim; only
log/die/step and the path variables come from the harness.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'install.sh').read_text()

PRELUDE = '\n'.join([
    ': "${AWG_BUILD_USER:=_awgbuild}"',
    ': "${BUILD_USER:=$AWG_BUILD_USER}"',
    'log()  { echo "==> $*"; echo "==> $*" >> "$LOGFILE"; }',
    'die()  { echo "ERROR: $*" >&2; exit 1; }',
    'step() { echo ""; echo "---- $* ----"; }',
])

SHIM_SHA256 = ('#!/usr/bin/python3\nimport hashlib,sys\n'
               'print(hashlib.sha256(open(sys.argv[-1],"rb").read()).hexdigest())\n')
# install(8) shim: only the last two arguments (source, destination) matter.
SHIM_INSTALL = ('#!/usr/bin/python3\nimport shutil,sys\nshutil.copy(sys.argv[-2], sys.argv[-1])\n')
SHIM_UNAME = ('#!/bin/sh\ncase "$1" in\n'
              '  -r) echo 8.0 ;;\n  -m) echo amd64 ;;\n'
              '  -v) echo "OpenBSD 8.0 (AWG.MP) #5: Sat Nov  7 17:09:34 UTC 2026" ;;\n'
              '  -s) echo OpenBSD ;;\nesac\n')
SHIM_DF = ('#!/bin/sh\nprintf "Filesystem Size Used Avail Capacity Mounted on\\n'
           '/dev/sd0a 1.1G 171M 923M 16%% /\\n"\n')
# grep shim: the awgversion capability probe is answered from the environment,
# everything else goes to the real grep.
SHIM_GREP = ('#!/bin/sh\ncase "$*" in\n'
             '  *awgversion*) [ "$GREP_ANSWER" = yes ] && exit 0; exit 1 ;;\n'
             '  *) exec /usr/bin/grep "$@" ;;\nesac\n')
# stat shim: -f %Su/%Sp are answered from a TAB-separated table ($STAT_TABLE:
# path, owner, mode); a "*" row is the fallback for path components the test
# does not care about. BSD stat(1) is what install.sh targets.
SHIM_STAT = ('#!/bin/sh\n'
             'row=$(grep -m1 -F "$3" "$STAT_TABLE" 2>/dev/null)\n'
             '[ -n "$row" ] || row=$(grep -m1 "^\\*" "$STAT_TABLE" 2>/dev/null)\n'
             'case "$2" in\n'
             '  %Su) printf \'%s\\n\' "$(printf \'%s\' "$row" | cut -f2)" ;;\n'
             '  *)   printf \'%s\\n\' "$(printf \'%s\' "$row" | cut -f3)" ;;\n'
             'esac\n')
SHIM_ID = '#!/bin/sh\necho 503\n'
# doas shim: record the argv so the test can prove which account compiles.
SHIM_DOAS = '#!/bin/sh\nprintf \'%s\\n\' "$@" >> "$CMDFILE"\n'
SHIM_MAKE = '#!/bin/sh\nprintf \'make %s\\n\' "$*" >> "$CMDFILE"\n'


def extract(name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\) \{.*?^\}', SOURCE)
    assert match, 'install.sh needs a %s() function' % name
    return match.group()


class Result:
    """subprocess result plus the shim side effects (recorded argv, log file)."""

    def __init__(self, proc, cmdlog, logfile):
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.cmdlog = cmdlog
        self.logfile = logfile


class InstallerTransaction(unittest.TestCase):
    def run_sh(self, body, shims, env=None, tree=None, files=None):
        """Run `body` (extracted shell functions) with PATH shims.

        `tree` is an optional {relative-path: mode} mapping of directories
        created in the scratch dir, so tree_trust's -e probe sees them.
        `files` is an optional {relative-path: content} mapping written next to
        the shims. `__W__` in body/env is replaced by the scratch directory.
        """
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'LOGFILE').write_text('')
            (w / 'CMDFILE').write_text('')
            for rel, mode in (tree or {}).items():
                p = w / rel
                p.mkdir(parents=True, exist_ok=True)
                os.chmod(p, mode)
            for rel, content in (files or {}).items():
                p = w / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content.replace('__W__', str(w)))
            for name, text in shims.items():
                p = w / name
                p.write_text(text)
                p.chmod(0o700)
            script = w / 'run.sh'
            script.write_text(PRELUDE + '\n' + body.replace('__W__', str(w)) + '\n')
            full = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'),
                        PATH=str(w) + ':' + os.environ['PATH'])
            if env:
                full.update({k: str(v).replace('__W__', str(w)) for k, v in env.items()})
            proc = subprocess.run(['sh', str(script)], env=full,
                                  capture_output=True, text=True)
            return Result(proc, (w / 'CMDFILE').read_text(), (w / 'LOGFILE').read_text())

    # ── usage / argument parsing ─────────────────────────────────────────────
    def test_usage_documents_new_flags(self):
        usage = extract('usage')
        for flag in ('--rollback-kernel-only', '--allow-root-build', 'AWG_BUILD_USER'):
            self.assertIn(flag, usage, 'usage must document %s' % flag)
        self.assertIn('useradd', usage, 'usage must name the builder creation command')

    def test_arg_parser_accepts_new_flags(self):
        parser = re.search(r'(?ms)^for arg in "\$@"; do\n(.*?)\ndone', SOURCE)
        self.assertIsNotNone(parser, 'main needs an argument parser loop')
        self.assertIn('--rollback-kernel-only) ROLLBACK_KERNEL_ONLY=yes', parser.group(1))
        self.assertIn('--allow-root-build)    ALLOW_ROOT_BUILD=yes', parser.group(1))

    def test_build_user_defaults_to_awgbuild(self):
        self.assertRegex(SOURCE, r'AWG_BUILD_USER=\$\{AWG_BUILD_USER:-_awgbuild\}')

    # ── AWG-014: build separation ────────────────────────────────────────────
    def test_root_build_refused_without_flag(self):
        body = 'BUILD_USER=\n' + extract('resolve_build_user') + '\nresolve_build_user\necho "user=$BUILD_USER"'
        r = self.run_sh(body, {'id': '#!/bin/sh\necho "id: _awgbuild: No such user" >&2\nexit 1'})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('does not exist', r.stderr)
        self.assertIn('useradd -s /sbin/nologin _awgbuild', r.stderr)

    def test_allow_root_build_is_explicit(self):
        body = 'BUILD_USER=\n' + extract('resolve_build_user') + '\nresolve_build_user\necho "user=$BUILD_USER"'
        r = self.run_sh(body, {'id': SHIM_ID}, {'ALLOW_ROOT_BUILD': 'yes'})
        self.assertEqual(r.returncode, 0)
        self.assertIn('user=root', r.stdout)
        self.assertIn('--allow-root-build', r.logfile)

    def test_builder_make_runs_make_through_doas(self):
        body = (extract('resolve_build_user') + '\n' + extract('builder_run') + '\n'
                + extract('builder_make') + '\nbuilder_make __W__ -j4\n')
        r = self.run_sh(body, {'id': SHIM_ID, 'doas': SHIM_DOAS, 'make': SHIM_MAKE})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('building as _awgbuild', r.logfile)
        # The doas shim records one argv entry per line.
        self.assertIn('-u\n_awgbuild\nsh\n-c\nmake "-j4"', r.cmdlog)

    def test_builder_make_root_requires_flag(self):
        body = (extract('resolve_build_user') + '\n' + extract('builder_run') + '\n'
                + extract('builder_make') + '\nBUILD_USER=root\nbuilder_make /tmp\n')
        r = self.run_sh(body, {'id': SHIM_ID, 'doas': SHIM_DOAS, 'make': SHIM_MAKE})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--allow-root-build', r.stderr)

    def test_every_make_goes_through_the_builder(self):
        """Root must not run a second make path that bypasses builder_make."""
        builder = extract('builder_make')
        self.assertRegex(builder, r'\bmake\b')
        # Command-position `make` invocations: builder_make plus the read-only
        # `make -V .OBJDIR` query that locates the built ifconfig binary.
        allowed = ('make"$cmd"', 'make -V .OBJDIR')
        for line in SOURCE.splitlines():
            if re.match(r'^\s*make(\s|$)', line):
                self.assertTrue(any(a in line for a in allowed),
                                'unexpected make invocation: %s' % line.strip())

    # ── AWG-014: source tree trust ───────────────────────────────────────────
    def test_tree_trust_accepts_root_and_builder(self):
        # Real directories so the -e probe succeeds; owner/mode come from the shim.
        tree = {'usr/src/sys/arch/amd64/compile/AWG.MP': 0o755,
                'usr/src/sys/arch/amd64/compile': 0o755,
                'usr/src/sys/arch': 0o755,
                'usr/src/sys': 0o755, 'usr/src': 0o755, 'usr': 0o755}
        table = ('__W__/usr/src/sys/arch/amd64/compile/AWG.MP\t_awgbuild\tdrwxr-xr-x\n'
                 '*\troot\tdrwxr-xr-x\n')
        body = extract('tree_trust') + '\ntree_trust __W__/usr/src/sys/arch/amd64/compile/AWG.MP\n'
        r = self.run_sh(body, {'stat': SHIM_STAT}, {'STAT_TABLE': '__W__/table'},
                        tree=tree, files={'table': table})
        self.assertEqual(r.returncode, 0)
        self.assertIn('trusted', r.stdout)

    def test_tree_trust_names_other_writable_component(self):
        tree = {'usr/src/sys/net': 0o777, 'usr/src/sys': 0o755,
                'usr/src': 0o755, 'usr': 0o755}
        table = ('__W__/usr/src/sys/net\troot\tdrwxrwxrwx\n'
                 '*\troot\tdrwxr-xr-x\n')
        body = extract('tree_trust') + '\ntree_trust __W__/usr/src/sys/net\n'
        r = self.run_sh(body, {'stat': SHIM_STAT},
                        {'STAT_TABLE': '__W__/table', 'BUILD_USER': 'root'},
                        tree=tree, files={'table': table})
        self.assertEqual(r.returncode, 0)
        self.assertIn('untrusted:', r.stdout)
        self.assertIn('usr/src/sys/net owner root mode drwxrwxrwx', r.stdout)
        self.assertIn('group/other writable', r.stdout)

    def test_tree_trust_names_group_writable_ancestor(self):
        tree = {'usr/src/sys': 0o775, 'usr/src': 0o755, 'usr': 0o755}
        table = ('__W__/usr/src/sys\troot\tdrwxrwxr-x\n'
                 '*\troot\tdrwxr-xr-x\n')
        body = (extract('tree_trust') + '\n' + extract('check_tree_trust')
                + '\ncheck_tree_trust __W__/usr/src/sys "build the kernel"\n')
        r = self.run_sh(body, {'stat': SHIM_STAT},
                        {'STAT_TABLE': '__W__/table', 'BUILD_USER': 'root'},
                        tree=tree, files={'table': table})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('untrusted source tree: untrusted:', r.stderr)
        self.assertIn('usr/src/sys owner root mode drwxrwxr-x', r.stderr)

    # ── AWG-029: paired rollback ─────────────────────────────────────────────
    def pair_tree(self, w, with_ifconfig_backup=True):
        """Fake /bsd + /sbin/ifconfig pair plus the manifest that describes it."""
        # The running pair and the backup hold the same bytes; the mismatch test
        # rewrites the running pair to simulate an installed upgrade.
        bsd = w / 'bsd'
        bsd.write_bytes(b'kernel-bytes\n')
        ifc = w / 'ifconfig'
        ifc.write_bytes(b'ifconfig-bytes\n')
        ifc.chmod(0o755)
        (w / 'bsd.awg-prev').write_bytes(b'kernel-bytes\n')
        if with_ifconfig_backup:
            (w / 'ifconfig.awg-prev').write_bytes(b'ifconfig-bytes\n')
        manifest = w / 'manifest'
        manifest.write_text('schema=1\n'
                            'timestamp=2026-11-08T00:00:00Z\n'
                            'uname_r=8.0\n'
                            'uname_v=OpenBSD 8.0 (AWG.MP) #5\n'
                            'release_tag=0.2.0\n'
                            'backup_bsd_sha256=' + hashlib.sha256(b'kernel-bytes\n').hexdigest() + '\n'
                            'backup_ifconfig_sha256=' + hashlib.sha256(b'ifconfig-bytes\n').hexdigest() + '\n'
                            'backup_if_awg_h_sha256=\n'
                            'installed_bsd_sha256=' + hashlib.sha256(b'kernel-bytes\n').hexdigest() + '\n'
                            'installed_ifconfig_sha256=' + hashlib.sha256(b'ifconfig-bytes\n').hexdigest() + '\n'
                            'installed_at=2026-11-08T00:00:00Z\n')
        return manifest

    ROLLBACK_BODY = (extract('manifest_get') + '\n' + extract('restore_pair_member')
                     + '\n' + extract('rollback_pair') + '\nrollback_pair\n')

    def pair_env(self, w):
        return {'MANIFEST': str(w / 'manifest'),
                'BSD_FILE': str(w / 'bsd'), 'IFCFG_FILE': str(w / 'ifconfig'),
                'BSD_BACKUP': str(w / 'bsd.awg-prev'),
                'IFCFG_BACKUP': str(w / 'ifconfig.awg-prev')}

    def test_rollback_restores_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.pair_tree(w)
            r = self.run_sh(self.ROLLBACK_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL},
                            self.pair_env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            self.assertIn('Pair restored', r.logfile)

    def test_rollback_without_ifconfig_backup_requires_kernel_only(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.pair_tree(w, with_ifconfig_backup=False)
            env = self.pair_env(w)
            r = self.run_sh(self.ROLLBACK_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL}, env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ifconfig.awg-prev', r.stderr)
            self.assertIn('--rollback-kernel-only', r.stderr)
            # /bsd is left alone when the pair is incomplete.
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            # The documented escape hatch restores the kernel alone.
            r2 = self.run_sh(self.ROLLBACK_BODY,
                             {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL},
                             dict(env, ROLLBACK_KERNEL_ONLY='yes'))
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            self.assertIn('Kernel-only rollback', r2.logfile)

    def test_rollback_rejects_garbage_backup(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.pair_tree(w)
            (w / 'bsd.awg-prev').write_bytes(b'truncated\n')
            r = self.run_sh(self.ROLLBACK_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL},
                            self.pair_env(w))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not match the manifest', r.stderr)
            # The ifconfig half is restored, the kernel half comes from the
            # safety copy taken in the same run: no partially applied pair.
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')

    # ── AWG-029: --check reports the pair ────────────────────────────────────
    CHECK_BODY = (extract('verify_ifconfig_source') + '\n' + extract('manifest_get')
                  + '\n' + extract('resolve_build_user') + '\n' + extract('tree_trust')
                  + '\n' + extract('check_plan') + '\ncheck_plan\n')

    def check_run(self, w, bsd_matches=True, ifc_matches=True, trust='trusted'):
        (w / 'sys').mkdir()
        (w / 'sys' / 'ifconfig.c').write_text('pristine\n')
        (w / 'ifcfg').mkdir()
        (w / 'ifcfg' / 'ifconfig.c').write_text('pristine\n')
        # The grep shim answers the awgversion probe, so the tree looks patched:
        # verify_ifconfig_source then hashes ifconfig.c.orig. Provide both.
        (w / 'ifcfg' / 'ifconfig.c.orig').write_text('pristine\n')
        self.pair_tree(w)
        if not bsd_matches:
            (w / 'bsd').write_bytes(b'other-kernel\n')
        if not ifc_matches:
            (w / 'ifconfig').write_bytes(b'other-ifconfig\n')
        env = {'MANIFEST': str(w / 'manifest'),
               'BSD_FILE': str(w / 'bsd'), 'IFCFG_FILE': str(w / 'ifconfig'),
               'BSD_BACKUP': str(w / 'bsd.awg-prev'),
               'IFCFG_BACKUP': str(w / 'ifconfig.awg-prev'),
               'SRCDIR': str(w / 'sys'), 'IFCFG_SRC': str(w / 'ifcfg'),
               'GREP_ANSWER': 'yes',
               'STAT_TABLE': '__W__/table'}
        shims = {'sha256': SHIM_SHA256, 'uname': SHIM_UNAME, 'df': SHIM_DF,
                 'grep': SHIM_GREP, 'id': SHIM_ID, 'stat': SHIM_STAT}
        files = {'table': '%s\troot\t%s\n*\troot\t%s\n' % (
            w / 'sys',
            'drwxr-xr-x' if trust == 'trusted' else 'drwxrwxr-x',
            'drwxr-xr-x' if trust == 'trusted' else 'drwxrwxr-x')}
        # verify_ifconfig_source reads the recorded release baseline from
        # $REPODIR/tools/upstream/<ver>.sha256 — provide it, point REPODIR here.
        (w / 'tools' / 'upstream').mkdir(parents=True, exist_ok=True)
        (w / 'tools' / 'upstream' / '8.0.sha256').write_text(
            hashlib.sha256(b'pristine\n').hexdigest() + '  sbin/ifconfig/ifconfig.c\n')
        env['REPODIR'] = str(w)
        return self.run_sh(self.CHECK_BODY, shims, env, files=files)

    def test_check_reports_matching_pair(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.check_run(w)
            # The signify key is absent on the host, so the plan reports rc=1;
            # the pair lines are what this test pins.
            self.assertIn('kernel sha256 : matches manifest', r.stdout)
            self.assertIn('ifconfig sha  : matches manifest', r.stdout)
            self.assertIn('ifconfig awg  : awgversion supported', r.stdout)
            self.assertIn('rollback      : pair present', r.stdout)
            self.assertIn('build user    : _awgbuild', r.stdout)
            self.assertIn('src trust     : trusted', r.stdout)

    def test_check_exits_nonzero_on_pair_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.check_run(w, ifc_matches=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ifconfig sha  : MISMATCH', r.stdout)
            self.assertIn('kernel sha256 : matches manifest', r.stdout)

    def test_check_exits_nonzero_on_untrusted_tree(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.check_run(w, trust='untrusted')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('src trust     : untrusted', r.stdout)


if __name__ == '__main__':
    unittest.main()
