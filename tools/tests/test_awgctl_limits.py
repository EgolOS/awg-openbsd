"""D3 — the size limits awgctl enforces are real bounds, not hints.

The review found two claims that were not true of the code:

* awgctl.c set `len = 4096` while the pointer was NULL and then called
  getline(3); that is an allocation *hint*, and a 65 KiB line was accepted.
  The replacements are explicit bounded readers (AWG_CONFIG_LINE_MAX,
  AWG_SECRET_LINE_MAX) that refuse a longer line before it is parsed.
* the SIOCGAWG size-probe loop cap (AWG_GET_PROBE_MAX = 64) was real but had
  no focused test. The fixture stub can now grow the reported size on every
  GET (AWGFIX_GET_GROW / _STOP), which lets this module test the cap at its
  exact boundary: 64 probes converge, the 65th iteration is refused.

Threshold pairs are used throughout: the last accepted input and the first
refused one, so a test that merely rejects "something long" cannot pass.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'sbin/awgctl/awgctl.c'
HARNESS = ROOT / 'tools/lab/awgctl-buffer-audit.c'
STUBS = ROOT / 'tools/lab/fixture-stubs.c'
BASE_KEY = 'YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA='  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic test key
PEER_KEY = 'ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA='  # scan:allow-fixture[sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05] synthetic peer key

CONF = (
    '[Interface]\n'
    f'PrivateKey = {BASE_KEY}\n'
    'Address = 10.9.19.1/24\n'
    '[Peer]\n'
    f'PublicKey = {PEER_KEY}\n'
    'Endpoint = 10.9.9.9:51820\n'
    'AllowedIPs = 10.0.0.0/8\n'
)


def cc(work, name, source, instrumented):
    binary = work / name
    cmd = ['cc', '-DAWGCTL_LINUX', '-Isrc', '-Isbin/awgctl', '-o', str(binary)]
    if instrumented:
        cmd += [f'-DAWGCTL_SOURCE="{source}"', str(HARNESS)]
    else:
        cmd += [str(source)]
    cmd += [str(STUBS), '-lutil']
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f'cc failed for {name}:\n{proc.stderr}')
    return binary


def run(binary, *args, stdin=None, env=None):
    return subprocess.run([str(binary), *args], input=stdin,
                          capture_output=True, text=True,
                          env=env or {'PATH': '/usr/bin:/bin'})


class Limits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tmp.name)
        cls.harness = cc(cls.work, 'limits-harness', SRC, True)
        cls.fixture = cc(cls.work, 'limits-fixture', SRC, False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # ---------------------------------------------------------------- G6 line
    def parse_line(self, length):
        """A comment line of `length` bytes (newline included) in a valid
        config, parsed through the harness."""
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / 'awg9.conf'
            conf.write_text('#' + 'A' * (length - 2) + '\n' + CONF)
            conf.chmod(0o600)
            return run(self.harness, 'parse-wipe', str(conf))

    def test_config_line_boundary(self):
        # The bound is documented as "at most cap - 1 bytes including the
        # newline"; 8191 is accepted, 8192 is refused before parsing.
        accepted = self.parse_line(8191)
        self.assertEqual(accepted.returncode, 0,
                         f'the accepted side of the boundary failed:\n'
                         f'{accepted.stderr}')
        refused = self.parse_line(8192)
        self.assertEqual(refused.returncode, 2,
                         f'rc {refused.returncode}, want EX_CONFIG (2)')
        self.assertIn('line longer than 8191 bytes', refused.stderr)

    def test_over_long_line_is_not_read_into_the_parser(self):
        proc = self.parse_line(200000)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('line longer than 8191 bytes', proc.stderr)
        # The refusal happens while reading, so the parse never reports a
        # configured key (no partial application).
        self.assertNotIn('RESULT parse', proc.stdout)

    def test_nul_still_refused_after_the_bound(self):
        """The bounded reader must keep the R7/N12 NUL refusal — the bound
        is not a replacement for the truncation check."""
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / 'awg9.conf'
            conf.write_text(CONF.replace('Address', 'Addr\0ess', 1))
            conf.chmod(0o600)
            proc = run(self.harness, 'parse-wipe', str(conf))
        self.assertEqual(proc.returncode, 2)
        self.assertIn('NUL byte in config', proc.stderr)

    # ------------------------------------------------------------ G6 secret
    def test_secret_line_boundary(self):
        # AWG_SECRET_LINE_MAX = 128, newline included: 127 bytes is the
        # longest accepted "key from stdin" line.
        ok = run(self.harness, 'secret-stdin', stdin='A' * 126 + '\n')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn('RESULT secret len=126', ok.stdout)
        refused = run(self.harness, 'secret-stdin', stdin='A' * 127 + '\n')
        self.assertEqual(refused.returncode, 2,
                         f'rc {refused.returncode}, want EX_CONFIG (2)')
        self.assertIn('secret line is longer than 127 bytes', refused.stderr)

    # ------------------------------------------------------- G6 probe loop
    def show_with_growth(self, stop):
        """`show` against a stub that keeps reporting a larger size for
        `stop` GETs (AWGFIX_GET_GROW=1), then converges."""
        with tempfile.TemporaryDirectory() as d:
            env = {'PATH': '/usr/bin:/bin',
                   'AWGFIX_STATE': f'{d}/absent.state',
                   'AWGFIX_LOG': f'{d}/ioctl.log',
                   'AWGFIX_GET_GROW': '1',
                   'AWGFIX_GET_GROW_STOP': str(stop)}
            proc = run(self.fixture, 'show', 'awg9', env=env)
            log = Path(f'{d}/ioctl.log')
            gets = (len(re.findall(r'^GET', log.read_text(), re.M))
                    if log.exists() else 0)
        return proc, gets

    def test_probe_cap_boundary(self):
        # The fixture converges on the call after the last growth and needs
        # one more call to observe that the size stopped changing, so
        # stop=62 converges inside 64 probes and stop=63 needs the 65th.
        ok, gets = self.show_with_growth(62)
        self.assertEqual(ok.returncode, 0,
                         f'the converging side of the cap failed rc '
                         f'{ok.returncode} after {gets} GETs\n{ok.stderr}')
        self.assertEqual(gets, 64, 'the converging run used a different '
                         'number of probes than the cap allows')

        refused, gets = self.show_with_growth(63)
        self.assertEqual(refused.returncode, 3,
                         f'rc {refused.returncode}, want EX_IOCTL (3)')
        self.assertIn('did not converge in 64 iterations', refused.stderr)
        self.assertEqual(gets, 64, 'the refused run exceeded the probe cap')
        self.assertNotIn('awg9:', refused.stdout,
                         'the refused probe printed a state it never read')


if __name__ == '__main__':
    unittest.main()
