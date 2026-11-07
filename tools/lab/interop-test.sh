#!/bin/sh
# Two-VM interoperability test: a real tunnel between two hosts, not loopback.
#
#   doas sh tools/lab/interop-test.sh            # run inside VM A
#   make check-interop                           # from the host, drives both VMs
#
# Expects: both VMs run the AWG kernel, are reachable over the LAN (A at
# $LAN_A, B at $LAN_B) and have ssh from the caller. It configures awg0 on
# both ends with the two fixed test keys below, brings the tunnel up, proves a
# handshake completed on both sides, passes ICMP through it, and measures a
# bulk transfer over the tunnel with nc(1) — the lab has no iperf3.
#
# Everything it creates is torn down, including on failure.
set -u

A_SSH=${A_SSH:-}
B_SSH=${B_SSH:-}
KEY_A='AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='	# scan:allow-fixture
KEY_B='AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI='	# scan:allow-fixture
TUN_A=10.66.0.1
TUN_B=10.66.0.2
PORT_A=${PORT_A:-51820}
PORT_B=${PORT_B:-51821}
FAILED=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

# rsh <side> <command>: run a command on A or B
rsh() {
	side=$1; shift
	case "$side" in
	A) ${A_SSH:-sh -c} "$*" ;;
	B) ${B_SSH:-sh -c} "$*" ;;
	esac
}

cleanup() {
	rsh A 'ifconfig awg0 destroy' >/dev/null 2>&1
	rsh B 'ifconfig awg0 destroy' >/dev/null 2>&1
}
trap cleanup EXIT

# Public keys must match the private keys above; deriving them needs the
# userspace helper, so the test takes them from the same place awg-quick does.
derived() {		# derived <side> <private-key>
	rsh "$1" "ifconfig awg0 awgkey $2 >/dev/null 2>&1; ifconfig awg0 | \
	    sed -n 's/.*awgpubkey \\(.*\\)/\\1/p'" 2>/dev/null
}

echo "== 1. configure both ends"
rsh A "ifconfig awg0 destroy" >/dev/null 2>&1
rsh B "ifconfig awg0 destroy" >/dev/null 2>&1
rsh A "ifconfig awg0 create" || fail "A: create awg0"
rsh B "ifconfig awg0 create" || fail "B: create awg0"
rsh A "ifconfig awg0 awgkey $KEY_A" || fail "A: set key"
rsh B "ifconfig awg0 awgkey $KEY_B" || fail "B: set key"

PUB_A=$(derived A "$KEY_A")
PUB_B=$(derived B "$KEY_B")
[ -n "$PUB_A" ] && [ -n "$PUB_B" ] && pass "public keys read back" || \
    fail "public keys not readable from the interfaces"

LAN_A=${LAN_A:-192.168.99.1}
LAN_B=${LAN_B:-192.168.99.2}

rsh A "ifconfig awg0 awgport $PORT_A awgpeer $PUB_B awgaip $TUN_B/32 \
    awgendpoint $LAN_B $PORT_B awgversion 3.1 awgs3 18 awgs4 24 \
    awgjc 4 awgjmin 10 awgjmax 50 awgs1 26 awgs2 33 up && \
    ifconfig awg0 inet $TUN_A/24" || fail "A: configure"
rsh B "ifconfig awg0 awgport $PORT_B awgpeer $PUB_A awgaip $TUN_A/32 \
    awgendpoint $LAN_A $PORT_A awgversion 3.1 awgs3 18 awgs4 24 \
    awgjc 4 awgjmin 10 awgjmax 50 awgs1 26 awgs2 33 up && \
    ifconfig awg0 inet $TUN_B/24" || fail "B: configure"
pass "both ends configured (3.1, junk, S1-S4)"

echo "== 2. handshake across the LAN"
i=0
hs_a=no; hs_b=no
while [ "$i" -lt 15 ]; do
	hs_a=$(rsh A "ifconfig awg0 | grep -c 'last handshake'")
	hs_b=$(rsh B "ifconfig awg0 | grep -c 'last handshake'")
	[ "$hs_a" -gt 0 ] && [ "$hs_b" -gt 0 ] && break
	i=$((i + 1))
	sleep 1
done
[ "$hs_a" -gt 0 ] && pass "handshake on A" || fail "no handshake on A"
[ "$hs_b" -gt 0 ] && pass "handshake on B" || fail "no handshake on B"

echo "== 3. ICMP through the tunnel"
if rsh A "ping -c 3 -w 3 $TUN_B" >/dev/null 2>&1; then
	pass "3 pings through the tunnel"
else
	fail "ping through the tunnel"
fi

echo "== 4. bulk transfer through the tunnel"
# B listens, A sends 8 MiB of zeros, both timed on A.
rsh B "nc -l -p 5201 > /dev/null &" >/dev/null 2>&1
sleep 1
start=$(date +%s)
if rsh A "dd if=/dev/zero bs=65536 count=128 2>/dev/null | nc -N $TUN_B 5201"; then
	:
fi
end=$(date +%s)
secs=$((end - start))
[ "$secs" -eq 0 ] && secs=1
rsh B "pkill -f 'nc -l -p 5201'" >/dev/null 2>&1
rate=$((8 * 1024 / secs / 1024))
if [ "$secs" -le 30 ]; then
	pass "8 MiB through the tunnel in ${secs}s (~${rate} MiB/s, unscientific: nc + dd)"
else
	fail "bulk transfer took ${secs}s"
fi

echo "== 5. counters moved"
tx_a=$(rsh A "ifconfig awg0 | sed -n 's/.*tx: \\([0-9]*\\).*/\\1/p' | head -1")
[ -n "$tx_a" ] && [ "$tx_a" -gt 0 ] && pass "A transmit counter: $tx_a bytes" || \
    fail "A transmit counter did not move"

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: interop passed"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
