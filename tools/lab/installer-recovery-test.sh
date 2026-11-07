#!/bin/sh
# Installer transaction rehearsal: kernel + ifconfig pair backup/rollback and
# the unprivileged build hand-off, on a disposable OpenBSD guest.
#
#   AWG_LAB_DISPOSABLE=1 doas sh tools/lab/installer-recovery-test.sh
#
# Runs as root inside the guest. Nothing is rebuilt from scratch: the "new"
# artifact is a copy of the current /bsd, so no 40-minute kernel build is
# needed. Every scenario takes a fresh snapshot of the pair it mutates, so the
# guest is left with the pair it started with.
set -u
[ "${AWG_LAB_DISPOSABLE:-0}" = 1 ] || {
	echo 'FAIL: set AWG_LAB_DISPOSABLE=1 only for disposable lab VMs' >&2; exit 1;
}

FAILS=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILS=$((FAILS + 1)); }

BSD=/bsd
IFC=/sbin/ifconfig
DB=/var/db/awg-install
MANIFEST=$DB/manifest
BSD_BAK=/bsd.awg-prev
IFC_BAK=/sbin/ifconfig.awg-prev
INSTALL_SH=${INSTALL_SH:-$(dirname "$0")/../../install.sh}
INSTALL_SH=$(cd "$(dirname "$INSTALL_SH")" && pwd)/$(basename "$INSTALL_SH")
NEW=/tmp/awg-new-kernel
STUB_INSTALL=/tmp/awg-stub-install
BUILD_USER=${AWG_BUILD_USER:-_awgbuild}

[ -x "$INSTALL_SH" ] || { echo "FAIL: installer not found: $INSTALL_SH" >&2; exit 1; }
[ -f "$BSD" ] || { echo "FAIL: no $BSD in the guest" >&2; exit 1; }

# Stub toolchain: `config` creates the compile dir, `make` drops a copy of the
# running kernel in obj/, `install` copies files but fails for ifconfig. This
# avoids a 40-minute kernel build for the rehearsal.
STUBDIR=/tmp/awg-stub
make_stub() {
	rm -rf "$STUBDIR"; mkdir -p "$STUBDIR"
	printf '#!/bin/sh\nmkdir -p ../compile/AWG.MP/obj\nexit 0\n' > "$STUBDIR/config"
	printf '#!/bin/sh\nmkdir -p obj\ncp -p %s obj/bsd\nexit 0\n' '"$AWG_STUB_KERNEL"' > "$STUBDIR/make"
	printf '#!/bin/sh\ncase "$*" in\n  *ifconfig*) exit 1 ;;\nesac\nexec /usr/bin/install "$@"\n' > "$STUBDIR/install"
	chmod 755 "$STUBDIR/config" "$STUBDIR/make" "$STUBDIR/install"
}

snapshot() {  # snapshot <tag>
	cp -p "$BSD" "$1.bsd" 2>/dev/null
	cp -p "$IFC" "$1.ifc" 2>/dev/null
	[ -f "$MANIFEST" ] && cp -p "$MANIFEST" "$1.manifest"
	[ -f "$BSD_BAK" ] && cp -p "$BSD_BAK" "$1.bsdprev"
	[ -f "$IFC_BAK" ] && cp -p "$IFC_BAK" "$1.ifcprev"
}

restore() {   # restore <tag>
	[ -f "$1.bsd" ] && cp -p "$1.bsd" "$BSD"
	[ -f "$1.ifc" ] && cp -p "$1.ifc" "$IFC"
	[ -f "$1.manifest" ] && { mkdir -p "$DB"; cp -p "$1.manifest" "$MANIFEST"; }
	[ -f "$1.bsdprev" ] && cp -p "$1.bsdprev" "$BSD_BAK"
	[ -f "$1.ifcprev" ] && cp -p "$1.ifcprev" "$IFC_BAK"
}

same() {  # same <file> <backup>
	[ -f "$1" ] && [ -f "$2" ] && cmp -s "$1" "$2"
}

# Seed the pair backup so a rollback has a manifest to verify against (the
# installer writes the same layout on a real install).
seed_pair() {
	mkdir -p "$DB"
	{
		printf 'schema=1\n'
		printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf 'uname_r=%s\n' "$(uname -r)"
		printf 'uname_v=%s\n' "$(uname -v)"
		printf 'release_tag=%s\n' "${AWG_RELEASE_TAG:-lab-seed}"
		printf 'backup_bsd_sha256=%s\n' "$(sha256 -q "$BSD")"
		printf 'backup_ifconfig_sha256=%s\n' "$(sha256 -q "$IFC")"
		printf 'backup_if_awg_h_sha256=%s\n' "$([ -f /usr/include/net/if_awg.h ] && sha256 -q /usr/include/net/if_awg.h)"
		printf 'installed_bsd_sha256=%s\n' "$(sha256 -q "$BSD")"
		printf 'installed_ifconfig_sha256=%s\n' "$(sha256 -q "$IFC")"
		printf 'installed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	} > "$MANIFEST"
	chmod 600 "$MANIFEST"
	cp -p "$BSD" "$BSD_BAK"
	cp -p "$IFC" "$IFC_BAK"
}

echo "== guest: $(uname -s) $(uname -r) $(uname -v) $(uname -m)"
echo "== installer: $INSTALL_SH"
echo "== build user: $BUILD_USER"
id -u "$BUILD_USER" >/dev/null 2>&1 || {
	echo "-- creating build user $BUILD_USER"
	useradd -s /sbin/nologin "$BUILD_USER" 2>/dev/null \
		|| { echo "FAIL: cannot create $BUILD_USER" >&2; exit 1; }
}
# Stub kernel: a copy of the running kernel (no 40-minute build).
cp -p "$BSD" "$NEW"
make_stub
# ── a. install pair, rollback, both files byte-identical ─────────────────────
echo "-- seed: backup pair + manifest"
seed_pair
echo "-- scenario a: install pair -> rollback -> pair identical"
snapshot /tmp/awga
printf 'new-kernel-bytes\n' > "$BSD"
printf 'new-ifconfig-bytes\n' > "$IFC"
sh "$INSTALL_SH" --rollback >/tmp/awga.log 2>&1
if [ $? -eq 0 ] && same "$BSD" /tmp/awga.bsd && same "$IFC" /tmp/awga.ifc; then
	pass "a: rollback restored /bsd and /sbin/ifconfig from the pair"
else
	fail "a: pair not restored (see /tmp/awga.log)"
	tail -5 /tmp/awga.log
fi

# ── b. ifconfig install fails, rollback restores the pair ────────────────────
echo "-- scenario b: ifconfig install fails -> rollback restores the pair"
snapshot /tmp/awgb
cp -p "$NEW" "$BSD"
AWG_STUB_KERNEL="$NEW" PATH="$STUBDIR:$PATH" sh "$INSTALL_SH" --kernel-only >/tmp/awgb.log 2>&1
sh "$INSTALL_SH" --rollback >/tmp/awgb2.log 2>&1
if same "$BSD" /tmp/awgb.bsd && same "$IFC" /tmp/awgb.ifc; then
	pass "b: rollback restored both halves after a failed ifconfig install"
else
	fail "b: pair left inconsistent after the failed install"
	tail -5 /tmp/awgb.log
fi

# ── c. missing ifconfig backup -> rollback refuses ───────────────────────────
echo "-- scenario c: ifconfig backup removed -> rollback refuses"
snapshot /tmp/awgc
printf 'new-kernel-bytes\n' > "$BSD"
rm -f "$IFC_BAK"
sh "$INSTALL_SH" --rollback >/tmp/awgc.log 2>&1
if [ $? -ne 0 ] && grep -q 'rollback-kernel-only' /tmp/awgc.log; then
	pass "c: rollback refused and named --rollback-kernel-only"
else
	fail "c: rollback did not refuse with the documented error"
	tail -5 /tmp/awgc.log
fi
sh "$INSTALL_SH" --rollback --rollback-kernel-only >/tmp/awgc2.log 2>&1
if [ $? -eq 0 ] && same "$BSD" /tmp/awgc.bsd; then
	pass "c: --rollback-kernel-only restored /bsd alone"
else
	fail "c: --rollback-kernel-only did not restore /bsd"
	tail -5 /tmp/awgc2.log
fi

# ── d. truncated /bsd.awg-prev -> refused, /bsd untouched ────────────────────
echo "-- scenario d: truncated /bsd.awg-prev -> refused, /bsd untouched"
snapshot /tmp/awgd
dd if="$NEW" of="$BSD_BAK" bs=1 count=4096 2>/dev/null
bsd_before=$(sha256 -q "$BSD")
sh "$INSTALL_SH" --rollback >/tmp/awgd.log 2>&1
rc=$?
bsd_after=$(sha256 -q "$BSD")
if [ $rc -ne 0 ] && [ "$bsd_before" = "$bsd_after" ]; then
	pass "d: truncated backup refused, $BSD sha256 unchanged"
else
	fail "d: $BSD changed or the refusal was not reported"
	tail -5 /tmp/awgd.log
fi

# ── e. untrusted source tree -> build refused ────────────────────────────────
echo "-- scenario e: group-writable source tree -> build refused"
TREE=${SRCDIR:-/usr/src/sys}
[ -d "$TREE" ] || TREE=/usr/src
tree_before=$(stat -f '%Su %Sp' "$TREE")
chmod g+w "$TREE"
sh "$INSTALL_SH" --build-only >/tmp/awge.log 2>&1
rc=$?
tree_after=$(stat -f '%Su %Sp' "$TREE")
if [ $rc -ne 0 ] && grep -q 'untrusted source tree' /tmp/awge.log; then
	pass "e: build refused for a group-writable tree ($tree_after)"
else
	fail "e: build was not refused (rc=$rc, $tree_after)"
	tail -5 /tmp/awge.log
fi
chmod g-w "$TREE"

# ── f. --check reports the pair, the build user and the trust verdict ────────
echo "-- scenario f: --check reports pair, build user and trust"
sh "$INSTALL_SH" --check >/tmp/awgf.log 2>&1
rc=$?
if grep -q 'build user' /tmp/awgf.log && grep -q 'src trust' /tmp/awgf.log \
   && grep -q 'ifconfig sha' /tmp/awgf.log && grep -q 'ifconfig awg' /tmp/awgf.log; then
	pass "f: --check printed pair, trust and build user (rc=$rc)"
else
	fail "f: --check output incomplete (rc=$rc)"
	cat /tmp/awgf.log
fi

rm -f "$NEW" "$STUB_INSTALL" /tmp/awga* /tmp/awgb* /tmp/awgc* /tmp/awgd* /tmp/awge* /tmp/awgf*
echo ""
if [ "$FAILS" -eq 0 ]; then echo "installer-recovery: all scenarios passed"; exit 0; fi
echo "installer-recovery: $FAILS scenario(s) failed"; exit 1
