#!/bin/sh
# Reconfiguration stress: change AWG parameters while traffic flows, then prove
# the interface still works. Run as root inside the lab VM:
#
#   doas sh tools/lab/stress-test.sh [iterations]
#
# This is the test that catches state bugs in the ioctl paths: a version switch
# frees and replaces sets of parameters, and the packet path reads them without
# a lock. It is bounded (default 200 iterations, a few seconds) so it can run in
# the normal gate rather than only overnight.
set -u

N="${1:-200}"
PORT="${AWG_TEST_PORT:-51998}"
KEY0='AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='	# scan:allow-fixture[sha1:de49f82911b9d304f1518e8855db8f9cab08b006]
PEER1='AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI='	# scan:allow-fixture[sha1:35e3bbf9261d562486ce87cec758ee0e03293c36]
FAILED=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

ifconfig awg0 >/dev/null 2>&1 && ifconfig awg0 destroy
ifconfig awg0 create || { echo "cannot create awg0"; exit 1; }
ifconfig awg0 awgkey "$KEY0"
ifconfig awg0 awgport "$PORT" up || { echo "cannot configure awg0"; exit 1; }

echo "== 1. version switch while a peer and traffic exist"
ifconfig awg0 awgpeer "$PEER1" awgaip 10.9.9.2/32 >/dev/null 2>&1 || \
    fail "peer creation"
i=0
while [ "$i" -lt "$N" ]; do
	# Alternate the version, which drops every 3.1 parameter each time.
	ifconfig awg0 awgversion 3.1 >/dev/null 2>&1 || fail "switch to 3.1 (#$i)"
	ifconfig awg0 awgs3 18 awgs4 24 >/dev/null 2>&1 || fail "s3/s4 (#$i)"
	ifconfig awg0 awgversion legacy >/dev/null 2>&1 || fail "switch to legacy (#$i)"
	i=$((i + 1))
done
pass "$N version switches survived"

echo "== 2. parameter churn while packets arrive"
(
	i=0
	while [ "$i" -lt "$N" ]; do
		ifconfig awg0 awgs1 $((i % 1281)) >/dev/null 2>&1
		ifconfig awg0 awgjc $((i % 65)) awgjmin 10 awgjmax 50 >/dev/null 2>&1
		i=$((i + 1))
	done
) &
churn=$!
i=0
while [ "$i" -lt 40 ]; do
	dd if=/dev/urandom bs=256 count=1 2>/dev/null | \
	    nc -u -w 0 127.0.0.1 "$PORT" >/dev/null 2>&1
	i=$((i + 1))
done
wait "$churn"
pass "parameter churn with traffic survived"

echo "== 3. peer add/remove churn"
i=0
while [ "$i" -lt 40 ]; do
	ifconfig awg0 awgpeer "$PEER1" awgaip "10.9.$((i % 250)).2/32" >/dev/null 2>&1
	ifconfig awg0 -awgaip "10.9.$((i % 250)).2/32" >/dev/null 2>&1
	i=$((i + 1))
done
pass "peer and allowed-ip churn survived"

echo "== 4. the interface still works afterwards"
if ifconfig awg0 awgport $((PORT + 1)) >/dev/null 2>&1; then
	pass "port change accepted"
else
	fail "port change rejected"
fi
if ifconfig awg0 | grep -q awgversion; then
	pass "configuration still readable"
else
	fail "configuration unreadable"
fi
ifconfig awg0 destroy >/dev/null 2>&1 && pass "teardown" || fail "teardown"

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all cases passed"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
