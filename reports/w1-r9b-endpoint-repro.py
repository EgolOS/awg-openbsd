#!/usr/bin/env python3
"""R9 repro: an unresolvable Endpoint leaves the interface created.

Runs the REAL sbin/awg-quick preparation path (same harness as
tools/tests/test_quick_up.py) against a config whose Endpoint is a name that
does not resolve, with ifconfig/route/netstat/getent mocked so every call is
recorded without touching a real interface. Prints the exit code, stderr and
the recorded call sequence: the reproduction is that the create/MTU/address
calls happen even though the endpoint name never resolves.

Usage: w1-r9b-endpoint-repro.py [ENDPOINT]   (default awg.example.invalid:51820)
"""
import os
import sys
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# BSD stat adapter (Linux host runs the same logic; the VM runs the real stat).
STAT_ADAPTER = '''
stat() {
 case "$2" in
 %Lp) printf '600\\n' ;;
 %u) printf '%s\\n' "$MOCK_OWNER" ;;
 *) return 1 ;;
 esac
}
'''

# Mock ifconfig/route/netstat/getent: every call is recorded in $CALLS, one
# argument per line, groups separated by a marker. The resolver answers from
# $RESOLVE (name=address lines); a name with no entry answers "not found".
MOCKS = '''
getent() {
 printf 'GETENT\\n' >> "$CALLS"
 printf 'ARG:%s\\n' "$1" >> "$CALLS"
 local entry
 entry="$(sed -n -E "s/^$2=//p" "$RESOLVE" | head -n1)"
 [[ -n "$entry" ]] || { printf 'ARG:%s\\n' "$2" >> "$CALLS"; return 2; }
 printf '%s %s\\n' "$entry" "$2"
 printf 'ARG:%s\\n' "$2" >> "$CALLS"
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
route() {
 printf 'ROUTE\\n' >> "$CALLS"
 printf 'interface: em0\\nmtu: 1500\\n'
 return 0
}
'''


def main():
    endpoint = sys.argv[1] if len(sys.argv) > 1 else 'awg.example.invalid:51820'
    text = ('[Interface]\nPrivateKey = dummy\nAddress = 10.0.0.1/24\n'
            'MTU = 1420\n[Peer]\nPublicKey = pub\nEndpoint = %s\n'
            'AllowedIPs = 0.0.0.0/0\n' % endpoint)
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        config = work / 'awg9.conf'
        config.write_text(text)
        config.chmod(0o600)
        calls = work / 'CALLS'
        calls.write_text('')
        resolve = work / 'RESOLVE'
        resolve.write_text('')          # nothing resolves
        source = (ROOT / 'sbin/awg-quick').read_text().split('\nif [[ $# -eq 1 &&')[0]
        driver = work / 'driver.bash'
        driver.write_text(source + STAT_ADAPTER + 'parse_options "$1"\n' + MOCKS + 'cmd_up\n')
        env = dict(os.environ, MOCK_OWNER='0', CALLS=str(calls),
                   SEEN=str(work / 'seen'), RESOLVE=str(resolve))
        r = subprocess.run(['bash', str(driver), str(config)], cwd=work,
                           env=env, capture_output=True, text=True)
        print('EXIT', r.returncode)
        print('--- stderr ---')
        print(r.stderr, end='')
        print('--- recorded calls ---')
        # Render the recorded log: each group starts with its marker line.
        for line in calls.read_text().splitlines():
            if line in ('IFC', 'ROUTE', 'GETENT'):
                print()
                print(line, end='')
            else:
                print(' ' + line[4:], end='')
        print()


main()
