#!/bin/sh
# Wire-level robustness check: throw malformed datagrams at a listening awg(4)
# interface and assert the kernel survives and the interface still works.
#
#   doas sh tools/lab/wire-test.sh
#
# Unlike smoke-test.sh this does not need a peer: it exercises the receive path
# with packets that must be dropped without touching state. A panic here is the
# failure this script exists to catch; the serial log is where it shows up.
set -u

PORT="${AWG_TEST_PORT:-51999}"
FAILED=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

alive() {		# alive <description>
	if ifconfig awg0 >/dev/null 2>&1; then
		pass "$1 (interface still usable)"
	else
		fail "$1 (interface gone — kernel or driver died)"
	fi
}

send() {		# send <description> <bytes>
	desc=$1; n=$2
	dd if=/dev/urandom bs=1 count="$n" 2>/dev/null | \
	    nc -u -w 1 127.0.0.1 "$PORT" >/dev/null 2>&1
	alive "$desc"
}

ifconfig awg0 >/dev/null 2>&1 && ifconfig awg0 destroy
ifconfig awg0 create || { echo "cannot create awg0 — kernel has no awg(4)"; exit 1; }
# A fixed key so the interface is fully initialised; value is a test fixture.
ifconfig awg0 awgkey AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE= # scan:allow-fixture
ifconfig awg0 awgport "$PORT" up || { echo "cannot configure awg0"; exit 1; }

echo "== empty and very short datagrams"
send "zero-length" 0
send "one byte" 1
send "half a header" 8

echo "== unstructured noise"
send "148 bytes of noise (one packet size)" 148
send "1500 bytes of noise" 1500
send "65000 bytes of noise" 65000

echo "== structured but wrong"
# Type 1 initiation with a garbage body: must fail authentication, not panic.
dd if=/dev/zero bs=1 count=148 2>/dev/null | \
    { printf '\001\000\000\000'; cat; } | \
    nc -u -w 1 127.0.0.1 "$PORT" >/dev/null 2>&1
alive "initiation-shaped packet with a zero MAC"

# 3.1 prefix-only packet: S4 bytes and nothing else.
if ifconfig awg0 awgversion 3.1 >/dev/null 2>&1; then
	if ifconfig awg0 awgs4 24 >/dev/null 2>&1; then
		send "S4 prefix with no payload" 24
	else
		echo "SKIP: awgs4 not accepted"
	fi
	ifconfig awg0 awgversion legacy >/dev/null 2>&1
else
	echo "SKIP: 3.1 parameters not accepted (legacy-only kernel)"
fi

echo "== handshake still possible afterwards"
# The interface must still accept a port change and a version switch.
if ifconfig awg0 awgport $((PORT + 1)) >/dev/null 2>&1; then
	pass "port change accepted after the flood"
else
	fail "port change rejected after the flood"
fi

ifconfig awg0 destroy >/dev/null 2>&1 && pass "teardown after the flood" || \
    fail "teardown after the flood"

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all cases survived"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
