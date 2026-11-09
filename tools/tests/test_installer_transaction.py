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
    ': "${LOCK_INITIALISE_GRACE:=30}"',
    ': "${WORK:=$PWD}"',
    'log()  { echo "==> $*"; echo "==> $*" >> "$LOGFILE"; }',
    'die()  { echo "ERROR: $*" >&2; exit 1; }',
    'step() { echo ""; echo "---- $* ----"; }',
])

SHIM_MKDIR = ('#!/usr/bin/env python3\nimport os,sys\n'
              'a=sys.argv[1:]\n'
              'if a and a[0]=="-p": a=a[1:]\n'
              'for p in a:\n'
              '    try: os.mkdir(p)\n'
              '    except FileExistsError:\n'
              '        sys.exit(1)\n')
SHIM_STAT_FS = ('#!/usr/bin/env python3\nimport os,sys\n'
                'a=sys.argv[1:]\n'
                "if a and a[0]=='-f': a=a[1:]\n"
                'f=a[0]; p=a[1] if len(a)>1 else "."\n'
                'st=os.stat(p)\n'
                "if f=='%Su': print('root')\n"
                "elif f=='%Sp': print('drwxr-xr-x')\n"
                "elif f=='%z': print(st.st_size)\n"
                'else: print(int(st.st_mtime))\n')  # BSD stat: real mtime/size
SHIM_PS = ('#!/bin/sh\nif [ "$1" = "-p" ] && [ "$2" = "1" ]; then\n'
           '  case "$3" in -o) echo "2026-11-08T10:00:00" ;; esac\n  exit 0\nfi\nexec /bin/ps "$@"\n')

SHIM_SHA256 = ('#!/usr/bin/env python3\nimport hashlib,sys\n'
               'print(hashlib.sha256(open(sys.argv[-1],"rb").read()).hexdigest())\n')
# install(8) shim: only the last two arguments (source, destination) matter;
# -d takes a single directory argument.
SHIM_INSTALL = ('#!/usr/bin/env python3\nimport os,shutil,sys\n'
                'a=sys.argv[1:]\n'
                'd=False\n'
                'while a and a[0].startswith("-"):\n'
                '    if a[0]=="-d": d=True; a=a[1:]\n'
                '    elif a[0] in ("-m","-o","-g","-u"): a=a[2:]\n'
                '    else: a=a[1:]\n'
                'if d: os.makedirs(a[-1], exist_ok=True)\n'
                'elif a: shutil.copy(a[-2], a[-1])\n')
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
    if name == 'record_installed_pair':
        return extract('prepare_durability_helper') + '\n' + match.group()
    return match.group()


class Result:
    """subprocess result plus the shim side effects (recorded argv, log file)."""

    def __init__(self, proc, cmdlog, logfile):
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.cmdlog = cmdlog
        self.logfile = logfile


class ShRunner:
    """Shared PATH-shim harness; mixed into the installer test cases."""

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
            for name, text in dict({'stat': SHIM_STAT_FS}, **shims).items():
                p = w / name
                p.write_text(text)
                p.chmod(0o700)
            script = w / 'run.sh'
            script.write_text(PRELUDE + '\n' + body.replace('__W__', str(w)) + '\n')
            full = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'), WORK=str(w),
                        PATH=str(w) + ':' + os.environ['PATH'])
            if env:
                full.update({k: str(v).replace('__W__', str(w)) for k, v in env.items()})
            proc = subprocess.run(['sh', str(script)], env=full,
                                  capture_output=True, text=True)
            return Result(proc, (w / 'CMDFILE').read_text(), (w / 'LOGFILE').read_text())


class InstallerTransaction(ShRunner, unittest.TestCase):
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

    ROLLBACK_BODY = (extract('manifest_get') + '\n' + extract('prevalidate_pair_member')
                     + '\n' + extract('restore_pair_member')
                     + '\n' + extract('remove_pair_member')
                     + '\n' + extract('restore_generation_pair')
                     + '\n' + extract('record_installed_pair')
                     + '\n' + extract('rollback_pair') + '\nrollback_pair\n')

    def pair_env(self, w):
        return {'MANIFEST': str(w / 'manifest'),
                'BSD_FILE': str(w / 'bsd'), 'IFCFG_FILE': str(w / 'ifconfig'),
                'KERNEL_SHA256': str(w / 'kernel.SHA256'),
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
            self.assertIn('Quadruple restored', r.logfile)

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
               'KERNEL_SHA256': str(w / 'kernel.SHA256'),
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


class InstallerLockAndPublish(ShRunner, unittest.TestCase):
    """R4-01: host-wide lock + pre-exit /bsd check.
    R4-02: generation-stamped immutable backups, atomic manifest,
    transactional publish with restore-on-failure, triple rollback."""

    # install(8) shim that fails the first invocation, succeeds afterwards:
    # models a publish step that dies after the backup pair exists.
    SHIM_INSTALL_FAIL_FIRST = ('#!/usr/bin/env python3\nimport os,shutil,sys\n'
                               'n=0\ntry:\n n=int(open(os.environ.get("CMDFILE","")).read() or 0)\n'
                               'except Exception:\n pass\n'
                               'if n==0:\n open(os.environ.get("CMDFILE",""),"w").write("1")\n sys.exit(1)\n'
                               'shutil.copy(sys.argv[-2], sys.argv[-1])\n')

    LOCK_BODY = (extract('new_lock_token') + '\n' + extract('proc_start_time')
             + '\n' + extract('lock_token_start') + '\n' + extract('lock_token_gen')
             + '\n' + extract('lock_owner_live')
             + '\n' + extract('lock_mv_t')
             + '\n' + extract('publish_lock_owner')
             + '\n' + extract('read_lock_owner') + '\n' + extract('lock_token_pid')
             + '\n' + extract('lock_record_age')
             + '\n' + extract('acquire_lock')
             + '\n' + extract('verify_lock_held') + '\nacquire_lock\nsleep 0.2\nverify_lock_held\n')

    def lock_env(self, w):
        return {'LOCKFILE': str(w / 'lock'), 'WORK': str(w)}

    def test_lock_acquired_when_free(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.run_sh(self.LOCK_BODY, {}, self.lock_env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Lock acquired', r.logfile)
            self.assertIn(str(w), r.logfile)

    def test_lock_refuses_when_held(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'lock').write_text('gen=4 pid=1 start=2026-11-08T10:00:00 nonce=y\n')
            r = self.run_sh(self.LOCK_BODY, {'mkdir': SHIM_MKDIR, 'stat': SHIM_STAT_FS,
                                             'ps': SHIM_PS},
                            self.lock_env(w))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('another install.sh run (pid 1) holds', r.stderr)
            # The holding lock generation is not touched by the refused run.
            self.assertEqual((w / 'lock').read_text().strip(),
                             'gen=4 pid=1 start=2026-11-08T10:00:00 nonce=y')

    def test_lock_reclaimed_from_dead_pid(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'lock').write_text('gen=4 pid=999999 start=x nonce=y\n')
            r = self.run_sh(self.LOCK_BODY, {'ps': SHIM_PS}, self.lock_env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Lock acquired', r.logfile)
            # The reclaimed lock carries this run's pid and the next generation
            # (published in the immutable successor slot).
            self.assertIn('gen=5 ', os.readlink(w / 'lock.claim-5'))
            self.assertNotIn('999999', os.readlink(w / 'lock.claim-5'))

    def test_lock_missing_pid_waits_then_reclaims(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'lock').write_text('')
            r = self.run_sh(self.LOCK_BODY, {'mkdir': SHIM_MKDIR, 'stat': SHIM_STAT_FS},
                            self.lock_env(w))
            self.assertNotEqual(r.returncode, 0)  # fresh: grace not passed
            self.assertIn('initialising', r.stderr)
            # The CAS slot is keyed on the observed generation (0 for a lock with
            # no generation field).
            self.assertFalse((w / 'lock.cas.0').exists())

    def test_lock_reclaim_race_exactly_one_wins(self):
        """Eight runs start together against a dead-pid lock: the reclaim
        publishes a generation through an exclusive-create CAS slot keyed on
        the observed generation, so exactly one may proceed (R6-N1)."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'lock').write_text('gen=4 pid=999999 start=x nonce=y\n')
            (w / 'mkdir').write_text(SHIM_MKDIR)
            (w / 'mkdir').chmod(0o700)
            script = w / 'acquire.sh'
            script.write_text(PRELUDE + '\n' + self.LOCK_BODY)
            env = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'),
                       LOCKFILE=str(w / 'lock'), WORK=str(w), PATH=str(w) + ':' + os.environ['PATH'])
            procs = [subprocess.Popen(['sh', str(script)], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(8)]
            for p in procs:
                p.communicate()
            winners = [p for p in procs if p.returncode == 0]
            self.assertEqual(len(winners), 1,
                             'exactly one run may take the lock, got %d' % len(winners))
            # The winner's pid is recorded in the published generation; the
            # winner's gen-5 token is the only successor of the dead root.
            self.assertIn('gen=5 ', os.readlink(w / 'lock.claim-5'))
            self.assertIn('pid=%s ' % winners[0].pid, os.readlink(w / 'lock.claim-5'))
            self.assertEqual(len(list(w.glob('lock.claim-*'))), 1)

    VERIFY_BODY = (extract('manifest_get') + '\n' + extract('verify_bsd_final')
                   + '\nverify_bsd_final\n')

    def bsd_check(self, bsd_bytes, manifest_text, stat_out='13'):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(bsd_bytes)
            (w / 'manifest').write_text(manifest_text)
            env = {'BSD_FILE': str(w / 'bsd'), 'MANIFEST': str(w / 'manifest')}
            r = self.run_sh(self.VERIFY_BODY,
                            {'sha256': SHIM_SHA256, 'stat': '#!/bin/sh\necho %s\n' % stat_out},
                            env)
            return r, w

    def test_bsd_check_passes_on_matching_manifest(self):
        sha = hashlib.sha256(b'kernel-bytes\n').hexdigest()
        r, _ = self.bsd_check(b'kernel-bytes\n',
                              'installed_bsd_sha256=%s\ninstalled_bsd_size=13\n' % sha)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bsd_check_flags_zero_byte(self):
        sha = hashlib.sha256(b'kernel-bytes\n').hexdigest()
        r, _ = self.bsd_check(b'', 'installed_bsd_sha256=%s\n' % sha, stat_out='0')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('0 bytes', r.stderr)

    def test_bsd_check_flags_hash_mismatch(self):
        r, _ = self.bsd_check(b'other-kernel\n',
                              'installed_bsd_sha256=' + hashlib.sha256(b'kernel-bytes\n').hexdigest()
                              + '\ninstalled_bsd_size=13\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('does not match the manifest', r.stderr)

    GEN_BODY = (extract('manifest_get') + '\n' + extract('next_generation') + '\n'
                + extract('backup_member') + '\n'
                + extract('write_backup_pair')
                + '\nwrite_backup_pair\necho "gen=$GENERATION bsd=$BSD_BACKUP ifc=$IFCFG_BACKUP"\n')

    def backup_env(self, w):
        return {'INSTALL_DB': str(w), 'MANIFEST': str(w / 'manifest'),
                'WORK': str(w),
                'BSD_FILE': str(w / 'bsd'), 'IFCFG_FILE': str(w / 'ifconfig'),
                'IFAWG_HEADER': str(w / 'if_awg.h'), 'AWGCTL_FILE': str(w / 'awgctl'),
                'KERNEL_SHA256': str(w / 'kernel.SHA256'),
                'KERNEL_SHA256_BACKUP_BASE': str(w / 'kernel.SHA256.awg-prev'),
                'BSD_BACKUP_BASE': str(w / 'bsd.awg-prev'),
                'IFCFG_BACKUP_BASE': str(w / 'ifconfig.awg-prev'),
                'IFAWG_BACKUP_BASE': str(w / 'if_awg.h.awg-prev'),
                'AWGCTL_BACKUP_BASE': str(w / 'awgctl.awg-prev'),
                'REPODIR': str(w)}

    def seed_pair(self, w):
        (w / 'bsd').write_bytes(b'kernel-bytes\n')
        (w / 'ifconfig').write_bytes(b'ifconfig-bytes\n')
        (w / 'if_awg.h').write_text('header\n')
        (w / 'awgctl').write_bytes(b'ctl-bytes\n')
        (w / 'kernel.SHA256').write_text('sha256-old  /bsd\n')

    def test_backup_pair_stamps_generation_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            (w / 'manifest').write_text('schema=1\ngeneration=3\n')
            r = self.run_sh(self.GEN_BODY,
                            {'sha256': SHIM_SHA256, 'stat': '#!/bin/sh\necho 13\n',
                             'uname': SHIM_UNAME},
                            self.backup_env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('gen=4', r.stdout)
            self.assertIn('bsd.awg-prev.4', r.stdout)
            self.assertEqual((w / 'bsd.awg-prev.4').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig.awg-prev.4').read_bytes(), b'ifconfig-bytes\n')
            self.assertEqual((w / 'if_awg.h.awg-prev.4').read_text(), 'header\n')
            self.assertEqual((w / 'awgctl.awg-prev.4').read_bytes(), b'ctl-bytes\n')
            self.assertEqual((w / 'kernel.SHA256.awg-prev.4').read_text(), 'sha256-old  /bsd\n')
            manifest = (w / 'manifest').read_text()
            self.assertIn('schema=3', manifest)
            self.assertIn('generation=4', manifest)
            self.assertIn('backup_bsd_path=' + str(w / 'bsd.awg-prev.4'), manifest)
            self.assertIn('backup_bsd_size=13', manifest)
            self.assertIn('backup_awgctl_sha256=' + hashlib.sha256(b'ctl-bytes\n').hexdigest(),
                          manifest)
            self.assertIn('backup_kernel_sha256_sha256='
                          + hashlib.sha256(b'sha256-old  /bsd\n').hexdigest(), manifest)
            # tmp+rename leaves no .tmp file behind
            self.assertFalse(list(w.glob('manifest.tmp.*')))

    PUBLISH_BODY = (extract('manifest_get') + '\n' + extract('backup_member')
                    + '\n' + extract('restore_pair_member')
                    + '\n' + extract('prevalidate_pair_member')
                    + '\n' + extract('remove_pair_member')
                    + '\n' + extract('restore_generation_pair')
                    + '\n' + extract('release_tag') + '\n' + extract('next_generation')
                    + '\n' + extract('write_backup_pair')
                    + '\n' + extract('publish_ifconfig') + '\n' + extract('publish_header')
                    + '\n' + extract('kernel_publish')
                    + '\n' + extract('publish_awgctl') + '\n' + extract('record_installed_pair')
                    + '\n' + extract('prune_old_backups') + '\n' + extract('restore_previous_pair')
                    + '\n' + extract('rollback_uncommitted')
                    + '\n' + extract('publish_abort')
                    + '\n' + extract('publish_pair') + '\npublish_pair\n')

    def publish_env(self, w):
        env = self.backup_env(w)
        env['BSD_STAGING'] = str(w / 'bsd.awg-staging')
        env['IFAWG_STAGED'] = str(w / 'build' / 'if_awg.h')
        env['IFCFG_ARTIFACT'] = str(w / 'build' / 'ifconfig')
        env['AWGCTL_ARTIFACT'] = str(w / 'build' / 'awgctl')
        env['KERNEL_OBJ'] = str(w / 'build' / 'bsd')
        return env

    def seed_build(self, w):
        (w / 'build').mkdir()
        (w / 'build' / 'ifconfig').write_bytes(b'new-ifconfig\n')
        (w / 'build' / 'awgctl').write_bytes(b'new-ctl\n')
        (w / 'build' / 'bsd').write_bytes(b'new-kernel\n')
        (w / 'build' / 'if_awg.h').write_text('new-header\n')
        (w / 'build' / 'ifconfig').chmod(0o755)
        (w / 'build' / 'awgctl').chmod(0o755)
        (w / 'build' / 'bsd').chmod(0o755)

    def test_publish_success_records_generation_pair(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            body = (self.PUBLISH_BODY
                    .replace('publish_pair\n',
                             'kernel_object_path() { echo "$KERNEL_OBJ"; }\n'
                             'verify_kernel_artifact() { log "verified $1"; }\n'
                             + 'publish_pair\n'))
            r = self.run_sh(body,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
                             'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
                             'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
                             'ln': '#!/bin/sh\nexit 0\n', 'rm': '#!/bin/sh\nexit 0\n',
                             'cmp': '#!/bin/sh\nexit 0\n'},
                            self.publish_env(w))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Published generation 1', r.logfile)
            manifest = (w / 'manifest').read_text()
            self.assertIn('generation=1', manifest)
            self.assertIn('installed_bsd_sha256=' + hashlib.sha256(b'new-kernel\n').hexdigest(),
                          manifest)
            self.assertIn('installed_ifconfig_sha256='
                          + hashlib.sha256(b'new-ifconfig\n').hexdigest(), manifest)
            self.assertIn('installed_awgctl_sha256='
                          + hashlib.sha256(b'new-ctl\n').hexdigest(), manifest)
            self.assertIn('installed_bsd_size=13', manifest)

    def test_publish_failure_restores_previous_pair(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            body = (self.PUBLISH_BODY
                    .replace('publish_pair\n',
                             'kernel_object_path() { echo "$KERNEL_OBJ"; }\n'
                             'verify_kernel_artifact() { log "verified $1"; }\n'
                             + 'publish_pair\n'))
            # install(8) fails the first time (the ifconfig swap), succeeds on
            # the restore: the run must end with the previous pair + manifest.
            r = self.run_sh(body,
                            {'sha256': SHIM_SHA256, 'install': self.SHIM_INSTALL_FAIL_FIRST,
                             'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
                             'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
                             'ln': '#!/bin/sh\nexit 0\n', 'rm': '#!/bin/sh\nexit 0\n',
                             'cmp': '#!/bin/sh\nexit 0\n'},
                            self.publish_env(w))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ifconfig publish failed', r.stderr)
            self.assertIn('previous pair restored from generation 1', r.stderr)
            # The generation-1 backup holds the previous pair; the restore
            # copied it back; the manifest was written once (not appended).
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            manifest = (w / 'manifest').read_text()
            self.assertIn('generation=1', manifest)
            self.assertNotIn('installed_bsd_sha256', manifest)
            self.assertFalse(list(w.glob('manifest.tmp.*')))

    # install(8) shim that fails the first awgctl install: the kernel, header
    # and tool are already published when the transaction breaks, and the
    # restore's awgctl copy succeeds.
    SHIM_INSTALL_FAIL_AWGCTL = ('#!/usr/bin/env python3\nimport os,shutil,sys\n'
                                'a=sys.argv[1:]\n'
                                'while a and a[0].startswith("-"):\n'
                                '    if a[0] in ("-m","-o","-g","-u"): a=a[2:]\n'
                                '    else: a=a[1:]\n'
                                'm=os.environ.get("AWGCTL_MARK","")\n'
                                'if a and os.path.basename(a[-1]).startswith("awgctl") and not os.path.exists(m):\n'
                                '    open(m,"w").write("1")\n'
                                '    sys.exit(1)\n'
                                'if len(a)>1: shutil.copy(a[-2], a[-1])\n')

    def test_publish_failure_after_kernel_restores_previous_kernel(self):
        """R5-R4-02: a break *after* the kernel is published (awgctl install)
        must bring back the previous kernel, not leave the new one."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            # A previous generation (1) is recorded; this run publishes gen 2.
            (w / 'manifest').write_text(
                'schema=2\ngeneration=1\n'
                'backup_bsd_path=' + str(w / 'bsd.awg-prev.1') + '\n'
                'backup_ifconfig_path=' + str(w / 'ifconfig.awg-prev.1') + '\n'
                'backup_if_awg_h_path=' + str(w / 'if_awg.h.awg-prev.1') + '\n'
                'backup_awgctl_path=' + str(w / 'awgctl.awg-prev.1') + '\n'
                'backup_bsd_sha256=' + hashlib.sha256(b'kernel-bytes\n').hexdigest() + '\n'
                'backup_ifconfig_sha256=' + hashlib.sha256(b'ifconfig-bytes\n').hexdigest()
                + '\n'
                'installed_bsd_sha256=' + hashlib.sha256(b'kernel-bytes\n').hexdigest() + '\n'
                'installed_ifconfig_sha256='
                + hashlib.sha256(b'ifconfig-bytes\n').hexdigest() + '\n')
            (w / 'bsd.awg-prev.1').write_bytes(b'kernel-bytes\n')
            (w / 'ifconfig.awg-prev.1').write_bytes(b'ifconfig-bytes\n')
            body = (self.PUBLISH_BODY
                    .replace('publish_pair\n',
                             'kernel_object_path() { echo "$KERNEL_OBJ"; }\n'
                             'verify_kernel_artifact() { log "verified $1"; }\n'
                             + 'publish_pair\n'))
            r = self.run_sh(body,
                            {'sha256': SHIM_SHA256, 'install': self.SHIM_INSTALL_FAIL_AWGCTL,
                             'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
                             'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
                             'ln': '#!/bin/sh\nexit 0\n', 'rm': '#!/bin/sh\nexit 0\n',
                             'cmp': '#!/bin/sh\nexit 0\n'},
                            dict(self.publish_env(w), AWGCTL_MARK=str(w / 'awgctl-failed')))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('awgctl publish failed', r.stderr)
            self.assertIn('previous pair restored from generation 2', r.stderr)
            # The published kernel (gen 2) is replaced by the recorded gen-2
            # backup — the previous kernel bytes, with the hash the manifest
            # recorded for that generation.
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            manifest = (w / 'manifest').read_text()
            self.assertIn('generation=2', manifest)
            # The restore rewrites the installed-pair keys with the restored
            # generation's hashes (the pre-existing manifest had none).
            self.assertIn('backup_bsd_sha256='
                          + hashlib.sha256(b'kernel-bytes\n').hexdigest(), manifest)
            # The new header/tool were published before the break and are
            # replaced by the recorded generation as a set.
            self.assertEqual((w / 'if_awg.h').read_text(), 'header\n')
            self.assertFalse(list(w.glob('manifest.tmp.*')))

    # R6-R4-02: PUBLISH_STARTED must be a recovery trap. A SIGTERM that lands
    # after the kernel is published (before awgctl) runs the same restore: the
    # previous kernel comes back with the hash the manifest recorded.
    SHIM_INSTALL_SIGTERM_AFTER_KERNEL = ('#!/usr/bin/env python3\nimport os,shutil,sys,signal\n'
                                         'a=sys.argv[1:]\n'
                                         'while a and a[0].startswith("-"):\n'
                                         '    if a[0] in ("-m","-o","-g","-u"): a=a[2:]\n'
                                         '    else: a=a[1:]\n'
                                         'if len(a)>1: shutil.copy(a[-2], a[-1])\n'
                                         'if os.path.basename(a[-1]).startswith("bsd"):\n'
                                         '    sys.stderr.flush()\n'
                                         '    os.kill(os.getppid(), signal.SIGTERM)\n')

    def test_sigterm_after_kernel_publish_restores_previous_kernel(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            self.seed_pair(w)
            self.seed_build(w)
            # A previous generation (1) is recorded; this run publishes gen 2.
            (w / 'manifest').write_text(
                'schema=2\ngeneration=1\n'
                'backup_bsd_path=' + str(w / 'bsd.awg-prev.1') + '\n'
                'backup_ifconfig_path=' + str(w / 'ifconfig.awg-prev.1') + '\n'
                'backup_if_awg_h_path=' + str(w / 'if_awg.h.awg-prev.1') + '\n'
                'backup_awgctl_path=' + str(w / 'awgctl.awg-prev.1') + '\n'
                'backup_bsd_sha256=' + hashlib.sha256(b'kernel-bytes\n').hexdigest() + '\n'
                'backup_ifconfig_sha256=' + hashlib.sha256(b'ifconfig-bytes\n').hexdigest() + '\n'
                'backup_if_awg_h_sha256=' + hashlib.sha256(b'header\n').hexdigest() + '\n'
                'backup_awgctl_sha256=' + hashlib.sha256(b'ctl-bytes\n').hexdigest() + '\n')
            (w / 'bsd.awg-prev.1').write_bytes(b'kernel-bytes\n')
            (w / 'ifconfig.awg-prev.1').write_bytes(b'ifconfig-bytes\n')
            (w / 'if_awg.h.awg-prev.1').write_text('header\n')
            (w / 'awgctl.awg-prev.1').write_bytes(b'ctl-bytes\n')
            body = (self.PUBLISH_BODY
                    .replace('extract', 'extract')
                    .replace('publish_pair\n',
                             'kernel_object_path() { echo "$KERNEL_OBJ"; }\n'
                             'verify_kernel_artifact() { log "verified $1"; }\n'
                             + 'publish_pair\n'))
            body = (extract('publish_interrupted') + '\n' + body)
            r = self.run_sh(body,
                            {'sha256': SHIM_SHA256,
                             'install': self.SHIM_INSTALL_SIGTERM_AFTER_KERNEL,
                             'stat': '#!/bin/sh\necho 13\n', 'uname': SHIM_UNAME,
                             'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n',
                             'ln': '#!/bin/sh\nexit 0\n', 'rm': '#!/bin/sh\nexit 0\n',
                             'cmp': '#!/bin/sh\nexit 0\n'},
                            self.publish_env(w))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('Interrupted mid-publish', r.stderr)
            # The published kernel (gen 2) is replaced by the recorded gen-1
            # backup — the previous kernel, with the hash the manifest holds.
            self.assertEqual((w / 'bsd').read_bytes(), b'kernel-bytes\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'ifconfig-bytes\n')
            self.assertEqual((w / 'if_awg.h').read_text(), 'header\n')
            self.assertEqual((w / 'awgctl').read_bytes(), b'ctl-bytes\n')
            manifest = (w / 'manifest').read_text()
            self.assertIn('backup_bsd_sha256='
                          + hashlib.sha256(b'kernel-bytes\n').hexdigest(), manifest)
            self.assertFalse(list(w.glob('manifest.tmp.*')))

    ROLLBACK_TRIPLE_BODY = (extract('manifest_get') + '\n'
                            + extract('prevalidate_pair_member') + '\n'
                            + extract('restore_pair_member') + '\n'
                            + extract('remove_pair_member') + '\n'
                            + extract('restore_generation_pair') + '\n'
                            + extract('record_installed_pair') + '\n'
                            + extract('rollback_pair') + '\nrollback_pair\n')

    def test_rollback_restores_kernel_tool_header_together(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'bsd').write_bytes(b'new-kernel\n')
            (w / 'ifconfig').write_bytes(b'new-ifconfig\n')
            (w / 'if_awg.h').write_text('new-header\n')
            (w / 'awgctl').write_bytes(b'new-ctl\n')
            (w / 'bsd.awg-prev.2').write_bytes(b'old-kernel\n')
            (w / 'ifconfig.awg-prev.2').write_bytes(b'old-ifconfig\n')
            (w / 'if_awg.h.awg-prev.2').write_text('old-header\n')
            (w / 'awgctl.awg-prev.2').write_bytes(b'old-ctl\n')
            (w / 'manifest').write_text(
                'schema=2\ngeneration=2\n'
                'backup_generation=2\n'
                'backup_bsd_path=' + str(w / 'bsd.awg-prev.2') + '\n'
                'backup_ifconfig_path=' + str(w / 'ifconfig.awg-prev.2') + '\n'
                'backup_if_awg_h_path=' + str(w / 'if_awg.h.awg-prev.2') + '\n'
                'backup_awgctl_path=' + str(w / 'awgctl.awg-prev.2') + '\n'
                'backup_bsd_sha256=' + hashlib.sha256(b'old-kernel\n').hexdigest() + '\n'
                'backup_ifconfig_sha256=' + hashlib.sha256(b'old-ifconfig\n').hexdigest() + '\n'
                'backup_if_awg_h_sha256=' + hashlib.sha256(b'old-header\n').hexdigest() + '\n'
                'backup_awgctl_sha256=' + hashlib.sha256(b'old-ctl\n').hexdigest() + '\n'
                'installed_bsd_sha256=' + hashlib.sha256(b'new-kernel\n').hexdigest() + '\n'
                'installed_ifconfig_sha256=' + hashlib.sha256(b'new-ifconfig\n').hexdigest() + '\n')
            env = self.backup_env(w)
            r = self.run_sh(self.ROLLBACK_TRIPLE_BODY,
                            {'sha256': SHIM_SHA256, 'install': SHIM_INSTALL,
                             'stat': '#!/bin/sh\necho 13\n',
                             'chmod': '#!/bin/sh\nexit 0\n', 'chown': '#!/bin/sh\nexit 0\n'},
                            env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((w / 'bsd').read_bytes(), b'old-kernel\n')
            self.assertEqual((w / 'ifconfig').read_bytes(), b'old-ifconfig\n')
            self.assertEqual((w / 'if_awg.h').read_text(), 'old-header\n')
            self.assertEqual((w / 'awgctl').read_bytes(), b'old-ctl\n')
            self.assertIn('Quadruple restored', r.logfile)
            # The manifest now records the restored generation as installed.
            manifest = (w / 'manifest').read_text()
            self.assertIn('installed_bsd_sha256=' + hashlib.sha256(b'old-kernel\n').hexdigest(),
                          manifest)
            self.assertIn('installed_ifconfig_sha256='
                          + hashlib.sha256(b'old-ifconfig\n').hexdigest(), manifest)


if __name__ == '__main__':
    unittest.main()
