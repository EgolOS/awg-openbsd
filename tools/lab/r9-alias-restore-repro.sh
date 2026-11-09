#!/bin/sh
# R9/R4-08 reproduction: aliases added by a failing `up` must not survive
# the restore.  Pre-state: one IPv4 primary + one IPv6 alias.  The `up`
# installs two more addresses and fails on the late SIOCSAWG field; the
# ifconfig view and the derived netstat view must come back byte-identical.
set -u
R=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$R/../.." && pwd)
W=$(cd "$(mktemp -d "$ROOT/.r9repro.XXXXXX")" && pwd)
trap 'rm -rf "$W"' EXIT
mkdir -p "$W/conf"
cc -DAWGCTL_LINUX -I"$ROOT/src" -I"$ROOT/sbin/awgctl" \
    -DCONF_DIR="\"$W/conf\"" "-DCONF_OWNER_OK(u)=1" \
    -DIFCONFIG_PATH="\"$ROOT/tools/lab/fixture-ifconfig.py\"" \
    -o "$W/awgctl-fixture" "$ROOT/sbin/awgctl/awgctl.c" \
    "$ROOT/tools/lab/fixture-stubs.c" -lutil || exit 1

cat > "$W/conf/awg9.conf" <<-'EOF'
	[Interface]
	PrivateKey = YHdnLWxhYi1rZXktMAAAAAAAAAAAAAAAAAAAAAAAAEA= # scan:allow-fixture[sha1:79ea366a4c32e5f1bdb33f7079ea4f76b624ca37] synthetic lab key
	MTU = 1500
	Address = 10.9.19.1/24
	Address = 10.9.19.2/24
	Address = fd00::5/128
	[Peer]
	PublicKey = ZhAm+vU7+EXMNH+QNv+d1EPlsNVDwz/HTrdlLoU1eFA=
	Endpoint = 10.9.9.9:51820
	AllowedIPs = 10.0.0.0/8
	EOF
chmod 600 "$W/conf/awg9.conf"

# Pre-state: v4 primary 10.0.0.1/24 with a v6 alias fd00::1/128, MTU 1420.
# `addrs` is the canonical list both fixtures read; addr/prefix/family mirror it.
cat > "$W/state" <<-'EOF'
	flags=4194270
	port=51820
	version=1
	peers=1
	tx=1420
	rx=1040
	handshake=17
	addrs=2:10.0.0.1/24 10:fd00::1/128
	addr=10.0.0.1
	prefix=24
	family=2
	mtu=1420
	up=1
	EOF
# Normalise the pre-state through the same fixture the run uses (it rewrites the
# address list into the canonical `addrs` key and derives the netstat view), then
# keep both views as the reference.
AWGFIX_STATE="$W/state" AWGFIX_ROUTES="$W/pre.routes" \
    python3 "$ROOT/tools/lab/fixture-ifconfig.py" awg9 up >/dev/null 2>&1
cp "$W/state" "$W/pre"
cp "$W/pre.routes" "$W/preroutes"

AWGFIX_ROUTES="$W/routes" AWGFIX_STATE="$W/state" AWGFIX_LOG="$W/log" \
    AWGFIX_IFC_LOG="$W/ifc" AWGFIX_SET_ERR=1 \
    "$W/awgctl-fixture" up awg9 --config "$W/conf/awg9.conf" >"$W/out" 2>&1
rc=$?
echo "exit status: $rc"
cat "$W/out"
echo "--- ifconfig calls ---"
cat "$W/ifc"
echo "--- netstat -rn -f inet / -f inet6 (pre) ---"
cat "$W/preroutes"
echo "--- netstat -rn -f inet / -f inet6 (post) ---"
cat "$W/routes"
# The address list is a set per family, not a serialisation: compare the sorted
# `addrs` tokens (the fixture's canonical ifconfig view) rather than the raw file.
list_addrs() { sed -n 's/^addrs=//p' "$1" | tr ' ' '\n' | sort | sed '/^$/d'; }
list_addrs "$W/pre" > "$W/pre.addrs"
list_addrs "$W/state" > "$W/post.addrs"
echo "--- pre addrs ---"; cat "$W/pre.addrs"
echo "--- post addrs ---"; cat "$W/post.addrs"
echo "--- diff (ifconfig address list) ---"
if diff -u "$W/pre.addrs" "$W/post.addrs" > "$W/diff" 2>&1; then
	echo "(empty — byte-identical)"
else
	cat "$W/diff"
fi
# The kernel lists addresses (and therefore their host routes) in insertion
# order, which is not part of the contract: compare the routing table as a set.
sort "$W/preroutes" > "$W/preroutes.s"
sort "$W/routes" > "$W/routes.s"
echo "--- diff (netstat routes) ---"
if diff -u "$W/preroutes.s" "$W/routes.s" > "$W/rdiff" 2>&1; then
	echo "(empty — byte-identical)"
else
	cat "$W/rdiff"
fi
