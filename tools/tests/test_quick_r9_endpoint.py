"""R9/R4-08b: the peer Endpoint is resolved during preparation, before the
first mutation, so a name that does not resolve leaves nothing created.

The production functions are taken verbatim from sbin/awg-quick (same harness
as test_quick_up); ifconfig/netstat are mocked and getent is the mocked
resolver, so the test sees both the resolver call and the mutation order
without touching a real interface. Nothing is cached: each run resolves its
own endpoint.

route(8) is a real executable in a private PATH, not a shell function: cmd_up
ends with `monitor_daemon`, whose `exec route -n monitor` bypasses a function
and runs the host binary. A function-only fixture therefore inherits the
blocking monitor (the harness waits for the pipe to close), which is what
hangs on OpenBSD. The shim answers every verb with the recorded table and
exits, so the backgrounded monitor has a bounded child lifetime on Linux and
OpenBSD alike.

(a) unresolvable name      -> non-zero exit, zero create/MTU/address calls
(b) name of the wrong family against the Address family -> field-specific error
(c) numeric endpoint       -> no resolver call at all, byte-identical argv
(d) a name that resolves   -> programmed with the resolved address
(e) two runs               -> each run resolves its own endpoint
(f) external route monitor -> blocks (negative control); the shim exits
"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from typing import NamedTuple

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

# Mock ifconfig/netstat/getent. Every call is recorded in $CALLS: one marker
# line (IFC/ROUTE/GETENT) followed by one ARG: line per argument. The
# resolver answers with $EP_ADDR (one address) and exits 2 when it is empty,
# like getent(1) for a name with no answer.
MOCKS = '''
getent() {
 printf 'GETENT\\n' >> "$CALLS"
 local a
 for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done
 [[ -n "$EP_ADDR" ]] || return 2
 printf '%s %s\\n' "$EP_ADDR" "$2"
 return 0
}
ifconfig() {
 if [[ ! -e "$SEEN" ]]; then : > "$SEEN"; return 1; fi
 printf 'IFC\\n' >> "$CALLS"
 local a
 for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done
 return 0
}
netstat() { printf 'ROUTE\\n' >> "$CALLS"; return 0; }
'''

# The route(8) stand-in, installed as an executable in a private PATH so that
# `exec route -n monitor` gets a real program. RECORD records the argv and
# answers with the em0 table that resolve_mtu and set_endpoint_direct_route
# parse.
RECORD = '''printf 'ROUTE\\n' >> "$CALLS"
for a in "$@"; do printf 'ARG:%s\\n' "$a" >> "$CALLS"; done
printf 'interface: em0\\nmtu: 1500\\n'
'''

# Fixed fixture: the monitor verb exits, so the backgrounded subshell reads
# its events and finishes (bounded child lifetime).
ROUTE_SHIM = '#!/bin/sh\n' + RECORD

# Negative control: an external monitor that keeps reading, like route(8) on
# OpenBSD. The run only ends when the harness kills the process group; the
# sleep bounds the child so the suite cannot outlive it.
ROUTE_SHIM_EXTERNAL = ('#!/bin/sh\n' + RECORD
                       + 'case "$*" in\n*monitor*) exec sleep 5 ;;\nesac\n')

ROUTE_SHIMS = {'bounded': ROUTE_SHIM, 'external': ROUTE_SHIM_EXTERNAL}


class RunResult(NamedTuple):
    """Outcome of one `awg-quick up` run; timed_out marks a killed group."""
    args: list
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class QuickR9EndpointResolution(unittest.TestCase):
    def run_up(self, text, ep_addr, preexists=False, monitor='bounded',
               timeout=5.0):
        """Run `awg-quick up` for `text`; the resolver answers with ep_addr.

        `monitor` selects the route(8) stand-in: 'bounded' (the fixed shim,
        monitor exits) or 'external' (a blocking monitor, the negative
        control). Returns (process result, recorded call groups) where each
        group is (marker, [arguments]); a timed-out run carries
        ``timed_out=True`` and its whole process group is killed.
        """
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            config = work / 'awg9.conf'
            config.write_text(text)
            config.chmod(0o600)
            calls = work / 'CALLS'
            calls.write_text('')
            seen = work / 'seen'
            if preexists:
                seen.touch()
            bindir = work / 'bin'
            bindir.mkdir()
            route_shim = bindir / 'route'
            route_shim.write_text(ROUTE_SHIMS[monitor])
            route_shim.chmod(0o755)
            source = (ROOT / 'sbin/awg-quick').read_text().split('\nif [[ $# -eq 1 &&')[0]
            driver = work / 'driver.bash'
            driver.write_text(source + STAT_ADAPTER + 'parse_options "$1"\n'
                              + MOCKS + 'cmd_up\n')
            env = dict(os.environ, MOCK_OWNER='0', CALLS=str(calls),
                       SEEN=str(seen), EP_ADDR=ep_addr,
                       PATH=str(bindir) + os.pathsep + os.environ.get('PATH', ''))
            r = self.run_bounded(['bash', str(driver), str(config)], cwd=work,
                                 env=env, timeout=timeout)
            return r, self.parse_calls(calls.read_text())

    @staticmethod
    def run_bounded(argv, cwd, env, timeout):
        """Run argv in its own process group, killing the group on timeout."""
        proc = subprocess.Popen(argv, cwd=cwd, env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
        return RunResult(argv, proc.returncode, out, err, timed_out)

    @staticmethod
    def parse_calls(log):
        groups = []
        for line in log.splitlines():
            if line in ('IFC', 'ROUTE', 'GETENT'):
                groups.append((line, []))
            elif line.startswith('ARG:') and groups:
                groups[-1][1].append(line[4:])
        return groups

    @staticmethod
    def joined(groups, marker, prefix):
        """The single recorded `marker` call starting with `prefix`, as one string."""
        hits = [' '.join(args) for m, args in groups
                if m == marker and args[:len(prefix)] == prefix]
        if len(hits) != 1:
            raise AssertionError('expected exactly one %s call %r, got %r'
                                 % (marker, prefix, hits))
        return hits[0]

    def config_call(self, groups):
        return self.joined(groups, 'IFC', ['awg9', 'awgversion'])

    @staticmethod
    def ifconfig_calls(groups):
        """Only the ifconfig (mutation) groups — the resolver is a separate marker."""
        return [args for marker, args in groups if marker == 'IFC']

    def test_a_unresolvable_name_exits_before_any_mutation(self):
        r, groups = self.run_up('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\n'  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                                'Address = 10.0.0.1/24\nMTU = 1420\n'
                                '[Peer]\nPublicKey = pub\n'
                                'Endpoint = awg.example.invalid:51820\n'
                                'AllowedIPs = 0.0.0.0/0\n', '')
        self.assertNotEqual(r.returncode, 0, 'unresolvable Endpoint must exit non-zero')
        self.assertIn('endpoint', r.stderr.lower(), 'error must name the Endpoint field')
        self.assertIn('awg.example.invalid:51820', r.stderr,
                      'error must name the endpoint that failed to resolve')
        self.assertIn('does not resolve', r.stderr)
        self.assertEqual([m for m, _ in groups], ['GETENT'],
                         'the name goes to the resolver once, then the run stops')
        self.assertEqual(self.ifconfig_calls(groups), [],
                         'no create/MTU/address call for an unresolvable Endpoint')

    def test_b_name_of_the_wrong_family_is_field_specific(self):
        cases = [
            # (label, Address, resolver answer, expected family in the message)
            ('v4 name on an IPv6 underlay', 'fd00::1/64', '203.0.113.7', 'inet6'),
            ('v6 name on an IPv4 underlay', '10.0.0.1/24', '2001:db8::1', 'inet'),
        ]
        for label, address, answer, family in cases:
            with self.subTest(case=label):
                r, groups = self.run_up(
                    '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nAddress = %s\n'  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                    '[Peer]\nPublicKey = pub\nEndpoint = gw.example.invalid:51820\n'
                    'AllowedIPs = 0.0.0.0/0\n' % address, answer)
                self.assertNotEqual(r.returncode, 0, 'exit must be non-zero')
                self.assertIn('endpoint', r.stderr.lower(),
                              'error must name the Endpoint field: %r' % r.stderr)
                self.assertIn(family, r.stderr,
                              'error must name the expected family: %r' % r.stderr)
                self.assertEqual(self.ifconfig_calls(groups), [],
                                 'nothing created for the wrong family')

    def test_c_numeric_endpoints_skip_the_resolver(self):
        cases = [
            ('IPv4', '10.0.0.1/24', '0.0.0.0/0', '1.2.3.4:51820', '1.2.3.4 51820', 'inet'),
            ('IPv6', 'fd00::1/64', '::/0', '[2001:db8::1]:51820', '2001:db8::1 51820', 'inet6'),
        ]
        for label, address, aips, endpoint, programmed, family in cases:
            with self.subTest(family=label):
                r, groups = self.run_up(
                    '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nAddress = %s\nMTU = 1420\n'  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                    '[Peer]\nPublicKey = pub\nEndpoint = %s\n'
                    'AllowedIPs = %s\n' % (address, endpoint, aips), '198.51.100.9')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn('GETENT', [m for m, _ in groups],
                                 'a numeric Endpoint must not reach the resolver')
                # The mutation order is the one the numeric fast-path always had:
                # create -> config -> address -> mtu -> up.
                def idx(prefix):
                    for i, (marker, args) in enumerate(groups):
                        if marker == 'IFC' and args[:len(prefix)] == prefix:
                            return i
                    self.fail('no ifconfig call %r in %r' % (prefix, groups))
                    return -1
                created = idx(['awg9', 'create'])
                config = idx(['awg9', 'awgversion'])
                addr = idx(['awg9', family, address])
                mtu = idx(['awg9', 'mtu', '1420'])
                up = idx(['awg9', 'up'])
                self.assertLess(created, config)
                self.assertLess(config, addr)
                self.assertLess(addr, mtu)
                self.assertLess(mtu, up)
                self.assertIn('awgendpoint ' + programmed, self.config_call(groups))

    def test_d_resolved_name_is_programmed_with_the_address(self):
        r, groups = self.run_up('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\n'  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                                'Address = 10.0.0.1/24\nMTU = 1420\n'
                                '[Peer]\nPublicKey = pub\n'
                                'Endpoint = gw.example.invalid:51820\n'
                                'AllowedIPs = 0.0.0.0/0\n', '203.0.113.42')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('awgendpoint 203.0.113.42 51820', self.config_call(groups),
                      'ifconfig must be called with the resolved address')
        resolver = [args for m, args in groups if m == 'GETENT']
        self.assertEqual(resolver, [['hosts', 'gw.example.invalid']],
                         'the name is resolved once, by getent')

    def test_e_resolution_is_not_cached_between_runs(self):
        text = ('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nAddress = 10.0.0.1/24\nMTU = 1420\n'  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                '[Peer]\nPublicKey = pub\nEndpoint = gw.example.invalid:51820\n'
                'AllowedIPs = 0.0.0.0/0\n')
        first, first_groups = self.run_up(text, '203.0.113.42')
        second, second_groups = self.run_up(text, '198.51.100.7')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('awgendpoint 203.0.113.42 51820', self.config_call(first_groups))
        self.assertIn('awgendpoint 198.51.100.7 51820', self.config_call(second_groups),
                      'the second run resolves its own endpoint (no cache)')

    def test_f_external_route_monitor_hangs_and_the_shim_does_not(self):
        """Negative control: the blocking monitor hangs, the shim finishes.

        Both runs use the same production cmd_up and the same resolved
        endpoint, so the only difference is the route(8) stand-in. The
        external monitor (like route(8) on OpenBSD) keeps reading, so the
        run needs a bounded process-group kill; the shim exits, so the
        backgrounded monitor finishes.
        """
        text = ('[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nAddress = 10.0.0.1/24\nMTU = 1420\n'  # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
                '[Peer]\nPublicKey = pub\nEndpoint = gw.example.invalid:51820\n'
                'AllowedIPs = 0.0.0.0/0\n')
        pre, pre_groups = self.run_up(text, '203.0.113.42', monitor='external',
                                      timeout=2.0)
        self.assertTrue(pre.timed_out,
                        'an external route monitor must block the run')
        self.assertIn('Backgrounding route monitor', pre.stderr,
                      'the hang must happen after the monitor is backgrounded')
        self.assertIn('awgendpoint 203.0.113.42 51820', self.config_call(pre_groups),
                      'the endpoint is programmed before the monitor blocks')
        self.assertIn(['awg9', 'up'], self.ifconfig_calls(pre_groups),
                      'cmd_up reaches up before the monitor blocks')
        post, post_groups = self.run_up(text, '203.0.113.42', monitor='bounded',
                                        timeout=2.0)
        self.assertFalse(post.timed_out, 'the shim monitor exits, so the run ends')
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertIn(['-n', 'monitor'],
                      [args for m, args in post_groups if m == 'ROUTE'],
                      'the monitor is the recorded `route -n monitor` call')
        self.assertIn('awgendpoint 203.0.113.42 51820', self.config_call(post_groups),
                      'the fixed fixture keeps the same programmed endpoint')


if __name__ == '__main__':
    unittest.main()
