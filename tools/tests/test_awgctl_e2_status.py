"""E2: `awgctl show` must report only what the kernel holds, and its
machine-readable mode must be byte-stable.

Two suites:

  TruthfulStatusTests  — field by field, the text status is compared against
                         the fixture's kernel state: every field the kernel
                         filled is reported with the kernel's value, and no
                         field the kernel did not fill is reported at all.
                         Covers no peers, never handshaked, interface down
                         and a link entry without if_data.

  JsonFormatTests      — the -j form is pinned byte for byte (fixed key order,
                         no ANSI, one record per peer), is identical across
                         runs, and agrees with the text form field by field.

The fixture (tools/lab/fixture-stubs.c) stands in for the kernel: it answers
SIOCGAWG from a state file and serves getifaddrs(3), so "what the kernel
holds" is exactly the state the test wrote.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CC = os.environ.get('CC', 'cc')

# Interface flag bits, from src/if_awg.h — kept here so a new flag cannot
# silently escape the "nothing phantom, nothing dropped" check below.
PUBLIC = 1 << 0
PORT = 1 << 2
RTABLE = 1 << 3
JC = 1 << 5
S12 = 1 << 6
H = 1 << 7
VERSION = 1 << 8
S3 = 1 << 9
S4 = 1 << 10
HPK = 1 << 11
CPA = 1 << 12
REKEY_AFTER_TIME = 1 << 13
REKEY_TIMEOUT = 1 << 14
REJECT_AFTER_TIME = 1 << 15
KEEPALIVE_TIMEOUT = 1 << 16
MAX_HANDSHAKE_ATTEMPTS = 1 << 17
TRAILERS = 1 << 18
COOKIES = 1 << 19
I1 = 1 << 20

ALL_FLAGS = 0xffffffff
# A 3.1 interface that has been configured: port, version, public key.
CONFIGURED = PORT | PUBLIC | VERSION | RTABLE

# Every field present. Two peers, each with its own counters, handshake and
# allowed IPs.
STATE_FULL = """\
flags={flags}
port=51820
version=1
peers=2
tx=12345
rx=54321
handshake=42
mtu=1420
up=1
ispec1=<b 0xc700000001><r 16><t>
ispec3=<b 0xc700000003><r 64><t>
jc=10
jmin=5
jmax=15
s1=1
s2=2
s3=3
s4=4
h1=100-400
h2=200
h3=300
h4=402-410
hpk=1
range1=86400
range2=120
range3=180
range4=10
range5=3
range6=40
random_trailers=1
disable_cookies=1
psk=1
pka=1
endpoint=1
aip1=10.9.0.2/32
aip2=fd00::2/128
""".format(flags=ALL_FLAGS)

STATE_NO_PEERS = """\
flags={flags}
port=51820
version=1
peers=0
tx=0
rx=0
handshake=0
mtu=1420
up=1
""".format(flags=CONFIGURED)

STATE_DOWN = STATE_NO_PEERS.replace('up=1', 'up=0')

# One peer that has never completed a handshake: the kernel's timestamp is 0.
STATE_NEVER_HANDSHAKED = """\
flags={flags}
port=51820
version=1
peers=1
tx=0
rx=0
handshake=0
mtu=1420
up=1
""".format(flags=CONFIGURED)

# Nothing configured: no flag is set, so the status must be empty of fields.
STATE_NO_FLAGS = """\
flags=0
port=0
version=0
peers=0
tx=0
rx=0
handshake=0
mtu=1420
up=0
"""

# A single peer with every field, for the byte pin.
STATE_PIN = """\
flags={flags}
port=51820
version=1
peers=1
tx=12345
rx=54321
handshake=42
mtu=1420
up=1
ispec1=<b 0xc700000001><r 16><t>
jc=10
jmin=5
jmax=15
s1=1
s2=2
s3=3
s4=4
h1=100-400
h2=200
h3=300
h4=400
hpk=1
range1=86400
range2=120
range3=180
range4=10
range5=3
range6=40
random_trailers=1
disable_cookies=1
psk=1
pka=1
endpoint=1
aip1=10.9.0.2/32
aip2=fd00::2/128
""".format(flags=ALL_FLAGS)

# The fixture's public key for peer 0 is 32 zero bytes; peer 1 differs in the
# first byte.
PEER0_KEY = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
PEER1_KEY = 'AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='

# The exact bytes `show -j` must print for STATE_PIN. Pinned so a key rename,
# a reordering, extra whitespace or a clock-derived value fails here.
PINNED_JSON = """\
[
{
  "name": "awg9",
  "state": "up",
  "mtu": 1420,
  "awgport": 51820,
  "awgrtable": 0,
  "awgpubkey": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
  "awgversion": "3.1",
  "awgjc": 10,
  "awgjmin": 5,
  "awgjmax": 15,
  "awgs1": 1,
  "awgs2": 2,
  "awgs3": 3,
  "awgs4": 4,
  "awgh1": "100-400",
  "awgh2": 200,
  "awgh3": 300,
  "awgh4": 400,
  "awgi1": "<b 0xc700000001><r 16><t>",
  "awgi2": "",
  "awgi3": "",
  "awgi4": "",
  "awgi5": "",
  "awghpk": true,
  "awgcpa": 40,
  "awgrekeyaftertime": 86400,
  "awgrekeytimeout": 120,
  "awgrejectaftertime": 180,
  "awgkeepalivetimeout": 10,
  "awgmaxhandshakeattempts": 3,
  "awgrandomtrailers": true,
  "awgdisablecookies": true,
  "peers": [
    {
      "awgpeer": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
      "awgdescr": "",
      "awgpsk": true,
      "awgpka": 100,
      "awgendpoint": "192.0.2.1:51820",
      "tx": 12345,
      "rx": 54321,
      "last_handshake": 1700000042,
      "awgaip": ["10.9.0.2/32", "fd00::2/128"]
    }
  ]
}
]
"""

# The interface keys the kernel's state implies for STATE_PIN, in the order
# awgctl(8) documents.
PINNED_ORDER = [
    'name', 'state', 'mtu', 'awgport', 'awgrtable', 'awgpubkey', 'awgversion',
    'awgjc', 'awgjmin', 'awgjmax', 'awgs1', 'awgs2', 'awgs3', 'awgs4',
    'awgh1', 'awgh2', 'awgh3', 'awgh4',
    'awgi1', 'awgi2', 'awgi3', 'awgi4', 'awgi5',
    'awghpk', 'awgcpa',
    'awgrekeyaftertime', 'awgrekeytimeout', 'awgrejectaftertime',
    'awgkeepalivetimeout', 'awgmaxhandshakeattempts',
    'awgrandomtrailers', 'awgdisablecookies', 'peers',
]

PEER_ORDER = ['awgpeer', 'awgdescr', 'awgpsk', 'awgpka', 'awgendpoint',
              'tx', 'rx', 'last_handshake', 'awgaip']


def expected_interface_keys(flags, ranges):
    """The key set the kernel's state implies, in documented order.

    `ranges` maps a range name to its (lo, hi). A field appears only when its
    flag is set, and the ranged/boolean ones only when the kernel holds a
    non-zero value — the same rule the text output has always used.
    """
    keys = ['awgstate', 'awgmtu']   # the link entry serves state and MTU
    if flags & PORT:
        keys.append('awgport')
    if flags & RTABLE:
        keys.append('awgrtable')
    if flags & PUBLIC:
        keys.append('awgpubkey')
    if flags & VERSION:
        keys.append('awgversion')
    if flags & JC:
        keys += ['awgjc', 'awgjmin', 'awgjmax']
    if flags & S12:
        keys += ['awgs1', 'awgs2']
        if flags & S3:
            keys.append('awgs3')
        if flags & S4:
            keys.append('awgs4')
    if flags & H:
        keys += ['awgh1', 'awgh2', 'awgh3', 'awgh4']
    for i in range(5):
        if flags & (I1 << i):
            keys.append('awgi%d' % (i + 1))
    if flags & HPK:
        keys.append('awghpk')
    if flags & CPA and ranges['cpa'][1] != 0:
        keys.append('awgcpa')
    for bit, name in ((REKEY_AFTER_TIME, 'awgrekeyaftertime'),
                      (REKEY_TIMEOUT, 'awgrekeytimeout'),
                      (REJECT_AFTER_TIME, 'awgrejectaftertime'),
                      (KEEPALIVE_TIMEOUT, 'awgkeepalivetimeout'),
                      (MAX_HANDSHAKE_ATTEMPTS, 'awgmaxhandshakeattempts')):
        if flags & bit and ranges[name][1] != 0:
            keys.append(name)
    if flags & TRAILERS:
        keys.append('awgrandomtrailers')
    if flags & COOKIES:
        keys.append('awgdisablecookies')
    return keys


def parse_text(out):
    """Parse `show` output into ({interface fields}, [peer fields, ...]).

    A peer starts at its `awgpeer` line; its counters arrive as one
    "tx: N, rx: N" line and its handshake as "last handshake: N seconds ago".
    """
    iface, peers = {}, []
    cur = None
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.endswith(':'):
            continue                      # "awg9:"
        if line.startswith('awgpeer '):
            cur = {'awgpeer': line.split(None, 1)[1]}
            peers.append(cur)
            continue
        if line.startswith('tx:'):
            # "tx: N, rx: M"
            parts = line.split()
            cur['tx'] = parts[1].rstrip(',')
            cur['rx'] = parts[3]
            continue
        if line.startswith('last handshake:'):
            cur['last_handshake'] = line.split(':', 1)[1].split()[0]
            continue
        key, _, value = line.partition(' ')
        key = key.rstrip(':')
        if cur is not None and key in ('awgdescr', 'awgpsk', 'awgpka',
                                       'awgendpoint', 'awgaip'):
            if key == 'awgaip':
                cur.setdefault('awgaip', []).append(value)
            else:
                cur[key] = value
        else:
            iface[key] = value
    return iface, peers


class FixtureCase(unittest.TestCase):
    """Builds awgctl against the host fixture once per test class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.binary = cls.work / 'awgctl-fixture'
        cc = subprocess.run(
            [CC, '-DAWGCTL_LINUX', '-I', str(ROOT / 'src'),
             '-I', str(ROOT / 'sbin/awgctl'),
             '-DCONF_DIR="%s"' % cls.work, '-DCONF_OWNER_OK(u)=1',
             '-DIFCONFIG_PATH="%s"' % (ROOT / 'tools/lab/fixture-ifconfig.py'),
             '-o', str(cls.binary), str(ROOT / 'sbin/awgctl/awgctl.c'),
             str(ROOT / 'tools/lab/fixture-stubs.c'), '-lutil'],
            capture_output=True, text=True)
        if cc.returncode != 0:
            raise unittest.SkipTest('fixture build failed: %s'
                                    % cc.stderr.strip()[:400])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory()
        self.dir = Path(self.case.name)
        self.state = self.dir / 'state'

    def tearDown(self):
        self.case.cleanup()

    def run_show(self, state_text, args=(), extra_env=None):
        self.state.write_text(state_text)
        env = dict(os.environ)
        env['AWGFIX_STATE'] = str(self.state)
        env.pop('AWGFIX_NODATA', None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run([str(self.binary), 'show', *args, 'awg9'],
                              env=env, capture_output=True, text=True)

    def text_status(self, state_text):
        run = self.run_show(state_text)
        self.assertEqual(run.returncode, 0, run.stderr)
        return parse_text(run.stdout)

    def json_status(self, state_text, flag='-j'):
        run = self.run_show(state_text, (flag,))
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)


class TruthfulStatusTests(FixtureCase):
    """Every reported field equals the kernel's, and nothing else is shown."""

    # ---- interface state and MTU (the AF_LINK if_data) ----

    def test_state_up(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgstate'], 'up')

    def test_state_down(self):
        iface, _ = self.text_status(STATE_DOWN)
        self.assertEqual(iface['awgstate'], 'down')

    def test_mtu_matches_kernel(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgmtu'], '1420')

    def test_mtu_follows_the_kernel_value(self):
        iface, _ = self.text_status(STATE_FULL.replace('mtu=1420', 'mtu=1500'))
        self.assertEqual(iface['awgmtu'], '1500')

    def test_no_mtu_when_kernel_serves_no_if_data(self):
        run = self.run_show(STATE_FULL, extra_env={'AWGFIX_NODATA': '1'})
        self.assertEqual(run.returncode, 0, run.stderr)
        iface, _ = parse_text(run.stdout)
        self.assertNotIn('awgmtu', iface,
                         'an MTU was reported although the kernel served none')

    def test_state_still_reported_without_if_data(self):
        """IFF_UP comes from the link flags, not if_data: it stays truthful."""
        run = self.run_show(STATE_FULL, extra_env={'AWGFIX_NODATA': '1'})
        iface, _ = parse_text(run.stdout)
        self.assertEqual(iface['awgstate'], 'up')

    # ---- interface fields from the SIOCGAWG reply ----

    def test_port(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgport'], '51820')

    def test_version(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgversion'], '3.1')

    def test_public_key(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgpubkey'], PEER0_KEY)

    def test_private_key_never_reported(self):
        run = self.run_show(STATE_FULL)
        self.assertNotIn('awgprivate', run.stdout)

    def test_jc_jmin_jmax(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual((iface['awgjc'], iface['awgjmin'], iface['awgjmax']),
                         ('10', '5', '15'))

    def test_s1_to_s4(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual([iface['awgs%d' % i] for i in (1, 2, 3, 4)],
                         ['1', '2', '3', '4'])

    def test_h1_to_h4_ranges(self):
        """A range prints one number when both bounds agree, else lo-hi."""
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual((iface['awgh1'], iface['awgh2'], iface['awgh3'],
                          iface['awgh4']),
                         ('100-400', '200', '300', '402-410'))

    def test_ispec_values(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgi1'], '<b 0xc700000001><r 16><t>')
        self.assertEqual(iface['awgi3'], '<b 0xc700000003><r 64><t>')
        for empty in ('awgi2', 'awgi4', 'awgi5'):
            self.assertEqual(iface[empty], '', '%s should be empty' % empty)

    def test_hpk_present(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awghpk'], '(present)')

    def test_cpa_range(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(iface['awgcpa'], '40')

    def test_five_timing_keys(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(
            [iface[k] for k in ('awgrekeyaftertime', 'awgrekeytimeout',
                                'awgrejectaftertime', 'awgkeepalivetimeout',
                                'awgmaxhandshakeattempts')],
            ['86400', '120', '180', '10', '3'])

    def test_trailer_and_cookie_flags(self):
        iface, _ = self.text_status(STATE_FULL)
        self.assertIn('awgrandomtrailers', iface)
        self.assertIn('awgdisablecookies', iface)

    # ---- peers ----

    def test_peer_list_one_line_per_peer(self):
        _, peers = self.text_status(STATE_FULL)
        self.assertEqual([p['awgpeer'] for p in peers], [PEER0_KEY, PEER1_KEY])

    def test_per_peer_counters(self):
        _, peers = self.text_status(STATE_FULL)
        self.assertEqual([(p['tx'], p['rx']) for p in peers],
                         [('12345', '54321'), ('24690', '108642')])

    def test_per_peer_last_handshake_age(self):
        """The text form reports age in seconds; the fixture's clock is fixed."""
        _, peers = self.text_status(STATE_FULL)
        for peer in peers:
            self.assertIn('last_handshake', peer)
            self.assertTrue(int(peer['last_handshake']) > 0)

    def test_per_peer_allowed_ips(self):
        _, peers = self.text_status(STATE_FULL)
        for peer in peers:
            self.assertEqual(peer['awgaip'], ['10.9.0.2/32', 'fd00::2/128'])

    def test_per_peer_psk_and_keepalive(self):
        _, peers = self.text_status(STATE_FULL)
        self.assertEqual(peers[0]['awgpsk'], '(present)')
        self.assertEqual(peers[0]['awgpka'], '100 (sec)')
        self.assertEqual(peers[1]['awgpka'], '101 (sec)')

    def test_peer_endpoint_reported(self):
        _, peers = self.text_status(STATE_FULL)
        for peer in peers:
            self.assertIn('awgendpoint', peer)

    # ---- the edge cases the card names ----

    def test_no_peers(self):
        iface, peers = self.text_status(STATE_NO_PEERS)
        self.assertEqual(peers, [])
        self.assertEqual(iface['awgport'], '51820')
        self.assertNotIn('tx', iface)
        self.assertNotIn('last handshake', iface)

    def test_never_handshaked(self):
        _, peers = self.text_status(STATE_NEVER_HANDSHAKED)
        self.assertEqual(len(peers), 1)
        self.assertNotIn('last_handshake', peers[0],
                         'a peer that never handshaked must have no handshake')
        self.assertEqual(peers[0]['tx'], '0')

    def test_interface_down(self):
        iface, peers = self.text_status(STATE_DOWN)
        self.assertEqual(iface['awgstate'], 'down')
        self.assertEqual(peers, [])

    # ---- nothing phantom, nothing dropped ----

    def test_key_set_matches_kernel_flags(self):
        """The reported interface keys are exactly the kernel's field set."""
        ranges = {'cpa': (40, 40), 'awgrekeyaftertime': (86400, 86400),
                  'awgrekeytimeout': (120, 120),
                  'awgrejectaftertime': (180, 180),
                  'awgkeepalivetimeout': (10, 10),
                  'awgmaxhandshakeattempts': (3, 3)}
        iface, _ = self.text_status(STATE_FULL)
        self.assertEqual(list(iface.keys()),
                         expected_interface_keys(ALL_FLAGS, ranges))

    def test_no_field_when_no_flag_is_set(self):
        iface, peers = self.text_status(STATE_NO_FLAGS)
        self.assertEqual(iface, {'awgstate': 'down', 'awgmtu': '1420'})
        self.assertEqual(peers, [])

    def test_partial_flags_report_only_their_fields(self):
        flags = PORT | PUBLIC | VERSION
        state = STATE_NO_PEERS.replace('flags=%d' % CONFIGURED,
                                       'flags=%d' % flags)
        iface, _ = self.text_status(state)
        self.assertEqual(list(iface.keys()),
                         ['awgstate', 'awgmtu', 'awgport', 'awgpubkey',
                          'awgversion'])

    def test_zero_valued_ranges_are_not_reported(self):
        """A flag set over a zero range prints nothing: the kernel holds 0."""
        state = STATE_FULL.replace('range6=40', 'range6=0')
        iface, _ = self.text_status(state)
        self.assertNotIn('awgcpa', iface)


class JsonFormatTests(FixtureCase):
    """-j is pinned byte for byte, stable across runs, one record per peer."""

    def raw_json(self, state_text):
        run = self.run_show(state_text, ('-j',))
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_pinned_bytes(self):
        self.assertEqual(self.raw_json(STATE_PIN), PINNED_JSON)

    def test_byte_stable_across_runs(self):
        runs = [self.raw_json(STATE_FULL) for _ in range(3)]
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_no_ansi_escapes(self):
        for state in (STATE_PIN, STATE_FULL, STATE_NO_PEERS):
            self.assertNotIn(chr(27), self.raw_json(state))

    def test_long_flag_same_output(self):
        short = self.raw_json(STATE_PIN)
        long = self.run_show(STATE_PIN, ('--json',))
        self.assertEqual(long.returncode, 0, long.stderr)
        self.assertEqual(long.stdout, short)

    def test_document_is_one_array_per_interface(self):
        doc = json.loads(self.raw_json(STATE_PIN))
        self.assertIsInstance(doc, list)
        self.assertEqual(len(doc), 1)
        self.assertEqual(doc[0]['name'], 'awg9')

    def test_interface_key_order(self):
        doc = json.loads(self.raw_json(STATE_PIN))
        self.assertEqual(list(doc[0].keys()), PINNED_ORDER)

    def test_peer_key_order(self):
        doc = json.loads(self.raw_json(STATE_PIN))
        self.assertEqual(list(doc[0]['peers'][0].keys()), PEER_ORDER)

    def test_one_record_per_peer(self):
        doc = json.loads(self.raw_json(STATE_FULL))
        peers = doc[0]['peers']
        self.assertEqual(len(peers), 2)
        self.assertEqual([p['awgpeer'] for p in peers], [PEER0_KEY, PEER1_KEY])
        self.assertEqual([(p['tx'], p['rx']) for p in peers],
                         [(12345, 54321), (24690, 108642)])
        for peer in peers:
            self.assertEqual(list(peer.keys()), PEER_ORDER)

    def test_peer_record_matches_kernel(self):
        doc = json.loads(self.raw_json(STATE_FULL))
        first = doc[0]['peers'][0]
        self.assertEqual(first['awgdescr'], '')
        self.assertIs(first['awgpsk'], True)
        self.assertEqual(first['awgpka'], 100)
        self.assertEqual(first['awgendpoint'], '192.0.2.1:51820')
        self.assertEqual(first['awgaip'], ['10.9.0.2/32', 'fd00::2/128'])
        # The kernel's own timestamp, not an age: the fixture writes
        # 1700000000 + handshake.
        self.assertEqual(first['last_handshake'], 1700000042)

    def test_ranges_are_numbers_or_strings(self):
        doc = json.loads(self.raw_json(STATE_PIN))
        iface = doc[0]
        self.assertEqual(iface['awgh1'], '100-400')
        self.assertEqual(iface['awgh2'], 200)
        self.assertEqual(iface['awgrekeyaftertime'], 86400)

    def test_no_phantom_keys_when_no_flag_is_set(self):
        doc = json.loads(self.raw_json(STATE_NO_FLAGS))
        self.assertEqual(list(doc[0].keys()),
                         ['name', 'state', 'mtu', 'peers'])
        self.assertEqual(doc[0]['peers'], [])

    def test_never_handshaked_has_no_handshake_key(self):
        doc = json.loads(self.raw_json(STATE_NEVER_HANDSHAKED))
        peer = doc[0]['peers'][0]
        self.assertNotIn('last_handshake', peer)
        self.assertEqual(peer['awgaip'], [])

    def test_no_peers_is_empty_array(self):
        out = self.raw_json(STATE_NO_PEERS)
        self.assertIn('"peers": []', out)
        self.assertEqual(json.loads(out)[0]['peers'], [])

    def test_json_agrees_with_text_field_by_field(self):
        """Both modes read one kernel state; they must not disagree."""
        iface, peers = self.text_status(STATE_FULL)
        doc = json.loads(self.raw_json(STATE_FULL))[0]
        self.assertEqual(doc['state'], iface['awgstate'])
        self.assertEqual(str(doc['mtu']), iface['awgmtu'])
        self.assertEqual(str(doc['awgport']), iface['awgport'])
        self.assertEqual(str(doc['awgrtable']), iface['awgrtable'])
        self.assertEqual(doc['awgpubkey'], iface['awgpubkey'])
        self.assertEqual(doc['awgversion'], iface['awgversion'])
        self.assertEqual(str(doc['awgjc']), iface['awgjc'])
        self.assertEqual(doc['awgh1'], iface['awgh1'])
        self.assertEqual(doc['awgi1'], iface['awgi1'])
        self.assertEqual(doc['awgrekeyaftertime'],
                         int(iface['awgrekeyaftertime']))
        for jpeer, tpeer in zip(doc['peers'], peers):
            self.assertEqual(jpeer['awgpeer'], tpeer['awgpeer'])
            self.assertEqual(str(jpeer['tx']), tpeer['tx'])
            self.assertEqual(str(jpeer['rx']), tpeer['rx'])
            self.assertEqual(jpeer['awgaip'], tpeer['awgaip'])


# A peer description carrying every character JSON cannot take raw. The state
# file is line-oriented, so the fixture decodes these C-style escapes back to
# the bytes a kernel would hold in p_description.
DESCR_ESCAPED = r'a:\"b:\\c:\td:\ne:\rf:\bg:\fh:\x01'
DESCR_BYTES = 'a:"b:\\c:\td:\ne:\rf:\x08g:\x0ch:\x01'
# What those bytes must look like inside the JSON document.
DESCR_JSON = 'a:\\"b:\\\\c:\\td:\\ne:\\rf:\\bg:\\fh:\\u0001'

STATE_DESCR = STATE_FULL + 'pdescr=%s\n' % DESCR_ESCAPED


class JsonEscapeTests(FixtureCase):
    """A description the kernel holds arrives intact and the document stays
    valid JSON: a control byte inside a string would break every consumer."""

    def raw_json(self, state_text):
        run = self.run_show(state_text, ('-j',))
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_document_still_parses(self):
        out = self.raw_json(STATE_DESCR)
        doc = json.loads(out)  # raises on a raw control character
        self.assertEqual(doc[0]['peers'][0]['awgdescr'], DESCR_BYTES)

    def test_escaped_value_is_pinned_byte_for_byte(self):
        out = self.raw_json(STATE_DESCR)
        self.assertIn('"awgdescr": "' + DESCR_JSON + '",', out)

    def test_escaped_form_is_the_json_encoding(self):
        """The bytes on the wire are exactly what json.dumps would produce."""
        out = self.raw_json(STATE_DESCR)
        self.assertIn('"awgdescr": ' + json.dumps(DESCR_BYTES) + ',', out)

    def test_no_raw_control_byte_inside_a_string(self):
        out = self.raw_json(STATE_DESCR)
        for bad in ('\x08', '\x0c', '\x01', '\r', '\t'):
            self.assertNotIn(bad, out)

    def test_escaped_run_is_byte_stable(self):
        runs = [self.raw_json(STATE_DESCR) for _ in range(3)]
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_text_form_keeps_the_kernel_bytes(self):
        """Only JSON escapes; the text form is still the kernel's raw value."""
        # Bytes, not text: universal-newline translation would turn the
        # description's CR into LF and hide what the emitter wrote.
        self.state.write_text(STATE_DESCR)
        env = dict(os.environ)
        env['AWGFIX_STATE'] = str(self.state)
        env.pop('AWGFIX_NODATA', None)
        run = subprocess.run([str(self.binary), 'show', 'awg9'],
                             env=env, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(b'awgdescr: ' + DESCR_BYTES.encode('latin-1') + b'\n',
                      run.stdout)

    def test_round_trip_survives_a_counter_update(self):
        """state_write() rewrites the file on a GET; the description must
        come back byte-identical on the next run."""
        env = {'AWGFIX_TRAFFIC': '1'}
        first = self.run_show(STATE_DESCR, ('-j',), extra_env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        after = self.state.read_text()
        self.assertIn('pdescr=%s' % DESCR_ESCAPED, after)
        second = self.run_show(after, ('-j',), extra_env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)[0]['peers'][0]['awgdescr'],
                         DESCR_BYTES)


if __name__ == '__main__':
    unittest.main()
