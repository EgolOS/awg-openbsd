#!/bin/sh
# Layer 2 gate: the AWG parameter matrix, every bound and every interdependent
# rule in awg_ioctl_check(). Run as root inside the lab VM:
#
#   doas sh tools/lab/matrix-test.sh
#
# smoke-test.sh proves a tunnel works and that bad input does not panic. This
# one proves the driver accepts exactly the configurations the protocol allows
# and refuses the rest — the failure mode here is silent: a value that should be
# rejected but is not only shows up as a peer that never completes a handshake.
set -u

KEY0='AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='	# scan:allow-fixture[sha1:de49f82911b9d304f1518e8855db8f9cab08b006]
FAILED=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

want_ok() {		# want_ok <description> <command...>
	desc=$1; shift
	if "$@" >/dev/null 2>&1; then pass "$desc"; else fail "$desc (rejected)"; fi
}

want_fail() {		# want_fail <description> <command...>
	desc=$1; shift
	if "$@" >/dev/null 2>&1; then fail "$desc (accepted)"; else pass "$desc"; fi
}

want_einval() {		# want_fail, and the kernel — not ifconfig — must say so
	desc=$1; shift
	err=$("$@" 2>&1 >/dev/null); rc=$?
	if [ "$rc" -eq 0 ]; then
		fail "$desc (accepted)"
	elif echo "$err" | grep -q 'Invalid argument'; then
		pass "$desc"
	else
		fail "$desc (rejected by userspace, kernel check not exercised: $err)"
	fi
}

ifconfig awg0 >/dev/null 2>&1 && ifconfig awg0 destroy
ifconfig awg0 create || { echo "cannot create awg0"; exit 1; }
ifconfig awg0 awgkey "$KEY0"

echo "== 1. S1-S4: 0..1280, then EINVAL"
for p in awgs1 awgs2; do
	want_ok     "$p 0"          ifconfig awg0 "$p" 0
	want_ok     "$p 1280 (max)" ifconfig awg0 "$p" 1280
	want_einval "$p 1281 (max+1)" ifconfig awg0 "$p" 1281
done
want_einval "awgs3 on legacy is refused" ifconfig awg0 awgs3 18

echo "== 2. junk: Jc 0..64, Jmin/Jmax 0..1500, and the jmin/jmax relation"
want_ok     "awgjc 0"        ifconfig awg0 awgjc 0
want_ok     "awgjc 64 (max)" ifconfig awg0 awgjc 64 awgjmin 10 awgjmax 50
want_einval "awgjc 65 (max+1)" ifconfig awg0 awgjc 65 awgjmin 10 awgjmax 50
want_einval "awgjc 64 with jmin 0" ifconfig awg0 awgjc 64 awgjmin 0 awgjmax 50
want_ok     "awgjmax 1500 (max)" ifconfig awg0 awgjmin 10 awgjmax 1500
want_einval "awgjmax 1501 (max+1)" ifconfig awg0 awgjmin 10 awgjmax 1501
want_einval "jc 4 with jmin 0" ifconfig awg0 awgjc 4 awgjmin 0 awgjmax 50
want_einval "jc 4 with jmin > jmax" ifconfig awg0 awgjc 4 awgjmin 60 awgjmax 10
want_ok     "jc 0 with jmin > jmax (no junk sent)" ifconfig awg0 awgjc 0 awgjmin 60 awgjmax 10

echo "== 3. H1-H4: single values on legacy, ranges and no overlap on 3.1"
want_ok     "legacy single H values" \
	ifconfig awg0 awgh1 1737146616 awgh2 705704376 awgh3 391058102 awgh4 2103979647
want_einval "legacy H range" ifconfig awg0 awgh1 1000-2000
want_ok     "version 3.1"    ifconfig awg0 awgversion 3.1
want_ok     "3.1 disjoint ranges" \
	ifconfig awg0 awgh1 1000-1999 awgh2 2000-2999 awgh3 3000-3999 awgh4 4000-4999
want_einval "3.1 overlapping H1/H2" \
	ifconfig awg0 awgh1 1000-2000 awgh2 1500-2500 awgh3 3000-3999 awgh4 4000-4999
want_fail   "3.1 reversed range (ifconfig)" ifconfig awg0 awgh3 6000-5000
want_ok     "3.1 disjoint ranges again" \
	ifconfig awg0 awgh1 1000-1999 awgh2 2000-2999 awgh3 3000-3999 awgh4 4000-4999

echo "== 4. header protection needs S1-S4 >= 12 (its nonce is taken from them)"
HPK="$KEY0"		# any 32-byte key; the nonce is what matters
want_ok     "s1-s4 12 with a key" \
	sh -c "ifconfig awg0 awgs1 12 awgs2 12 awgs3 12 awgs4 12 awghpk $HPK"
want_einval "s4 11 with a key" \
	sh -c "ifconfig awg0 awgs1 12 awgs2 12 awgs3 12 awgs4 11 awghpk $HPK"
want_ok     "key cleared, short prefixes accepted again" \
	sh -c "ifconfig awg0 -awghpk && ifconfig awg0 awgs1 4 awgs2 4 awgs3 4 awgs4 4"

echo "== 5. timings: bounds, ordering, and reject > keepalive + rekey"
want_ok     "awgrekeyaftertime 65535" ifconfig awg0 awgrekeyaftertime 65535
want_fail   "awgrekeyaftertime 65536 (ifconfig)" ifconfig awg0 awgrekeyaftertime 65536
want_fail   "reversed timing range (ifconfig)" ifconfig awg0 awgrekeytimeout 10-5
want_einval "reject 1-2 with pka 10-12 (sum in one call)" \
	ifconfig awg0 awgrejectaftertime 1-2 awgkeepalivetimeout 10-12
want_ok     "reject 170-190 with pka 10-12" \
	ifconfig awg0 awgrejectaftertime 170-190 awgkeepalivetimeout 10-12
want_ok     "cpa 0-32" ifconfig awg0 awgcpa 0-32

echo "== 6. I1-I5 signature descriptors"
want_ok     "I1 with a constant, random tail and timestamp" \
	sh -c "ifconfig awg0 awgi1 '<b 0xc700000001><r 16><t>'"
want_ok     "I1 at the UDP payload bound" sh -c "ifconfig awg0 awgi1 '<r 65507>'"
want_einval "I1 above the bound"          sh -c "ifconfig awg0 awgi1 '<r 65508>'"
want_einval "I1 unclosed tag"             sh -c "ifconfig awg0 awgi1 '<b 0xc7'"
want_einval "I1 unknown modifier"         sh -c "ifconfig awg0 awgi1 '<q 4>'"
want_ok     "I1 cleared"                  sh -c "ifconfig awg0 awgi1 ''"

echo "== 7. version switching"
want_ok     "back to legacy" ifconfig awg0 awgversion legacy
want_einval "legacy with an S3 value" ifconfig awg0 awgs3 18
want_ok     "3.1 again"      ifconfig awg0 awgversion 3.1
want_ok     "legacy again (reset path)" ifconfig awg0 awgversion legacy
want_ok     "3.1 once more (reset path, twice in a row)" ifconfig awg0 awgversion 3.1
want_ok     "legacy once more (reset path, twice in a row)" ifconfig awg0 awgversion legacy

echo "== 8. teardown"
want_ok "awg0 destroy" ifconfig awg0 destroy

echo
if [ "$FAILED" -eq 0 ]; then
	echo "RESULT: all cases passed"
	exit 0
fi
echo "RESULT: $FAILED case(s) failed"
exit 1
