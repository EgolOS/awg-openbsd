#!/bin/sh
# Build AmneziaWG kernel + patched ifconfig on OpenBSD 8.0 with the tree on /home (large partition).
set -e
HOST=${LABHOST:-10.0.2.2:8000}
LABDIR=${LABDIR:-/lab}
BUILD=/home/build
# One private fixture root per run (mktemp, not a fixed /tmp/awgfix) so two
# builds in the same guest never overwrite each other's tarballs.
FIX=$(mktemp -d "${TMPDIR:-/tmp}/awgfix.XXXXXX")

echo "== fixtures =="
df -h /home /usr | tail -3
mkdir -p "$FIX" && cd "$FIX"
for f in sys80.tar.gz driver.tgz; do
	[ -f "$f" ] || ftp -V -o "$f" "http://$HOST/$f"
done

echo "== sources =="
mkdir -p "$BUILD"
[ -d "$BUILD/sys/net" ] || tar xzf sys80.tar.gz -C "$BUILD"
mkdir -p /usr/src/sbin
[ -f /usr/src/sbin/ifconfig/ifconfig.c ] || tar xzf src80.tar.gz -C /usr/src sbin/ifconfig 2>/dev/null || {
	ftp -V -o src80.tar.gz "http://$HOST/src80.tar.gz"
	tar xzf src80.tar.gz -C /usr/src sbin/ifconfig
}

rm -rf "$FIX/repo" && mkdir -p "$FIX/repo" && tar xzf driver.tgz -C "$FIX/repo"
R="$FIX/repo/awg-openbsd-kernel"

echo "== driver sources =="
cp "$R"/src/if_awg.h "$R"/src/if_awg.c "$R"/src/awg_noise.c "$R"/src/awg_noise.h "$BUILD/sys/net/"
cp "$R"/src/AWG.MP "$BUILD/sys/arch/amd64/conf/"

if ! grep -q 'pseudo-device awg' "$BUILD/sys/conf/files"; then
	awk '
	  /^pseudo-device wg: ifnet$/ { print; print "pseudo-device awg: ifnet"; next }
	  /^file net\/if_wg\.c/      { print; print "file net/if_awg.c\t\t\tawg"; print "file net/awg_noise.c\t\t\tawg"; next }
	  { print }
	' "$BUILD/sys/conf/files" > "$FIX/files.new"
	mv "$FIX/files.new" "$BUILD/sys/conf/files"
fi
grep -n 'awg' "$BUILD/sys/conf/files"

# shared crypto/cookie files must also be reachable from the awg attribute
if ! grep -q 'wg_cookie\.c.*awg' "$BUILD/sys/conf/files"; then
	awk '
	  $1 == "file" && ($2 == "net/wg_cookie.c" || $2 == "crypto/blake2s.c" ||
	      $2 == "crypto/curve25519.c" || $2 == "crypto/chachapoly.c") &&
	      $0 !~ /awg/ { print $0 " | awg"; next }
	  { print }
	' "$BUILD/sys/conf/files" > "$FIX/files2.new"
	mv "$FIX/files2.new" "$BUILD/sys/conf/files"
fi

echo "== ifconfig =="
cp "$R"/src/if_awg.h /usr/include/net/if_awg.h
cd /usr/src/sbin/ifconfig
[ -f ifconfig.c.orig ] || cp ifconfig.c ifconfig.c.orig
grep -q awgversion ifconfig.c || patch ifconfig.c "$R"/sbin/ifconfig/ifconfig_awg.patch
make
make install

echo "== kernel =="
cd "$BUILD/sys/arch/amd64/conf"
config AWG.MP
cd ../compile/AWG.MP
make -j"$(sysctl -n hw.ncpu)" > /home/build/kernel.log 2>&1
make install
ls -l /bsd
echo "KERNEL-DONE"
