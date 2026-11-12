#!/bin/sh
# R12b/R13 oracle controls — C1..C14 for canon_state() in awgctl-fixture-test.sh.
#
#   sh tools/lab/oracle-controls.sh
#
# Each case renders one or two state files through canon_state and compares
# the canonical text against the required verdict (equal / different, an
# exact render, or one list shared by all three models). The function is
# extracted from the suite so the control exercises the exact comparator the
# gate uses. C11..C13 additionally run the two fixture observers — the C
# getifaddrs stub (through tools/lab/fixture-probe.c) and the Python
# ifconfig writer — on the same state, so a schema, duplicate-rule or
# projection change that splits the observers is caught here, not only in
# review. Prints PASS/FAIL per case and exits non-zero when any is wrong.
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
eval "$(sed -n '/^canon_state() {/,/^}/p' "$here/awgctl-fixture-test.sh")"
command -v canon_state >/dev/null 2>&1 || {
	echo "FAIL: canon_state() not found in awgctl-fixture-test.sh" >&2
	exit 1
}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/oracle-controls.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
CC=${CC:-cc}
PYTHON=${PYTHON:-python3}
PROBE="$WORK/fixture-probe"
if ! $CC -DAWGCTL_LINUX -I"$here/../../src" -I"$here/../../sbin/awgctl" \
    -o "$PROBE" "$here/fixture-probe.c" "$here/fixture-stubs.c" -lutil \
    2>"$WORK/probe-cc.log"; then
	echo "FAIL: fixture-probe build failed" >&2
	cat "$WORK/probe-cc.log" >&2
	exit 1
fi

FAILED=0
# check <case> <equal|different> <state-a> <state-b>
check() {
	case_id=$1 want=$2 sa=$3 sb=$4
	printf '%s\n' "$sa" > "$WORK/a"
	printf '%s\n' "$sb" > "$WORK/b"
	ca=$(canon_state "$WORK/a")
	cb=$(canon_state "$WORK/b")
	if [ "$want" = equal ]; then
		[ "$ca" = "$cb" ] && { echo "PASS: $case_id"; return; }
		echo "FAIL: $case_id (expected equal)"
	else
		[ "$ca" != "$cb" ] && { echo "PASS: $case_id"; return; }
		echo "FAIL: $case_id (expected different)"
	fi
	echo "  A: $(printf '%s' "$ca" | tr '\n' ';')" >&2
	echo "  B: $(printf '%s' "$cb" | tr '\n' ';')" >&2
	FAILED=$((FAILED + 1))
}

# group <token-list> — family grouping with intra-family order kept, the same
# normalisation canon_state applies. The C observer serves the mirrored
# primary before the aliases, so its raw order is not the canonical one; the
# family order is not a real distinction (C8), so the observers are compared
# through this grouping.
group() {
	awk '{
		n = split($0, tok, " ")
		for (i = 1; i <= n; i++) {
			t = tok[i]
			if (t == "") continue
			c = index(t, ":")
			fm = (c > 0) ? substr(t, 1, c - 1) : "0"
			cnt[fm]++
			grp[fm, cnt[fm]] = t
		}
		m = 0
		for (fm in cnt) keys[++m] = fm
		for (i = 1; i < m; i++)
			for (j = i + 1; j <= m; j++)
				if (keys[j] + 0 < keys[i] + 0) { t = keys[i]; keys[i] = keys[j]; keys[j] = t }
		out = ""
		for (i = 1; i <= m; i++) {
			fm = keys[i]
			for (j = 1; j <= cnt[fm]; j++)
				out = out (out == "" ? "" : " ") grp[fm, j]
		}
		print out
	}'
}

# check_lists <case> <state> — the C observer (getifaddrs), the Python writer
# (fixture-ifconfig.py) and the canonical render must agree on the effective
# address list: same count, same addresses.
check_lists() {
	case_id=$1 sa=$2
	printf '%s\n' "$sa" > "$WORK/a"
	cp "$WORK/a" "$WORK/py"
	c_list=$(AWGFIX_STATE="$WORK/a" AWGFIX_ROUTES="$WORK/a.routes" \
	    "$PROBE" | tr '\n' ' ')
	c_list=$(printf '%s' "$c_list" | group)
	AWGFIX_STATE="$WORK/py" AWGFIX_ROUTES="$WORK/py.routes" \
	    "$PYTHON" "$here/fixture-ifconfig.py" awg9 up >/dev/null 2>&1
	py_list=$(grep '^addrs=' "$WORK/py" | head -1 | cut -d= -f2- | group)
	can_list=$(canon_state "$WORK/a" "family mtu addrs" | sed -n 's/^addrs=//p' | group)
	if [ "$c_list" = "$py_list" ] && [ "$c_list" = "$can_list" ]; then
		echo "PASS: $case_id (all three render: $c_list)"
		return
	fi
	echo "FAIL: $case_id (the observers and the model disagree)"
	echo "  C observer: $c_list" >&2
	echo "  Py writer : $py_list" >&2
	echo "  canonical : $can_list" >&2
	FAILED=$((FAILED + 1))
}

# check_render <case> <expected-text> <state> <fields> — the projection must
# render exactly the expected lines, in order.
check_render() {
	case_id=$1 want=$2 sa=$3 fields=$4
	printf '%s\n' "$sa" > "$WORK/a"
	got=$(canon_state "$WORK/a" "$fields")
	if [ "$got" = "$want" ]; then
		echo "PASS: $case_id"
		return
	fi
	echo "FAIL: $case_id (render is not the expected projection)"
	printf '%s\n' "$want" | sed 's/^/  want: /' >&2
	printf '%s\n' "$got" | sed 's/^/  got:  /' >&2
	FAILED=$((FAILED + 1))
}

# C1: legacy representation — addrs absent, the aliases list carries the
# address the C observer serves; a changed alias must be visible.
check C1 different \
"family=10
addr=fd00::1
prefix=128
aliases=10:fd00::1/64" \
"family=10
addr=fd00::1
prefix=128
aliases=10:fd00::2/64"

# C2: addrs absent (mirrored primary) vs present-empty (cleared interface).
check C2 different \
"family=2
addr=192.0.2.1
prefix=24" \
"addrs="

# C3: the same effective list, materialised (addrs) vs legacy
# (primary + aliases) — the two representations must agree.
check C3 equal \
"addrs=10:fd00::1/128 2:10.0.0.1/24
family=10
addr=fd00::1
prefix=128" \
"family=10
addr=fd00::1
prefix=128
aliases=2:10.0.0.1/24"

# C4: field order shuffled, same values.
check C4 equal \
"flags=4194270
port=51820
version=1
peers=1
mtu=1420
addrs=10:fd00::1/128
up=1
ispec1=<b 0xc700000001><r 16><t>" \
"up=1
addrs=10:fd00::1/128
peers=1
version=1
port=51820
flags=4194270
mtu=1420
ispec1=<b 0xc700000001><r 16><t>"

# C5: same addresses, order swapped within one family.
check C5 different \
"addrs=10:fd00::1/128 10:fd00::2/64" \
"addrs=10:fd00::2/64 10:fd00::1/128"

# C6: ispec2 absent vs ispec2 empty (the C reader zero-initialises).
check C6 equal \
"ispec1=<b 0xc700000001><r 16><t>" \
"ispec1=<b 0xc700000001><r 16><t>
ispec2="

# C7: a non-empty I2 that changes.
check C7 different \
"ispec2=<b 0x01>" \
"ispec2=<b 0x02>"

# C8: families grouped — the same list in another family order.
check C8 equal \
"addrs=10:fd00::1/128 2:10.0.0.1/24" \
"addrs=2:10.0.0.1/24 10:fd00::1/128"

# C9: R13 projection — a caller-supplied field list renders exactly those
# fields and nothing else (no I-spec appended).
check_render C9 "family=10
mtu=1420
addr=fd00::1
prefix=128" \
"family=10
addr=fd00::1
prefix=128
mtu=1420
ispec1=<b 0xc700000001><r 16><t>
ispec3=<b 0x07>" "family mtu addr prefix"

# C10: the default render (no projection) still carries every I-spec.
check_render C10 "flags=
port=
version=
peers=
tx=
rx=
handshake=
family=10
mtu=1420
addrs=10:fd00::1/128
up=
addr=fd00::1
prefix=128
ispec1=<b 0xc700000001><r 16><t>
ispec2=
ispec3=
ispec4=
ispec5=" \
"family=10
addr=fd00::1
prefix=128
mtu=1420
ispec1=<b 0xc700000001><r 16><t>" ""

# C11: R13 schema — a legacy alias token with no family prefix is ignored by
# the C observer (no colon) and by the Python writer (empty address), so the
# model must ignore it too and all three must render one list.
check_lists C11 "family=10
addr=fd00::1
prefix=128
mtu=1420
aliases=10:fd00::2/64 10.9.9.9/32"

# C12: R13 duplicates — the aliases repeat the mirrored primary. One rule for
# all three models: the first entry of a (family, address) pair is kept and
# the prefix of the repeat is ignored, so the observer reports exactly the
# addresses the canonical text shows.
check_lists C12 "family=10
addr=fd00::1
prefix=128
mtu=1420
aliases=10:fd00::1/64 10:fd00::1/128"

# C13: R13 duplicates — a repeated non-primary alias. The same rule collapses
# it, so the C observer, the Python writer and the canonical render agree.
check_lists C13 "family=10
addr=fd00::1
prefix=128
mtu=1420
aliases=2:10.0.0.1/24 2:10.0.0.1/24 10:fd00::2/64"

# R14: family tags must normalize in entries/persistence, not only dedup keys.
# Both materialized and legacy inputs must also support a real family clear.
check C15 equal "addrs=0:fd00::1/128" "addrs=10:fd00::1/128"
check_lists C15 "family=10
addr=fd00::1
prefix=128
addrs=0:fd00::1/128 10:fd00::1/64 2:10.0.0.1/24"
check_lists C16 "family=10
addr=fd00::1
prefix=128
aliases=0:fd00::2/64 2:10.0.0.1/24"
check_clear() {
	case_id=$1 state=$2 sub=$3 expected=$4
	printf '%s\n' "$state" > "$WORK/clear"
	if ! AWGFIX_STATE="$WORK/clear" AWGFIX_ROUTES="$WORK/clear.routes" \
	    "$PYTHON" "$here/fixture-ifconfig.py" awg9 "$sub"; then
		echo "FAIL: $case_id (clear command failed)"
		FAILED=$((FAILED + 1))
		return
	fi
	observed=$(AWGFIX_STATE="$WORK/clear" "$PROBE" | tr '\n' ' ' | group)
	persisted=$(sed -n 's/^addrs=//p' "$WORK/clear" | group)
	modeled=$(canon_state "$WORK/clear" addrs | sed -n 's/^addrs=//p' | group)
	if [ "$observed" = "$expected" ] && [ "$persisted" = "$expected" ] && [ "$modeled" = "$expected" ]; then
		echo "PASS: $case_id (actual $sub; all three: $expected)"
	else
		echo "FAIL: $case_id (clear retained wrong addresses: C=$observed Py=$persisted oracle=$modeled)"
		FAILED=$((FAILED + 1))
	fi
}
check_clear C17 "addrs=0:fd00::1/128 2:10.0.0.1/24" -inet6 '2:10.0.0.1/24'
check_clear C18 "family=10
addr=fd00::1
prefix=128
aliases=0:fd00::2/64 2:10.0.0.1/24" -inet6 '2:10.0.0.1/24'
check_clear C19 "addrs=0:fd00::1/128 2:10.0.0.1/24" -inet '10:fd00::1/128'
# A legacy mirrored family=0 must remain equal after Python persists family=10.
check C22 equal "family=0
addr=fd00::1
prefix=128" "family=10
addr=fd00::1
prefix=128"
check_lists C23 "family=0
addr=fd00::1
prefix=128
aliases=0:fd00::2/64"
check_clear C24 "family=0
addr=fd00::1
prefix=128
aliases=0:fd00::2/64" -inet6 ''

# C14: R13 address-list oracle — a failed-UP recovery that loses the secondary
# address must fail the suite and name the address it lost. The suite runs
# with AWGFIX_UP_DROP (the fixture's `up` writes the list without it), so a
# restore that re-installs the list and then fails the `up` leaves the loss.
c14_rc=0
AWGFIX_UP_DROP=fd00::2 AWGFIX_ROUTES="$WORK/c14.routes" \
    sh "$here/awgctl-fixture-test.sh" >"$WORK/c14.log" 2>&1
c14_rc=$?
lost_secondary() {
	"$PYTHON" "$here/gate-records.py" loss "$1"
}
c14_line=$(grep '^FAIL: F13c' "$WORK/c14.log" | head -1)
if [ "$c14_rc" = 1 ] && lost_secondary "$WORK/c14.log"; then
	echo "PASS: C14 (suite rc=$c14_rc, failure names 10:fd00::2/64)"
else
	echo "FAIL: C14 (suite rc=$c14_rc, F13c line: ${c14_line:-none})"
	sed -n '1,3p' "$WORK/c14.log" >&2
	FAILED=$((FAILED + 1))
fi

# C20: this real suite fails only F13c for MTU, keeping both addresses.
# C14 must reject that evidence, even though the PRE half names the secondary.
cat > "$WORK/wrong-mtu.py" <<PY
#!/usr/bin/env python3
import os
import json
from pathlib import Path
import subprocess
import sys
rc = subprocess.call([sys.executable, "$here/fixture-ifconfig.py"] + sys.argv[1:])
if len(sys.argv) > 2 and sys.argv[2] == "up" and os.environ.get("AWGFIX_UP_ERR"):
    state = Path(os.environ["AWGFIX_STATE"])
    if os.environ.get("CONTROL_COUNTER_RECEIPT"):
        before = dict(line.split("=", 1) for line in state.read_text().splitlines())
        after = dict(before)
        for key in ("tx", "rx", "handshake"):
            after[key] = str(int(before[key]) + 1)
        state.write_text("".join(key + "=" + value + "\\n" for key, value in after.items()))
        Path(os.environ["CONTROL_COUNTER_RECEIPT"]).write_text(json.dumps([before, after]))
    else:
        state.write_text(state.read_text().replace("mtu=1420\\n", "mtu=1419\\n"))
sys.exit(rc)
PY
AWGCTL_GATE_IFCONFIG="$WORK/wrong-mtu.py" AWGFIX_ROUTES="$WORK/c20.routes" \
    sh "$here/awgctl-fixture-test.sh" > "$WORK/c20.log" 2>&1
c20_rc=$?
if [ "$c20_rc" = 1 ] && "$PYTHON" "$here/gate-records.py" loss-mtu "$WORK/c20.log" &&
    ! lost_secondary "$WORK/c20.log"; then
	echo 'PASS: C20 (C14 rejects executed F13c MTU-only failure)'
else
	echo 'FAIL: C20 (C14 accepted wrong-MTU evidence or control did not execute correctly)'
	FAILED=$((FAILED + 1))
fi

# C21: mutate real persisted counters only; the F13c projection stays green.
CONTROL_COUNTER_RECEIPT="$WORK/counters.json" AWGCTL_GATE_IFCONFIG="$WORK/wrong-mtu.py" \
    AWGFIX_ROUTES="$WORK/c21.routes" sh "$here/awgctl-fixture-test.sh" > "$WORK/c21.log" 2>&1
c21_rc=$?
if [ "$c21_rc" = 0 ] && "$PYTHON" "$here/gate-records.py" counters "$WORK/counters.json" "$WORK/c21.log"; then
	echo "PASS: C21 (actual counter-only mutation; suite $(grep -c '^PASS:' "$WORK/c21.log") PASS / 0 FAIL)"
else
	echo 'FAIL: C21 (counter-only mutation was rejected or did not execute)'
	FAILED=$((FAILED + 1))
fi

if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all oracle controls passed"
	exit 0
fi
echo "RESULT: $FAILED oracle control(s) failed"
exit 1
