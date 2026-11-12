"""D4/D5 — buffer-level audits for awgctl's secret sinks.

Two claims in the E2 remediation report could not be checked by the ioctl
fixture:

D5/G8  F15 "passes" on the pre-fix source because load_key() rejects the
       short-decode key while parsing, so serialise_config()'s new b64_pton
       checks are never reached. This module calls serialise_config()
       directly (tools/lab/awgctl-buffer-audit.c includes the production
       source) with a short decode for every secret/public field.
D4     The show size-probe buffer must be wiped with its LIVE capacity
       before realloc(3) frees the old key-bearing copy, and the parser's
       raw line buffer must be wiped, not just freed — on the success path
       and on the errx() refusal path (through the registered slot). Both
       are observed through recording wrappers around
       calloc/realloc/free/explicit_bzero.

Every group is mutation-controlled: the same harness is rebuilt from a copy
of the production source with exactly one fix removed, and the corresponding
assertion must then FAIL. A test that passes on both trees proves nothing,
so the mutation runs are part of this module.
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


def build(work, source, name):
    """Compile the harness against `source` (production or a mutant)."""
    binary = work / name
    cmd = ['cc', '-DAWGCTL_LINUX', '-Isrc', '-Isbin/awgctl',
           f'-DAWGCTL_SOURCE="{source}"', '-o', str(binary),
           str(HARNESS), str(STUBS), '-lutil']
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f'cc failed for {name}:\n{proc.stderr}')
    return binary


def run(binary, *args, stdin=None, env=None):
    return subprocess.run([str(binary), *args], input=stdin,
                          capture_output=True, text=True,
                          env=env or {'PATH': '/usr/bin:/bin'})


def events(stdout):
    """Parse the harness's EV lines into (kind, a, b, n) tuples."""
    out = []
    for line in stdout.splitlines():
        parts = line.split()
        if not parts or parts[0] != 'EV':
            continue
        kind = parts[1]
        if kind == 'calloc':
            out.append(('calloc', parts[2], None, int(parts[3])))
        elif kind == 'realloc':
            out.append(('realloc', parts[2], parts[3], int(parts[4])))
        elif kind == 'bzero':
            out.append(('bzero', parts[2], None, int(parts[3])))
        elif kind == 'free':
            out.append(('free', parts[2], None, 0))
    return out


def line_max_of(stdout):
    match = re.search(r'line_max=(\d+)', stdout)
    if match is None:
        raise AssertionError(f'harness printed no INFO line:\n{stdout}')
    return int(match.group(1))


def realloc_wipes(evs):
    """For every realloc of a live block: (old_ptr, live_capacity,
    wiped_size_or_None), the wipe being the immediately preceding event on
    that same pointer."""
    caps = {}
    rows = []
    prev = None
    for ev in evs:
        kind, a, b, n = ev
        if kind == 'calloc':
            caps[a] = n
        elif kind == 'realloc':
            if a in caps:
                wiped = (prev[3] if prev is not None and prev[0] == 'bzero'
                         and prev[1] == a else None)
                rows.append((a, caps[a], wiped))
            caps.pop(a, None)
            caps[b] = n
        elif kind == 'free':
            caps.pop(a, None)
        prev = ev
    return rows


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tmp.name)
        cls.bin = build(cls.work, SRC, 'audit-prod')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class SerialiseChecks(Harness):
    """D5/G8: serialise_config() refuses a short decode in every key field."""

    def assert_refused(self, case, field):
        proc = run(self.bin, case)
        self.assertEqual(proc.returncode, 2,
                         f'{case}: rc {proc.returncode}, want 2\n{proc.stderr}')
        self.assertIn(f'{field} (key): invalid base64', proc.stderr)

    def test_private_key(self):
        self.assert_refused('serialise-bad-private', 'PrivateKey')

    def test_header_protection_key(self):
        self.assert_refused('serialise-bad-hpk', 'HeaderProtectionKey')

    def test_public_key(self):
        self.assert_refused('serialise-bad-pubkey', 'PublicKey')

    def test_preshared_key(self):
        self.assert_refused('serialise-bad-psk', 'PresharedKey')

    def test_valid_keys_are_accepted(self):
        """Control: the same harness reaches the checks and accepts a valid
        cfg, so the refusals above are not a harness artefact."""
        proc = run(self.bin, 'serialise-ok')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('RESULT accepted private=11', proc.stdout)


class SerialiseMutation(Harness):
    """D5: removing the serialise_config b64 guards must flip the cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pattern = re.compile(
            r'\t*if \(b64_pton\([^;]*?\)\s*!=\s*\(int\)sizeof\([^;]*?\)\)\s*\n'
            r'\t*erx\(EX_CONFIG, "[^;]*?invalid base64"\);')
        text = SRC.read_text()
        mutant_text, n = pattern.subn('\t\t(void)0;', text)
        if n != 4:
            raise AssertionError(
                f'serialise guards found {n}x, expected 4 — re-anchor the '
                'mutation against the current source')
        mutant = cls.work / 'no-serialise-checks.c'
        mutant.write_text(mutant_text)
        cls.mutant = build(cls.work, mutant, 'audit-no-serialise-checks')

    def test_mutant_accepts_rejected_keys(self):
        for case in ('serialise-bad-private', 'serialise-bad-hpk',
                     'serialise-bad-pubkey', 'serialise-bad-psk'):
            with self.subTest(case=case):
                prod = run(self.bin, case)
                mut = run(self.mutant, case)
                self.assertEqual(prod.returncode, 2)
                self.assertEqual(mut.returncode, 0,
                                 f'{case}: the mutant still refuses — the '
                                 f'test does not discriminate\n{mut.stderr}')


class ShowBuffer(Harness):
    """D4/G3: the show GET buffer is wiped with its live capacity before
    realloc(3) frees it, and before the final free."""

    def run_show(self, binary=None):
        with tempfile.TemporaryDirectory() as d:
            env = {'PATH': '/usr/bin:/bin',
                   'AWGFIX_STATE': f'{d}/absent.state'}
            proc = run(binary or self.bin, 'show-wipe', env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc, events(proc.stdout)

    def test_grow_wipes_the_old_block_first(self):
        _proc, evs = self.run_show()
        rows = realloc_wipes(evs)
        self.assertTrue(rows, 'the probe never grew — no realloc observed')
        for old, capacity, wiped in rows:
            self.assertEqual(wiped, capacity,
                             f'block {old} was reallocated away after a wipe '
                             f'of {wiped} bytes; its live capacity was '
                             f'{capacity}')

    def test_final_free_is_preceded_by_a_full_wipe(self):
        _proc, evs = self.run_show()
        caps = {}
        last_bzero = None
        final_free = None
        for kind, a, b, n in evs:
            if kind == 'calloc':
                caps[a] = n
            elif kind == 'realloc':
                caps.pop(a, None)
                caps[b] = n
            elif kind == 'bzero':
                last_bzero = (a, n)
            elif kind == 'free':
                final_free = (a, caps.get(a), last_bzero)
                caps.pop(a, None)
        self.assertIsNotNone(final_free, 'the GET buffer was never freed')
        ptr, capacity, wipe = final_free
        self.assertEqual(wipe, (ptr, capacity),
                         f'free of {ptr} (capacity {capacity}) was not '
                         f'preceded by a full wipe; last bzero was {wipe}')


class ShowMutation(Harness):
    """D4: delete the pre-realloc wipe; the ShowBuffer assertions must fail."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        text = SRC.read_text()
        start = text.index('\t\t/* D4: wipe the key-bearing block before '
                           'realloc(3) frees it.')
        end = text.index('d.awgd_interface = realloc(', start)
        mutant = cls.work / 'no-show-wipe.c'
        mutant.write_text(text[:start] + text[end:])
        cls.mutant = build(cls.work, mutant, 'audit-no-show-wipe')

    def test_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            env = {'PATH': '/usr/bin:/bin',
                   'AWGFIX_STATE': f'{d}/absent.state'}
            proc = run(self.mutant, 'show-wipe', env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = realloc_wipes(events(proc.stdout))
        self.assertTrue(rows)
        self.assertTrue(any(wiped != capacity for _, capacity, wiped in rows),
                        'the mutant still wipes the old block with its live '
                        'capacity: the assertion is not discriminating')


class ParserLineBuffer(Harness):
    """D4: parse_config's raw line buffer is wiped, not just freed."""

    def parse(self, text, binary=None):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / 'awg9.conf'
            conf.write_text(text)
            conf.chmod(0o600)
            proc = run(binary or self.bin, 'parse-wipe', str(conf))
        return proc, events(proc.stdout)

    def test_line_buffer_wiped_before_free(self):
        proc, evs = self.parse(CONF)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line_max = line_max_of(proc.stdout)
        calloc = [e for e in evs if e[0] == 'calloc' and e[3] == line_max]
        self.assertTrue(calloc, f'no {line_max}-byte line buffer allocation')
        ptr = calloc[0][1]
        sequence = [(k, n) for k, a, _b, n in evs if a == ptr]
        self.assertEqual([s[0] for s in sequence], ['calloc', 'bzero', 'free'],
                         f'unexpected lifecycle for the line buffer: {sequence}')
        self.assertEqual(sequence[1][1], line_max,
                         'the line buffer was wiped with less than its full '
                         'capacity')

    def test_refused_parse_still_wipes_the_line(self):
        """A NUL refusal exits through errx(); the registered slot must still
        wipe the raw line (the atexit cleanup), which the harness prints from
        a handler that runs after it."""
        proc, evs = self.parse(CONF.replace('Address', 'Addr\0ess', 1))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('NUL byte in config', proc.stderr)
        line_max = line_max_of(proc.stdout)
        calloc = [e for e in evs if e[0] == 'calloc' and e[3] == line_max]
        self.assertTrue(calloc, 'the line buffer was never allocated')
        ptr = calloc[0][1]
        wipes = [e for e in evs if e[0] == 'bzero' and e[1] == ptr]
        self.assertTrue(wipes,
                        'the refused parse left the raw line buffer unwiped')
        self.assertEqual(wipes[-1][3], line_max)


class ParserMutation(Harness):
    """D4: replace the parser's wipe with a plain free; the test must fail."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        text = SRC.read_text()
        old = '\tsecret_free(line, AWG_CONFIG_LINE_MAX);'
        if text.count(old) != 1:
            raise AssertionError('parser wipe site not unique — re-anchor')
        # "The wipe is missing" as a plausible alternative implementation:
        # free the line and clear the registered slot, so the atexit cleanup
        # does not double-free it (the registration stays in the mutant).
        mutant = cls.work / 'no-line-wipe.c'
        mutant.write_text(text.replace(
            old, '\tfree(line);\n\tg_secret.sb_buf = NULL;\n'
                 '\tg_secret.sb_len = 0;'))
        cls.mutant = build(cls.work, mutant, 'audit-no-line-wipe')

    def test_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / 'awg9.conf'
            conf.write_text(CONF)
            conf.chmod(0o600)
            proc = run(self.mutant, 'parse-wipe', str(conf))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line_max = line_max_of(proc.stdout)
        calloc = [e for e in events(proc.stdout)
                  if e[0] == 'calloc' and e[3] == line_max]
        ptr = calloc[0][1]
        kinds = [k for k, a, _b, _n in events(proc.stdout) if a == ptr]
        self.assertNotIn('bzero', kinds,
                         'the mutant still wipes the line buffer: the '
                         'assertion is not discriminating')


if __name__ == '__main__':
    unittest.main()
