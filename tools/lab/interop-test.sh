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
# This script configures and destroys lab interfaces. Never infer consent
# from an SSH target or run destructive tests on a general-purpose server.
[ "${AWG_LAB_DISPOSABLE:-0}" = 1 ] || {
	echo 'FAIL: set AWG_LAB_DISPOSABLE=1 only for disposable lab VMs' >&2; exit 1;
}
[ -n "${B_SSH:-}" ] || { echo 'FAIL: B_SSH is required' >&2; exit 1; }
CREATED_A=0 CREATED_B=0
DIR_A= DIR_B=

A_SSH=${A_SSH:-}
B_SSH=${B_SSH:-}
KEY_A='AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='	# scan:allow-fixture[sha1:de49f82911b9d304f1518e8855db8f9cab08b006]
KEY_B='AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI='	# scan:allow-fixture[sha1:35e3bbf9261d562486ce87cec758ee0e03293c36]
TUN_A=10.66.0.1
TUN_B=10.66.0.2
PORT_A=${PORT_A:-51820}
PORT_B=${PORT_B:-51821}
FAILED=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

# rsh <side> <command>: run a command on A or B.
# A_SSH/B_SSH are trusted operator shell commands, never config/peer input.
# The command goes over stdin to a remote `sh -s`, so quoting is parsed exactly
# once (by the remote shell) instead of being mangled by ssh + doas + make.
rsh() {
	side=$1; shift
	case "$side" in
	A) printf '%s\n' "$*" | sh -c "${A_SSH:-sh}" ;;
	B) printf '%s\n' "$*" | sh -c "${B_SSH:-sh}" ;;
	esac
}

cleanup() {
	[ "$CREATED_A" = 0 ] || rsh A 'ifconfig awg0 destroy' >/dev/null 2>&1
	[ "$CREATED_B" = 0 ] || rsh B 'ifconfig awg0 destroy' >/dev/null 2>&1
	for side in A B; do
		if [ "$side" = A ]; then dir=$DIR_A; else dir=$DIR_B; fi
		case "$dir" in
		/var/tmp/awg-interop.*)
			rsh "$side" "if [ -f '$dir/pid' ]; then kill \$(cat '$dir/pid') 2>/dev/null || true; fi; rm -rf '$dir'" >/dev/null 2>&1 ;;
		esac
	done
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# Never change firewall policy implicitly. Operators prepare isolated guests.
for side in A B; do
	rsh "$side" 'pfctl -si' | grep -q '^Status: Disabled' || {
		fail "$side: PF must already be disabled in the disposable lab"; exit 1;
	}
	if rsh "$side" 'ifconfig awg0 >/dev/null 2>&1'; then
		fail "$side: awg0 already exists; refusing to replace it"; exit 1
	fi
done

# Public keys must match the private keys above; deriving them needs the
# userspace helper, so the test takes them from the same place awg-quick does.
derived() {		# derived <side> <private-key>
	rsh "$1" "ifconfig awg0 awgkey $2 >/dev/null 2>&1; ifconfig awg0 | \
	    sed -n 's/.*awgpubkey \\(.*\\)/\\1/p'" 2>/dev/null
}

echo "== 1. configure both ends"
rsh A "ifconfig awg0 create" || { fail "A: create awg0"; exit 1; }
CREATED_A=1
rsh B "ifconfig awg0 create" || { fail "B: create awg0"; exit 1; }
CREATED_B=1
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
# Nothing is sent until there is traffic: no PersistentKeepalive is configured
# on purpose, so one datagram is enough to trigger the initiation.
rsh A "ping -c 1 -w 2 $TUN_B >/dev/null 2>&1 || true"
i=0
hs_a=no; hs_b=no
while [ "$i" -lt 15 ]; do
	hs_a=$(rsh A "ifconfig awg0 | grep -c 'last handshake'")
	hs_b=$(rsh B "ifconfig awg0 | grep -c 'last handshake'")
	# A handshake that completed but has no traffic yet still shows as rx > 0.
	[ "$hs_a" -gt 0 ] || hs_a=$(rsh A "ifconfig awg0 | awk '/rx:/{print (\$4 > 0)}'")
	[ "$hs_b" -gt 0 ] || hs_b=$(rsh B "ifconfig awg0 | awk '/rx:/{print (\$4 > 0)}'")
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
# Verify both the sender status and actual receiver bytes/digest. Elapsed
# seconds alone is neither successful delivery nor a throughput benchmark.
DIR_A=$(rsh A 'umask 077; mktemp -d /var/tmp/awg-interop.XXXXXXXX') || exit 1
DIR_B=$(rsh B 'umask 077; mktemp -d /var/tmp/awg-interop.XXXXXXXX') || exit 1
for dir in "$DIR_A" "$DIR_B"; do
	case "$dir" in /var/tmp/awg-interop.*) ;; *) fail 'invalid remote work directory'; exit 1 ;; esac
done
rsh A "dd if=/dev/zero of='$DIR_A/send' bs=65536 count=128 2>/dev/null" || {
	fail 'could not create transfer fixture'; exit 1;
}
rsh B "nohup nc -d -l $TUN_B 5201 > '$DIR_B/received' 2>'$DIR_B/error' </dev/null & echo \$! > '$DIR_B/pid'" || exit 1
sleep 1
start=$(date +%s)
if rsh A "nc -N -w 10 $TUN_B 5201 < '$DIR_A/send'"; then
	i=0
	while [ "$i" -lt 15 ]; do
		count=$(rsh B "wc -c < '$DIR_B/received'" | tr -d ' \n')
		[ "$count" = 8388608 ] && break
		i=$((i + 1)); sleep 1
	done
	count=$(rsh B "wc -c < '$DIR_B/received'" | tr -d ' \n')
	digest=$(rsh B "sha256 -q '$DIR_B/received'")
	if [ "$count" = 8388608 ] &&
	    [ "$digest" = 2daeb1f36095b44b318410b3f4e8b5d989dcc7bb023d1426c492dab0a3053e74 ]; then
		pass "8 MiB received with matching SHA256 ($(( $(date +%s) - start ))s; not a benchmark)"
	else
		fail "bulk transfer receiver status/length/digest mismatch"
	fi
else
	fail 'bulk transfer sender failed'
fi

echo "== 5. counters moved"
tx_a=$(rsh A "ifconfig awg0 | sed -n 's/.*tx: \\([0-9]*\\).*/\\1/p' | head -1")
[ -n "$tx_a" ] && [ "$tx_a" -gt 0 ] && pass "A transmit counter: $tx_a bytes" || \
    fail "A transmit counter did not move"

echo
echo "note: PF configuration was not changed"
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: interop passed"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
