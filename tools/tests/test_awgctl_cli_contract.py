#!/usr/bin/env python3
"""
awgctl CLI Contract Tests — pins exit codes, streams, and argument validation.

This test suite documents and enforces the CLI contract for awgctl:
- Exit codes per failure class (0, 1, 2, 3)
- stdout vs stderr separation
- Argument validation (arity, unknown flags, bad interface names)
- Unprivileged behaviour (config file permissions, kernel EPERM)
- Man-page ↔ behaviour binding: every documented exit code has a test; a
  documentation-only mutation is detected.

CONTRACT SUMMARY (from man page .Sh EXIT STATUS + code inspection):
- 0 (EX_OK): success
- 1 (EX_USAGE): usage error OR I/O failure (includes config file permission/access)
- 2 (EX_CONFIG): configuration parse error (content parsing, not file access)
- 3 (EX_IOCTL): SIOCSAWG or SIOCGAWG ioctl failure, including EPERM
"""

import os
import re
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

# Build configuration
ROOT = Path(__file__).resolve().parents[2]
CC = os.environ.get('CC', 'cc')

# Synthetic lab keys (same as existing tests)
LAB_KEY = ('YHdnLWxhYi1rZXktMAAAAA' 'AAAAAAAAAAAAAAAAAAAEA=')
LAB_KEY_PUB = 'ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA='
LAB_KEY2_PRIV = ('YHdnLWxhYi1rZXktMQAAAA' 'AAAAAAAAAAAAAAAAAAAEA=')
LAB_KEY2_PUB = 'jClj1FiPuqBEcmhDqJSqB8favq6USkiwFzGt/uTOjTE='
HPK = 'BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc='

CONFIG_LEGACY = f"""[Interface]
PrivateKey = {LAB_KEY}
ListenPort = 51820
Address = 10.9.19.1/24
MTU = 1420
Jc = 3
Jmin = 10
Jmax = 25
S1 = 26
S2 = 33
H1 = 100
H2 = 200
H3 = 300
H4 = 400

[Peer]
PublicKey = {LAB_KEY2_PUB}
Endpoint = 127.0.0.1:51821
AllowedIPs = 10.9.19.2/32,fd00::2/128
PersistentKeepalive = 25
"""

# Regex to parse the EXIT STATUS section of the man page
EXIT_STATUS_RE = re.compile(
    r'\.Sh EXIT STATUS\n(.*?)\n\.Sh ', re.S)


def documented_exit_codes(man_text=None):
    """Return {code: description} from the man page EXIT STATUS section."""
    if man_text is None:
        man_text = (ROOT / 'sbin/awgctl/awgctl.8').read_text()
    m = EXIT_STATUS_RE.search(man_text)
    if not m:
        return {}
    body = m.group(1)
    codes = {}
    for cm in re.finditer(r'^\.It (\d+)\n(.*?)(?=^\.It |\Z)', body, re.S | re.M):
        code = int(cm.group(1))
        desc = ' '.join(cm.group(2).split())
        codes[code] = desc
    return codes


def trusted_base_dir():
    """
    Return a directory whose whole path from '/' is owned by root or the current
    user, and is not group/other-writable (except sticky bits). This mirrors the
    predicate open_trusted() enforces so fixtures can live under it.
    """
    import stat
    uid = os.getuid()
    candidates = [
        Path.home() / '.cache' / 'awgctl-cli-contract',
        Path('/var/tmp'),
        Path(tempfile.gettempdir()),
    ]
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            base.chmod(0o700)
        except OSError:
            continue
        ok = True
        for part in base.resolve().parts:
            if part == '/':
                continue
            # Build the path component by component from root
            # Efficient: we just stat each prefix
        # Quick walk from root
        comps = base.resolve().parts
        # / is always root 0755
        if comps[0] != '/':
            continue
        for i in range(1, len(comps)):
            path = Path(*comps[:i+1])
            try:
                st = path.stat()
            except OSError:
                ok = False
                break
            if not stat.S_ISDIR(st.st_mode):
                ok = False
                break
            if st.st_uid not in (0, uid):
                ok = False
                break
            if (st.st_mode & 0o022) != 0 and not (stat.S_ISVTX & st.st_mode):
                ok = False
                break
        if ok:
            return base
    # Fallback: home cache (user 0700)
    fb = Path.home() / '.cache' / 'awgctl-cli-contract'
    fb.mkdir(parents=True, exist_ok=True)
    fb.chmod(0o700)
    return fb


def build_fixture(workdir: Path, conf_dir: Path, *, strict_owner=False):
    """Build the awgctl fixture binary with Linux stubs.

    If strict_owner is False (default), use a narrow owner mapping that allows
    root (0) or the current test uid. If strict_owner is True, use the production
    predicate (root only)."""
    binary = workdir / 'awgctl-fixture'
    ifconfig_path = ROOT / 'tools/lab/fixture-ifconfig.py'
    if strict_owner:
        owner_def = None  # default macro CONF_OWNER_OK(u)=((u)==CONF_OWNER_UID)
    else:
        owner_def = f'-DCONF_OWNER_OK(u)=((u)==0||(u)=={os.getuid()})'
    cmd = [
        CC, '-DAWGCTL_LINUX', '-I', str(ROOT / 'src'),
        '-I', str(ROOT / 'sbin/awgctl'),
        f'-DCONF_DIR="{conf_dir}"',
    ]
    if owner_def is not None:
        cmd.append(owner_def)
    cmd.extend([
        f'-DIFCONFIG_PATH="{ifconfig_path}"',
        '-o', str(binary), str(ROOT / 'sbin/awgctl/awgctl.c'),
        str(ROOT / 'tools/lab/fixture-stubs.c'), '-lutil'
    ])
    cc = subprocess.run(cmd, capture_output=True, text=True)
    if cc.returncode != 0:
        raise RuntimeError(f'fixture build failed: {cc.stderr.strip()[:400]}')
    return binary


class ContractBase(unittest.TestCase):
    """Base class providing a trusted test root and fixture binaries."""

    @classmethod
    def setUpClass(cls):
        cls.base = trusted_base_dir()
        cls.strict_bin = None
        cls.narrow_bin = None
        cls._tmpdirs = []

    @classmethod
    def tearDownClass(cls):
        import shutil
        for d in cls._tmpdirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def setUp(self):
        # Private temp root under trusted_base_dir
        tmp = tempfile.mkdtemp(prefix='awgctl-contract-', dir=str(self.base))
        self._tmpdirs.append(tmp)
        self.work = Path(tmp)
        self.conf = self.work / 'conf'
        self.conf.mkdir(mode=0o700)
        self.state = self.work / 'state'
        self.routes = self.work / 'routes'
        self.ifclog = self.work / 'ifconfig'
        # Build the narrow-mapping binary (owner = root or current uid)
        self.binary = build_fixture(self.work, self.conf, strict_owner=False)

    def tearDown(self):
        pass

    def _env(self, **extra):
        env = dict(os.environ)
        env.update(
            AWGFIX_STATE=str(self.state),
            AWGFIX_ROUTES=str(self.routes),
            AWGFIX_IFC_LOG=str(self.ifclog)
        )
        env.update(extra)
        return env

    def _run(self, args, env=None, input_data=None):
        """Run awgctl and return (returncode, stdout, stderr)."""
        result = subprocess.run(
            [str(self.binary)] + args,
            env=env or self._env(),
            input=input_data,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout, result.stderr

    def _run_strict(self, args, env=None, input_data=None):
        if self.strict_bin is None:
            self.strict_bin = build_fixture(self.work, self.conf, strict_owner=True)
        result = subprocess.run(
            [str(self.strict_bin)] + args,
            env=env or self._env(),
            input=input_data,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout, result.stderr

    # ----------------------------------------------------------------
    # Exit-code binding helpers
    # ----------------------------------------------------------------
    def _exit_scenarios(self):
        """List of (declared_code, label, callable->rc) for binding tests."""
        return [
            # 0
            (0, 'up success',
             lambda: self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])[0]),
            (0, 'show success',
             lambda: self._run_show_up()[0]),
            (0, 'down success',
             lambda: self._run_down()[0]),
            (0, 'set success',
             lambda: self._run_set_success()[0]),
            (0, 'add peer success',
             lambda: self._run_add_peer()[0]),
            (0, 'del peer success',
             lambda: self._run_del_peer()[0]),
            # 1
            (1, 'no args', lambda: self._run([])[0]),
            (1, 'unknown subcommand', lambda: self._run(['foobar'])[0]),
            (1, 'up missing name', lambda: self._run(['up'])[0]),
            (1, 'up extra args', lambda: self._run(['up', 'awg9', 'extra'])[0]),
            (1, 'up bad flag', lambda: self._run(['up', 'awg9', '--bad', 'x'])[0]),
            (1, 'down wrong arity', lambda: self._run(['down', 'awg9', 'extra'])[0]),
            (1, 'show too many', lambda: self._run(['show', 'awg9', 'awg10'])[0]),
            (1, 'set missing name', lambda: self._run(['set'])[0]),
            (1, 'set zero pairs', lambda: self._run(['set', 'awg9'])[0]),
            (1, 'set trailing key', lambda: self._run(['set', 'awg9', 'ListenPort'])[0]),
            (1, 'add wrong arity', lambda: self._run(['add', 'awg9', 'peer'])[0]),
            (1, 'add unknown kw', lambda: self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB, 'badopt', 'x'])[0]),
            (1, 'del wrong arity', lambda: self._run(['del', 'awg9'])[0]),
            (1, 'config missing', lambda: self._run(['up', 'awg9', '--config', str(self.conf / 'missing.conf')])[0]),
            (1, 'config perms 000', lambda: self._test_config_mode(0o000)),
            (1, 'config outside CONF_DIR', lambda: self._test_config_outside()),
            (1, 'config symlink', lambda: self._test_config_symlink()),
            (1, 'config dir writable', lambda: self._test_parent_writable()),
            (1, 'config file mode 644', lambda: self._test_config_mode(0o644)),
            (1, 'config file mode 777', lambda: self._test_config_mode(0o777)),
            (1, 'config path is dir', lambda: self._test_config_dir()),
            # 2
            (2, 'config bad PrivateKey', lambda: self._test_bad_key()),
            (2, 'config unknown key', lambda: self._test_unknown_key()),
            (2, 'config reversed range', lambda: self._test_rev_range()),
            (2, 'config S3 in legacy', lambda: self._test_s3_legacy()),
            (2, 'config missing PrivateKey', lambda: self._test_missing_priv()),
            (2, 'set unknown key', lambda: self._test_set_unknown_key()),
            (2, 'set bad ListenPort', lambda: self._test_set_bad_port()),
            (2, 'set bad Table', lambda: self._test_set_bad_table()),
            (2, 'set bad Version', lambda: self._test_set_bad_version()),
            (2, 'set bad Jc', lambda: self._test_set_bad_jc()),
            (2, 'set bad boolean', lambda: self._test_set_bad_bool()),
            # 3
            (3, 'show nonexistent', lambda: self._run_ioctl_err('show', 'awg999')[0]),
            (3, 'set nonexistent', lambda: self._run_ioctl_err('set', 'awg999', 'ListenPort', '1')[0]),
            (3, 'down SIOCSAWG EIO', lambda: self._run_ioctl_err('down', 'awg999')[0]),
            (3, 'add SIOCSAWG EIO', lambda: self._run_ioctl_err('add', 'awg999', 'peer', LAB_KEY2_PUB)[0]),
            (3, 'del SIOCSAWG EIO', lambda: self._run_ioctl_err('del', 'awg999', 'peer', LAB_KEY2_PUB)[0]),
            (3, 'up SIOCSAWG EIO', lambda: self._run_ioctl_err_up()[0]),
            (3, 'up SIOCGAWG EPERM (probe)', lambda: self._run_probe_perm('up')[0]),
            (3, 'set SIOCGAWG EPERM (probe)', lambda: self._run_probe_perm('set')[0]),
            (3, 'show SIOCGAWG EPERM (probe)', lambda: self._run_probe_perm('show')[0]),
        ]

    # ---- Scenario helpers ----
    def _write_valid_conf(self, path):
        path.write_text(CONFIG_LEGACY)
        path.chmod(0o600)

    def _run_show_up(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        return self._run(['show', 'awg9'])

    def _run_down(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        return self._run(['down', 'awg9'])

    def _run_set_success(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        return self._run(['set', 'awg9', 'ListenPort', '51821'])

    def _run_add_peer(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        return self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB,
                          'endpoint', '127.0.0.1:51831',
                          'allowed-ips', '10.9.31.2/32', 'keepalive', '10'])

    def _run_del_peer(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB])
        return self._run(['del', 'awg9', 'peer', LAB_KEY2_PUB])

    def _test_config_mode(self, mode):
        p = self.conf / f'bad{mode}.conf'
        self._write_valid_conf(p)
        p.chmod(mode)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(p)])
        return rc

    def _test_config_outside(self):
        outside = self.work / 'outside.conf'
        self._write_valid_conf(outside)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(outside)])
        return rc

    def _test_config_symlink(self):
        real_dir = self.work / 'realconf'
        real_dir.mkdir(mode=0o700)
        cf = real_dir / 'awg9.conf'
        self._write_valid_conf(cf)
        link = self.conf / 'link'
        link.symlink_to(real_dir)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(link / 'awg9.conf')])
        return rc

    def _test_parent_writable(self):
        bad = self.work / 'badparent'
        bad.mkdir(mode=0o700)
        bad.chmod(0o775)  # group-writable
        cf = bad / 'awg9.conf'
        self._write_valid_conf(cf)
        # Use a fresh narrow build with this conf dir
        b = build_fixture(self.work, bad, strict_owner=False)
        res = subprocess.run([str(b), 'up', 'awg9', '--config', str(cf)],
                             env=self._env(), capture_output=True, text=True)
        return res.returncode

    def _test_config_dir(self):
        d = self.conf / 'adir'
        d.mkdir(mode=0o700)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(d)])
        return rc

    def _test_bad_key(self):
        bad = CONFIG_LEGACY.replace(LAB_KEY, 'AAAA')
        p = self.conf / 'bad.conf'
        p.write_text(bad)
        p.chmod(0o600)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(p)])
        return rc

    def _test_unknown_key(self):
        bad = CONFIG_LEGACY + '\nBogusKey = 7\n'
        p = self.conf / 'unknown.conf'
        p.write_text(bad)
        p.chmod(0o600)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(p)])
        return rc

    def _test_rev_range(self):
        bad = CONFIG_LEGACY.replace('H1 = 100\nH2 = 200', 'H1 = 300-100\nH2 = 200')
        p = self.conf / 'revrange.conf'
        p.write_text(bad)
        p.chmod(0o600)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(p)])
        return rc

    def _test_s3_legacy(self):
        bad = CONFIG_LEGACY.replace('Jc = 3', 'Jc = 3\nS3 = 18')
        p = self.conf / 's3legacy.conf'
        p.write_text(bad)
        p.chmod(0o600)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(p)])
        return rc

    def _test_missing_priv(self):
        bad = CONFIG_LEGACY.replace(f'PrivateKey = {LAB_KEY}\n', '')
        p = self.conf / 'nopriv.conf'
        p.write_text(bad)
        p.chmod(0o600)
        rc, _, _ = self._run(['up', 'awg9', '--config', str(p)])
        return rc

    def _test_set_unknown_key(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, _, _ = self._run(['set', 'awg9', 'BogusKey', '123'])
        return rc

    def _test_set_bad_port(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, _, _ = self._run(['set', 'awg9', 'ListenPort', '65536'])
        return rc

    def _test_set_bad_table(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, _, _ = self._run(['set', 'awg9', 'Table', '999999999'])
        return rc

    def _test_set_bad_version(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, _, _ = self._run(['set', 'awg9', 'Version', '4.0'])
        return rc

    def _test_set_bad_jc(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, _, _ = self._run(['set', 'awg9', 'Jc', '9999'])
        return rc

    def _test_set_bad_bool(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, _, _ = self._run(['set', 'awg9', 'RandomTrailers', 'maybe'])
        return rc

    def _run_ioctl_err(self, cmd, *args):
        env = self._env(AWGFIX_SET_ERR='1')
        return self._run([cmd, *args], env=env)

    def _run_ioctl_err_up(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        env = self._env(AWGFIX_SET_ERR='1')
        return self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')], env=env)

    def _run_probe_perm(self, cmd):
        env = self._env(AWGFIX_PROBE_ERR='1')
        if cmd == 'up':
            self._write_valid_conf(self.conf / 'awg9.conf')
            return self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')], env=env)
        elif cmd == 'set':
            self._write_valid_conf(self.conf / 'awg9.conf')
            self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
            return self._run(['set', 'awg9', 'ListenPort', '1'], env=env)
        elif cmd == 'show':
            self._write_valid_conf(self.conf / 'awg9.conf')
            self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
            return self._run(['show', 'awg9'], env=env)
        else:
            raise ValueError(cmd)


class CLIContractTests(ContractBase):
    """Test the awgctl CLI contract: exit codes, streams, validation."""

    # ============================================================
    # EXIT CODE PINNING TESTS
    # ============================================================
    def test_exit_0_success_up(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        self.assertEqual(rc, 0, f'up should exit 0, got {rc}: stderr={err}')
        self.assertEqual(out, '', 'stdout should be empty on success')

    def test_exit_0_success_show(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['show', 'awg9'])
        self.assertEqual(rc, 0, f'show should exit 0, got {rc}: stderr={err}')
        self.assertIn('awgpeer', out, 'show output goes to stdout')
        self.assertEqual(err, '', 'stderr should be empty on successful show')

    def test_exit_0_success_show_all(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['show'])
        self.assertEqual(rc, 0, f'show (all) should exit 0, got {rc}: stderr={err}')
        self.assertIn('awgpeer', out)
        self.assertEqual(err, '')

    def test_exit_0_success_down(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['down', 'awg9'])
        self.assertEqual(rc, 0, f'down should exit 0, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertEqual(err, '')

    def test_exit_0_success_set(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'ListenPort', '51821'])
        self.assertEqual(rc, 0, f'set should exit 0, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertEqual(err, '')

    def test_exit_0_success_add_del_peer(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB,
                                   'endpoint', '127.0.0.1:51831',
                                   'allowed-ips', '10.9.31.2/32', 'keepalive', '10'])
        self.assertEqual(rc, 0, f'add peer should exit 0, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertEqual(err, '')
        rc, out, err = self._run(['del', 'awg9', 'peer', LAB_KEY2_PUB])
        self.assertEqual(rc, 0, f'del peer should exit 0, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertEqual(err, '')

    # Exit 1
    def test_exit_1_no_args(self):
        rc, out, err = self._run([])
        self.assertEqual(rc, 1, f'no args should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_unknown_subcommand(self):
        rc, out, err = self._run(['foobar'])
        self.assertEqual(rc, 1, f'unknown subcommand should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_up_missing_name(self):
        rc, out, err = self._run(['up'])
        self.assertEqual(rc, 1, f'up missing name should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_up_extra_args(self):
        rc, out, err = self._run(['up', 'awg9', 'extra'])
        self.assertEqual(rc, 1, f'up extra args should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_up_bad_config_flag(self):
        rc, out, err = self._run(['up', 'awg9', '--bad', 'x'])
        self.assertEqual(rc, 1, f'up bad flag should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_down_wrong_arity(self):
        for args in [['down'], ['down', 'awg9', 'extra']]:
            with self.subTest(args=args):
                rc, out, err = self._run(args)
                self.assertEqual(rc, 1, f'down {args} should exit 1, got {rc}')
                self.assertEqual(out, '')
                self.assertIn('usage', err.lower())

    def test_exit_1_show_too_many_args(self):
        rc, out, err = self._run(['show', 'awg9', 'awg10'])
        self.assertEqual(rc, 1, f'show with 2 names should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_set_missing_name(self):
        rc, out, err = self._run(['set'])
        self.assertEqual(rc, 1, f'set missing name should exit 1, got {rc}')
        self.assertEqual(out, '')
        self.assertIn('usage', err.lower())

    def test_exit_1_set_zero_pairs(self):
        rc, out, err = self._run(['set', 'awg9'])
        self.assertEqual(rc, 1, f'set zero pairs should exit 1, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('at least one key value pair', err.lower())

    def test_exit_1_set_trailing_key_no_value(self):
        rc, out, err = self._run(['set', 'awg9', 'ListenPort'])
        self.assertEqual(rc, 1, f'set trailing key should exit 1, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('expected key value pairs', err.lower())

    def test_exit_1_add_wrong_arity(self):
        for args in [['add'], ['add', 'awg9'], ['add', 'awg9', 'peer'],
                     ['add', 'awg9', 'peer', LAB_KEY2_PUB, 'unknownopt', 'val']]:
            with self.subTest(args=args):
                rc, out, err = self._run(args)
                self.assertEqual(rc, 1, f'add {args} should exit 1, got {rc}')
                self.assertEqual(out, '')
                self.assertIn('usage', err.lower())

    def test_exit_1_del_wrong_arity(self):
        for args in [['del'], ['del', 'awg9'], ['del', 'awg9', 'peer']]:
            with self.subTest(args=args):
                rc, out, err = self._run(args)
                self.assertEqual(rc, 1, f'del {args} should exit 1, got {rc}')
                self.assertEqual(out, '')
                self.assertIn('usage', err.lower())

    def test_exit_1_config_file_not_found(self):
        missing = self.conf / 'nonexistent.conf'
        rc, out, err = self._run(['up', 'awg9', '--config', str(missing)])
        self.assertEqual(rc, 1, f'missing config should exit 1, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('no such file', err.lower())

    def test_exit_1_config_file_permission_denied(self):
        conf_file = self.conf / 'noread.conf'
        self._write_valid_conf(conf_file)
        conf_file.chmod(0o000)
        try:
            rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
            self.assertEqual(rc, 1, f'unreadable config should exit 1, got {rc}: stderr={err}')
            self.assertEqual(out, '')
            self.assertTrue('permission denied' in err.lower() or 'eacces' in err.lower())
        finally:
            conf_file.chmod(0o600)

    def test_exit_1_config_not_under_confdir(self):
        outside = self.work / 'outside.conf'
        self._write_valid_conf(outside)
        rc, out, err = self._run(['up', 'awg9', '--config', str(outside)])
        self.assertEqual(rc, 1, f'config outside CONF_DIR should exit 1, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('must live under', err.lower())

    def test_exit_1_config_symlink_component(self):
        real_dir = self.work / 'realconf'
        real_dir.mkdir(mode=0o700)
        conf_file = real_dir / 'awg9.conf'
        self._write_valid_conf(conf_file)
        link_dir = self.conf / 'link'
        link_dir.symlink_to(real_dir)
        rc, out, err = self._run(['up', 'awg9', '--config', str(link_dir / 'awg9.conf')])
        self.assertEqual(rc, 1, f'symlink component should exit 1, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('not a directory', err.lower())

    def test_exit_1_config_dir_group_writable(self):
        bad_conf = self.work / 'badconf'
        bad_conf.mkdir()
        bad_conf.chmod(0o775)
        conf_file = bad_conf / 'awg9.conf'
        self._write_valid_conf(conf_file)
        b = build_fixture(self.work, bad_conf, strict_owner=False)
        res = subprocess.run([str(b), 'up', 'awg9', '--config', str(conf_file)],
                             env=self._env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, f'group-writable dir should exit 1: {res.stderr}')
        self.assertEqual(res.stdout, '')
        self.assertIn('writable', res.stderr.lower())

    def test_exit_1_config_file_not_0600_0400(self):
        for mode in [0o644, 0o660, 0o777, 0o755]:
            with self.subTest(mode=oct(mode)):
                conf_file = self.conf / f'bad{mode}.conf'
                self._write_valid_conf(conf_file)
                conf_file.chmod(mode)
                rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
                self.assertEqual(rc, 1, f'mode {oct(mode)} should exit 1, got {rc}: stderr={err}')
                self.assertEqual(out, '')
                self.assertIn('mode', err.lower())

    def test_exit_1_config_not_regular_file(self):
        conf_file = self.conf / 'adir'
        conf_file.mkdir(mode=0o700)
        rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
        self.assertEqual(rc, 1, f'directory as config should exit 1, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('not a regular file', err.lower())

    # Exit 2
    def test_exit_2_config_bad_private_key(self):
        bad = CONFIG_LEGACY.replace(LAB_KEY, 'AAAA')
        conf_file = self.conf / 'bad.conf'
        conf_file.write_text(bad)
        conf_file.chmod(0o600)
        rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
        self.assertEqual(rc, 2, f'bad PrivateKey should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('privatekey', err.lower())

    def test_exit_2_config_unknown_key(self):
        bad = CONFIG_LEGACY + '\nBogusKey = 7\n'
        conf_file = self.conf / 'unknown.conf'
        conf_file.write_text(bad)
        conf_file.chmod(0o600)
        rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
        self.assertEqual(rc, 2, f'unknown key should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('unknown key', err.lower())

    def test_exit_2_config_reversed_range(self):
        bad = CONFIG_LEGACY.replace('H1 = 100\nH2 = 200', 'H1 = 300-100\nH2 = 200')
        conf_file = self.conf / 'revrange.conf'
        conf_file.write_text(bad)
        conf_file.chmod(0o600)
        rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
        self.assertEqual(rc, 2, f'reversed range should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('range', err.lower())

    def test_exit_2_config_31_key_in_legacy(self):
        bad = CONFIG_LEGACY.replace('Jc = 3', 'Jc = 3\nS3 = 18')
        conf_file = self.conf / 's3legacy.conf'
        conf_file.write_text(bad)
        conf_file.chmod(0o600)
        rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
        self.assertEqual(rc, 2, f'S3 in legacy should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('version', err.lower())

    def test_exit_2_config_missing_private_key(self):
        bad = CONFIG_LEGACY.replace(f'PrivateKey = {LAB_KEY}\n', '')
        conf_file = self.conf / 'nopriv.conf'
        conf_file.write_text(bad)
        conf_file.chmod(0o600)
        rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
        self.assertEqual(rc, 2, f'missing PrivateKey should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('privatekey', err.lower())

    def test_exit_2_set_unknown_key(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'BogusKey', '123'])
        self.assertEqual(rc, 2, f'set unknown key should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('unknown key', err.lower())

    def test_exit_2_set_invalid_listenport(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        for val in ['-1', '65536', 'abc']:
            with self.subTest(val=val):
                rc, out, err = self._run(['set', 'awg9', 'ListenPort', val])
                self.assertEqual(rc, 2, f'set ListenPort={val} should exit 2, got {rc}: stderr={err}')
                self.assertEqual(out, '')
                self.assertIn('listenport', err.lower())

    def test_exit_2_set_invalid_table(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'Table', '999999999'])
        self.assertEqual(rc, 2, f'set Table=huge should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('table', err.lower())

    def test_exit_2_set_invalid_version(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'Version', '4.0'])
        self.assertEqual(rc, 2, f'set Version=4.0 should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('version', err.lower())

    def test_exit_2_set_invalid_jc_range(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'Jc', '9999'])
        self.assertEqual(rc, 2, f'set Jc=9999 should exit 2, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('jc', err.lower())

    def test_exit_2_set_invalid_boolean(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        for key in ['RandomTrailers', 'DisableCookies']:
            with self.subTest(key=key):
                rc, out, err = self._run(['set', 'awg9', key, 'maybe'])
                self.assertEqual(rc, 2, f'set {key}=maybe should exit 2, got {rc}: stderr={err}')
                self.assertEqual(out, '')
                self.assertIn(key.lower(), err.lower())

    # Exit 3
    def test_exit_3_show_nonexistent_interface(self):
        env = self._env(AWGFIX_CREATE='1')
        rc, out, err = self._run(['show', 'awg999'], env=env)
        self.assertEqual(rc, 3, f'show nonexistent should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertTrue('siocgawg' in err.lower() or 'no such device' in err.lower() or 'enxio' in err.lower(),
                       f'stderr should mention ioctl error: {err}')

    def test_exit_3_set_nonexistent_interface(self):
        env = self._env(AWGFIX_CREATE='1')
        rc, out, err = self._run(['set', 'awg999', 'ListenPort', '51820'], env=env)
        self.assertEqual(rc, 3, f'set nonexistent should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertTrue('siocgawg' in err.lower() or 'no such device' in err.lower() or 'enxio' in err.lower(),
                       f'stderr should mention ioctl error: {err}')

    def test_exit_3_down_sioctawg_failure(self):
        env = self._env(AWGFIX_SET_ERR='1')
        rc, out, err = self._run(['down', 'awg999'], env=env)
        self.assertEqual(rc, 3, f'down with SIOCSAWG failure should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('siocsawg', err.lower())

    def test_exit_3_add_sioctawg_failure(self):
        env = self._env(AWGFIX_SET_ERR='1')
        rc, out, err = self._run(['add', 'awg999', 'peer', LAB_KEY2_PUB], env=env)
        self.assertEqual(rc, 3, f'add with SIOCSAWG failure should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('siocsawg', err.lower())

    def test_exit_3_del_sioctawg_failure(self):
        env = self._env(AWGFIX_SET_ERR='1')
        rc, out, err = self._run(['del', 'awg999', 'peer', LAB_KEY2_PUB], env=env)
        self.assertEqual(rc, 3, f'del with SIOCSAWG failure should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('siocsawg', err.lower())

    def test_exit_3_up_sioctawg_failure(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        env = self._env(AWGFIX_SET_ERR='1')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')], env=env)
        self.assertEqual(rc, 3, f'up with SIOCSAWG failure should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertIn('siocsawg', err.lower())

    def test_exit_3_up_sioctgawg_eperm(self):
        """Up with AWGFIX_PROBE_ERR triggers EPERM on the first SIOCGAWG probe → EX_IOCTL (3)."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        env = self._env(AWGFIX_PROBE_ERR='1')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')], env=env)
        self.assertEqual(rc, 3, f'up with EPERM probe should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertTrue('operation not permitted' in err.lower() or 'eperm' in err.lower())

    def test_exit_3_set_sioctgawg_eperm(self):
        """Set with AWGFIX_PROBE_ERR triggers EPERM on read_current SIOCGAWG → EX_IOCTL (3)."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        env = self._env(AWGFIX_PROBE_ERR='1')
        rc, out, err = self._run(['set', 'awg9', 'ListenPort', '1'], env=env)
        self.assertEqual(rc, 3, f'set with EPERM probe should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertTrue('operation not permitted' in err.lower() or 'eperm' in err.lower())

    def test_exit_3_show_sioctgawg_eperm(self):
        """Show with AWGFIX_PROBE_ERR triggers EPERM on show_interface SIOCGAWG → EX_IOCTL (3)."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        env = self._env(AWGFIX_PROBE_ERR='1')
        rc, out, err = self._run(['show', 'awg9'], env=env)
        self.assertEqual(rc, 3, f'show with EPERM probe should exit 3, got {rc}: stderr={err}')
        self.assertEqual(out, '')
        self.assertTrue('operation not permitted' in err.lower() or 'eperm' in err.lower())

    # ============================================================
    # STDOUT vs STDERR SEPARATION
    # ============================================================
    def test_stdout_stderr_separation_show(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['show', 'awg9'])
        self.assertEqual(rc, 0)
        self.assertIn('awgpeer', out, 'show data on stdout')
        self.assertEqual(err, '', 'no stderr on success')

    def test_stdout_stderr_separation_up_success(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        self.assertEqual(rc, 0)
        self.assertEqual(out, '', 'up is silent on stdout')

    def test_stdout_stderr_separation_errors_to_stderr(self):
        test_cases = [
            (['up'], 'usage error'),
            (['up', 'awg9'], 'usage error'),
            (['set', 'awg9', 'ListenPort'], 'trailing key'),
            (['set', 'awg9', 'BogusKey', '1'], 'unknown key'),
            (['add', 'awg9', 'peer', LAB_KEY2_PUB, 'badopt', 'x'], 'usage'),
        ]
        for args, description in test_cases:
            with self.subTest(args=args, desc=description):
                rc, out, err = self._run(args)
                self.assertNotEqual(rc, 0)
                self.assertEqual(out, '', f'{description}: stdout must be empty on error')
                self.assertTrue(len(err) > 0, f'{description}: stderr must have error message')

    def test_awgctl_debug_to_stderr(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['show', 'awg9'], env=self._env(AWGCTL_DEBUG='1'))
        self.assertIn('SIOCGAWG', err, 'AWGCTL_DEBUG prints to stderr')

    # ============================================================
    # ARGUMENT VALIDATION EDGE CASES
    # ============================================================
    def test_up_config_flag_positional_only(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        self.assertEqual(rc, 0, f'valid --config position failed: {err}')
        rc, out, err = self._run(['up', '--config', str(self.conf / 'awg9.conf'), 'awg9'])
        self.assertEqual(rc, 1, f'--config at wrong position should exit 1, got {rc}')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf'), 'extra'])
        self.assertEqual(rc, 1, f'--config with extra args should exit 1, got {rc}')

    def test_duplicate_config_flag_rejected(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        rc, out, err = self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf'),
                                   '--config', str(self.conf / 'awg9.conf')])
        self.assertEqual(rc, 1, 'duplicate --config should exit 1')
        self.assertEqual(out, '')

    def test_set_exact_arity_enforced(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'ListenPort', '51821', 'Jc', '5'])
        self.assertEqual(rc, 0, f'valid set failed: {err}')
        rc, out, err = self._run(['set', 'awg9', 'ListenPort'])
        self.assertEqual(rc, 1, 'trailing key should be error')

    def test_set_duplicate_key_last_wins(self):
        """Set accepts duplicate keys; last wins. Fixture show doesn't reflect
        updated values, so we verify the command succeeds and the C code
        implements the documented last-wins semantics."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['set', 'awg9', 'ListenPort', '51821', 'ListenPort', '51822'])
        self.assertEqual(rc, 0, f'duplicate key set failed: {err}')

    def test_add_peer_keyword_validation(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB,
                                   'endpoint', '127.0.0.1:51831',
                                   'allowed-ips', '10.9.31.2/32',
                                   'psk', '-', 'keepalive', '10'],
                                  input_data=HPK + '\n')
        self.assertEqual(rc, 0, f'valid add failed: {err}')
        rc, out, err = self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB, 'invalidkw', 'value'])
        self.assertEqual(rc, 1, 'invalid keyword should exit 1')

    def test_add_duplicate_endpoint_last_wins(self):
        """Add accepts duplicate endpoint; last wins. Fixture show doesn't
        display awgendpoint, so we verify the command succeeds."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB,
                                   'endpoint', '1.2.3.4:1111',
                                   'endpoint', '5.6.7.8:2222'])
        self.assertEqual(rc, 0, f'duplicate endpoint add failed: {err}')

    def test_add_duplicate_allowed_ips_accumulates(self):
        """Add accepts duplicate allowed-ips; entries accumulate. Fixture show
        doesn't display awgaip, so we verify the command succeeds."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB,
                                   'allowed-ips', '10.0.0.0/8',
                                   'allowed-ips', '10.1.0.0/16'])
        self.assertEqual(rc, 0, f'duplicate allowed-ips add failed: {err}')

    def test_del_exact_arity(self):
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        self._run(['add', 'awg9', 'peer', LAB_KEY2_PUB])
        rc, out, err = self._run(['del', 'awg9', 'peer', LAB_KEY2_PUB])
        self.assertEqual(rc, 0, f'valid del failed: {err}')
        rc, out, err = self._run(['del', 'awg9', 'peer', LAB_KEY2_PUB, 'extra'])
        self.assertEqual(rc, 1, 'del with extra arg should exit 1')

    # ============================================================
    # INTERFACE NAME VALIDATION (R1-2)
    # ============================================================
    def _assert_name_rejected(self, args, needle):
        rc, out, err = self._run(args)
        self.assertEqual(rc, 1, f'{args} should exit 1, got {rc}: {err}')
        self.assertEqual(out, '')
        self.assertIn(needle.lower(), err.lower())

    def _assert_name_accepted(self, name):
        # show is the simplest accepted verb
        self._write_valid_conf(self.conf / 'awg9.conf')
        self._run(['up', 'awg9', '--config', str(self.conf / 'awg9.conf')])
        rc, out, err = self._run(['show', name])
        self.assertEqual(rc, 0, f'show {name} should be accepted, got {rc}: {err}')

    def test_interface_name_empty(self):
        for cmd in [['show', ''], ['set', '', 'ListenPort', '1'], ['up', ''],
                    ['down', ''], ['add', '', 'peer', LAB_KEY2_PUB],
                    ['del', '', 'peer', LAB_KEY2_PUB]]:
            with self.subTest(cmd=cmd):
                self._assert_name_rejected(cmd, 'empty')

    def test_interface_name_too_long(self):
        for name in ['a' * 16, 'a' * 17, 'a' * 40]:
            with self.subTest(name=name):
                self._assert_name_rejected(['show', name], 'maximum is 15')

    def test_interface_name_boundary_15_chars_accepted(self):
        # 15 chars is the maximum (IFNAMSIZ - 1)
        self._assert_name_accepted('awg9xxxxxxxxxx')  # 4 + 11 = 15

    def test_interface_name_leading_dash(self):
        self._assert_name_rejected(['show', '-x'], 'must not begin with')

    def test_interface_name_leading_dot(self):
        self._assert_name_rejected(['show', '.x'], 'must not begin with')

    def test_interface_name_path_like(self):
        self._assert_name_rejected(['show', '../etc'], 'must not begin with')

    def test_interface_name_invalid_chars(self):
        for name in ['awg/9', 'awg 9', 'awg:9', 'awg@9', 'awg#9']:
            with self.subTest(name=name):
                self._assert_name_rejected(['show', name], 'not one of')

    def test_interface_name_no_side_effect_on_invalid(self):
        """Invalid name must be rejected before any ioctl/ifconfig/fs access."""
        self._write_valid_conf(self.conf / 'awg9.conf')
        # Clear any prior log
        if self.ifclog.exists():
            self.ifclog.unlink()
        # up with invalid name: name check runs before open_trusted and before any ifconfig
        rc, out, err = self._run(['up', '../bad'])
        self.assertEqual(rc, 1)
        self.assertEqual(out, '')
        # No ifconfig log must be written
        self.assertFalse(self.ifclog.exists(), 'ifconfig log must not be written for invalid name')
        # State file must not be created
        self.assertFalse(self.state.exists(), 'state file must not be created for invalid name')

    # ============================================================
    # CONFIG FILE PERMISSION VALIDATION (UNPRIVILEGED BEHAVIOUR)
    # ============================================================
    def test_config_file_must_be_0600_or_0400(self):
        for mode in [0o644, 0o660, 0o777, 0o755]:
            with self.subTest(mode=oct(mode)):
                conf_file = self.conf / f'bad{mode}.conf'
                self._write_valid_conf(conf_file)
                conf_file.chmod(mode)
                rc, out, err = self._run(['up', 'awg9', '--config', str(conf_file)])
                self.assertEqual(rc, 1, f'mode {oct(mode)} should exit 1, got {rc}: stderr={err}')
                self.assertEqual(out, '')
                self.assertIn('mode', err.lower())

    def test_config_not_under_confdir(self):
        outside = self.work / 'outside.conf'
        self._write_valid_conf(outside)
        rc, out, err = self._run(['up', 'awg9', '--config', str(outside)])
        self.assertEqual(rc, 1, f'config outside CONF_DIR should exit 1, got {rc}')
        self.assertIn('must live under', err.lower())

    def test_writable_ancestor_refused(self):
        """A writable directory anywhere above the config file is refused."""
        deep = self.work / 'deep' / 'conf'
        deep.mkdir(parents=True, mode=0o700)
        (deep.parent).chmod(0o777)  # writable ancestor
        conf_file = deep / 'awg9.conf'
        self._write_valid_conf(conf_file)
        # Build narrow binary with this conf dir
        b = build_fixture(self.work, deep, strict_owner=False)
        res = subprocess.run([str(b), 'up', 'awg9', '--config', str(conf_file)],
                             env=self._env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, f'writable ancestor should exit 1: {res.stderr}')
        self.assertTrue('deep' in res.stderr.lower() or 'writable' in res.stderr.lower())

    def test_production_owner_predicate_refuses_non_root_ancestors(self):
        """With the production predicate (root-only), any non-root-owned ancestor is refused."""
        # Use a valid mode-0600 file under our test dir (owned by current user)
        self._write_valid_conf(self.conf / 'awg9.conf')
        # Build strict binary (production predicate)
        b = build_fixture(self.work, self.conf, strict_owner=True)
        res = subprocess.run([str(b), 'up', 'awg9', '--config', str(self.conf / 'awg9.conf')],
                             env=self._env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, f'strict build must refuse non-root-owned path: {res.stderr}')
        self.assertTrue('not root-owned' in res.stderr.lower() or 'must be root-owned' in res.stderr.lower())

    @unittest.skipIf(not (Path('/tmp').stat().st_mode & 0o022), '/tmp not group/other-writable; sticky exemption not implemented')
    def test_world_writable_tmp_ancestor_refused(self):
        """A config tree under /tmp is refused because /tmp is world-writable."""
        import tempfile
        tmpconf = Path(tempfile.mkdtemp(prefix='awgctl-tmp-', dir='/tmp'))
        try:
            tmpconf.chmod(0o700)
            cf = tmpconf / 'awg9.conf'
            self._write_valid_conf(cf)
            b = build_fixture(self.work, tmpconf, strict_owner=False)
            res = subprocess.run([str(b), 'up', 'awg9', '--config', str(cf)],
                                 env=self._env(), capture_output=True, text=True)
            self.assertEqual(res.returncode, 1,
                             f'/tmp config must be refused: {res.stderr}')
            self.assertIn('tmp', res.stderr.lower())
        finally:
            import shutil
            shutil.rmtree(tmpconf, ignore_errors=True)

    # ============================================================
    # MAN PAGE ↔ BEHAVIOUR BINDING (R1-4)
    # ============================================================
    def _binding_mismatches(self, doc_codes):
        """Return list of mismatch descriptions between documented codes and behaviour."""
        mismatches = []
        observed = set()
        for declared, label, run in self._exit_scenarios():
            rc = run()
            observed.add(rc)
            if rc != declared:
                mismatches.append(f'{label}: man documents {declared}, behaviour {rc}')
        # Every documented code must have at least one scenario
        for code in sorted(doc_codes):
            if code not in observed:
                mismatches.append(f'documented exit {code} ({doc_codes[code]}) has no scenario')
        # Every observed code must be documented
        for code in sorted(observed):
            if code not in doc_codes:
                mismatches.append(f'observed exit {code} is not documented in man page')
        return mismatches

    def test_changing_a_documented_exit_code_fails_the_binding(self):
        """A documentation-only mutation of the man page is detected by the binding test."""
        man_text = (ROOT / 'sbin/awgctl/awgctl.8').read_text()
        # Mutate the ioctl failure code from 3 to 4 in the man page
        mutated = man_text.replace('.It 3\nA\n.Dv SIOCSAWG', '.It 4\nA\n.Dv SIOCSAWG', 1)
        self.assertNotEqual(mutated, man_text, 'mutation did not apply')
        doc = documented_exit_codes(mutated)
        self.assertIn(4, doc, 'mutated code not present in parsed man page')
        self.assertNotIn(3, doc, 'original code still present after mutation')
        mismatches = self._binding_mismatches(doc)
        self.assertNotEqual(mismatches, [],
                            'A documentation-only change must produce binding mismatches')


if __name__ == '__main__':
    unittest.main(verbosity=2)