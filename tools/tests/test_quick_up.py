"""R4-08: cmd_up must parse, resolve and serialise the whole configuration
before it mutates the interface, so an invalid address, peer endpoint,
allowed-IP or truncated config leaves the interface and MTU untouched.

The production functions are taken verbatim from sbin/awg-quick (same harness
as test_quick_parser); ifconfig/route are mocked and every call is recorded,
so the test observes the mutation order, not a reimplementation of it.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

# BSD stat adapter (Linux CI runs the same logic; the VM runs the real stat).
STAT_ADAPTER = '''
stat() {
 case "$2" in
 %Lp) printf '600\\n' ;;
 %u) printf '%s\\n' "$MOCK_OWNER" ;;
 *) return 1 ;;
 esac
}
'''

# Mock ifconfig/route/netstat/getent: the first ifconfig (get_real_interface)
# reports "not present" (marker file), every later call succeeds and is
# appended to $CALLS, one argument per line, groups separated by a blank line.
# getent answers every name with $EP_ADDR so the resolution is hermetic.
MOCKS = '''
getent() { printf '%s %s\\n' "$EP_ADDR" "$2"; return 0; }
ifconfig() {
 if [[ ! -e "$SEEN" ]]; then : > "$SEEN"; return 1; fi
 printf 'IFC\\n' >> "$CALLS"
 local a
 for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done
 return 0
}
netstat() { printf 'ROUTE\\n' >> "$CALLS"; return 0; }
route() {
 printf 'ROUTE\\n' >> "$CALLS"
 printf 'interface: em0\\nmtu: 1500\\n'
 return 0
}
'''


class QuickUpOrder(unittest.TestCase):
    def run_up(self, text, extra=''):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            config = work / 'awg9.conf'
            config.write_text(text)
            config.chmod(0o600)
            calls = work / 'CALLS'
            calls.write_text('')
            source = (ROOT / 'sbin/awg-quick').read_text().split('\nif [[ $# -eq 1 &&')[0]
            driver = work / 'driver.bash'
            driver.write_text(source + STAT_ADAPTER + 'parse_options "$1"\n' + MOCKS + extra
                              + 'cmd_up\n')
            env = dict(os.environ, MOCK_OWNER='0', CALLS=str(calls), SEEN=str(work / 'seen'),
                       EP_ADDR='203.0.113.7')
            r = subprocess.run(['bash', str(driver), str(config)], cwd=work,
                               env=env, capture_output=True, text=True)
            groups = []
            for chunk in calls.read_text().split('IFC\n'):
                args = [ln[4:] for ln in chunk.splitlines() if ln.startswith('ARG:')]
                if args:
                    groups.append(args)
            return r, groups

    VALID = ('[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
             'MTU = 1420\n[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
             'AllowedIPs = 0.0.0.0/0\n')

    def test_valid_config_order(self):
        r, groups = self.run_up(self.VALID)
        self.assertEqual(r.returncode, 0, r.stderr)
        # Order of the mutations: create -> config (endpoint + allowed-IPs
        # serialised in one call) -> addresses -> mtu -> up.
        def idx(prefix):
            for i, g in enumerate(groups):
                if g[:len(prefix)] == prefix:
                    return i
            self.fail('no ifconfig call %r in %r' % (prefix, groups))
            return -1
        created = idx(['awg9', 'create'])
        config = idx(['awg9', 'awgversion'])
        addr = idx(['awg9', 'inet', '10.0.0.1/24'])
        mtu = idx(['awg9', 'mtu', '1420'])
        up = idx(['awg9', 'up'])
        self.assertLess(created, config)
        self.assertLess(config, addr)
        self.assertLess(addr, mtu)
        self.assertLess(mtu, up)
        # The endpoint and allowed-IP are in the single config mutation.
        # R9: the endpoint is the resolved address, not the config's name.
        joined = ' '.join(groups[config])
        self.assertIn('awgendpoint 203.0.113.7 51820', joined)
        self.assertIn('awgaip 0.0.0.0/0', joined)

    def test_invalid_second_address_leaves_interface_and_mtu(self):
        r, groups = self.run_up(
            '[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24, 10.0.0.1/256\n'
            '[Peer]\nPublicKey = pub\nAllowedIPs = 0.0.0.0/0\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('invalid prefix', r.stderr)
        self.assertEqual(groups, [], 'no ifconfig mutation before validation')

    def test_bad_peer_endpoint_leaves_interface_and_mtu(self):
        r, groups = self.run_up(
            '[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
            '[Peer]\nPublicKey = pub\nEndpoint = example.com\nAllowedIPs = 0.0.0.0/0\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not host:port', r.stderr)
        self.assertEqual(groups, [])

    def test_bad_allowed_ip_leaves_interface_and_mtu(self):
        r, groups = self.run_up(
            '[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
            '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
            'AllowedIPs = 10.0.0.0/256\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('invalid prefix', r.stderr)
        self.assertEqual(groups, [])

    def test_truncated_config_leaves_interface_and_mtu(self):
        r, groups = self.run_up('[Interface]\nAddress = 10.0.0.1/24\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('PrivateKey not set', r.stderr)
        self.assertEqual(groups, [])

    def test_operational_failure_after_create_destroys_interface(self):
        # The endpoint is valid, the config mutation fails: the trap must
        # destroy the created interface (restore), not leave a half-set one.
        r, groups = self.run_up(
            self.VALID,
            'apply_config() { cmd ifconfig "$REAL_INTERFACE" awgpeer broken; return 1; }\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(groups[0][:2], ['awg9', 'create'])
        self.assertEqual(groups[1][:2], ['awg9', 'awgpeer'])
        self.assertEqual(groups[2][:2], ['awg9', 'destroy'])
        self.assertNotIn(['awg9', 'mtu'], groups, 'MTU not applied after the failure')


if __name__ == '__main__':
    unittest.main()
