"""E2-HYGIENE R1-2 (R2-hardened, R3-1 kernel byte fix): the declared ship list is verified against a
staged install.

docs/SHIP-LIST.md is the definitive release file list (name, install path,
owner:group, mode). This suite proves the packaging inputs agree with it by
executing, repo-only (no host access):

1. `install.sh --all` against a sandboxed root with recording shims — the
   staged install records the destination path, owner, group and mode of every
   published artefact (same sandbox as test_installer_r6_staging, with the
   `install-rec` shim in front of PATH so -o/-g/-m are captured, not dropped).
   R2-1: the shim maps every destination under $INSTALL_SANDBOX_ROOT (the
   temp staging root, so the hard-coded /usr/share/man/man8/ifconfig.8 and the
   /usr/include/net, /usr/local/sbin directories are contained), records a
   row only after the copy and mode succeed, and fails closed on escapes.
   R3-2: symlink escapes are rejected by resolving the destination path before
   containment check. The suite then asserts the staged file exists with the
   recorded mode and the source artefact's bytes for all seven rows, and an
   injected copy failure (INSTALL_REC_FAIL) proves the recorded rows track
   real copies.
2. The standalone bsd.prog.mk install of sbin/awgctl. The host here is Linux
   (GNU make, no /usr/share/mk/bsd.prog.mk), so the install rules are the
   documented bsd.prog.mk ones (`install -c -s -o ${BINOWN} -g ${BINGRP}
   -m ${BINMODE} ${PROG} ${DESTDIR}${BINDIR}/${PROG}` and the MAN counterpart
   with MANOWN/MANGRP/MANMODE). The simulation reads PROG/MAN/BINDIR/MANDIR/
   BINOWN/BINGRP/BINMODE/MANOWN/MANGRP/MANMODE from sbin/awgctl/Makefile
   (bsd.prog.mk defaults where unset). R2-2: the PROG input is provisioned by
   this test itself — the same cc invocation the root Makefile uses on Linux
   (`-DAWGCTL_LINUX -Isrc -Isbin/awgctl … -lutil`) builds awgctl.c into the
   sandbox — so the test does not depend on the ignored binary earlier tests
   leave in the tree. The staged install runs the recording shim and the
   staged bytes/mode are asserted. This is labelled a bsd.prog.mk rule
   simulation, distinct from the native install.sh run.
3. The root Makefile has an explicit `install` target, so `make install`
   cannot fall back to the implicit `%.sh -> %` rule that created an untracked
   executable ./install (v0.2.1-rc1 defect class). `make -n install` prints
   the install.sh invocation and creates no ./install; `make check-gitclean`
   stays clean after `make install`.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_installer_r6_staging import InstallerR6Staging

ROOT = Path(__file__).resolve().parents[2]
SHIP = ROOT / 'docs' / 'SHIP-LIST.md'
AWGCTL_MK = ROOT / 'sbin' / 'awgctl' / 'Makefile'
SHIMS = Path(__file__).resolve().parent / 'shims'
RECORDING_SHIM = SHIMS / 'install-rec'

# bsd.prog.mk(5) defaults for the variables this suite reads.
PROG_MK_DEFAULTS = {
    'BINDIR': '/usr/sbin',
    'MANDIR': '/usr/share/man',
    'BINOWN': 'root', 'BINGRP': 'bin', 'BINMODE': '555',
    'MANOWN': 'root', 'MANGRP': 'bin', 'MANMODE': '444',
}


def parse_ship_list():
    """Rows of the 'Installed artefacts' table: {file, path, ownergroup, mode}."""
    rows = []
    for line in SHIP.read_text().splitlines():
        m = re.match(r'\|\s*(\d+)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|', line)
        if m:
            clean = lambda s: s.strip().strip('`').strip()
            rows.append({'file': clean(m.group(2)),
                         'path': clean(m.group(3)),
                         'ownergroup': clean(m.group(4)),
                         'mode': clean(m.group(5))})
    return rows


def parse_prog_mk():
    """Makefile variable assignments, with bsd.prog.mk defaults for unset keys."""
    values = dict(PROG_MK_DEFAULTS)
    for line in AWGCTL_MK.read_text().splitlines():
        m = re.match(r'([A-Z][A-Z0-9_]*)\s*=\s*(\S+)', line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


def read_record(path):
    """Recorded install rows: [{path, ownergroup, mode, type, staged}] (TSV).

    The recording shim appends the staged path as a 6th column; older 4/5
    column rows (plain install shim) stay readable with staged = path.
    """
    rows = []
    if not os.path.isfile(path):
        return rows
    for line in open(path):
        parts = line.rstrip('\n').split('\t')
        if len(parts) == 6:
            rows.append({'path': parts[0], 'ownergroup': '%s:%s' % (parts[1], parts[2]),
                         'mode': parts[3], 'type': parts[4], 'staged': parts[5]})
        elif len(parts) == 5:
            rows.append({'path': parts[0], 'ownergroup': '%s:%s' % (parts[1], parts[2]),
                         'mode': parts[3], 'type': parts[4], 'staged': parts[0]})
        elif len(parts) == 4:
            rows.append({'path': parts[0], 'ownergroup': '%s:%s' % (parts[1], parts[2]),
                         'mode': parts[3], 'type': 'file', 'staged': parts[0]})
    return rows


def staged_problems(rec, want_bytes):
    """Problems proving the recorded install really landed: [] when it did.

    Checks the staged file exists, carries the recorded filesystem mode, and
    holds the source artefact's bytes (want_bytes None = bytes not compared).
    """
    problems = []
    if not os.path.isfile(rec['staged']):
        problems.append('%s: recorded as installed but %s does not exist'
                        % (rec['path'], rec['staged']))
        return problems
    st = os.stat(rec['staged'])
    got_mode = oct(st.st_mode & 0o777)[2:]
    if got_mode != rec['mode']:
        problems.append('%s: staged mode %s != recorded mode %s'
                        % (rec['path'], got_mode, rec['mode']))
    if want_bytes is not None and Path(rec['staged']).read_bytes() != want_bytes:
        problems.append('%s: staged bytes differ from the source artefact' % rec['path'])
    return problems


class ShipListStagedInstall(unittest.TestCase):
    """The staged install publishes exactly the ship list, with its modes."""

    def sandbox(self, w, fail_copy=None):
        """R6 sandbox plus the ifconfig.8 man page the patched tree carries.

        PATH gets a private directory whose `install` is a symlink to the
        recording shim (install-rec), so -o/-g/-m land in $INSTALL_RECORD
        while the other shims (make, stat, sha256, ...) stay the R6 set.
        INSTALL_SANDBOX_ROOT keeps every destination (including the hard-coded
        /usr/share/man/man8/ifconfig.8) inside the staging tree;
        INSTALL_REC_FAIL injects a copy failure for one artefact.
        """
        base = InstallerR6Staging()
        base.sandbox(w)
        base.tree(w)
        (w / 'ifcfg' / 'ifconfig.8').write_text('.Dd awg\n')
        # Mirror the canonical /sbin/ifconfig layout under the staging prefix
        # (the R6 default is a flat w/ifconfig) so recorded destinations match
        # the ship list paths verbatim.
        (w / 'sbin').mkdir(exist_ok=True)
        (w / 'sbin' / 'ifconfig').write_bytes(b'ifconfig-bytes\n')
        shim = w / 'rec-shim'
        shim.mkdir()
        os.symlink(str(RECORDING_SHIM), str(shim / 'install'))
        env = base.env(w)
        env['IFCFG_FILE'] = str(w / 'sbin' / 'ifconfig')
        env['PATH'] = str(shim) + ':' + env['PATH']
        env['INSTALL_RECORD'] = str(w / 'install-record.tsv')
        env['INSTALL_SANDBOX_ROOT'] = str(w)
        if fail_copy:
            env['INSTALL_REC_FAIL'] = fail_copy
        return env

    def run_install(self, w, args, env):
        return subprocess.run(['sh', str(ROOT / 'install.sh')] + args, env=env,
                              capture_output=True, text=True, cwd=str(w))

    def artefact_sources(self, w, stdout):
        """Where each ship-list artefact's bytes come from for this run.

        The built members are named by the installer's own log lines
        (`ifconfig built:`, `awgctl built:`, `Verified <kernel-obj-path>`, `Staged driver header in`);
        the kernel is the object directory the run resolved (OBJDIR/bsd) — the
        pre-publish build artefact, not the installed destination. The man
        pages and the awg-quick script are repo files.

        R4-1 fix: the sandbox fixture uses the kernel obj path from the
        installer's 'Verified' log line (pre-publish source). Missing source
        is a hard failure for every row — no silent None fallback.
        """
        def log_path(prefix):
            # prefix includes trailing space (e.g. 'ifconfig built: ')
            m = re.search(re.escape(prefix) + r'(\S+)', stdout)
            return m.group(1) if m else None
        # Kernel obj path appears in "Verified <path> (size, ...)" from verify_kernel_artifact
        kernel_obj = log_path('Verified ')
        if kernel_obj and kernel_obj.endswith(')'):
            kernel_obj = kernel_obj.split(' ')[0]
        return {
            'bsd': kernel_obj,  # R4-1: kernel obj path from installer log (pre-publish source)
            'ifconfig': log_path('ifconfig built: '),
            'ifconfig.8': str(w / 'ifcfg' / 'ifconfig.8'),
            'if_awg.h': log_path('Staged driver header in '),
            'awgctl': log_path('awgctl built: '),
            'awgctl.8': str(ROOT / 'sbin' / 'awgctl' / 'awgctl.8'),
            'awg-quick': str(ROOT / 'sbin' / 'awg-quick'),
        }

    def assert_staged_matches_ship_list(self, w, stdout, names=None):
        """Every recorded row exists on disk with its mode and source bytes."""
        rows = parse_ship_list()
        self.assertEqual(len(rows), 7, 'the ship list has seven artefacts')
        recorded = [r for r in read_record(str(w / 'install-record.tsv'))
                    if r['type'] == 'file']
        by_name = {}
        for rec in recorded:
            by_name.setdefault(os.path.basename(rec['path']), []).append(rec)
        sources = self.artefact_sources(w, stdout)
        for row in rows:
            # The ship list names e.g. "ifconfig (patched)" / "awgctl (tool)":
            # the artefact name is the first word.
            name = row['file'].split()[0]
            if names is not None and name not in names:
                continue
            hits = by_name.get(name, [])
            self.assertEqual(len(hits), 1,
                             '%s installed exactly once: %r' % (name, hits))
            rec = hits[0]
            self.assertEqual(rec['ownergroup'], row['ownergroup'],
                             '%s owner:group' % name)
            self.assertEqual(rec['mode'], row['mode'], '%s mode' % name)
            # The staging prefix mirrors the canonical install path
            # (BSD_FILE/IFCFG_FILE/... map /bsd, /usr/... under the prefix);
            # the ifconfig man page keeps the literal /usr/share/man/man8
            # destination of publish_ifconfig, contained by the sandbox root.
            self.assertTrue(
                rec['path'].endswith(row['path'].lstrip('/')),
                '%s destination %s under the staging prefix matches %s'
                % (name, rec['path'], row['path']))
            # R4-1: source bytes MUST exist for every row — no silent None fallback.
            src = sources.get(name)
            self.assertIsNotNone(src, '%s: no source path in artefact_sources' % name)
            self.assertTrue(os.path.isfile(src), '%s source missing: %s' % (name, src))
            want = Path(src).read_bytes()
            problems = staged_problems(rec, want)
            self.assertEqual(problems, [], '%s staged install: %s' % (name, problems))
        # Directories created by the publish steps are not artefacts.
        all_names = {row['file'].split()[0] for row in rows}
        for rec in recorded:
            if names is None:
                self.assertIn(os.path.basename(rec['path']), all_names,
                              'no artefact outside the ship list: %r' % rec)

    def test_staged_all_install_matches_ship_list(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env = self.sandbox(w)
            r = self.run_install(w, ['--all'], env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assert_staged_matches_ship_list(w, r.stdout)

    def test_staged_all_copy_failure_is_not_certified(self):
        """Adversarial control (R2-1): a failed copy is not a shipped artefact.

        install.sh tolerates the ifconfig.8 install failing, so the proof is
        the recorded/staged state: with the copy injected to fail, the
        ship-list verification reports the missing staged file (the assertion
        goes red) instead of accepting a pre-copy record.
        """
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env = self.sandbox(w, fail_copy='ifconfig.8')
            r = self.run_install(w, ['--all'], env)
            self.assertEqual(r.returncode, 0, r.stderr)
            recorded = [rec for rec in read_record(str(w / 'install-record.tsv'))
                        if os.path.basename(rec['path']) == 'ifconfig.8']
            self.assertEqual(recorded, [],
                             'a failed copy must record nothing: %r' % recorded)
            problems = staged_problems({'path': '/usr/share/man/man8/ifconfig.8',
                                        'staged': os.path.join(str(w), 'usr/share/man/man8/ifconfig.8'),
                                        'mode': '444'}, None)
            self.assertTrue(any('does not exist' in p for p in problems),
                            'the failed copy must be reported: %r' % problems)

    def test_staged_kernel_only_install_matches_subset(self):
        """--kernel-only ships rows 1+4 (kernel + header), per the ship list."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env = self.sandbox(w)
            r = self.run_install(w, ['--kernel-only'], env)
            self.assertEqual(r.returncode, 0, r.stderr)
            recorded = read_record(str(w / 'install-record.tsv'))
            names = [os.path.basename(rec['path']) for rec in recorded]
            self.assertIn('bsd', names)
            self.assertIn('if_awg.h', names)
            self.assertNotIn('awgctl', names, '--kernel-only ships no awgctl')
            self.assertNotIn('awg-quick', names, '--kernel-only ships no awg-quick')
            self.assert_staged_matches_ship_list(w, r.stdout, names={'bsd', 'if_awg.h'})

    def provision_awgctl_build(self, w):
        """Build the PROG input in the sandbox (R2-2): the root Makefile's
        Linux cc invocation of the real awgctl.c, into w/build/awgctl.

        Returns (path, build command) so the evidence records how the input
        was produced; independent of any build output earlier tests leave.
        """
        (w / 'build').mkdir(parents=True, exist_ok=True)
        out = w / 'build' / 'awgctl'
        cc = (os.environ.get('CC') or 'cc')
        if not Path(cc).exists() and __import__('shutil').which(cc) is None:
            cc = 'gcc'
        cmd = [cc, '-DAWGCTL_LINUX', '-I' + str(ROOT / 'src'),
               '-I' + str(AWGCTL_MK.parent), '-o', str(out),
               str(AWGCTL_MK.parent / 'awgctl.c'), '-lutil']
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, 'sandbox awgctl build: %s' % r.stderr)
        self.assertTrue(out.is_file(), 'the sandbox build produced %s' % out)
        return out, ' '.join(cmd)

    def test_standalone_progmk_install_matches_ship_list(self):
        """The bsd.prog.mk install (simulated rules, this host has no bsd.prog.mk)
        places awgctl + awgctl.8 at the release destinations and modes."""
        values = parse_prog_mk()
        self.assertEqual(values.get('PROG'), 'awgctl')
        self.assertEqual(values.get('MAN'), 'awgctl.8')
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            record = w / 'progmk-record.tsv'
            staged = w / 'destdir'
            # The PROG input is provisioned here (sandbox build), not taken
            # from the tree's ignored build output.
            prog, build_cmd = self.provision_awgctl_build(w)
            man = AWGCTL_MK.parent / values['MAN']
            env = dict(os.environ)
            env['INSTALL_RECORD'] = str(record)
            env['INSTALL_SANDBOX_ROOT'] = str(staged)
            # install(1) with the bsd.prog.mk variables, staged under DESTDIR:
            # the documented `install -c -s -o ${BINOWN} -g ${BINGRP}
            # -m ${BINMODE} ${PROG} ${DESTDIR}${BINDIR}/${PROG}` and the MAN
            # counterpart (MANOWN/MANGRP/MANMODE, MANDIR/man8).
            bin_dir = os.path.join(str(staged), values['BINDIR'].lstrip('/'))
            man_dir = os.path.join(str(staged), values['MANDIR'].lstrip('/'), 'man8')
            for args in (
                ['-d', '-o', values['BINOWN'], '-g', values['BINGRP'], '-m', '755', bin_dir],
                ['-c', '-s', '-o', values['BINOWN'], '-g', values['BINGRP'],
                 '-m', values['BINMODE'], str(prog),
                 os.path.join(bin_dir, values['PROG'])],
                ['-d', '-o', values['MANOWN'], '-g', values['MANGRP'], '-m', '755', man_dir],
                ['-c', '-o', values['MANOWN'], '-g', values['MANGRP'],
                 '-m', values['MANMODE'], str(man),
                 os.path.join(man_dir, values['MAN'])],
            ):
                r = subprocess.run([sys.executable, str(RECORDING_SHIM)] + args,
                                   env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, 'install %s: %s' % (args, r.stderr))
            rows = {r['file'].split()[0]: r for r in parse_ship_list()}
            recs = {os.path.basename(r['path']): r for r in read_record(str(record))}
            for name in ('awgctl', 'awgctl.8'):
                self.assertIn(name, recs, '%s staged by the standalone install' % name)
                self.assertEqual(recs[name]['mode'], rows[name]['mode'],
                                 'standalone %s mode vs ship list' % name)
                self.assertEqual(recs[name]['ownergroup'], rows[name]['ownergroup'],
                                 'standalone %s owner:group vs ship list' % name)
                want = os.path.join(str(staged), rows[name]['path'].lstrip('/'))
                self.assertEqual(os.path.normpath(recs[name]['path']),
                                 os.path.normpath(want),
                                 'standalone %s destination vs ship list' % name)
                src_bytes = (prog if name == 'awgctl' else man).read_bytes()
                problems = staged_problems(recs[name], src_bytes)
                self.assertEqual(problems, [], 'standalone %s staged: %s' % (name, problems))

    def test_make_install_is_explicit_and_leaves_no_untracked_install(self):
        """`make install` runs install.sh (no implicit %.sh rule) and the tree
        stays porcelain-clean (the ./install untracked-executable class)."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env = self.sandbox(w)
            # -n: the recipe is the install.sh invocation, and the implicit
            # rule must not add a second command creating ./install.
            n = subprocess.run(['make', '-n', 'install'], cwd=str(ROOT),
                               capture_output=True, text=True)
            self.assertEqual(n.returncode, 0, n.stderr)
            self.assertIn('install.sh', n.stdout)
            self.assertNotIn('cat install.sh >install', n.stdout)
            self.assertFalse((ROOT / 'install').exists(),
                             'make -n install must not create ./install')
            r = subprocess.run(['/usr/bin/make', 'install'], cwd=str(ROOT), env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((ROOT / 'install').exists(),
                             'make install must not create ./install')
            g = subprocess.run(['git', 'status', '--porcelain'], cwd=str(ROOT),
                               capture_output=True, text=True)
            self.assertEqual(g.returncode, 0, g.stderr)
            self.assertEqual(g.stdout.strip(), '',
                             'tree stays porcelain-clean after make install: %s' % g.stdout)
            c = subprocess.run(['make', 'check-gitclean'], cwd=str(ROOT),
                               capture_output=True, text=True)
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertIn('git tree: clean', c.stdout)

    def test_kernel_corruption_regression(self):
        """R4-1: corrupting the staged kernel (mode preserved 700) must make
        the shared seven-row verifier go red. The pre-publish obj/bsd is the
        source of truth; the staged /bsd must match it byte-for-byte."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env = self.sandbox(w)
            r = self.run_install(w, ['--all'], env)
            self.assertEqual(r.returncode, 0, r.stderr)
            # Baseline: verifier passes on unmodified staged install
            self.assert_staged_matches_ship_list(w, r.stdout)
            # Corrupt ONLY the staged kernel, preserve mode 700
            staged_bsd = w / 'bsd'
            self.assertTrue(staged_bsd.is_file())
            keep_mode = os.stat(staged_bsd).st_mode & 0o777
            self.assertEqual(keep_mode, 0o700, 'kernel mode must be 700')
            staged_bsd.write_bytes(b'CORRUPTED-SHIP-BYTES\n')
            os.chmod(staged_bsd, keep_mode)
            # The shared verifier must now fail (byte mismatch detected)
            with self.assertRaises(AssertionError) as cm:
                self.assert_staged_matches_ship_list(w, r.stdout)
            self.assertIn('staged bytes differ', str(cm.exception))

    def test_symlink_escape_regression(self):
        """R4-2: symlink escapes (file and directory) must be rejected by
        install-rec — no outside mutation, no success record. The shim resolves
        the destination before containment check (R3-2 behaviour)."""
        WT = Path(__file__).resolve().parents[2]
        SHIM = WT / 'tools' / 'tests' / 'shims' / 'install-rec'
        
        def run_case(label, make_symlink, dst, dir_mode=False):
            with tempfile.TemporaryDirectory() as d:
                base = Path(d)
                sandbox = base / 'sandbox'
                outside = base / 'outside'
                sandbox.mkdir()
                outside.mkdir()
                src = base / 'src.bin'
                src.write_bytes(b'escape-probe-bytes\n')
                if make_symlink:
                    os.symlink(str(outside), str(sandbox / 'usr'))
                record = base / 'record.tsv'
                env = dict(os.environ)
                env['INSTALL_SANDBOX_ROOT'] = str(sandbox)
                env['INSTALL_RECORD'] = str(record)
                args = [sys.executable, str(SHIM)]
                if dir_mode:
                    args += ['-d', '-m', '755', dst]
                else:
                    args += ['-c', '-m', '555', str(src), dst]
                r = subprocess.run(args, env=env, capture_output=True, text=True)
                rec = record.read_text().strip() if record.is_file() else ''
                written = sorted(str(p.relative_to(outside)) for p in outside.rglob('*'))
                return r.returncode, rec, written, r.stderr.strip()
        
        # D1: symlink escape, FILE dst /usr/escape-proof
        rc, rec, written, stderr = run_case('D1', True, '/usr/escape-proof')
        self.assertNotEqual(rc, 0, 'D1 symlink file escape must fail: rc=%d' % rc)
        self.assertEqual(rec, '', 'D1 no record on escape')
        self.assertEqual(written, [], 'D1 no outside write: %r' % written)
        self.assertIn('escapes sandbox root', stderr)
        
        # D2: symlink escape, DIR dst /usr/escape-dir
        rc, rec, written, stderr = run_case('D2', True, '/usr/escape-dir', dir_mode=True)
        self.assertNotEqual(rc, 0, 'D2 symlink dir escape must fail: rc=%d' % rc)
        self.assertEqual(rec, '', 'D2 no record on escape')
        self.assertEqual(written, [], 'D2 no outside write: %r' % written)
        self.assertIn('escapes sandbox root', stderr)
        
        # E1: control — contained install (no symlink) must succeed
        rc, rec, written, stderr = run_case('E1', False, '/usr/ok')
        self.assertEqual(rc, 0, 'E1 control must succeed: %s' % stderr)
        self.assertTrue(rec, 'E1 must record success')
        self.assertEqual(written, [], 'E1 no outside write: %r' % written)


if __name__ == '__main__':
    unittest.main()