"""Exercise production parser/functions; no network commands or privileged hooks."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class QuickParser(unittest.TestCase):
    def run_parser(self, text, suffix, owner='0'):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / 'expanded-file').touch()
            config = work / 'fixture.conf'
            config.write_text(text)
            config.chmod(0o600)
            # The definitions are taken verbatim from production, not a second parser.
            source = (ROOT / 'sbin/awg-quick').read_text().split('\nif [[ $# -eq 1 &&')[0]
            # BSD stat adapter: Linux tests cover logic, VM tests cover actual ownership.
            source += '''
stat() {
 case "$2" in
 %Lp) printf '600\\n' ;;
 %u) printf '%s\\n' "$MOCK_OWNER" ;;
 *) return 1 ;;
 esac
}
parse_options "$1"
'''
            driver = work / 'driver.bash'
            driver.write_text(source + suffix)
            env = dict(os.environ, MOCK_OWNER=owner)
            return subprocess.run(['bash', str(driver), str(config)], cwd=work,
                                  env=env, capture_output=True, text=True)

    def test_lists_do_not_expand_filenames(self):
        r = self.run_parser('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nAddress = *, 10.0.0.1/24\nDNS = *.invalid, 1.1.1.1\n[Peer]\nPublicKey = dummy\nAllowedIPs = *\n',  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                            'printf "ADDR:%s\\n" "${ADDRESSES[@]}"; printf "DNS:%s\\n" "${DNS_SEARCH[@]}"; REAL_INTERFACE=awg9; ifconfig() { printf "ARG:%s\\n" "$@"; }; build_config_args; apply_config\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('ADDR:*\n', r.stdout)
        self.assertIn('ARG:*\n', r.stdout)
        self.assertNotIn('expanded-file', r.stdout)

    def test_user_owned_config_is_refused(self):
        r = self.run_parser('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\n', 'echo PARSED\n', owner='1000')  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('PARSED', r.stdout)

    def test_all_secret_keys_use_stdin_in_argument_order(self):
        r = self.run_parser('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\n', '''  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
PRIVATE_KEY=private-fixture; HPK=header-fixture; PEER_COUNT=1
PEER_PUBKEYS=(public-fixture); PEER_PSKS=(psk-fixture)
REAL_INTERFACE=awg9
ifconfig() { printf 'ARG:%s\\n' "$@"; while IFS= read -r key; do printf 'STDIN:%s\\n' "$key"; done; }
build_config_args
apply_config
''')
        self.assertEqual(r.returncode, 0, r.stderr)
        arguments = [line for line in r.stdout.splitlines() if line.startswith('ARG:')]
        self.assertNotIn('fixture', '\n'.join(line for line in arguments if 'public-fixture' not in line))
        self.assertEqual([line for line in r.stdout.splitlines() if line.startswith('STDIN:')],
                         ['STDIN:private-fixture', 'STDIN:header-fixture', 'STDIN:psk-fixture'])

    def test_decimal_leading_zero_range(self):
        r = self.run_parser('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nH1 = 08-09\n', 'echo PARSED\n')  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('PARSED', r.stdout)


if __name__ == '__main__':
    unittest.main()
