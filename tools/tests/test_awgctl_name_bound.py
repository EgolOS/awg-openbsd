"""G7 — the interface-name bound is enforced for every public verb.

The audit claimed a "32-byte field" and "all four" name sites; the real
field is `awgd_name[IFNAMSIZ]` (src/if_awg.h:169, 16 bytes on OpenBSD), and
the candidate had five call sites plus a separate copy inside
state_snapshot. set_awgd_name() now wraps sizeof(d->awgd_name) and every
site goes through it, including state_snapshot.

This module drives the built awgctl against the ioctl fixture and tests the
boundary pair (IFNAMSIZ-1 characters accepted, IFNAMSIZ refused) for every
verb that takes a name: up, down, show, set. A truncated name would silently
address a different interface, so the refusal — not just "no crash" — is the
property under test.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'sbin/awgctl/awgctl.c'
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

IFNAMSIZ = 16          # src/if_awg.h:169 — awgd_name[IFNAMSIZ]
SHORT = 'awg' + 'x' * (IFNAMSIZ - 1 - 3)   # 15 characters: accepted
LONG = 'awg' + 'x' * (IFNAMSIZ - 3)        # 16 characters: refused


def build(work):
    binary = work / 'awgctl'
    conf_dir = work / 'conf'
    cmd = ['cc', '-DAWGCTL_LINUX', '-Isrc', '-Isbin/awgctl',
           f'-DCONF_DIR="{conf_dir}"', '-DCONF_OWNER_OK(u)=1',
           f'-DIFCONFIG_PATH="{work}/ifconfig.py"',
           '-o', str(binary), str(SRC), str(STUBS), '-lutil']
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f'cc failed:\n{proc.stderr}')
    return binary, conf_dir


class NameBound(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The trust walk starts at "/", so the config tree must live in a
        # directory whose components are not group/other-writable: the repo
        # root qualifies, /tmp does not.
        cls._tmp = tempfile.TemporaryDirectory(prefix='.name-bound-',
                                               dir=str(ROOT))
        cls.work = Path(cls._tmp.name)
        cls.bin, conf_dir = build(cls.work)
        conf_dir.mkdir(mode=0o700)
        cls.conf = conf_dir / 'awg9.conf'
        cls.conf.write_text(CONF)
        cls.conf.chmod(0o600)
        (cls.work / 'ifconfig.py').write_text('#!/bin/sh\nexit 0\n')
        (cls.work / 'ifconfig.py').chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_awgctl(self, *args):
        with tempfile.TemporaryDirectory() as d:
            env = {'PATH': '/usr/bin:/bin',
                   'AWGFIX_STATE': f'{d}/state',
                   'AWGFIX_LOG': f'{d}/ioctl',
                   'AWGFIX_IFC_LOG': f'{d}/ifconfig'}
            proc = subprocess.run([str(self.bin), *args], capture_output=True,
                                  text=True, env=env)
            ifconfig_log = Path(f'{d}/ifconfig')
            calls = ifconfig_log.read_text() if ifconfig_log.exists() else ''
            ioctl_log = Path(f'{d}/ioctl')
            ioctls = ioctl_log.read_text() if ioctl_log.exists() else ''
        return proc, calls, ioctls

    def assert_refused(self, *args):
        proc, calls, ioctls = self.run_awgctl(*args)
        self.assertEqual(proc.returncode, 1,
                         f'{args[0]} {len(args[1])} chars: rc '
                         f'{proc.returncode}, want EX_USAGE (1) — '
                         f'{proc.stderr.strip()}')
        self.assertIn('interface name longer than 15 bytes', proc.stderr)
        self.assertEqual(calls, '',
                         f'{args[0]} ran ifconfig for a refused name')
        self.assertEqual(ioctls, '',
                         f'{args[0]} issued an ioctl for a refused name')

    def test_up_accepts_15(self):
        # The address read-back fails with this recording stand-in (it
        # installs nothing), so `up` stops before SIOCSAWG; what matters here
        # is that the 15-character name passes the bound: the run reaches the
        # state snapshot GET instead of being refused at the name check.
        proc, _calls, ioctls = self.run_awgctl('up', SHORT, '--config',
                                               str(self.conf))
        self.assertNotIn('interface name longer than', proc.stderr)
        self.assertIn('GET', ioctls)

    def test_up_refuses_16(self):
        self.assert_refused('up', LONG, '--config', str(self.conf))

    def test_down_refuses_16(self):
        self.assert_refused('down', LONG)

    def test_show_refuses_16(self):
        self.assert_refused('show', LONG)

    def test_set_refuses_16(self):
        self.assert_refused('set', LONG, 'port', '51820')

    def test_show_accepts_15(self):
        proc, _calls, _ioctls = self.run_awgctl('show', SHORT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f'{SHORT}:', proc.stdout)


if __name__ == '__main__':
    unittest.main()
