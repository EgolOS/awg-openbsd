#!/bin/sh
# AWG-007 admission-cap rehearsal (cookie-less 3.1 profile). Disposable guest only.
#   doas sh awg007-admission.sh
set -u
[ "${AWG_LAB_DISPOSABLE:-0}" = 1 ] || {
	echo 'FAIL: set AWG_LAB_DISPOSABLE=1 only for disposable lab VMs' >&2
	exit 1
}
cd "$(dirname "$0")" || exit 1
[ -x ./hsflood ] || cc -O2 -o hsflood hsflood.c || {
	echo 'FAIL: cannot build hsflood' >&2
	exit 1
}

PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "ok: $*"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL: $*"; }

for i in 6 7; do ifconfig awg$i destroy 2>/dev/null || true; done
ifconfig awg6 create || exit 1
ifconfig awg7 create || exit 1
K4=$(openssl rand -base64 32); K5=$(openssl rand -base64 32)
printf '%s\n' "$K4" | ifconfig awg6 awgkey -
printf '%s\n' "$K5" | ifconfig awg7 awgkey -
ifconfig awg6 awgport 51930 awgversion 3.1 awgs1 26 awgs2 33 awgs3 18 awgs4 24 \
    awgh1 100 awgh2 200 awgh3 300 awgh4 400 awgdisablecookies 1
ifconfig awg7 awgport 51931 awgversion 3.1 awgs1 26 awgs2 33 awgs3 18 awgs4 24 \
    awgh1 100 awgh2 200 awgh3 300 awgh4 400 awgdisablecookies 1
P4=$(ifconfig awg6 | sed -n 's/.*awgpubkey //p')
P5=$(ifconfig awg7 | sed -n 's/.*awgpubkey //p')
ifconfig awg6 inet 10.96.0.1/32 up  # scan:allow-fixture[sha1:35fd9ba5f73fe271f5141eb8eb766a2ba941b10f]: RFC1918 lab address
ifconfig awg7 inet 10.96.0.2/32 up  # scan:allow-fixture[sha1:7d4665659b4de3a66a64f1a52e867d2eecd0ed2d]: RFC1918 lab address
ifconfig awg6 awgpeer "$P5" awgaip 10.96.0.2/32 awgendpoint 127.0.0.1 51931 awgpka 5  # scan:allow-fixture[sha1:7d4665659b4de3a66a64f1a52e867d2eecd0ed2d]: RFC1918 lab address
ifconfig awg7 awgpeer "$P4" awgaip 10.96.0.1/32 awgendpoint 127.0.0.1 51930  # scan:allow-fixture[sha1:35fd9ba5f73fe271f5141eb8eb766a2ba941b10f]: RFC1918 lab address
sleep 5
if ifconfig awg6 | grep -q 'last handshake'; then
	ok "handshake with DisableCookies on: $(ifconfig awg6 | sed -n 's/.*last handshake: //p')"
else
	bad "no handshake with DisableCookies on"; ifconfig awg6; exit 1
fi
E0=$(netstat -i -I awg6 | tail -1 | awk '{ print $(NF-3) }')
echo "ierrors before: $E0"
echo "--- sustained flood: 20000 initiations at ~2000/s ---"
./hsflood 127.0.0.1 51930 20000 174 500 26 100
sleep 1
E1=$(netstat -i -I awg6 | tail -1 | awk '{ print $(NF-3) }')
D=$((E1 - E0))
echo "ierrors after : $E1 (dropped $D)"
[ "$D" -gt 15000 ] && ok "admission cap dropped $D of 20000" || bad "dropped only $D of 20000"

# Force a fresh handshake: the bucket must have refilled.
ifconfig awg7 destroy
sleep 2
ifconfig awg7 create
printf '%s\n' "$K5" | ifconfig awg7 awgkey -
ifconfig awg7 awgport 51931 awgversion 3.1 awgs1 26 awgs2 33 awgs3 18 awgs4 24 \
    awgh1 100 awgh2 200 awgh3 300 awgh4 400 awgdisablecookies 1
ifconfig awg7 inet 10.96.0.2/32 up  # scan:allow-fixture[sha1:7d4665659b4de3a66a64f1a52e867d2eecd0ed2d]: RFC1918 lab address
ifconfig awg7 awgpeer "$P4" awgaip 10.96.0.1/32 awgendpoint 127.0.0.1 51930  # scan:allow-fixture[sha1:35fd9ba5f73fe271f5141eb8eb766a2ba941b10f]: RFC1918 lab address
sleep 6
if ifconfig awg6 | grep -q 'last handshake'; then
	ok "fresh handshake admitted after the flood: $(ifconfig awg6 | sed -n 's/.*last handshake: //p')"
else
	bad "no handshake after the flood"
fi
for i in 6 7; do ifconfig awg$i destroy 2>/dev/null || true; done
echo "== result: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
