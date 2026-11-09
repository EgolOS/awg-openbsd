#!/bin/sh
# awgctl lab test — runs on the lab VM as root, guarded by AWG_LAB_DISPOSABLE.
#
#   AWG_LAB_DISPOSABLE=1 sh tools/lab/awgctl-test.sh
#
# Cases: up/show idempotency, down, config rejection, key exposure, loopback
# handshake with real X25519 keypairs, the legacy vs 3.1 field sets, version
# and grouped-field survival across set/add/del, MTU prevalidation, and the
# stdin secret paths. Prints PASS/FAIL per case, exits non-zero on any
# failure.
set -u

[ "${AWG_LAB_DISPOSABLE:-0}" = 1 ] || {
	echo "set AWG_LAB_DISPOSABLE=1 for disposable lab VMs" >&2
	exit 1
}

AWGCTL=${AWGCTL:-./awgctl}
CONFDIR=${CONFDIR:-/etc/amnezia/amneziawg}
FAILED=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

# Real X25519 keypairs (tools/lab/x25519-keygen.py, seeded so the run is
# reproducible). The peer must carry the public half of a pair; the previous
# fixture reused the private-key strings as public keys, which decodes fine
# but never completes a handshake.
KEY0_PRIV='YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA='	# scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37]
KEY0_PUB='ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA='	# scan:allow-fixture[sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05]
KEY1_PRIV='YHdnLWxhYi1rZXktMQAAAAAAAAAAAAAAAAAAAAAAAEA='	# scan:allow-fixture[sha1:a20da63b5ead89d83417164ca35b7179c5f91cdf]
KEY1_PUB='jClj1FiPuqBEcmhDqJSqB8favq6USkiwFzGt/uTOjTE='	# scan:allow-fixture[sha1:174cde8bbc97e52dd9c739f2e0e5a76f625795c3]
HPK='BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc='	# scan:allow-fixture[sha1:2c1babcb02ceb92a912b26204981ca3c9c66bea6]

IFACE=awg9
IFACE2=awg10

cleanup() {
	ifconfig "$IFACE" destroy 2>/dev/null
	ifconfig "$IFACE2" destroy 2>/dev/null
}
cleanup
trap cleanup EXIT

# Normalise `ifconfig awg9` and `awgctl show awg9` to one "key value" pair
# per line. BSD sed does not expand \n in a substitution, so the pairs are
# split with awk instead; both sides use the same normaliser, then sort+diff.
norm_pairs() {
	awk '{ for (i = 1; i <= NF; i++) if ($i ~ /^(awgport|awgrtable|awgpubkey|awgversion|awgjc|awgjmin|awgjmax|awgs[1-4]|awgh[1-4]|awgi[1-5]|awghpk|awgcpa|awgrekeyaftertime|awgrekeytimeout|awgrejectaftertime|awgkeepalivetimeout|awgmaxhandshakeattempts|awgrandomtrailers|awgdisablecookies|awgpeer|awgdescr|awgpsk|awgpka|awgendpoint|awgaip)(:)?$/) { if (i < NF) print $i, $(i + 1); else print $i } }'
}
# `show` indents every field with a tab; strip it so one pattern matches the
# output of both tools (the old assertions anchored on ^awgversion and only
# matched the tab-free normalised stream).
flat() { sed 's/^[[:space:]]*//'; }
has_field() {	# has_field <text> <field>
	echo "$1" | flat | grep -qE "^$2( |$)"
}

write_legacy_conf() {
	cat > "$CONFDIR/$1.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	ListenPort = 51820
	Address = 10.9.19.1/24 # scan:allow-fixture[sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4]
	MTU = 1420
	Jc = 3
	Jmin = 10
	Jmax = 25
	S1 = 26
	S2 = 33
	H1 = 100
	H2 = 200
	H3 = 300
	H4 = 400

	[Peer]
	PublicKey = $KEY1_PUB
	Endpoint = 127.0.0.1:51821
	AllowedIPs = 10.9.19.2/32,fd00::2/128 # scan:allow-fixture[sha1:9c0214d26dcb5755b8a26890d52f74b389cca6ab]
	PersistentKeepalive = 25
	EOF
	chmod 600 "$CONFDIR/$1.conf"
}

echo "== kernel: $(uname -v)"
case "$(uname -v)" in
AWG*)	pass "custom AWG kernel is running" ;;
*)	fail "not running an AWG kernel: $(uname -v)" ;;
esac
[ -x "$AWGCTL" ] || { echo "awgctl binary not found at $AWGCTL" >&2; exit 1; }
mkdir -p "$CONFDIR"
chmod 700 "$CONFDIR"

echo
echo "== 1. up: config with one peer, kernel state matches the file"
write_legacy_conf awg9
if "$AWGCTL" up "$IFACE" > /dev/null 2>&1; then
	pass "awgctl up $IFACE exits 0"
else
	fail "awgctl up $IFACE failed"
fi
IFC=$(ifconfig "$IFACE" 2>/dev/null)
check_field() {	# check_field <label> <grep pattern>
	if echo "$IFC" | grep -q "$2"; then
		pass "ifconfig shows $1"
	else
		fail "ifconfig missing $1 (wanted $2)"
	fi
}
check_field "port"		"awgport 51820"
check_field "version legacy"	"awgversion legacy"
check_field "jc/jmin/jmax"	"awgjc 3 awgjmin 10 awgjmax 25"
check_field "s1/s2"		"awgs1 26 awgs2 33"
check_field "h1-h4"		"awgh1 100 awgh2 200 awgh3 300 awgh4 400"
check_field "peer key"		"awgpeer $KEY1_PUB"
check_field "endpoint"		"awgendpoint 127.0.0.1 51821"
check_field "allowed-ips v4"	"awgaip 10.9.19.2/32" # scan:allow-fixture[sha1:9c0214d26dcb5755b8a26890d52f74b389cca6ab]
check_field "allowed-ips v6"	"awgaip fd00::2/128"
check_field "keepalive"		"awgpka 25 (sec)"
# Real ifconfig prints the address with a netmask, not the conf's CIDR.
check_field "address"		"inet 10.9.19.1 netmask 0xffffff00" # scan:allow-fixture[sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4]
check_field "mtu"		"mtu 1420"
# The peer list must reach the kernel: i_peers_count is what the ioctl loop
# runs over, so a missing count shows up here as an absent peer.
SH=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$SH" "awgpeer $KEY1_PUB"; then
	pass "show reports the configured peer (i_peers_count)"
else
	fail "show reports no peer for a one-peer config"
fi

echo
echo "== 2. show equals ifconfig, field by field"
A=$(echo "$IFC" | norm_pairs | sort)
B=$(echo "$SH" | norm_pairs | sort)
if [ -n "$A" ] && [ "$A" = "$B" ]; then
	pass "awgctl show matches ifconfig on all compared fields"
else
	fail "awgctl show differs from ifconfig"
	echo "$A" > /tmp/awgctl-ifc.$$
	echo "$B" > /tmp/awgctl-show.$$
	diff -u /tmp/awgctl-ifc.$$ /tmp/awgctl-show.$$
	rm -f /tmp/awgctl-ifc.$$ /tmp/awgctl-show.$$
fi

echo
echo "== 3. idempotent up, down destroys, down on missing interface"
S1=$(echo "$SH" | flat | grep -v -E '^(tx:|last handshake)')
"$AWGCTL" up "$IFACE" >/dev/null 2>&1 || fail "second awgctl up failed"
S2=$("$AWGCTL" show "$IFACE" 2>/dev/null | flat | grep -v -E '^(tx:|last handshake)')
if [ "$S1" = "$S2" ]; then
	pass "awgctl up twice is idempotent"
else
	fail "second up changed the interface"
fi
"$AWGCTL" down "$IFACE" >/dev/null 2>&1 || fail "awgctl down failed"
if ifconfig "$IFACE" >/dev/null 2>&1; then
	fail "interface survives awgctl down"
else
	pass "awgctl down destroys the interface (peers go with it)"
fi
DOWNMSG=$("$AWGCTL" down "$IFACE" 2>&1)
rc=$?
if [ $rc -ne 0 ] && [ -n "$DOWNMSG" ]; then
	pass "awgctl down on a missing interface exits $rc with a message"
else
	fail "awgctl down on a missing interface: rc=$rc msg=$DOWNMSG"
fi

echo
echo "== 4. bad configs refused with exit 2, no partial state"
"$AWGCTL" up "$IFACE" >/dev/null 2>&1 || fail "baseline up failed"
BASE=$("$AWGCTL" show "$IFACE" 2>/dev/null | flat | grep -v -E '^(tx:|last handshake)')
cat > "$CONFDIR/badkey.conf" <<-EOF
	[Interface]
	PrivateKey = AAAA
	ListenPort = 51820
	EOF
chmod 600 "$CONFDIR/badkey.conf"
cat > "$CONFDIR/unknown.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	BogusKey = 7
	EOF
chmod 600 "$CONFDIR/unknown.conf"
cat > "$CONFDIR/revrange.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	H1 = 300-100
	EOF
chmod 600 "$CONFDIR/revrange.conf"
cat > "$CONFDIR/s3legacy.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	Version = legacy
	S3 = 18
	EOF
chmod 600 "$CONFDIR/s3legacy.conf"
for bad in badkey unknown revrange s3legacy; do
	"$AWGCTL" up "$IFACE" --config "$CONFDIR/$bad.conf" >/dev/null 2>&1
	rc=$?
	[ "$rc" = 2 ] && pass "$bad.conf refused with exit 2" || \
		fail "$bad.conf exit code is $rc, expected 2"
done
NOW=$("$AWGCTL" show "$IFACE" 2>/dev/null | flat | grep -v -E '^(tx:|last handshake)')
if [ "$BASE" = "$NOW" ]; then
	pass "interface unchanged after the four refused configs"
else
	fail "rejected configs changed the interface"
fi

echo
echo "== 5. private key never appears in ps"
"$AWGCTL" up "$IFACE" --config "$CONFDIR/awg9.conf" >/dev/null 2>&1 &
UPPID=$!
seen=0
i=0
while [ $i -lt 50 ]; do
	if ps -ax -o command= 2>/dev/null | grep -qF "$KEY0_PRIV"; then
		seen=1
		break
	fi
	i=$((i + 1))
done
wait "$UPPID" 2>/dev/null || fail "background up failed"
if [ "$seen" = 0 ]; then
	pass "private key absent from ps -ax -o command (50 samples)"
else
	fail "private key visible in ps"
fi

echo
echo "== 6. loopback round trip: $IFACE <-> $IFACE2, counters move"
write_legacy_conf awg9
cat > "$CONFDIR/awg10.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY1_PRIV
	ListenPort = 51821
	Address = 10.9.19.2/24 # scan:allow-fixture[sha1:9c0214d26dcb5755b8a26890d52f74b389cca6ab]
	Jc = 3
	Jmin = 10
	Jmax = 25
	S1 = 26
	S2 = 33
	H1 = 100
	H2 = 200
	H3 = 300
	H4 = 400

	[Peer]
	PublicKey = $KEY0_PUB
	Endpoint = 127.0.0.1:51820
	AllowedIPs = 10.9.19.1/32 # scan:allow-fixture[sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4]
	PersistentKeepalive = 5
	EOF
chmod 600 "$CONFDIR/awg10.conf"
"$AWGCTL" up "$IFACE" >/dev/null 2>&1 || fail "up $IFACE failed"
"$AWGCTL" up "$IFACE2" >/dev/null 2>&1 || fail "up $IFACE2 failed"
i=0
while [ $i -lt 8 ]; do
	if "$AWGCTL" show "$IFACE" 2>/dev/null | grep -q 'last handshake'; then
		break
	fi
	sleep 1
	i=$((i + 1))
done
if [ $i -lt 8 ]; then
	pass "handshake completed over loopback (real keypair)"
else
	fail "no handshake within 8s"
fi
TXRX=$("$AWGCTL" show "$IFACE" 2>/dev/null | sed -n 's/.*tx: \([0-9]*\), rx: \([0-9]*\).*/\1 \2/p' | head -1)
set -- $TXRX
case "${1:-0}" in 0|"") fail "tx counter did not move" ;; *) pass "tx counter moved ($1)" ;; esac
case "${2:-0}" in 0|"") fail "rx counter did not move" ;; *) pass "rx counter moved ($2)" ;; esac

echo
echo "== 7. 3.1 interface prints the 3.1-only fields"
cat > "$CONFDIR/awg31.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	ListenPort = 51830
	Version = 3.1
	Jc = 3
	Jmin = 10
	Jmax = 25
	S1 = 20
	S2 = 21
	S3 = 22
	S4 = 23
	H1 = 100-200
	H2 = 201-300
	H3 = 301-400
	H4 = 401-500
	I1 = <b 0xc700000001><r 16><t>
	HeaderProtectionKey = $HPK
	ContentPaddingAddition = 18
	RekeyAfterTime = 110-130
	RekeyTimeout = 4-6
	RejectAfterTime = 170-190
	KeepaliveTimeout = 10-12
	MaxHandshakeAttempts = 3
	RandomTrailers = 1
	DisableCookies = 0
	EOF
chmod 600 "$CONFDIR/awg31.conf"
"$AWGCTL" up "$IFACE" --config "$CONFDIR/awg31.conf" >/dev/null 2>&1 || \
	fail "up with 3.1 config failed"
SH31=$("$AWGCTL" show "$IFACE" 2>/dev/null)
for f in "awgversion 3.1" "awgs3 22" "awgs4 23" "awgh1 100-200" \
	"awgi1 <b 0xc700000001><r 16><t>" "awghpk (present)" "awgcpa 18" \
	"awgrekeyaftertime 110-130" "awgrekeytimeout 4-6" \
	"awgrejectaftertime 170-190" "awgkeepalivetimeout 10-12" \
	"awgmaxhandshakeattempts 3" "awgrandomtrailers"; do
	if has_field "$SH31" "$f"; then
		pass "3.1 show prints $f"
	else
		fail "3.1 show missing $f"
	fi
done
IFC31=$(ifconfig "$IFACE" 2>/dev/null)
A=$(echo "$IFC31" | norm_pairs | sort)
B=$(echo "$SH31" | norm_pairs | sort)
if [ -n "$A" ] && [ "$A" = "$B" ]; then
	pass "3.1 show matches ifconfig"
else
	fail "3.1 show differs from ifconfig"
	echo "$A" > /tmp/awgctl-ifc.$$
	echo "$B" > /tmp/awgctl-show.$$
	diff -u /tmp/awgctl-ifc.$$ /tmp/awgctl-show.$$
	rm -f /tmp/awgctl-ifc.$$ /tmp/awgctl-show.$$
fi
# Legacy show (case 1 snapshot) must not invent the 3.1-only keys.
if echo "$SH" | flat | grep -qE '^(awgs3|awgs4|awgi[1-5]|awghpk|awgcpa)'; then
	fail "legacy show invents 3.1-only fields"
else
	pass "legacy show omits the 3.1-only fields"
fi

echo
echo "== 8. 3.1 keeps its version across set, add peer and del peer"
"$AWGCTL" up "$IFACE" --config "$CONFDIR/awg31.conf" >/dev/null 2>&1 || \
	fail "8: up with 3.1 config failed"
V=$(echo "$("$AWGCTL" show "$IFACE" 2>/dev/null)" | flat | grep -c '^awgversion 3.1')
[ "$V" = 1 ] && pass "8: fresh 3.1 interface shows awgversion 3.1" || \
	fail "8: fresh interface is not 3.1"
"$AWGCTL" add "$IFACE" peer "$KEY1_PUB" endpoint 127.0.0.1:51831 allowed-ips 10.9.31.2/32 keepalive 10 >/dev/null 2>&1 || fail "8: add peer failed" # scan:allow-fixture[sha1:4219b000d0b510eb4a5b80931a50d82d69c7ca3d]
V=$(echo "$("$AWGCTL" show "$IFACE" 2>/dev/null)" | flat | grep -c '^awgversion 3.1')
[ "$V" = 1 ] && pass "8: version stays 3.1 after add peer" || \
	fail "8: add peer reset the version"
if has_field "$("$AWGCTL" show "$IFACE" 2>/dev/null)" "awgpeer $KEY1_PUB"; then
	pass "8: added peer is present"
else
	fail "8: added peer missing after add"
fi
"$AWGCTL" set "$IFACE" ListenPort 51831 >/dev/null 2>&1 || \
	fail "8: set ListenPort failed"
V=$(echo "$("$AWGCTL" show "$IFACE" 2>/dev/null)" | flat | grep -c '^awgversion 3.1')
[ "$V" = 1 ] && pass "8: version stays 3.1 after set ListenPort" || \
	fail "8: set ListenPort reset the version"
if has_field "$("$AWGCTL" show "$IFACE" 2>/dev/null)" "awgport 51831"; then
	pass "8: set ListenPort applied (awgport 51831)"
else
	fail "8: set ListenPort not applied"
fi
"$AWGCTL" del "$IFACE" peer "$KEY1_PUB" >/dev/null 2>&1 || \
	fail "8: del peer failed"
V=$(echo "$("$AWGCTL" show "$IFACE" 2>/dev/null)" | flat | grep -c '^awgversion 3.1')
[ "$V" = 1 ] && pass "8: version stays 3.1 after del peer" || \
	fail "8: del peer reset the version"
if has_field "$("$AWGCTL" show "$IFACE" 2>/dev/null)" "awgpeer $KEY1_PUB"; then
	fail "8: deleted peer still present"
else
	pass "8: deleted peer is gone"
fi
# The 3.1 parameters set by `up` must survive the partial commands too.
S8=$(echo "$("$AWGCTL" show "$IFACE" 2>/dev/null)" | flat | grep -c '^awgs3 22')
[ "$S8" = 1 ] && pass "8: partial commands keep the 3.1 parameter set" || \
	fail "8: partial commands lost awgs3"

echo
echo "== 9. grouped set keeps the siblings it does not name"
"$AWGCTL" set "$IFACE" Jc 7 >/dev/null 2>&1 || fail "9: set Jc failed"
G=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$G" "awgjc 7"; then
	pass "9: set Jc 7 applied"
else
	fail "9: set Jc 7 not applied"
fi
if has_field "$G" "awgjmin 10 awgjmax 25"; then
	pass "9: Jmin/Jmax survive a Jc-only set (grouped fields)"
else
	fail "9: Jmin/Jmax were zeroed by a Jc-only set"
fi
"$AWGCTL" set "$IFACE" S2 44 >/dev/null 2>&1 || fail "9: set S2 failed"
G=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$G" "awgs1 20 awgs2 44"; then
	pass "9: S1 survives an S2-only set"
else
	fail "9: S1 was zeroed by an S2-only set"
fi
"$AWGCTL" set "$IFACE" H2 250 >/dev/null 2>&1 || fail "9: set H2 failed"
G=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$G" "awgh1 100-200" && has_field "$G" "awgh2 250" && \
   has_field "$G" "awgh3 301-400" && has_field "$G" "awgh4 401-500"; then
	pass "9: H1/H3/H4 survive an H2-only set"
else
	fail "9: a partial H set lost its siblings"
fi

echo
echo "== 10. MTU is validated before the interface is mutated"
cat > "$CONFDIR/badmtu.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	ListenPort = 51840
	MTU = 100
	EOF
chmod 600 "$CONFDIR/badmtu.conf"
# R4-08: the driver's SIOCSIFMTU takes 1..9000, so 9001 must be refused too.
cat > "$CONFDIR/badmtu9001.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0_PRIV
	ListenPort = 51840
	MTU = 9001
	EOF
chmod 600 "$CONFDIR/badmtu9001.conf"
"$AWGCTL" up "$IFACE2" --config "$CONFDIR/awg10.conf" >/dev/null 2>&1
BASE_MTU=$("$AWGCTL" show "$IFACE2" 2>/dev/null)
BASE_NPEERS=$(echo "$BASE_MTU" | flat | grep -c '^awgpeer')
BASE_M=$(ifconfig "$IFACE2" 2>/dev/null | sed -n 's/.*mtu \([0-9]*\).*/\1/p' | head -1)
for badmtu in badmtu badmtu9001; do
	"$AWGCTL" up "$IFACE2" --config "$CONFDIR/$badmtu.conf" >/dev/null 2>&1
	rc=$?
	[ "$rc" = 2 ] && pass "MTU ($badmtu) refused with exit 2" || \
		fail "MTU ($badmtu) exit code is $rc, expected 2"
done
NOW_MTU=$("$AWGCTL" show "$IFACE2" 2>/dev/null)
NOW_NPEERS=$(echo "$NOW_MTU" | flat | grep -c '^awgpeer')
NOW_M=$(ifconfig "$IFACE2" 2>/dev/null | sed -n 's/.*mtu \([0-9]*\).*/\1/p' | head -1)
if [ "$NOW_NPEERS" = "$BASE_NPEERS" ] && [ "$NOW_M" = "$BASE_M" ]; then
	pass "MTU refusals leave peer count ($NOW_NPEERS) and $NOW_M unchanged"
else
	fail "MTU refusal changed the interface (peers $BASE_NPEERS->$NOW_NPEERS, $BASE_M->$NOW_M)"
fi

echo
echo "== 12. R4-06: an unrelated set keeps the rotated key; a lost update fails"
"$AWGCTL" up "$IFACE" --config "$CONFDIR/awg9.conf" >/dev/null 2>&1
PRE=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$PRE" "awgpubkey $KEY0_PUB"; then
	pass "12: baseline interface carries the KEY0 pubkey"
else
	fail "12: baseline interface is not KEY0"
fi
# Rotate out-of-band with a second `set PrivateKey -` from stdin (KEY1).
"$AWGCTL" set "$IFACE" PrivateKey - <<EOF >/dev/null 2>&1
$KEY1_PRIV
EOF
rc=$?
[ "$rc" = 0 ] && pass "12: rotate to KEY1 exits 0" || fail "12: rotate exits $rc"
# An unrelated set (ListenPort) must not replay the pre-rotation key.
"$AWGCTL" set "$IFACE" ListenPort 51991 >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "12: unrelated set ListenPort exits 0" || \
	fail "12: unrelated set ListenPort exits $rc"
POST=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$POST" "awgpubkey $KEY1_PUB"; then
	pass "12: rotated key survives the unrelated set (no stale replay)"
else
	fail "12: unrelated set replayed a stale key (pubkey is not KEY1)"
fi
if has_field "$POST" "awgport 51991"; then
	pass "12: the unrelated set's own field (port 51991) applied"
else
	fail "12: the unrelated set's port did not apply"
fi
# Concurrent pair: the key write reads the state, waits for stdin, then
# compare-and-swaps. The port write lands inside that window, so the key
# write's token is stale and it must fail loudly instead of reverting.
( { sleep 1; echo "$KEY0_PRIV"; } | "$AWGCTL" set "$IFACE" PrivateKey - ) 2>/dev/null &
SLOWPID=$!
sleep 0.3
"$AWGCTL" set "$IFACE" ListenPort 51992 >/dev/null 2>&1
fastrc=$?
wait "$SLOWPID"
slowrc=$?
[ "$fastrc" = 0 ] && pass "12: concurrent ListenPort set succeeds" || \
	fail "12: concurrent ListenPort set exits $fastrc"
if [ "$slowrc" != 0 ]; then
	pass "12: concurrent key set loses the race and exits $slowrc (not a silent revert)"
else
	fail "12: concurrent key set overwrote the winner (rc 0)"
fi
CONC=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$CONC" "awgport 51992" && has_field "$CONC" "awgpubkey $KEY1_PUB"; then
	pass "12: winner's port and the rotated key both survive the loser"
else
	fail "12: lost update mixed the two writes (port/key mismatch)"
fi

echo
echo "== 13. R4-07: every path component is trusted, not just the file"
mkdir -p "$CONFDIR/nested"
chmod 777 "$CONFDIR/nested"
cp "$CONFDIR/awg9.conf" "$CONFDIR/nested/deep.conf"
chmod 600 "$CONFDIR/nested/deep.conf"
"$AWGCTL" up "$IFACE" --config "$CONFDIR/nested/deep.conf" >/dev/null 2>&1
rc=$?
if [ "$rc" != 0 ]; then
	pass "13: world-writable intermediate directory refused (exit $rc)"
else
	fail "13: world-writable intermediate directory accepted"
fi
ln -sfn nested "$CONFDIR/link"
"$AWGCTL" up "$IFACE" --config "$CONFDIR/link/deep.conf" >/dev/null 2>&1
rc=$?
if [ "$rc" != 0 ]; then
	pass "13: symlinked path component refused (exit $rc)"
else
	fail "13: symlinked path component accepted"
fi
rm -f "$CONFDIR/link"
# The file that is verified is the file that is read: replace the config with
# a different key while the tool holds the descriptor; the read must return
# the opened file, not the swapped-in one.
cp "$CONFDIR/awg9.conf" "$CONFDIR/swap.conf"
chmod 600 "$CONFDIR/swap.conf"
( "$AWGCTL" up "$IFACE" --config "$CONFDIR/swap.conf" >/dev/null 2>&1 ) &
SWAPPID=$!
sleep 0.2
sed "s/$KEY0_PRIV/$KEY1_PRIV/" "$CONFDIR/awg9.conf" > "$CONFDIR/swap.conf.new"
mv -f "$CONFDIR/swap.conf.new" "$CONFDIR/swap.conf"
wait "$SWAPPID"
rc=$?
[ "$rc" = 0 ] && pass "13: up with a swapped config exits 0" || \
	fail "13: up with a swapped config exits $rc"
SW=$("$AWGCTL" show "$IFACE" 2>/dev/null)
if has_field "$SW" "awgpubkey $KEY0_PUB"; then
	pass "13: the descriptor that was checked is the descriptor that was read"
else
	fail "13: read a swapped-in file instead of the verified descriptor"
fi
rm -f "$CONFDIR/swap.conf"
rm -rf "$CONFDIR/nested"

echo
echo "== 14. R4-09: ABI/argument failures are real failures"
"$AWGCTL" show "$IFACE2" >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "14: show on a live interface exits 0" || \
	fail "14: show on a live interface exits $rc"
"$AWGCTL" show "${IFACE2}x" >/dev/null 2>&1
rc=$?
if [ "$rc" != 0 ]; then
	pass "14: show on an explicitly named missing interface exits $rc (ENOTTY not swallowed)"
else
	fail "14: show on a missing interface reports success"
fi
"$AWGCTL" set "$IFACE2" ListenPort 51993 extra >/dev/null 2>&1
rc=$?
if [ "$rc" != 0 ]; then
	pass "14: set with an unmatched trailing token exits $rc (exact arity)"
else
	fail "14: set ignored an unmatched trailing token"
fi
"$AWGCTL" set "$IFACE2" ListenPort 51993 >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "14: the same set with exact arity exits 0" || \
	fail "14: exact-arity set exits $rc"
if has_field "$("$AWGCTL" show "$IFACE2" 2>/dev/null)" "awgport 51993"; then
	pass "14: exact-arity set applied the port"
else
	fail "14: exact-arity set did not apply the port"
fi
# A duplicate address is idempotent only when it is on *this* interface:
# `up` twice must succeed and the address must read back on the target.
"$AWGCTL" up "$IFACE2" --config "$CONFDIR/awg10.conf" >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "14: up twice with the same address is idempotent" || \
	fail "14: second up with a duplicate address exits $rc"
if ifconfig "$IFACE2" 2>/dev/null | grep -q "inet 10.9.19.2"; then # scan:allow-fixture[sha1:9c0214d26dcb5755b8a26890d52f74b389cca6ab] lab topology
	pass "14: address verified present on the target interface (readback)"
else
	fail "14: address not read back on the target interface"
fi

echo
echo "== 15. R4-10: literal secrets warn, buffers are wiped"
"$AWGCTL" set "$IFACE2" PrivateKey "$KEY0_PRIV" 2>/tmp/awgctl-lit.$$
rc=$?
[ "$rc" = 0 ] && pass "15: literal key argument still accepted" || \
	fail "15: literal key argument exits $rc"
if grep -qi "process listing" /tmp/awgctl-lit.$$; then
	pass "15: literal secret warns to prefer stdin"
else
	fail "15: literal secret accepted silently"
fi
rm -f /tmp/awgctl-lit.$$
P15=$("$AWGCTL" show "$IFACE2" 2>/dev/null)
if has_field "$P15" "awgpubkey $KEY0_PUB"; then
	pass "15: literal key is the key the kernel installed"
else
	fail "15: literal key not applied"
fi

echo
echo "== 11. secrets come from the file or stdin, not only from argv"
"$AWGCTL" set "$IFACE2" PrivateKey - <<EOF >/dev/null 2>&1
$KEY1_PRIV
EOF
rc=$?
[ "$rc" = 0 ] && pass "set PrivateKey - reads the key from stdin" || \
	fail "set PrivateKey - from stdin exits $rc"
P=$("$AWGCTL" show "$IFACE2" 2>/dev/null)
if has_field "$P" "awgpubkey $KEY1_PUB"; then
	pass "stdin key is the key the kernel installed (pubkey matches)"
else
	fail "stdin key not applied"
fi
"$AWGCTL" add "$IFACE2" peer "$KEY0_PUB" psk - allowed-ips 10.9.41.1/32 <<EOF >/dev/null 2>&1 # scan:allow-fixture[sha1:30c517bfcaf1db81e16331e993f8738db0451838] lab topology
$HPK
EOF
rc=$?
[ "$rc" = 0 ] && pass "add ... psk - reads the PSK from stdin" || \
	fail "add ... psk - from stdin exits $rc"
if has_field "$("$AWGCTL" show "$IFACE2" 2>/dev/null)" "awgpeer $KEY0_PUB"; then
	pass "peer added with a stdin PSK"
else
	fail "peer with a stdin PSK not installed"
fi
seen=0
i=0
while [ $i -lt 20 ]; do
	if ps -ax -o command= 2>/dev/null | grep -qF "$KEY1_PRIV"; then
		seen=1
		break
	fi
	i=$((i + 1))
done
[ "$seen" = 0 ] && pass "stdin secret absent from ps (20 samples)" || \
	fail "stdin secret visible in ps"

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all cases passed"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
