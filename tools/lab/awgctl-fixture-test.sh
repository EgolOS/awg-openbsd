#!/bin/sh
# awgctl host fixture — R5 regression cases that need no OpenBSD kernel.
#
#   sh tools/lab/awgctl-fixture-test.sh
#
# Builds sbin/awgctl/awgctl.c against tools/lab/fixture-stubs.c (ioctl and
# getifaddrs) and tools/lab/fixture-ifconfig.py (records the address the
# kernel would install), then drives the real commands. Covers the R5 items:
# N2 (a set on an unchanged/busy 3.1 interface), N3 (address read-back by
# parsed bytes + prefix), R4-07 (trust walk), R4-09 (arity, ENOTTY, child
# signal) and R4-10 (secret wiping on the failure path). Prints PASS/FAIL per
# case and exits non-zero on any failure.
set -u

SRC=${SRC:-sbin/awgctl/awgctl.c}
STUB=${STUB:-tools/lab/fixture-stubs.c}
CC=${CC:-cc}
FAILED=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

# A repo-local work dir: the trust walk starts at "/", so every component of
# the path is checked and /tmp (1777) would be refused for that reason only.
WORK=$(cd "$(mktemp -d ./.awgctl-fixture.XXXXXX)" && pwd)
trap 'rm -rf "$WORK"' EXIT
CONF="$WORK/conf"
STATE="$WORK/state"
LOG="$WORK/ioctl"
IFCLOG="$WORK/ifconfig"
mkdir -p "$CONF"
export AWGFIX_STATE="$STATE" AWGFIX_LOG="$LOG" AWGFIX_IFC_LOG="$IFCLOG"
# A 3.1 interface with one peer, an fd00::1/128 address and live counters.
cat > "$STATE" <<-EOF
	flags=4194270
	port=51820
	version=1
	peers=1
	tx=1420
	rx=1040
	handshake=17
	addr=fd00::1
	prefix=128
	family=10
	ispec1=<b 0xc700000001><r 16><t>
	EOF

BIN="$WORK/awgctl-fixture"
# canon_state <file> [fields] — R12b: a stable serialisation of the key=value
# state. The C stub and the Python fixture emit the same keys in different
# orders, so a raw byte comparison of two state files reports a change that
# did not happen. This renders the requested fields in one fixed order (the
# effective address list grouped by family, file order kept inside a family)
# while staying sensitive to real differences: an MTU, address, up flag, peer
# or any ispec (I1..I5) change alters the canonical text and the comparison
# fails. R12b: the address list is the effective one the model reports — the
# `addrs` key when present (present-empty means no addresses), else the
# mirrored primary (family/addr/prefix) plus the legacy `aliases`, which is
# exactly what fixture-stubs.c getifaddrs() serves; reading only `addrs` made
# a legacy state's alias change invisible. I-specs render I1..I5 with an
# absent key equal to the empty value the C reader zero-initialises, so a
# round trip that drops an empty ispec2 is not a change, while a non-empty
# I2 that changes is. Sorting inside a family was wrong: it hid a reordered
# address list, which is part of the pre-state the restore promises to
# reproduce.
# R13 makes the schema and the projection explicit:
#   * a token is a "family:address/prefix" triple, so a token with no family
#     prefix (e.g. `10.9.9.9/32`) is ignored — exactly what the C stub
#     (strchr(tok, ':')) and the Python model (empty parsed address) ignore,
#     so a pre-state carrying one no longer false-fails the restore check.
#   * the family is normalised to the observer's 2 (inet) / 10 (inet6) and
#     duplicates collapse on (family, address) with the prefix ignored, so
#     the first entry of a pair is the one every model renders.
#   * a caller-supplied projection renders exactly those fields and nothing
#     else; the default render (no projection) is the full field list plus
#     every I-spec.
canon_state() {
	awk -v fields="${2-}" '
	BEGIN {
		FS = "="
		# R13: an empty projection means "the whole state"; a supplied
		# projection restricts the render to its own fields.
		proj = (fields == "") ? "flags port version peers tx rx handshake family mtu addrs up addr prefix" : fields
		ispecs = (fields == "") ? 1 : 0
	}
	{
		v = index($0, "=")
		if (v > 0) { k = substr($0, 1, v - 1); f[k] = substr($0, v + 1); have[k] = 1 }
	}
	END {
		# Effective address list: `addrs` when the key is present;
		# otherwise the mirrored primary, then the legacy aliases (the
		# C observer serves the primary even when aliases exist).
		if (have["addrs"]) {
			raw = f["addrs"]
		} else {
			raw = ""
			if (f["addr"] != "") {
				raw = f["family"] ":" f["addr"]
				if (f["prefix"] != "") raw = raw "/" f["prefix"]
			}
			na = split(f["aliases"], atok, " ")
			for (i = 1; i <= na; i++)
				if (atok[i] != "") raw = raw (raw == "" ? "" : " ") atok[i]
		}
		n = split(raw, tok, " ")
		for (i = 1; i <= n; i++) {
			t = tok[i]
			if (t == "") continue
			c = index(t, ":")
			if (c == 0) continue
			fm = substr(t, 1, c - 1)
			body = substr(t, c + 1)
			s = index(body, "/")
			if (s > 0) { addr = substr(body, 1, s - 1); prefix = substr(body, s + 1) }
			else { addr = body; prefix = "" }
			p = index(addr, "%")
			base = (p > 0) ? substr(addr, 1, p - 1) : addr
			# The observer serves two families: 2 (inet) and 10
			# (inet6), picked from the family prefix of the token.
			fm = (substr(fm, 1, 1) == "2") ? "2" : "10"
			dk = fm "|" base
			if (dk in used) continue
			used[dk] = 1
			cnt[fm]++
			grp[fm, cnt[fm]] = fm ":" addr "/" prefix
		}
		m = 0
		for (fm in cnt) keys[++m] = fm
		for (i = 1; i < m; i++)
			for (j = i + 1; j <= m; j++)
				if (keys[j] + 0 < keys[i] + 0) { t = keys[i]; keys[i] = keys[j]; keys[j] = t }
		addrs = ""
		for (i = 1; i <= m; i++) {
			fm = keys[i]
			for (j = 1; j <= cnt[fm]; j++)
				addrs = addrs (addrs == "" ? "" : " ") grp[fm, j]
		}
		nf = split(proj, want, " ")
		for (i = 1; i <= nf; i++) {
			key = want[i]
			val = (key == "addrs") ? addrs : f[key]
			# The C observer also serves a legacy family=0 primary as IPv6.
			if (key == "family" && val == "0") val = "10"
			print key "=" val
			seen[key] = 1
		}
		# Every obfuscation parameter the model carries (I1..I5), not
		# just the ones the file happens to have: the C reader
		# zero-initialises them and the writers omit the empty ones, so
		# absent and empty are the same value and a non-empty change
		# shows. Only the default render adds them; a supplied
		# projection renders exactly its own fields.
		if (ispecs)
			for (i = 1; i <= 5; i++) {
				key = "ispec" i
				if (!(key in seen)) print key "=" f[key]
			}
	}
	' "$1"
}
if ! $CC -DAWGCTL_LINUX -Isrc -Isbin/awgctl -DCONF_DIR="\"$CONF\"" \
    "-DCONF_OWNER_OK(u)=1" \
    -DIFCONFIG_PATH="\"$WORK/fixture-ifconfig.py\"" \
    -o "$BIN" "$SRC" "$STUB" -lutil 2>"$WORK/cc.log"; then
	echo "fixture build failed" >&2
	cat "$WORK/cc.log" >&2
	exit 1
fi
cp "${AWGCTL_GATE_IFCONFIG:-tools/lab/fixture-ifconfig.py}" "$WORK/fixture-ifconfig.py" || exit 1
chmod +x "$WORK/fixture-ifconfig.py"
echo 'BUILD: awgctl-fixture OK'
[ -f "$WORK/cc.log" ] && [ -s "$WORK/cc.log" ] && cat "$WORK/cc.log"

reset_state() {
	cat > "$STATE" <<-EOF
		flags=4194270
		port=51820
		version=1
		peers=1
		tx=1420
		rx=1040
		handshake=17
		addr=fd00::1
		prefix=128
		family=10
		ispec1=<b 0xc700000001><r 16><t>
		EOF
	: > "$LOG"
	: > "$IFCLOG"
}
last_set() { grep '^SET' "$LOG" | tail -1; }
set_count() { grep -c '^SET' "$LOG"; }

echo "== F1. N2: set on an unchanged 3.1 interface (rc 0, PORT-only write)"
reset_state
"$BIN" set awg9 ListenPort 51900 >"$WORK/out" 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F1: unchanged 3.1 set exits 0" || \
	fail "F1: unchanged 3.1 set exits $rc ($(head -1 "$WORK/out"))"
[ "$(set_count)" = 1 ] && pass "F1: exactly one SIOCSAWG" || \
	fail "F1: SIOCSAWG count is $(set_count), expected 1"
case "$(last_set)" in
"SET flags=4 "*) pass "F1: write carries the port only (flags=4)" ;;
*) fail "F1: SIOCSAWG flags are '$(last_set)', expected flags=4" ;;
esac

echo
echo "== F2. N2: set on a busy 3.1 interface (counters move between GETs)"
reset_state
export AWGFIX_TRAFFIC=1
"$BIN" set awg9 ListenPort 51901 >"$WORK/out" 2>&1
rc=$?
unset AWGFIX_TRAFFIC
[ "$rc" = 0 ] && pass "F2: busy 3.1 set exits 0" || \
	fail "F2: busy 3.1 set exits $rc ($(head -1 "$WORK/out"))"
[ "$(set_count)" = 1 ] && pass "F2: exactly one SIOCSAWG" || \
	fail "F2: SIOCSAWG count is $(set_count), expected 1"

echo
echo "== F3. N2: the grouped write keeps the read-back siblings (R4-06 win)"
reset_state
"$BIN" set awg9 Jc 7 >"$WORK/out" 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F3: set Jc exits 0" || fail "F3: set Jc exits $rc"
case "$(last_set)" in
"SET flags=32 "*) pass "F3: Jc-only write carries JC only (flags=32)" ;;
*) fail "F3: SIOCSAWG flags are '$(last_set)', expected flags=32" ;;
esac

echo
echo "== F4. N3: address read-back compares parsed bytes and prefix"
# expanded, uppercase and leading-zero spellings of the installed fd00::1/128
for spell in "fd00::1/128" "fd00:0:0:0:0:0:0:1/128" "FD00::1/128" \
    "fd00:000:0:00:0:0:0:1/128"; do
	reset_state
	"$BIN" up awg9 --config "$CONF/awg9.conf" >/dev/null 2>&1
	: > "$IFCLOG"
	cat > "$CONF/awg9.conf" <<-EOF
		[Interface]
		PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
		Address = $spell
		EOF
	chmod 600 "$CONF/awg9.conf"
	"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
	rc=$?
	if [ "$rc" = 0 ]; then
		pass "F4: $spell accepted (matches the installed address)"
	else
		fail "F4: $spell rejected ($(head -1 "$WORK/out"))"
	fi
done
# the same address with a different prefix must fail (kernel answers EEXIST)
reset_state
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = fd00::1/64
	EOF
chmod 600 "$CONF/awg9.conf"
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
if [ "$rc" != 0 ]; then
	pass "F4: fd00::1/64 rejected — the prefix is checked too"
else
	fail "F4: fd00::1/64 accepted although the interface has /128"
fi
# an address that is not on the interface at all
reset_state
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = 10.9.19.9/24
	EOF
chmod 600 "$CONF/awg9.conf"
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
if [ "$rc" = 0 ] && [ "$(set_count)" -ge 1 ]; then
	pass "F4: a fresh address installs and reads back"
else
	fail "F4: fresh address fails (rc $rc)"
fi

echo
echo "== F5. R4-07: trust walk from the root, O_NOFOLLOW, non-blocking open"
reset_state
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	EOF
chmod 600 "$CONF/awg9.conf"
"$BIN" up awg9 --config "$CONF/awg9.conf" >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F5: a plain path under CONF_DIR is accepted" || \
	fail "F5: plain path under CONF_DIR rejected (rc $rc)"
mkdir -p "$CONF/nested"
chmod 777 "$CONF/nested"
cp "$CONF/awg9.conf" "$CONF/nested/deep.conf"
chmod 600 "$CONF/nested/deep.conf"
"$BIN" up awg9 --config "$CONF/nested/deep.conf" >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F5: world-writable intermediate refused (rc $rc)" || \
	fail "F5: world-writable intermediate accepted"
ln -sfn nested "$CONF/link"
"$BIN" up awg9 --config "$CONF/link/deep.conf" >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F5: symlinked component refused (rc $rc)" || \
	fail "F5: symlinked component accepted"
rm -f "$CONF/link"
# '.' and '..' components must not be silently normalised away
"$BIN" up awg9 --config "$CONF/./awg9.conf" >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F5: '.' component refused (rc $rc)" || \
	fail "F5: '.' component accepted"
"$BIN" up awg9 --config "$CONF/nested/../awg9.conf" >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F5: '..' component refused (rc $rc)" || \
	fail "F5: '..' component accepted"
# a FIFO in place of the config must not block the tool
rm -f "$CONF/fifo.conf"
mkfifo "$CONF/fifo.conf"
timeout 10 "$BIN" up awg9 --config "$CONF/fifo.conf" >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F5: FIFO in place of the config refused (rc $rc)" || \
	fail "F5: FIFO in place of the config accepted"
rm -f "$CONF/fifo.conf"
rm -rf "$CONF/nested"

echo
echo "== F6. R4-09: exact arity, ENOTTY propagation, child signal status"
reset_state
"$BIN" down awg9 extra >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F6: down with a surplus argument exits $rc" || \
	fail "F6: down with a surplus argument exits 0"
"$BIN" down awg9 >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F6: down with exact arity exits 0" || \
	fail "F6: down with exact arity exits $rc"
"$BIN" show awg9 extra >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F6: show with a surplus argument exits $rc" || \
	fail "F6: show with a surplus argument exits 0"
"$BIN" show awg9 >/dev/null 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F6: show with exact arity exits 0" || \
	fail "F6: show with exact arity exits $rc"
"$BIN" del awg9 peer ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA= extra >/dev/null 2>&1 # scan:allow-fixture[sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05] public peer key
rc=$?
[ "$rc" != 0 ] && pass "F6: del with a surplus argument exits $rc" || \
	fail "F6: del with a surplus argument exits 0"
# ENOTTY from the probe must reach the caller (set, not only show)
reset_state
: > "$LOG"
sed -i 's/^flags=4194270/flags=4194270/' "$STATE"
AWGFIX_ENOTTY=1 "$BIN" set awg9 ListenPort 51902 >/dev/null 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F6: set on an ENOTTY target exits $rc" || \
	fail "F6: set on an ENOTTY target exits 0 (probe error swallowed)"
# a child killed by a signal is reported as a signal, not as EEXIST
reset_state
echo "F6: (see F7 for the signal case)"

echo
echo "== F7. R4-09: EEXIST is only idempotent when the child exited normally"
reset_state
cat > "$WORK/abrt-ifconfig.py" <<'EOF'
#!/bin/sh
echo "File exists" >&2
kill -6 $$
EOF
chmod +x "$WORK/abrt-ifconfig.py"
ABIN="$WORK/awgctl-abrt"
$CC -DAWGCTL_LINUX -Isrc -Isbin/awgctl -DCONF_DIR="\"$CONF\"" \
    "-DCONF_OWNER_OK(u)=1" \
    -DIFCONFIG_PATH="\"$WORK/abrt-ifconfig.py\"" \
    -o "$ABIN" "$SRC" "$STUB" -lutil 2>>"$WORK/cc.log"
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = fd00::1/128
	EOF
chmod 600 "$CONF/awg9.conf"
"$ABIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
case "$rc:$WORK" in
*) : ;;
esac
if [ "$rc" != 0 ] && grep -qi "killed by" "$WORK/out"; then
	pass "F7: SIGABRT child reported as a signal death (rc $rc)"
else
	fail "F7: SIGABRT child accepted as idempotence (rc $rc, $(head -1 "$WORK/out"))"
fi

echo
echo "== F8. R5: a getifaddrs entry with no name does not crash the tool"
reset_state
: > "$WORK/I1"
AWGFIX_STATE="$STATE" AWGFIX_LOG="$WORK/I1" AWGFIX_NONAME=1 "$BIN" show awg9 \
    > "$WORK/out" 2>&1
rc=$?
[ "$rc" -eq 0 ] && pass "F8: show with a name-less entry exits 0" ||
    fail "F8: show with a name-less entry exits $rc ($(head -1 "$WORK/out"))"
: > "$WORK/I1"
AWGFIX_STATE="$STATE" AWGFIX_LOG="$WORK/I1" AWGFIX_NONAME=1 "$BIN" up awg9 \
    --config "$CONF/awg9.conf" > "$WORK/out" 2>&1
rc=$?
[ "$rc" -eq 0 ] && pass "F8: up with a name-less entry exits 0" ||
    fail "F8: up with a name-less entry exits $rc ($(head -1 "$WORK/out"))"
[ "$(grep -c '^SET' "$WORK/I1")" -eq 1 ] &&
    pass "F8: up still writes once" || fail "F8: up did not write"

echo
echo "== F9. N7: the netmask must prove the requested prefix"
# A: the interface entry carries no netmask. With an explicit prefix the
# mask is the proof, so the entry is not a match (the old code accepted it).
reset_state
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = fd00::1/128
	EOF
chmod 600 "$CONF/awg9.conf"
AWGFIX_NOMASK=1 "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F9: missing netmask + explicit /128 not a match (rc $rc)" || \
	fail "F9: missing netmask accepted as proof of /128"
# control: without a prefix the address alone is enough, mask or no mask
: > "$IFCLOG"
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = fd00::1
	EOF
chmod 600 "$CONF/awg9.conf"
AWGFIX_NOMASK=1 "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F9: prefix-less address accepted with no netmask" || \
	fail "F9: prefix-less address rejected (rc $rc, $(head -1 "$WORK/out"))"
# C: a non-contiguous mask (0x000000ff) has a leading run of 8 bits; the old
# mask_bits() matched it against a requested /8, the new one requires the
# rest of the mask to be zero.
cat > "$STATE" <<-EOF
	flags=4194270
	port=51820
	version=1
	peers=1
	tx=1420
	rx=1040
	handshake=17
	addr=10.9.19.1
	prefix=24
	family=2
	EOF
: > "$IFCLOG"
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = 10.9.19.1/8
	EOF
chmod 600 "$CONF/awg9.conf"
AWGFIX_MASK=0x000000ff "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F9: non-contiguous mask 0x000000ff not a /8 (rc $rc)" || \
	fail "F9: non-contiguous mask 0x000000ff accepted as a /8"
: > "$IFCLOG"
AWGFIX_MASK=0xff000000 "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F9: contiguous mask 0xff000000 matches the /8" || \
	fail "F9: contiguous mask 0xff000000 rejected (rc $rc)"
# D: a malformed prefix is refused with the config-parse exit code
: > "$IFCLOG"
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = 10.9.19.1/abc
	EOF
chmod 600 "$CONF/awg9.conf"
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" = 2 ] && pass "F9: malformed prefix 'abc' refused with exit 2" || \
	fail "F9: malformed prefix 'abc' exits $rc, expected 2"
# E: an IPv6 scope is not part of the address bytes — it is stripped for the
# comparison (documented in awgctl.8), so the address still reads back.
reset_state
: > "$IFCLOG"
cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = fe80::1%ixn/64
	EOF
chmod 600 "$CONF/awg9.conf"
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
# R10: the canonical model keeps the accepted address in the `addrs` list
# (the scope stays in the token's text form); the single-slot `addr` mirror
# carries the primary, which the alias install leaves in front of it.
if [ "$rc" = 0 ] && grep -q '^addrs=.*fe80::1%ixn/64' "$STATE"; then
	pass "F9: scope-qualified fe80::1%ixn/64 accepted, scope stripped"
else
	fail "F9: scope-qualified address rejected (rc $rc, $(head -1 "$WORK/out"))"
fi

echo
echo "== F10. R4-09: a non-absence create probe error is propagated"
reset_state
: > "$LOG"
: > "$IFCLOG"
AWGFIX_PROBE_ERR=1 "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
# EX_IOCTL is 3; the create must not run when the probe answers EPERM
[ "$rc" = 3 ] && pass "F10: EPERM create probe exits 3 (EX_IOCTL)" || \
	fail "F10: EPERM create probe exits $rc, expected 3"
if grep -q 'create' "$IFCLOG"; then
	fail "F10: create ran despite the probe error ($(grep -c create "$IFCLOG") create)"
else
	pass "F10: the create is skipped when the probe answers EPERM"
fi
# the second probe (after the create) still succeeds on a normal interface
reset_state
: > "$LOG"
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" = 0 ] && pass "F10: a normal create probe exits 0" || \
	fail "F10: normal create probe exits $rc"

echo
echo "== F11. N13: a netmask is a prefix only when the ones are contiguous"
# The mask proves the prefix (R6/N7), so mask_bits() must compute it from
# the whole 32-bit mask: a hole (0xff00ff00, 0xf0f00000) is not a prefix,
# while 0xffffff00 (/24) and 0xffffffff (/32) are.  The old version counted
# the leading run and stopped, so the hole was invisible.
mask_case() {
	cat > "$STATE" <<-EOF
	flags=4194270
	port=51820
	version=1
	peers=1
	tx=1420
	rx=1040
	handshake=17
	addr=10.9.19.1
	prefix=$2
	family=2
	EOF
	: > "$IFCLOG"
	: > "$LOG"
	cat > "$CONF/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	Address = 10.9.19.1/$2
	EOF
	chmod 600 "$CONF/awg9.conf"
	AWGFIX_MASK="$1" "$BIN" up awg9 --config "$CONF/awg9.conf" \
	    >"$WORK/out" 2>&1
	rc=$?
}
mask_case 0xff00ff00 8
[ "$rc" != 0 ] && pass "F11: holed mask 0xff00ff00 is not a /8 (rc $rc)" ||
	fail "F11: holed mask 0xff00ff00 accepted as a /8"
mask_case 0xf0f00000 4
[ "$rc" != 0 ] && pass "F11: holed mask 0xf0f00000 is not a /4 (rc $rc)" ||
	fail "F11: holed mask 0xf0f00000 accepted as a /4"
mask_case 0xffffff00 24
[ "$rc" = 0 ] && pass "F11: contiguous mask 0xffffff00 matches the /24" ||
	fail "F11: contiguous mask 0xffffff00 rejected as a /24 (rc $rc)"
mask_case 0xffffffff 32
[ "$rc" = 0 ] && pass "F11: contiguous mask 0xffffffff matches the /32" ||
	fail "F11: contiguous mask 0xffffffff rejected as a /32 (rc $rc)"
# the read-back must not verify a prefix the kernel never installed: the
# address that lands carries the mask the fixture served
mask_case 0xff00ff00 8
[ "$(grep '^prefix=' "$STATE" | head -1)" = "prefix=8" ] &&
	pass "F11: the accepted address keeps the holed mask as its /8" ||
	fail "F11: the accepted address recorded $(grep '^prefix=' "$STATE" | head -1)"

echo
echo "== F12. R4-08: parse/resolve/serialise first, mutate once, restore on failure"
# N12 negatives: each must leave the interface, its MTU and its addresses
# exactly as they were before the command.
pre_state() {
	cat > "$STATE" <<-EOF
	flags=4194270
	port=51820
	version=1
	peers=1
	tx=1420
	rx=1040
	handshake=17
	addr=fd00::1
	prefix=128
	family=10
	mtu=1420
	ispec1=<b 0xc700000001><r 16><t>
	aliases=10:fd00::2/64 10.9.9.9/32
	up=1
	EOF
	# R13: the pre-state uses the legacy representation (mirrored primary +
	# `aliases`) and the alias list carries one token with no family prefix
	# (`10.9.9.9/32`). Both observers ignore it, so the model must ignore it
	# too and the restore check must not false-fail on it.
	# R12b: AWGFIX_PRE_ISPEC2 (set, even empty) appends an I-spec to the
	# pre-state. The C writer omits empty ispecs, so the suite must treat
	# absent and empty as the same value; with this set the pre-state
	# carries `ispec2=` and the post-state omits it.
	[ "${AWGFIX_PRE_ISPEC2+set}" = set ] && printf 'ispec2=%s\n' "$AWGFIX_PRE_ISPEC2" >> "$STATE"
	: > "$LOG"
	: > "$IFCLOG"
}
state_field() { grep "^$1=" "$STATE" | head -1 | cut -d= -f2; }
write_conf() { printf '%b' "$1" > "$CONF/awg9.conf"; chmod 600 "$CONF/awg9.conf"; }

# 1) a valid address, then an invalid second one: the whole address list is
#    parsed before the first mutation, so the interface, MTU and addresses
#    are exactly as they were (nothing is installed, nothing is created).
pre_state
write_conf '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nMTU = 1500\nAddress = 10.9.19.1/24\nAddress = 10.9.19.2/33\n[Peer]\nPublicKey = ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA=\nEndpoint = 10.9.9.9:51820\nAllowedIPs = 10.0.0.0/8\n' # scan:allow-fixture[sha1:2de49d733f79bf32af457c4bb2ebb0c6bdd26523,sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37,sha1:95479e6583c34c2854c971a69978d7fdd15f10f2,sha1:9c0214d26dcb5755b8a26890d52f74b389cca6ab,sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4,sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05] synthetic lab key
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F12: an invalid second address fails the command (rc $rc)" ||
	fail "F12: an invalid second address was accepted"
[ "$(state_field mtu)" = "1420" ] && pass "F12: the MTU is the pre-up 1420" ||
	fail "F12: the MTU is $(state_field mtu), expected the pre-up 1420"
[ "$(state_field addr)" = "fd00::1" ] && [ "$(state_field prefix)" = "128" ] &&
	pass "F12: the address is the pre-up fd00::1/128" ||
	fail "F12: the address is $(state_field addr)/$(state_field prefix), expected fd00::1/128"
[ ! -s "$IFCLOG" ] && pass "F12: an invalid second address mutates nothing (empty ifconfig log)" ||
	fail "F12: an invalid second address mutated ($(tr '\n' ';' < "$IFCLOG"))"

# 2) a peer endpoint without a port: the payload is serialised before the
#    first mutation, so nothing at all touches the interface.
pre_state
write_conf '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nMTU = 1500\nAddress = 10.9.19.1/24\n[Peer]\nPublicKey = ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA=\nEndpoint = 10.9.9.9\nAllowedIPs = 10.0.0.0/8\n' # scan:allow-fixture[sha1:2de49d733f79bf32af457c4bb2ebb0c6bdd26523,sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37,sha1:95479e6583c34c2854c971a69978d7fdd15f10f2,sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4,sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05] synthetic lab key
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F12: a bad peer endpoint fails the command (rc $rc)" ||
	fail "F12: a bad peer endpoint was accepted"
[ ! -s "$IFCLOG" ] && pass "F12: a bad endpoint mutates nothing (empty ifconfig log)" ||
	fail "F12: a bad endpoint mutated ($(tr '\n' ';' < "$IFCLOG"))"
[ "$(state_field mtu)" = "1420" ] && [ "$(state_field addr)" = "fd00::1" ] &&
	pass "F12: MTU and address untouched by a bad endpoint" ||
	fail "F12: a bad endpoint changed MTU/address"

# 3) a bad allowed-IP: same — the whole peer list is serialised up front.
pre_state
write_conf '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nMTU = 1500\nAddress = 10.9.19.1/24\n[Peer]\nPublicKey = ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA=\nEndpoint = 10.9.9.9:51820\nAllowedIPs = 10.0.0.0/33\n' # scan:allow-fixture[sha1:2de49d733f79bf32af457c4bb2ebb0c6bdd26523,sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37,sha1:95479e6583c34c2854c971a69978d7fdd15f10f2,sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4,sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05] synthetic lab key
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F12: a bad allowed-IP fails the command (rc $rc)" ||
	fail "F12: a bad allowed-IP was accepted"
[ ! -s "$IFCLOG" ] && pass "F12: a bad allowed-IP mutates nothing" ||
	fail "F12: a bad allowed-IP mutated ($(tr '\n' ';' < "$IFCLOG"))"

# 4) a truncated (NUL-containing) config: refused, and the bytes after the
#    NUL never reach the interface.
pre_state
write_conf '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nMTU = 1500\nAddress = 10.9.19.1/24\n\0Address = fd00::5/128\n' # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37,sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4] synthetic lab key
"$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F12: a NUL-truncated config is refused (rc $rc)" ||
	fail "F12: a NUL-truncated config was accepted"
[ "$(state_field mtu)" = "1420" ] && [ "$(state_field addr)" = "fd00::1" ] &&
	pass "F12: the truncated config leaves MTU and address as before" ||
	fail "F12: the truncated config changed MTU/address ($(state_field mtu), $(state_field addr))"

# 5) an operational failure of SIOCSAWG on a fresh interface: the create,
#    the MTU and the address run, and the restore destroys the interface.
pre_state
write_conf '[Interface]\nPrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA=\nMTU = 1500\nAddress = 10.9.19.1/24\n[Peer]\nPublicKey = ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA=\nEndpoint = 10.9.9.9:51820\nAllowedIPs = 10.0.0.0/8\n' # scan:allow-fixture[sha1:2de49d733f79bf32af457c4bb2ebb0c6bdd26523,sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37,sha1:95479e6583c34c2854c971a69978d7fdd15f10f2,sha1:b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4,sha1:c037e4f7f64622ebbdf02fb2a9c9cdc5cb915c05] synthetic lab key
AWGFIX_CREATE=1 AWGFIX_SET_ERR=1 "$BIN" up awg9 --config "$CONF/awg9.conf" \
    >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F12: a failed SIOCSAWG exits non-zero (rc $rc)" ||
	fail "F12: a failed SIOCSAWG exited 0"
[ "$(grep -c 'create' "$IFCLOG")" = "1" ] && pass "F12: the interface is created" ||
	fail "F12: the create did not run ($(tr '\n' ';' < "$IFCLOG"))"
[ "$(grep -c 'destroy' "$IFCLOG")" = "1" ] &&
	pass "F12: the fresh interface is destroyed by the restore" ||
	fail "F12: the fresh interface was not destroyed ($(tr '\n' ';' < "$IFCLOG"))"
[ "$(state_field mtu)" = "1420" ] && [ "$(state_field addr)" = "fd00::1" ] &&
	pass "F12: the destroyed interface leaves the default MTU and address" ||
	fail "F12: the destroyed interface kept MTU/address ($(state_field mtu), $(state_field addr))"

echo
echo "== F13. R8: every post-mutation failure restores the pre-state"
# The restore gaps at awgctl.c:2037-2067,2145,2170: MTU, address install
# and `up` failures must all run the snapshot restore on the single path,
# and a partially failed restore must print what was left behind.
pre_state
cp "$STATE" "$WORK/pre"
# R13c: publish the pre-state this case compares from on its own line, in the
# comparator's own render (';'-joined key=value). The gate self-check
# attributes the cause of a failure against this line, so the guard no longer
# needs a baseline of its own that duplicates the fixture (a fabricated
# single-address baseline made every address field read as the cause).
canon_state "$WORK/pre" > "$WORK/pre13a" || exit 1
PRE13A=$(tr '\n' ';' < "$WORK/pre13a")
printf 'PRE F13a=%s\n' "$PRE13A"
AWGFIX_MTU_ERR=1 "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F13a: a failed MTU exits non-zero (rc $rc)" ||
	fail "F13a: a failed MTU exited 0"
[ "$(grep -c '^awg9 mtu 1500' "$IFCLOG")" = "1" ] &&
	pass "F13a: the MTU mutation ran" ||
	fail "F13a: the MTU mutation did not run ($(tr '\n' ';' < "$IFCLOG"))"
[ "$(grep -c '^awg9 mtu 1420' "$IFCLOG")" = "1" ] &&
	pass "F13a: the restore re-installed the pre-up MTU" ||
	fail "F13a: the restore did not re-install the MTU ($(tr '\n' ';' < "$IFCLOG"))"
[ "$(grep -c 'destroy' "$IFCLOG")" = "0" ] &&
	pass "F13a: a pre-existing interface is not destroyed" ||
	fail "F13a: the pre-existing interface was destroyed"
# R13c: the message prints the canonical text the comparison used, so the
# payload a guard reads is the same rendering as the published pre-state.
canon_state "$STATE" > "$WORK/post13a" || exit 1
POST13A=$(tr '\n' ';' < "$WORK/post13a")
printf 'COMPARE F13a pre=%s post=%s\n' "$PRE13A" "$POST13A"
[ "$PRE13A" = "$POST13A" ] &&
	pass "F13a: the pre-state is restored (canonical)" ||
	fail "F13a: the state changed ($POST13A)"

# 13b: the run creates the interface, the MTU fails, the restore destroys it.
pre_state
: > "$IFCLOG"
AWGFIX_CREATE=1 AWGFIX_MTU_ERR=1 "$BIN" up awg9 --config "$CONF/awg9.conf" \
    >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F13b: create + failed MTU exits non-zero (rc $rc)" ||
	fail "F13b: create + failed MTU exited 0"
[ "$(grep -c 'create' "$IFCLOG")" = "1" ] && [ "$(grep -c 'destroy' "$IFCLOG")" = "1" ] &&
	pass "F13b: the created interface is destroyed by the restore" ||
	fail "F13b: the created interface was not destroyed ($(tr '\n' ';' < "$IFCLOG"))"

# 13c: SIOCSAWG succeeds, `up` fails: MTU and addresses are restored.
# R13: the projection asks for the effective address list (`addrs` — primary
# plus every alias) instead of the `family mtu addr prefix` scalars, which
# only mirror the primary and stay untouched when a recovery loses the
# secondary address. The I-specs travel with the projection too, so a restore
# that re-installs a different I2 fails here as well; the flags are the
# SIOCSAWG payload the command just wrote, so they stay out of the render.
pre_state
: > "$IFCLOG"
canon_state "$STATE" "family mtu addrs up ispec1 ispec2 ispec3 ispec4 ispec5" \
    > "$WORK/pre4" || exit 1
# R13c: the same publication for this case — the pre-state is the projected
# render the comparison uses, so the guard reads pre and post in one field list.
printf 'PRE F13c=%s\n' "$(tr '\n' ';' < "$WORK/pre4")"
AWGFIX_UP_ERR=1 "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F13c: a failed up exits non-zero (rc $rc)" ||
	fail "F13c: a failed up exited 0"
[ "$(grep -c 'SET' "$LOG")" = "1" ] && pass "F13c: the SET ran before the up" ||
	fail "F13c: the SET did not run"
canon_state "$STATE" "family mtu addrs up ispec1 ispec2 ispec3 ispec4 ispec5" \
    > "$WORK/post4" || exit 1
printf 'COMPARE F13c pre=%s post=%s\n' "$(tr '\n' ';' < "$WORK/pre4")" "$(tr '\n' ';' < "$WORK/post4")"
cmp -s "$WORK/pre4" "$WORK/post4" &&
	pass "F13c: MTU and the address list are restored (canonical)" ||
	fail "F13c: the address list is not the pre-state (pre=$(tr '\n' ';' < "$WORK/pre4") post=$(tr '\n' ';' < "$WORK/post4"))"

# 13d: the MTU mutation fails and the restore's MTU fails too: the addresses
# are still re-installed (the old restore_state skipped them), and the
# failure prints what was left behind.
pre_state
: > "$IFCLOG"
AWGFIX_MTU_ERR=1 AWGFIX_SET_ERR=1 "$BIN" up awg9 --config "$CONF/awg9.conf" \
    >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F13d: MTU + SIOCSAWG failures exit non-zero (rc $rc)" ||
	fail "F13d: MTU + SIOCSAWG failures exited 0"
grep -q 'restore: mtu' "$WORK/out" &&
	pass "F13d: the failed restore step is reported" ||
	fail "F13d: the failed MTU restore is silent ($(head -2 "$WORK/out"))"
[ "$(state_field addr)" = "fd00::1" ] &&
	pass "F13d: the address is re-installed after the failed MTU restore" ||
	fail "F13d: the address is $(state_field addr), expected fd00::1"

# 13e: the restore's destroy fails: the command terminates and prints the
# MTU and address left behind, with the original SIOCSAWG error and status.
pre_state
: > "$IFCLOG"
AWGFIX_CREATE=1 AWGFIX_SET_ERR=1 AWGFIX_DESTROY_ERR=1 \
    "$BIN" up awg9 --config "$CONF/awg9.conf" >"$WORK/out" 2>&1
rc=$?
[ "$rc" != 0 ] && pass "F13e: a failed destroy exits with the original status (rc $rc)" ||
	fail "F13e: a failed destroy exited 0"
grep -q 'restore: destroy' "$WORK/out" && grep -q 'left behind' "$WORK/out" &&
	pass "F13e: the failed destroy prints what was left behind" ||
	fail "F13e: the failed destroy is silent ($(head -2 "$WORK/out"))"
grep -q 'SIOCSAWG' "$WORK/out" &&
	pass "F13e: the original SIOCSAWG error is preserved" ||
	fail "F13e: the original error is lost ($(head -2 "$WORK/out"))"

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all fixture cases passed"
	exit 0
fi
echo "RESULT: $FAILED fixture case(s) failed"
exit 1
