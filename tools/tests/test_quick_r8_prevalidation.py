"""R8/R4-08: every field is parsed and resolved before the interface is
mutated, and a failure leaves the interface, MTU and routing state
byte-identical. One negative per field (Address, AllowedIPs, Endpoint,
MTU, DNS, NUL bytes, truncated last line), each run against an interface
that pre-exists and one the run creates. The mock ifconfig/route/netstat
keep state in files, so the test compares the exact pre-state (ifconfig
output + route table) with the post-state, byte for byte.
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

# Stateful mock ifconfig/route/netstat/getent: the interface keeps its MTU and
# address in $IFSTATE and the kernel route table lives in $ROUTES; every
# call is recorded in $CALLS. The first ifconfig answers "not present"
# (marker file) unless $SEEN is pre-created — that selects created-vs-existing.
# getent answers every name with $EP_ADDR (R9: the endpoint resolves in the
# Address family before the interface is created).
MOCKS = '''
getent() { printf '%s %s\\n' "$EP_ADDR" "$2"; return 0; }
ifconfig() {
 if [[ ! -e "$SEEN" ]]; then : > "$SEEN"; return 1; fi
 local mtu addr
 mtu="$(sed -n '1p' "$IFSTATE" | cut -d= -f2)"
 addr="$(sed -n '2p' "$IFSTATE" | cut -d= -f2)"
 case "${2:-}" in
 create|destroy) printf 'mtu=%s\\naddr=%s\\n' "$MTU0" "$ADDR0" > "$IFSTATE" ;;
 mtu) printf 'mtu=%s\\naddr=%s\\n' "${3:-$mtu}" "$addr" > "$IFSTATE" ;;
 inet|inet6) printf 'mtu=%s\\naddr=%s\\n' "$mtu" "${3:-$addr}" > "$IFSTATE" ;;
 esac
 printf 'IFC\\n' >> "$CALLS"
 local a
 for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done
 return 0
}
netstat() { cat "$ROUTES"; }
route() {
 case "$1" in
 -n) printf 'interface: em0\\nmtu: 1500\\n' ;;
 *) printf '%s\\n' "$*" >> "$ROUTES" ;;
 esac
 return 0
}
'''


class QuickR8Prevalidation(unittest.TestCase):
    def run_up(self, text, preexists):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            config = work / 'awg9.conf'
            config.write_text(text)
            config.chmod(0o600)
            calls = work / 'CALLS'
            calls.write_text('')
            ifstate = work / 'IFSTATE'
            ifstate.write_text('mtu=1420\naddr=fd00::1\n')
            routes = work / 'ROUTES'
            routes.write_text('default 192.168.1.1 em0\n')  # scan:allow-fixture[sha1:90aa44756bd2f4fc2390f903a6f25f43216b0790]
            seen = work / 'seen'
            if preexists:
                seen.touch()
            source = (ROOT / 'sbin/awg-quick').read_text().split('\nif [[ $# -eq 1 &&')[0]
            driver = work / 'driver.bash'
            driver.write_text(source + STAT_ADAPTER + 'parse_options "$1"\n'
                              + MOCKS + 'cmd_up\n')
            env = dict(os.environ, MOCK_OWNER='0', CALLS=str(calls),
                       SEEN=str(seen), IFSTATE=str(ifstate),
                       ROUTES=str(routes), MTU0='1420', ADDR0='fd00::1',
                       EP_ADDR='203.0.113.7')
            r = subprocess.run(['bash', str(driver), str(config)], cwd=work,
                               env=env, capture_output=True, text=True)
            return (r, ifstate.read_bytes(), routes.read_bytes())

    CASES = {
        'Address octet': ('[Interface]\nPrivateKey = dummy\n'
                          'Address = 10.0.0.1/24, 10.0.0.999/24\n'
                          '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                          'AllowedIPs = 0.0.0.0/0\n', 'Address'),
        'Address v4 prefix': ('[Interface]\nPrivateKey = dummy\n'
                              'Address = 10.0.0.1/33\n'
                              '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                              'AllowedIPs = 0.0.0.0/0\n', 'Address'),
        'AllowedIPs prefix': ('[Interface]\nPrivateKey = dummy\n'
                              'Address = 10.0.0.1/24\n'
                              '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                              'AllowedIPs = 10.0.0.0/33\n', 'AllowedIPs'),
        'Endpoint port': ('[Interface]\nPrivateKey = dummy\n'
                          'Address = 10.0.0.1/24\n'
                          '[Peer]\nPublicKey = pub\nEndpoint = example.com:70000\n'
                          'AllowedIPs = 0.0.0.0/0\n', 'endpoint'),
        'MTU range': ('[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
                      'MTU = 100\n'
                      '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                      'AllowedIPs = 0.0.0.0/0\n', 'MTU'),
        'DNS octet': ('[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
                      'DNS = 1.2.3.999\n'
                      '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                      'AllowedIPs = 0.0.0.0/0\n', 'DNS'),
        'NUL bytes': ('[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
                      'MTU = 1420\x009\n'
                      '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                      'AllowedIPs = 0.0.0.0/0\n', 'NUL'),
        'Truncated last line': ('[Interface]\nPrivateKey = dummy\n'
                                'Address = 10.0.0.1/24\n'
                                '[Peer]\nPublicKey = pub\nEndpoint = example.com:51820\n'
                                'AllowedIPs = 0.0.0.0/0', 'truncated'),
    }

    def _check(self, name):
        text, field = self.CASES[name]
        for preexists in (False, True):
            with self.subTest(field=name, interface='pre-existing' if preexists else 'created'):
                r, ifstate, routes = self.run_up(text, preexists)
                self.assertNotEqual(r.returncode, 0, 'exit must be non-zero')
                self.assertIn(field, r.stderr,
                              'error message must name the field: %r' % r.stderr)
                self.assertEqual(ifstate, b'mtu=1420\naddr=fd00::1\n',
                                 'ifconfig state (MTU + address) must be byte-identical')
                self.assertEqual(routes, b'default 192.168.1.1 em0\n', # scan:allow-fixture[sha1:90aa44756bd2f4fc2390f903a6f25f43216b0790]
                                 'route table must be byte-identical')

    def test_address_octet(self):
        self._check('Address octet')

    def test_address_v4_prefix(self):
        self._check('Address v4 prefix')

    def test_allowedips_prefix(self):
        self._check('AllowedIPs prefix')

    def test_endpoint_port(self):
        self._check('Endpoint port')

    def test_mtu_range(self):
        self._check('MTU range')

    def test_dns_octet(self):
        self._check('DNS octet')

    def test_nul_bytes(self):
        self._check('NUL bytes')

    def test_truncated_last_line(self):
        self._check('Truncated last line')


if __name__ == '__main__':
    unittest.main()
