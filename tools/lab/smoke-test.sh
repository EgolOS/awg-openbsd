#!/bin/sh
# Negative and smoke tests for awg(4), run inside a lab VM as root.
#
#   doas sh tools/lab/smoke-test.sh
#
# Prints PASS/FAIL per case and exits non-zero if any case fails, so it is the
# gate docs/TESTING.md refers to. It must never panic the kernel: a panic here
# is a failure even if nothing is printed afterwards.
#
# Scope: bounds/negative tests, key handling, and a handshake over loopback
# between two interfaces on the same host. It is NOT a throughput test — both
# addresses are local, so an ICMP echo never crosses the tunnel. Measured
# throughput needs two VMs on a private L2 (docs/TESTING.md).
set -u

FAILED=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

expect_fail() {		# expect_fail <description> <command...>
	desc=$1; shift
	if "$@" >/dev/null 2>&1; then
		fail "$desc (command succeeded)"
	else
		pass "$desc"
	fi
}

expect_ok() {		# expect_ok <description> <command...>
	desc=$1; shift
	if "$@" >/dev/null 2>&1; then
		pass "$desc"
	else
		fail "$desc (command failed)"
	fi
}

# wait_handshake <interface> <seconds>
wait_handshake() {
	i=0
	while [ "$i" -lt "$2" ]; do
		if ifconfig "$1" 2>/dev/null | grep -q 'last handshake'; then
			return 0
		fi
		sleep 1
		i=$((i + 1))
	done
	return 1
}

cleanup() {
	ifconfig awg0 destroy 2>/dev/null
	ifconfig awg1 destroy 2>/dev/null
	[ -n "${NC_PID:-}" ] && kill "$NC_PID" 2>/dev/null
}

cleanup
trap cleanup EXIT

# Fixed lab keys: valid 32-byte base64, never used outside this VM.
KEY0='AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='	# scan:allow-fixture[sha1:de49f82911b9d304f1518e8855db8f9cab08b006]
KEY1='AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI='	# scan:allow-fixture[sha1:35e3bbf9261d562486ce87cec758ee0e03293c36]

echo "== kernel: $(uname -v)"
case "$(uname -v)" in
AWG*)	pass "custom AWG kernel is running" ;;
*)	fail "not running an AWG kernel: $(uname -v)" ;;
esac

echo
echo "== 1. device lifecycle and key handling"
expect_ok "awg0 create" ifconfig awg0 create
expect_ok "awg1 create" ifconfig awg1 create
expect_ok "awg0 key from stdin (awgkey -)" sh -c "printf '%s\n' '$KEY0' | ifconfig awg0 awgkey -"
expect_ok "awg1 key as an argument (awgkey <b64>)" ifconfig awg1 awgkey "$KEY1"
expect_fail "short key rejected" ifconfig awg0 awgkey 'AAAA'

P0=$(ifconfig awg0 | sed -n 's/.*awgpubkey //p')
P1=$(ifconfig awg1 | sed -n 's/.*awgpubkey //p')
case "$P0$P1" in
"")	fail "interfaces have no public key" ;;
*)	pass "keypairs derived from the configured keys" ;;
esac

echo
echo "== 2. configuration bounds (legacy): EINVAL, device unaffected"
for p in awgs1 awgs2; do
	expect_fail "$p 65535 rejected" ifconfig awg0 "$p" 65535
	expect_fail "$p 1281 rejected (AWG_MAX_PADDING)" ifconfig awg0 "$p" 1281
done
expect_fail "awgjc 65535 rejected" ifconfig awg0 awgjc 65535
expect_fail "awgjc 65 rejected (AWG_MAX_JUNK)" ifconfig awg0 awgjc 65
expect_fail "awgjmax 1501 rejected" ifconfig awg0 awgjmax 1501
expect_fail "3.1-only parameter rejected on a legacy interface" \
	ifconfig awg0 awgs3 18
expect_fail "I1 rejected on a legacy interface" \
	ifconfig awg0 awgi1 '<b 0xc7><r 16>'
expect_ok "device still usable after rejected values" ifconfig awg0

echo
echo "== 3. legacy handshake: two interfaces on this host, endpoint 127.0.0.1"
expect_ok "awg0 port" ifconfig awg0 awgport 51820
expect_ok "awg1 port" ifconfig awg1 awgport 51821
expect_ok "awg0 obfuscation parameters" ifconfig awg0 awgjc 4 awgjmin 10 \
	awgjmax 50 awgs1 26 awgs2 33 awgh1 1737146616 awgh2 705704376 \
	awgh3 391058102 awgh4 2103979647
expect_ok "awg1 obfuscation parameters" ifconfig awg1 awgjc 4 awgjmin 10 \
	awgjmax 50 awgs1 26 awgs2 33 awgh1 1737146616 awgh2 705704376 \
	awgh3 391058102 awgh4 2103979647
if [ -n "$P0" ] && [ -n "$P1" ]; then
	# awgpka gives this side a reason to initiate; the other side stays
	# quiet. Persistent keepalive on both ends makes them initiate in the
	# same instant, and WireGuard drops the losing response ("unknown
	# handshake response") until a retry — correct, but it makes a 8s
	# assertion flaky.
	expect_ok "awg0 peer" ifconfig awg0 awgpeer "$P1" awgaip 10.9.9.2/32 \
		awgendpoint 127.0.0.1 51821 awgpka 5
	expect_ok "awg1 peer" ifconfig awg1 awgpeer "$P0" awgaip 10.9.9.1/32 \
		awgendpoint 127.0.0.1 51820
else
	fail "cannot configure peers without public keys"
fi
expect_ok "awg0 address" ifconfig awg0 inet 10.9.9.1/32 up
expect_ok "awg1 address" ifconfig awg1 inet 10.9.9.2/32 up
# The handshake is the real data-path assertion here: it only completes if
# junk packets, prefixes, magic headers, crypto and the UDP path all work.
if wait_handshake awg0 8; then
	pass "handshake completed over the obfuscated UDP path"
	pass "$(ifconfig awg0 | sed -n 's/^[[:space:]]*\(last handshake.*\)/\1/p' | head -1)"
else
	fail "handshake did not complete within 8s"
fi
TX=$(ifconfig awg0 | sed -n 's/.*tx: \([0-9]*\).*/\1/p' | head -1)
RX=$(ifconfig awg0 | sed -n 's/.*rx: \([0-9]*\).*/\1/p' | head -1)
case "${TX:-0}" in
0|"")	fail "awg0 tx counter did not move" ;;
*)	pass "awg0 tx counter moved ($TX)" ;;
esac
case "${RX:-0}" in
0|"")	fail "awg0 rx counter did not move" ;;
*)	pass "awg0 rx counter moved ($RX)" ;;
esac

echo
echo "== 4. malformed input from the wire (must not panic)"
nc -u -l 127.0.0.1 51820 >/dev/null 2>&1 &
NC_PID=$!
sleep 1
printf 'x' | nc -u -w 1 127.0.0.1 51820 2>/dev/null
printf 'garbage-that-is-not-a-handshake' | nc -u -w 1 127.0.0.1 51820 2>/dev/null
dd if=/dev/zero bs=1024 count=32 2>/dev/null | nc -u -w 1 127.0.0.1 51820 2>/dev/null
sleep 1
kill "$NC_PID" 2>/dev/null
NC_PID=""
pass "sent truncated and oversized datagrams, kernel alive: $(uname -v)"
expect_ok "device still usable after malformed input" ifconfig awg0

echo
echo "== 5. 3.1 interface: header protection, S3/S4, I1, ranges"
expect_ok "awg1 version 3.1" ifconfig awg1 awgversion 3.1
expect_ok "awg1 S1-S4 + header protection key" ifconfig awg1 \
	awgs1 20 awgs2 21 awgs3 22 awgs4 23 awghpk "$P1"
expect_ok "awg1 I1 spec" ifconfig awg1 awgi1 '<b 0xc700000001><r 16><t>'
expect_ok "awg1 timing ranges" ifconfig awg1 awgrekeyaftertime 110-130 \
	awgrekeytimeout 4-6 awgrejectaftertime 170-190
expect_ok "awg1 parameters round-trip" ifconfig awg1
expect_fail "reject-after <= keepalive + rekey rejected" ifconfig awg1 \
	awgrejectaftertime 1-2 awgkeepalivetimeout 10-12 awgrekeyaftertime 100-120
expect_fail "I1 above the UDP payload bound rejected" \
	ifconfig awg1 awgi1 '<r 65508>'
expect_ok "I1 at the UDP payload bound accepted (skipped at send time)" \
	ifconfig awg1 awgi1 '<r 65507>'

echo
echo "== 6. teardown"
expect_ok "awg0 destroy" ifconfig awg0 destroy
expect_ok "awg1 destroy" ifconfig awg1 destroy

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all cases passed"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
