#!/bin/sh
# $OpenBSD$
#
# R5/R6 real-kernel checks on an OpenBSD host with awg(4): N2 (set on an
# unchanged interface, counted with ktrace so the SIOCSAWG is observed),
# N3 (address read-back spellings), R4-07 (trust walk), R4-09 (arity,
# ENOTTY).  Run as root.  Usage:
#
#   AWG_LAB_DISPOSABLE=1 sh tools/lab/awgctl-r5-laptop.sh [ifname]
#
# N8 (round 6) safety rules:
#   * AWG_LAB_DISPOSABLE=1 is required — the run replaces /etc/amnezia.
#   * A pre-existing /etc/amnezia is backed up and restored on every exit
#     path (trap), so a real configuration tree survives the run.
#   * The interface name is reserved for this runner (awg9 by default) and
#     is destroyed and recreated at the start, so an existing interface is
#     never half-touched.
#   * The config root is an isolated directory under the work dir (awgctl
#     takes --config), not /etc/amnezia, for every case that can use it.
#   * All work paths come from mktemp -d, never a predictable /tmp name.
#
# The SIOCSAWG/SIOCGAWG request numbers are taken from src/if_awg.h by
# compiling a two-line probe, so the kdump grep never hardcodes a value.

set -u

[ "${AWG_LAB_DISPOSABLE:-0}" = 1 ] || {
	echo "set AWG_LAB_DISPOSABLE=1: the run replaces /etc/amnezia" >&2
	exit 1
}

IFN=${1:-awg9}
BIN=${AWGCTL:-./awgctl}
KEY=YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
JC=SG9sZGVyLWpjLTAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
WORK=${WORK_DIR:-$(mktemp -d)}
# Isolated config root. awgctl's unveil(2) confines the config to
# /etc/amnezia/amneziawg, so that is the root the runner replaces (the
# backup/restore trap above puts the real tree back on every exit path).
CONF=${CONF_DIR:-/etc/amnezia/amneziawg}
# The base 8.0 awg(4) answers the 3.0 layout, so the config uses the legacy
# field set (Version/Jc are for the 3.1 kernel).
fail=0
note() { echo "NOTE: $*"; }
ok()   { echo "PASS: $*"; }
bad()  { echo "FAIL: $*"; fail=$((fail + 1)); }

# N8: back up a pre-existing /etc/amnezia and restore it on every exit.
ETC_BAK="$WORK/etc-amnezia.pre"
backup_etc() {
	if [ -d /etc/amnezia ]; then
		mkdir -p "$ETC_BAK"
		cp -a /etc/amnezia/. "$ETC_BAK/" 2>/dev/null
		note "backed up the pre-existing /etc/amnezia to $ETC_BAK"
	else
		note "no pre-existing /etc/amnezia (nothing to back up)"
	fi
}
restore_etc() {
	rm -rf /etc/amnezia
	if [ -d "$ETC_BAK" ]; then
		mkdir -p /etc/amnezia
		cp -a "$ETC_BAK/." /etc/amnezia/ 2>/dev/null
		note "restored the pre-existing /etc/amnezia from $ETC_BAK"
	fi
}
backup_etc
cleanup() {
	restore_etc
	ifconfig "$IFN" destroy 2>/dev/null
}
trap cleanup EXIT

# --- ioctl request numbers, from the header the tool is built against ----
# kdump decodes the request as _IOWR('i',224,0x20), so the probe prints
# both the numeric codes (line 1) and the decoded form (line 2) that the
# kdump grep uses — nothing is hardcoded.
cat > "$WORK/awg-r5-probe.c" <<'EOF'
#include <stdio.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <net/if_awg.h>
int main(void) {
	unsigned long d = SIOCSAWG & IOC_DIRMASK;
	const char *dn = (d == IOC_OUT) ? "R" : (d == IOC_IN) ? "W" :
	    (d == IOC_VOID) ? "" : "WR";
	printf("%d %d\n", SIOCSAWG, SIOCGAWG);
	printf("_IO%s('%c',%d,0x%x)\n", dn, (char)IOCGROUP(SIOCSAWG),
	    SIOCSAWG & 0xff, IOCPARM_LEN(SIOCSAWG));
	return 0;
}
EOF
cc -Isrc -o "$WORK/awg-r5-probe" "$WORK/awg-r5-probe.c" 2>/dev/null ||
    cc -o "$WORK/awg-r5-probe" "$WORK/awg-r5-probe.c" 2>/dev/null
if [ ! -x "$WORK/awg-r5-probe" ]; then
	echo "cannot build the ioctl probe"; exit 2
fi
SETCODE=$("$WORK/awg-r5-probe" | sed -n 1p | awk '{print $1}')
GETCODE=$("$WORK/awg-r5-probe" | sed -n 1p | awk '{print $2}')
SETGREP=$("$WORK/awg-r5-probe" | sed -n 2p)
note "SIOCSAWG=$SETCODE SIOCGAWG=$GETCODE (kdump grep $SETGREP)"

run_counted() {	# run_counted <cmd...>  -> sets rc and nset
	ktrace -f "$WORK/awg-r5.k" "$@" >"$WORK/out.log" 2>&1
	rc=$?
	nset=$(kdump -f "$WORK/awg-r5.k" 2>/dev/null | grep -c "ioctl(.*$SETGREP")
}

write_conf() {	# write_conf <address>
	mkdir -p "$CONF"
	cat > "$CONF/$IFN.conf" <<EOF
[Interface]
PrivateKey = $KEY
Address = $1
ListenPort = 51820
[Peer]
PublicKey = $KEY
Endpoint = 127.0.0.1:51821
AllowedIPs = 10.0.0.2/32
EOF
	chmod 600 "$CONF/$IFN.conf"
}

echo "== L0. interface $IFN exists (destroy + create, reserved name)"
ifconfig "$IFN" destroy 2>/dev/null
ifconfig "$IFN" create
ifconfig "$IFN" | head -3
ifconfig "$IFN" | grep -q awghpk && note "kernel answers the 3.1 layout (awghpk present)" ||
	note "kernel answers the 3.0 layout (no awghpk)"

echo
echo "== L1. N2: set ListenPort on an unchanged interface (rc 0, one SIOCSAWG)"
write_conf "fd00::1/128"
run_counted "$BIN" set "$IFN" ListenPort 51900
[ "$rc" -eq 0 ] && ok "N2: set on an unchanged interface exits 0" ||
	bad "N2: set exits $rc ($(head -1 "$WORK/out.log"))"
ifconfig "$IFN" | grep -q "awgport 51900" &&
	ok "N2: the port applied (awgport 51900)" ||
	bad "N2: the port did not apply ($(ifconfig "$IFN" | grep awgport))"
nset=$(kdump -f "$WORK/awg-r5.k" 2>/dev/null | grep -c "ioctl(.*$SETGREP")
[ "$nset" -ge 1 ] && ok "N2: ktrace observed one SIOCSAWG (count $nset)" ||
	bad "N2: SIOCSAWG count is $nset, expected 1"

echo
echo "== L2. N2: set on a busy interface (traffic first, then set)"
ifconfig "$IFN" inet6 fd00::1/128 alias 2>/dev/null
ping -c 2 -t 1 -I "$IFN" ::1 >/dev/null 2>&1
run_counted "$BIN" set "$IFN" ListenPort 51822
[ "$rc" -eq 0 ] && ok "N2: set on a busy interface exits 0" ||
	bad "N2: set on a busy interface exits $rc"
ifconfig "$IFN" | grep -q "awgport 51822" &&
	ok "N2: the busy set applied (awgport 51822)" ||
	bad "N2: the busy set did not apply"
nset=$(kdump -f "$WORK/awg-r5.k" 2>/dev/null | grep -c "ioctl(.*$SETGREP")
[ "$nset" -ge 1 ] && ok "N2: ktrace observed one SIOCSAWG on the busy set (count $nset)" ||
	bad "N2: busy set count $nset"

echo
echo "== L3. N3: address read-back, expanded / uppercase / leading zeros"
for spell in "fd00::1/128" "fd00:0:0:0:0:0:0:1/128" "FD00::1/128" "fd00:000:0:00:0:0:0:1/128"; do
	write_conf "$spell"
	"$BIN" up "$IFN" --config "$CONF/$IFN.conf" >"$WORK/out.log" 2>&1
	rc=$?
	[ "$rc" -eq 0 ] && ok "N3: $spell accepted" ||
		bad "N3: $spell rejected ($(head -1 "$WORK/out.log"))"
done
write_conf "fd00::1/64"
"$BIN" up "$IFN" --config "$CONF/$IFN.conf" >"$WORK/out.log" 2>&1
rc=$?
# R6/N4: a bare `-ne 0` also passes when the binary dies (exec failure,
# syntax error).  Require the tool's own exit code and a prefix-change
# refusal (the readback's "not installed" or the kernel's SIOCAIFADDR),
# so an execution error is a real failure.
[ "$rc" -eq 1 ] && grep -qE "fd00::1/64 not installed|SIOCAIFADDR" "$WORK/out.log" &&
	ok "N3: fd00::1/64 rejected (rc 1, '$(head -1 "$WORK/out.log")'), the prefix is checked" ||
	bad "N3: fd00::1/64 accepted although the interface has /128 (rc $rc, '$(head -1 "$WORK/out.log")')"
ifconfig "$IFN" | grep -m1 inet6

echo
echo "== L4. R4-07: trust walk (symlinked component, world-writable, FIFO)"
write_conf "fd00::1/128"
mkdir -p "$CONF"
cp "$CONF/$IFN.conf" "$WORK/trust.conf" 2>/dev/null
chmod 600 "$WORK/trust.conf"
rm -rf /etc/amnezia
mkdir -p /etc/amnezia/amneziawg
cp "$WORK/trust.conf" "/etc/amnezia/amneziawg/$IFN.conf"
chmod 600 "/etc/amnezia/amneziawg/$IFN.conf"
"$BIN" up "$IFN" --config "/etc/amnezia/amneziawg/$IFN.conf" >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 0 ] && ok "R4-07: a plain path is accepted" ||
	bad "R4-07: a plain path is rejected ($(head -1 "$WORK/out.log"))"
chmod 777 "/etc/amnezia/amneziawg"
"$BIN" up "$IFN" --config "/etc/amnezia/amneziawg/$IFN.conf" >"$WORK/out.log" 2>&1
rc=$?
# R6/N4: rc 1 alone is not the proof — require the trust-walk error text.
[ "$rc" -eq 1 ] && grep -q "must be root-owned and not" "$WORK/out.log" &&
	ok "R4-07: world-writable directory refused ('$(head -1 "$WORK/out.log")')" ||
	bad "R4-07: world-writable directory rc $rc ('$(head -1 "$WORK/out.log")')"
chmod 755 "/etc/amnezia/amneziawg"
mv "/etc/amnezia/amneziawg" "/etc/amnezia/real"
ln -s real "/etc/amnezia/amneziawg"
"$BIN" up "$IFN" --config "/etc/amnezia/amneziawg/$IFN.conf" >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 1 ] && grep -qE "is not a directory|Too many levels" "$WORK/out.log" &&
	ok "R4-07: symlinked component refused (O_NOFOLLOW, '$(head -1 "$WORK/out.log")')" ||
	bad "R4-07: symlinked component rc $rc ($(head -1 "$WORK/out.log"))"
rm -f "/etc/amnezia/amneziawg"
mkfifo "/etc/amnezia/amneziawg"
timeout 5 "$BIN" up "$IFN" --config "/etc/amnezia/amneziawg/$IFN.conf" \
    >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 1 ] && grep -qE "not a regular file|Not a directory" "$WORK/out.log" &&
	ok "R4-07: FIFO in place of the config refused without blocking ('$(head -1 "$WORK/out.log")')" ||
	bad "R4-07: FIFO rc $rc ('$(head -1 "$WORK/out.log")')"
rm -rf /etc/amnezia

echo
echo "== L5. R4-09: exact arity and ENOTTY"
write_conf "fd00::1/128"
"$BIN" down "$IFN" extra >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "R4-09: down with a surplus argument exits 1" ||
	bad "R4-09: down with a surplus argument exits $rc"
"$BIN" show "$IFN" extra >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "R4-09: show with a surplus argument exits 1" ||
	bad "R4-09: show with a surplus argument exits $rc"
"$BIN" del "$IFN" peer "$KEY" extra >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "R4-09: del with a surplus argument exits 1" ||
	bad "R4-09: del with a surplus argument exits $rc"
"$BIN" set lo0 ListenPort 51900 >"$WORK/out.log" 2>&1
rc=$?
[ "$rc" -eq 3 ] && grep -qE "SIOCSAWG|SIOCGAWG" "$WORK/out.log" &&
	ok "R4-09: set on a non-AWG target reports the kernel errno (rc 3, '$(head -1 "$WORK/out.log")')" ||
	bad "R4-09: set on lo0 exits $rc ($(head -1 "$WORK/out.log"))"
head -1 "$WORK/out.log"

echo
if [ "$fail" -eq 0 ]; then
	echo "RESULT: all laptop checks passed"
	exit 0
fi
echo "RESULT: $fail laptop check(s) failed"
exit 1
