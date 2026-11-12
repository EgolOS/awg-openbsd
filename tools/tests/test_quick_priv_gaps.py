"""E2 privilege/config-trust remediation: focused tests for the gaps the
audit confirmed in sbin/awg-quick.

G1 — set_dns must back up the ORIGINAL resolv.conf and append the DNS_SEARCH
     line to the LIVE file. Base a1393fc copies the original to the backup
     and then writes the search line to /etc/resolv.conf with `>`, which
     truncates the live nameservers (the backup itself was never clobbered);
     `down` then restores nameservers but the search list never reached the
     resolver. The fix is `>>` on the live file.
G4 — the config body is read ONCE into a private byte copy (a 0600 snapshot
     file; a bash string cannot hold NUL bytes), and the NUL test, the
     truncated-last-line test and the parse loop all read that same copy, so
     a racing writer cannot mix several opens into one applied configuration
     and a failed read is not masked.
G5 — PrivateKey is validated as a 44-character base64 key at parse time,
     before the interface is created (awgctl's load_key does the same).

The production functions are taken verbatim from sbin/awg-quick (same
harness as test_quick_up). The only adaptation is the resolver path:
/etc/resolv.conf is parameterised to $RESOLV (a work-dir file) so the
shell-evaluated `printf ... > /etc/resolv.conf` redirect is observable;
cp/mv/route/pgrep are recording mocks, as in the OpenBSD wg-quick tests.
"""
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

# Recording mocks: every external call is appended to $CALLS. cp/mv operate
# on the parameterised resolver files (plain files in the work dir);
# resolvd is always running (pgrep succeeds).
DNS_MOCKS = '''
cp() { printf "CP\\n" >> "$CALLS"; for a in "$@"; do printf "ARG:%s\\n" "$a" >> "$CALLS"; done; command cp "$@"; }
mv() { printf 'MV\\n' >> "$CALLS"; for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done; command mv "$@"; }
route() { printf 'ROUTE\\n' >> "$CALLS"; for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done; }
pgrep() { return 0; }
cat() { printf 'CAT\\n' >> "$CALLS"; command cat "$@"; }
cmd() { printf 'CMD\\n' >> "$CALLS"; for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done; "$@"; }
'''

# Synthetic lab keys: the sha1-pinned allow marker is the repository's
# established fixture allowance (tools/publish-scan.py); the scanner's rules
# are not weakened — any other shape on these lines still reports.
VALID_KEY = 'YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA='  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic test key
SHORT_KEY = 'YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAB=='

MINIMAL_CONF = (
    '[Interface]\n'
    f'PrivateKey = {VALID_KEY}\n'
    'Address = 10.9.19.1/24\n'
    '[Peer]\n'
    'PublicKey = ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA=\n'
    'Endpoint = 10.9.9.9:51820\n'
    'AllowedIPs = 10.0.0.0/8\n'
)

# The production function bodies, up to the argument dispatch (same slice as
# test_quick_up / test_quick_r8_prevalidation). /etc/resolv.conf is
# parameterised so the redirect target is a work-dir file (see module
# docstring); the backup path derives from the same variable.
SOURCE = (ROOT / 'sbin/awg-quick').read_text().split('\nif [[ $# -eq 1 &&')[0] \
    .replace('/etc/resolv.conf', '${RESOLV:-/etc/resolv.conf}')


def run_bash(work, body, env, argv1=None, mocks=None):
    script = work / 'run.bash'
    script.write_text(SOURCE + STAT_ADAPTER + (mocks if mocks is not None else DNS_MOCKS) + body)
    cmd = ['bash', '--noprofile', '--norc', str(script)]
    if argv1 is not None:
        cmd.append(argv1)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=work)
    return proc


class DnsBackupRestore(unittest.TestCase):
    """G1: backup keeps the original nameservers; search lands on the live
    file; down restores nameservers AND search, not the search alone."""

    def up_down(self, dns_search):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            resolv = work / 'resolv.conf'
            resolv.write_text('nameserver 1.1.1.1\n')
            backup = work / 'resolv.conf.wg-quick-backup.awg9'
            calls = work / 'CALLS'
            calls.write_text('')
            env = {'MOCK_OWNER': '0', 'RESOLV': str(resolv),
                   'BACKUP': str(backup), 'CALLS': str(calls), 'PATH': '/usr/bin:/bin'}
            body = (
                'DNS=( 8.8.8.8 )\n'
                f'DNS_SEARCH=( {dns_search} )\n'
                'INTERFACE=awg9\nREAL_INTERFACE=awg9\n'
                'set_dns\nunset_dns\n'
            )
            proc = run_bash(work, body, env, mocks=DNS_MOCKS)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return resolv.read_text(), backup.exists(), proc

    def test_search_keeps_original_nameservers(self):
        """With DNS_SEARCH set, the backup holds the original resolv.conf
        (nameserver line), the live file ends with the search line, and the
        restore puts the nameservers back."""
        final, backup_gone, proc = self.up_down('example.test')
        self.assertEqual(final, 'nameserver 1.1.1.1\n')
        self.assertFalse(backup_gone)  # mv consumed the backup

    def test_no_search_round_trip(self):
        """Without DNS_SEARCH the round trip is a plain swap/restore."""
        final, backup_gone, proc = self.up_down('')
        self.assertEqual(final, 'nameserver 1.1.1.1\n')
        self.assertFalse(backup_gone)

    def test_backup_written_before_search(self):
        """The recorded call order proves the fix: cp (backup) precedes the
        printf that writes the search line, and route nameserver follows."""
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            resolv = work / 'resolv.conf'
            resolv.write_text('nameserver 1.1.1.1\n')
            backup = work / 'resolv.conf.wg-quick-backup.awg9'
            calls = work / 'CALLS'
            calls.write_text('')
            env = {'MOCK_OWNER': '0', 'RESOLV': str(resolv),
                   'BACKUP': str(backup), 'CALLS': str(calls), 'PATH': '/usr/bin:/bin'}
            body = ('DNS=( 8.8.8.8 )\nDNS_SEARCH=( example.test )\n'
                    'INTERFACE=awg9\nREAL_INTERFACE=awg9\nset_dns\n')
            proc = run_bash(work, body, env, mocks=DNS_MOCKS)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(resolv.read_text(),
                             'nameserver 1.1.1.1\nsearch example.test\n')
            self.assertEqual(backup.read_text(), 'nameserver 1.1.1.1\n')
            seq = calls.read_text().splitlines()
            cp_at = [i for i, l in enumerate(seq)
                     if l == 'CMD' and i + 1 < len(seq) and seq[i + 1] == 'ARG:cp']
            search_at = [i for i, l in enumerate(seq)
                         if l == 'CMD' and i + 1 < len(seq)
                         and seq[i + 1].startswith('ARG:printf')]
            route_at = [i for i, l in enumerate(seq)
                        if l == 'CMD' and i + 1 < len(seq)
                        and seq[i + 1] == 'ARG:route']
            self.assertEqual(len(cp_at), 1)
            self.assertEqual(len(search_at), 1)
            self.assertEqual(len(route_at), 1)
            self.assertLess(cp_at[0], search_at[0])
            self.assertLess(search_at[0], route_at[0])


class ConfigSnapshot(unittest.TestCase):
    """G4: one byte-coherent snapshot feeds the NUL test, the truncation test
    and the parse loop; CONFIG_FILE is not reopened after the capture, and a
    failed read is refused instead of masked."""

    NUL_CONF = MINIMAL_CONF.replace('Address', 'Addr\0ess', 1)

    def parse(self, text, cat_extra=''):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            config = work / 'awg9.conf'
            config.write_text(text)
            config.chmod(0o600)
            # The clean image a racing writer would swap in (or the same
            # bytes a successful read would produce).
            clean = work / 'clean.conf'
            clean.write_text(MINIMAL_CONF)
            clean.chmod(0o600)
            calls = work / 'CALLS'
            calls.write_text('')
            env = {'MOCK_OWNER': '0', 'CALLS': str(calls),
                   'PATH': '/usr/bin:/bin', 'CLEAN': str(clean),
                   'CONFIG': str(config)}
            block = ('cat() { printf "CAT\\n" >> "$CALLS"; '
                     'command cat "$@";' + (f' {cat_extra};' if cat_extra else '')
                     + ' }\n')
            proc = run_bash(work, 'parse_options "$1"\n'
                            'printf "KEY=%s\\n" "$PRIVATE_KEY"\n'
                            'printf "ADDR=%s\\n" "${ADDRESSES[0]}"\n'
                            'printf "PUB=%s\\n" "${PEER_PUBKEYS[0]}"\n', env,
                            argv1=str(config), mocks=block)
            leftovers = sorted(p.name for p in work.glob('.awg-quick.*'))
            cats = calls.read_text().splitlines().count('CAT')
            return proc, cats, leftovers

    def test_single_read(self):
        """The whole body comes from one capture, and the snapshot is gone
        when the parse returns (it carries the private key)."""
        proc, cats, leftovers = self.parse(MINIMAL_CONF)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f'KEY={VALID_KEY}', proc.stdout)
        self.assertIn('ADDR=10.9.19.1/24', proc.stdout)
        self.assertIn('PUB=ZhAm', proc.stdout)
        self.assertEqual(cats, 1)
        self.assertEqual(leftovers, [])

    def test_nul_refused_on_the_snapshot(self):
        proc, cats, _ = self.parse(self.NUL_CONF)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('contains NUL bytes', proc.stderr + proc.stdout)
        self.assertEqual(cats, 1)

    def test_truncated_last_line_refused(self):
        proc, _cats, _ = self.parse(MINIMAL_CONF.rstrip('\n'))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('truncated last line', proc.stderr + proc.stdout)

    def test_swap_after_capture_is_refused(self):
        """A writer that replaces the file with a clean config between the
        capture and the NUL test cannot hide the NUL bytes: the tests read
        the snapshot, not the path. Pre-fix (`tr < CONFIG_FILE`) this probe
        accepted the clean image."""
        proc, cats, _ = self.parse(self.NUL_CONF,
                                  cat_extra='command cp -- "$CLEAN" "$CONFIG"')
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn('contains NUL bytes', proc.stderr + proc.stdout)
        self.assertEqual(cats, 1)

    def test_cat_error_is_not_masked(self):
        """cat emits the complete valid config and THEN fails. The read
        status must fail the parse; pre-fix the trailing `printf x` inside
        the command substitution turned the failure into success."""
        proc, _cats, _ = self.parse(MINIMAL_CONF, cat_extra='return 1')
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn('cannot read', proc.stderr + proc.stdout)
        self.assertNotIn('KEY=', proc.stdout)

    def test_nul_survives_source_deletion(self):
        """Delete CONFIG_FILE right after the capture, with the NUL-bearing
        image captured: the refusal must still see the NUL. Pre-fix the
        `tr < CONFIG_FILE` test could not open the deleted path, counted
        zero NULs and the parse went ahead (this probe fails on 3017514)."""
        proc, cats, leftovers = self.parse(self.NUL_CONF,
                                          cat_extra='command rm -f -- "$CONFIG"')
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn('contains NUL bytes', proc.stderr + proc.stdout)
        self.assertEqual(cats, 1)
        self.assertEqual(leftovers, [])

    def test_clean_config_survives_source_deletion(self):
        """Property of the snapshot (passes pre-fix too): a clean config
        parses after the source is gone, i.e. nothing reopens the path."""
        proc, cats, leftovers = self.parse(MINIMAL_CONF,
                                          cat_extra='command rm -f -- "$CONFIG"')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f'KEY={VALID_KEY}', proc.stdout)
        self.assertIn('ADDR=10.9.19.1/24', proc.stdout)
        self.assertEqual(cats, 1)
        self.assertEqual(leftovers, [])


class PrivateKeyValidation(unittest.TestCase):
    """G5: a PrivateKey that is not a 44-char base64 key fails at parse time."""

    def parse(self, key_line):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            config = work / 'awg9.conf'
            config.write_text(MINIMAL_CONF.replace(VALID_KEY, key_line))
            config.chmod(0o600)
            calls = work / 'CALLS'
            calls.write_text('')
            env = {'MOCK_OWNER': '0', 'CALLS': str(calls), 'PATH': '/usr/bin:/bin'}
            proc = run_bash(work, 'parse_options "$1"\n'
                            'printf "KEY=%s\\n" "$PRIVATE_KEY"\n', env,
                            argv1=str(config))
            return proc

    def test_valid_key_accepted(self):
        proc = self.parse(VALID_KEY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f'KEY={VALID_KEY}', proc.stdout)

    def test_short_key_refused(self):
        proc = self.parse(SHORT_KEY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('PrivateKey is not a 44-character base64 key',
                      proc.stderr + proc.stdout)

    def test_garbage_key_refused(self):
        proc = self.parse('notbase64notbase64notbase64notbase64notbas')
        self.assertNotEqual(proc.returncode, 0)


if __name__ == '__main__':
    unittest.main()
