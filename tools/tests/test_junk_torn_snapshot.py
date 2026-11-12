"""AWG-020 junk-triple snapshot regression (E2-KERNEL-A).

Host-side regression for the junk packet length bound. The production
awg_send_junk() is EXTRACTED VERBATIM from src/if_awg.c with a regex (the
same technique test_hp_vector.py uses for awg_hp_init), so the harness stays
in sync with the kernel source automatically: any edit to the function body
changes what this test compiles. No kernel headers are needed; the function
only touches sc_awg_jc/jmin/jmax, so the softc stand-in declares just those
three fields, and awg_peer_get_endpoint/awg_send_buf are stubs.
arc4random_uniform and arc4random_buf are deterministic stubs (seeded LCG) so
the two builds are bit-comparable; the kernel uses the same arc4random(3)
API. This is userspace arithmetic over the real function text, not native
OpenBSD runtime coverage.

Torn reads: awg_ioctl_set (:3462-3464) stores jc, jmin, jmax in that order,
so a reader preempted between two stores sees a mixed triple. The pre-patch
function reads the triple in the guard (:1789-1791, five loads: jc, jmin,
jmax, jmin, jmax) and re-reads it in the loop body (:1795-1800). The harness
models the writer landing between those two read groups: the softc fields are
accessors that serve configuration A for the first `nA` loads and
configuration B afterwards. The current-tree build is driven with a sweep
nA=0..7 so every torn prefix order is exercised: the pre-patch guard issues
five loads (jc, jmin, jmax, jmin, jmax), so thresholds 0..5 split the guard
itself and 6..7 split the guard from the loop body; the patched function
issues exactly three loads, so every threshold yields a coherent snapshot
(nA<=2 splits the snapshot, nA>=3 is fully coherent). The observable pairs include
  A(4,64,128)  + B(4,64,128)     coherent A
  A(4,64,128)  + B(9,64,128)     jc written first
  A(4,64,128)  + B(4,900,128)    jmin written, jmax not  (probe: span -771)
  A(4,64,128)  + B(9,900,1000)   all three written (coherent B)

The RED control compiles the pre-patch function text from the cycle base
(ec16a75) in the same harness; its torn run reproduces the recorded probe
(span -771 -> uint32_t wrap -> pktlen up to 65535 > 1500). The patched
function reads one snapshot, so the guard and the loop agree and every length
stays within the 1500-byte buffer.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = 'ec16a751ff5c69bbc8a4a731ffbee5ffb7f61801'
FUNC_RE = re.compile(r'(?ms)^void\nawg_send_junk\(.*?^\}')

PREPATCH_NA = 5   # guard loads jc, jmin, jmax, jmin, jmax
# Sweep the writer-landing threshold across the whole pre-patch read group
# (guard: 5 loads, loop body: 5 loads). nA=0..7 covers every torn prefix
# order on the current-tree build; the patched 3-load snapshot stays coherent
# at every threshold, so the same sweep is GREEN at HEAD and RED at base.
NA_SWEEP = range(0, 8)
# R1 review: the fixed sibling evidence/kernel-junk sink was shared by every
# worktree in the cycle root, so two concurrent runs overwrote each other's
# harness .c files. The evidence now lands in a per-run root derived from the
# private TMPDIR (AWG_EVIDENCE_DIR overrides for a caller that wants to keep
# the run's evidence set, e.g. the cycle root).
EVIDENCE = Path(os.environ.get('AWG_EVIDENCE_DIR')
                or os.path.join(os.environ.get('TMPDIR', '/tmp'),
                                'awg-evidence', 'kernel-junk'))

STUBS = '''
/* The kernel reads the three junk fields as plain loads off the softc. The
 * harness routes those loads through accessor function pointers so a writer
 * can land between the guard reads and the loop-body reads: the first `nA`
 * loads return configuration A, later loads return configuration B. */
struct awg_softc {
	uint16_t (*rd_jc)(void);
	uint16_t (*rd_jmin)(void);
	uint16_t (*rd_jmax)(void);
};
#define sc_awg_jc   rd_jc()
#define sc_awg_jmin rd_jmin()
#define sc_awg_jmax rd_jmax()
struct awg_peer;
struct awg_endpoint { struct sockaddr_storage e_remote; };

static int rd_count, nA;
static uint16_t jc_a, jmin_a, jmax_a, jc_b, jmin_b, jmax_b;
static uint16_t
sel(uint16_t a, uint16_t b)
{
	return ++rd_count <= nA ? a : b;
}
static uint16_t
rd_jc(void)
{
	return sel(jc_a, jc_b);
}
static uint16_t
rd_jmin(void)
{
	return sel(jmin_a, jmin_b);
}
static uint16_t
rd_jmax(void)
{
	return sel(jmax_a, jmax_b);
}

/* Deterministic stand-ins for the kernel's arc4random(3) API (the real
 * kernel uses the same entry points; a seeded LCG makes the two builds
 * bit-comparable). The buffer stub fills a 65536-byte backing store instead
 * of the 1500-byte stack buffer so an over-long length is observable without
 * the stack overrun killing the process; the recorded length is what the
 * kernel writes. */
static uint64_t rng_state = 0x2545F4914F6CDD1DULL;
uint32_t
arc4random_uniform(uint32_t bound)
{
	rng_state ^= rng_state << 13;
	rng_state ^= rng_state >> 7;
	rng_state ^= rng_state << 17;
	return bound ? (uint32_t)(rng_state % bound) : 0;
}
static uint8_t backing[65536];
void
arc4random_buf(void *buf, size_t len)
{
	uint8_t *p = backing;
	size_t i;
	(void)buf;
	for (i = 0; i < len; i++) {
		rng_state ^= rng_state << 13;
		rng_state ^= rng_state >> 7;
		rng_state ^= rng_state << 17;
		p[i] = (uint8_t)(rng_state >> 33);
	}
}

static size_t sent_bytes;
static unsigned sent_pkts;
static uint16_t sent_max;
static void
awg_peer_get_endpoint(struct awg_peer *peer, struct awg_endpoint *endpoint)
{
	(void)peer;
	memset(endpoint, 0, sizeof(*endpoint));
}
static void
awg_send_buf(struct awg_softc *sc, struct awg_endpoint *e, const void *buf,
    size_t len)
{
	(void)sc; (void)e; (void)buf;
	sent_bytes += len;
	sent_pkts++;
	if (len > sent_max)
		sent_max = (uint16_t)len;
}
'''

MAIN = '''
int
main(int argc, char **argv)
{
	/* argv: nA jcA jminA jmaxA jcB jminB jmaxB [seed].
	 * The first nA field loads return A, later loads return B. */
	struct awg_softc sc;

	sc.rd_jc = rd_jc;
	sc.rd_jmin = rd_jmin;
	sc.rd_jmax = rd_jmax;
	nA = atoi(argv[1]);
	jc_a = (uint16_t)atoi(argv[2]);
	jmin_a = (uint16_t)atoi(argv[3]);
	jmax_a = (uint16_t)atoi(argv[4]);
	jc_b = (uint16_t)atoi(argv[5]);
	jmin_b = (uint16_t)atoi(argv[6]);
	jmax_b = (uint16_t)atoi(argv[7]);
	if (argc > 8)
		rng_state = strtoull(argv[8], NULL, 10);
	awg_send_junk(&sc, NULL);
	printf("pkts=%u bytes=%zu max=%u\\n", sent_pkts, sent_bytes,
	    sent_max);
	return 0;
}
'''


def harness_source(func_text):
    return ('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n'
            '#include <stdint.h>\n#include <sys/socket.h>\n'
            + STUBS + func_text + MAIN)


def build(source, tmpdir, name):
    c = Path(tmpdir) / (name + '.c')
    c.write_text(source)
    exe = Path(tmpdir) / name
    subprocess.run(['cc', '-std=c99', '-D_DEFAULT_SOURCE', '-Wall', '-Werror',
                    str(c), '-o', str(exe)], check=True, capture_output=True)
    return exe


def run(exe, nA, jcA, jminA, jmaxA, jcB, jminB, jmaxB, seed=None):
    argv = [str(exe), str(nA), str(jcA), str(jminA), str(jmaxA),
            str(jcB), str(jminB), str(jmaxB)]
    if seed is not None:
        argv.append(str(seed))
    out = subprocess.check_output(argv).decode()
    return {kv.split('=', 1)[0]: int(kv.split('=', 1)[1])
            for kv in out.split()}


# (label, A=(jc,jmin,jmax), B=(jc,jmin,jmax)) — the four torn orders of the
# A(4,64,128) -> B(9,900,1000) ioctl, plus clamped/edge values the bound
# must cover (B = A there, i.e. one coherent configuration).
CONFIGS = [
    ('coherent A',          (4, 64, 128), (4, 64, 128)),
    ('torn jc new',         (4, 64, 128), (9, 64, 128)),
    ('torn jmin new',       (4, 64, 128), (4, 900, 128)),
    ('coherent B',          (9, 900, 1000), (9, 900, 1000)),
    ('torn jmax new',       (4, 64, 128), (4, 64, 1000)),
    ('jmin==jmax==1',       (1, 1, 1), (1, 1, 1)),
    ('jc=1 range',          (1, 64, 128), (1, 64, 128)),
    ('jc=0 skip',           (0, 64, 128), (0, 64, 128)),
    ('edge 1499/1500',      (4, 1499, 1500), (4, 1499, 1500)),
    ('edge jmax=1500',      (2, 1, 1500), (2, 1, 1500)),
    ('edge jmax=1501',      (4, 1, 1501), (4, 1, 1501)),
    ('uint16 max 65535',    (4, 1, 65535), (4, 1, 65535)),
    ('jc large 65535',      (65535, 64, 128), (65535, 64, 128)),
    ('torn edge 1501',      (4, 1, 1500), (4, 1501, 1500)),
]

# (label, jc, jmin, jmax) — single coherent configuration, zero/inverted.
SKIP_CONFIGS = [('jc=0', 0, 64, 128), ('jmin=0', 4, 0, 128),
                ('jmin>jmax', 4, 200, 128)]


class JunkTornSnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('cc'):
            raise unittest.SkipTest('requires a userspace C compiler')
        if not shutil.which('git'):
            raise unittest.SkipTest('requires git for the pre-patch control')
        cls.tmp = tempfile.TemporaryDirectory()
        current = (ROOT / 'src/if_awg.c').read_text()
        m_cur = FUNC_RE.search(current)
        assert m_cur is not None, 'awg_send_junk not found in src/if_awg.c'
        base = subprocess.check_output(
            ['git', '-C', str(ROOT), 'show', f'{BASE}:src/if_awg.c']).decode()
        m_base = FUNC_RE.search(base)
        assert m_base is not None, 'awg_send_junk not found at the cycle base'
        cls.patched = build(harness_source(m_cur.group()), cls.tmp.name,
                            'junk_patched')
        cls.prepatch = build(harness_source(m_base.group()), cls.tmp.name,
                             'junk_prepatch')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_patched_bound_all_configs(self):
        """GREEN: every clamped/torn/edge config sends pktlen <= 1500 at
        every writer-landing threshold nA=0..7 (all four torn prefix orders
        of the A->B ioctl, plus the coherent A and coherent B ends)."""
        for label, a, b in CONFIGS:
            for nA in NA_SWEEP:
                with self.subTest(label=label, nA=nA):
                    r = run(self.patched, nA, *a, *b, seed=12345)
                    self.assertLessEqual(r['max'], 1500,
                                         f'{label} nA={nA}')

    def test_harness_artifacts_match_build(self):
        """Evidence: write the exact harness sources compiled in this run to
        the cycle-root evidence dir so the stored .c files are the harness
        that produced the logs (nA is an argv parameter, no mode CLI)."""
        evidence_dir = EVIDENCE
        if evidence_dir is None:
            evidence_dir = Path(self.tmp.name) / 'evidence' / 'kernel-junk'
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / 'harness_patched.c').write_text(
            harness_source(FUNC_RE.search(
                (ROOT / 'src/if_awg.c').read_text()).group()))
        (evidence_dir / 'harness_prepatch.c').write_text(
            harness_source(FUNC_RE.search(
                subprocess.check_output(
                    ['git', '-C', str(ROOT), 'show',
                     f'{BASE}:src/if_awg.c']).decode()).group()))

    def test_prepatch_torn_overflows(self):
        """RED control: the pre-patch arithmetic overruns the 1500 buffer.
        Guard (4,64,128) passes, the loop re-reads jmin 900 with jmax 128:
        span -771 promotes to uint32_t and uniform wraps -> pktlen up to
        65535 (recorded probe: 25680)."""
        r = run(self.prepatch, PREPATCH_NA, 4, 64, 128, 4, 900, 128,
                seed=12345)
        self.assertGreater(r['max'], 1500)
        self.assertLessEqual(r['max'], 65535)

    def test_zero_and_inverted_skip(self):
        """Zero-length/skip path preserved: jc=0, jmin=0, jmin>jmax send 0."""
        for label, jc, jmin, jmax in SKIP_CONFIGS:
            with self.subTest(label):
                for exe, name in ((self.patched, 'patched'),
                                  (self.prepatch, 'pre-patch')):
                    r = run(exe, PREPATCH_NA, jc, jmin, jmax, jc, jmin, jmax,
                            seed=7)
                    self.assertEqual(r['pkts'], 0, f'{label} {name}')

    def test_distribution_preserved(self):
        """Same seed, in-range config: patched and pre-patch emit identical
        packet counts and byte totals, so the snapshot did not change the
        distribution for valid (jc, jmin, jmax)."""
        for label, jc, jmin, jmax in [('A', 4, 64, 128), ('B', 4, 900, 1000),
                                      ('fixed', 3, 77, 77),
                                      ('wide', 2, 1, 1500)]:
            with self.subTest(label):
                a = run(self.prepatch, PREPATCH_NA, jc, jmin, jmax,
                        jc, jmin, jmax, seed=4242)
                b = run(self.patched, PREPATCH_NA, jc, jmin, jmax,
                        jc, jmin, jmax, seed=4242)
                self.assertEqual(a['pkts'], b['pkts'])
                self.assertEqual(a['bytes'], b['bytes'])

    def test_jc_burst_count(self):
        """The loop bound is the snapshot: patched and pre-patch send jc
        datagrams for a stable configuration."""
        for jc in (1, 4, 65535):
            with self.subTest(jc=jc):
                a = run(self.prepatch, PREPATCH_NA, jc, 64, 128, jc, 64, 128,
                        seed=99)
                b = run(self.patched, PREPATCH_NA, jc, 64, 128, jc, 64, 128,
                        seed=99)
                self.assertEqual(a['pkts'], jc)
                self.assertEqual(b['pkts'], jc)


if __name__ == '__main__':
    unittest.main()
