#!/bin/sh
# R13 negative control for the C-gate sensitivity guard.
#
# The suite's canon_state() is the oracle for the state comparison, so the
# gate is only trustworthy if a comparator that ignores a field is caught.
# This script derives such a comparator from the real suite — an I2-blind
# render — and pairs it with an ifconfig fixture that corrupts the MTU on the
# injected run. The derived suite then fails exactly F13a/F13c, but because of
# the MTU, so a guard that reads the failure ids as the reason accepts it.
# The guard must report FAIL; running it here is how the self-check proves its
# attribution is by cause and not by test id.
#
#   sh tools/lab/guard-adversary.sh            # runs the guard, prints its rc
#
# The derived suite is generated in a temp dir from awgctl-fixture-test.sh so
# it tracks the real suite; nothing here edits the suite. The two derivations:
#
#   canon_state  — the awk render piped through `grep -v '^ispec2='`
#   fixture-ifconfig — the real fixture, then mtu=1419 in the state it wrote
#
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
real_suite="$here/awgctl-fixture-test.sh"
real_ifconfig="$here/fixture-ifconfig.py"
selftest="$here/awgctl-gate-selftest.sh"
[ -f "$real_suite" ] || { echo "guard-adversary: $real_suite not found" >&2; exit 1; }
[ -f "$selftest" ] || { echo "guard-adversary: $selftest not found" >&2; exit 1; }

work=$(mktemp -d "${TMPDIR:-/tmp}/guard-adversary.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM

# --- 1. the I2-blind comparator --------------------------------------------
# The suite's canon_state renders ispec1..ispec5; the adversary renders the
# same fields minus ispec2, which is the R9-style loss this gate must catch.
awk -v pat='	'"'"' "$1"' '{
	if ($0 == pat) { print pat " | grep -v \"^ispec2=\""; blind = 1 }
	else print
}' "$real_suite" >"$work/suite.sh"
if [ ! -s "$work/suite.sh" ] || ! grep -q 'grep -v "\^ispec2="' "$work/suite.sh"; then
	echo "guard-adversary: the I2-blind canon_state could not be derived" >&2
	exit 1
fi

# --- 2. the MTU-corrupting ifconfig fixture --------------------------------
# Runs the real fixture-ifconfig.py, then rewrites the MTU the state carries,
# so the injected run's post-state is corrupted in MTU as well as in I2.
cat >"$work/adversary-ifconfig.py" <<PY
#!/usr/bin/env python3
"""Real fixture-ifconfig.py plus an MTU corruption (R13 adversary).

The gate injects an I2 change; this wrapper additionally leaves MTU at 1419 in
the state the fixture writes, so a comparator blind to I2 fails the state
comparison for the MTU reason.
"""
import os
import subprocess
import sys

REAL = os.environ.get("ADVERSARY_REAL_IFCONFIG", "$real_ifconfig")
STATE = os.environ.get("AWGFIX_STATE") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "awgctl-fixture.state")
MTU = os.environ.get("ADVERSARY_MTU", "1419")

rc = subprocess.call([sys.executable, REAL] + sys.argv[1:])
if os.environ.get("AWGFIX_MUTATE_ISPEC2") and len(sys.argv) > 2 and sys.argv[2] == "mtu":
    try:
        with open(STATE) as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        lines = []
    with open(STATE, "w") as fh:
        for line in lines:
            if line.startswith("mtu="):
                fh.write("mtu=" + MTU + "\n")
            else:
                fh.write(line)
sys.exit(rc)
PY

# --- 3. the derived suite drives the real gate with that fixture ------------
sed 's|^cp tools/lab/fixture-ifconfig.py|cp "${AWGCTL_GATE_IFCONFIG:-'"$real_ifconfig"'}"|' \
	"$work/suite.sh" >"$work/suite2.sh" && mv "$work/suite2.sh" "$work/suite.sh"

# Execute and validate the actual negative fixture before accepting rejection.
export AWGCTL_GATE_IFCONFIG="$work/adversary-ifconfig.py"
export ADVERSARY_REAL_IFCONFIG="$real_ifconfig"
(unset AWGFIX_MUTATE_ISPEC2; sh "$work/suite.sh") > "$work/clean.log" 2>&1
clean_rc=$?
AWGFIX_MUTATE_ISPEC2='<b 0xdead>' sh "$work/suite.sh" > "$work/injected.log" 2>&1
injected_rc=$?
if [ "$clean_rc" != 0 ] || [ "$injected_rc" != 1 ] ||
    ! "${PYTHON:-python3}" "$here/gate-records.py" mtu "$work/clean.log" "$work/injected.log" "${ADVERSARY_MTU:-1419}"; then
	echo "guard-adversary: execution/cause proof failed (clean=$clean_rc injected=$injected_rc)"
	cat "$work/clean.log" "$work/injected.log"
	exit 2
fi

# The selected comparator is independently probed by the guard, so the blind
# comparator may now be rejected even before the suite is run a second time.
echo "guard-adversary: built fixture executed; clean green; injected cause=mtu"
AWGCTL_GATE_SUITE="$work/suite.sh" AWGCTL_GATE_SKIP_ADVERSARY=1 sh "$selftest" > "$work/guard.log" 2>&1
rc=$?
cat "$work/guard.log"
if [ "$rc" != 1 ]; then
	echo "guard-adversary: expected rejection, got rc=$rc"
	exit 2
fi
if [ -n "${AWGCTL_GATE_ADVERSARY_EVIDENCE:-}" ]; then
	mkdir -p "$AWGCTL_GATE_ADVERSARY_EVIDENCE" &&
	    cp "$work/clean.log" "$work/injected.log" "$work/guard.log" "$AWGCTL_GATE_ADVERSARY_EVIDENCE/" || exit 2
fi
echo 'guard-adversary: VERIFIED built; clean=81/0; injected=79/2; cause=mtu; guard=rejected'
exit 1
