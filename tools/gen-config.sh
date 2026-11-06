#!/bin/sh
# gen-config.sh [--endpoint host:port] [--net 10.8.0.1/24] [--client 10.8.0.2/32]
#
# Prints a matching server/client config pair for this host. Keys are generated
# here and printed once — redirect the output somewhere private:
#
#   umask 077; doas sh tools/gen-config.sh --endpoint vpn.example.com:51820 > pair.txt
#
# Requires the awg kernel to be running (it asks the driver for public keys).
set -eu

ENDPOINT=${ENDPOINT:-vpn.example.com:51820}
NET=${NET:-10.8.0.1/24}
CLIENT=${CLIENT:-10.8.0.2/32}

while [ $# -gt 0 ]; do
	case "$1" in
	--endpoint) ENDPOINT=$2; shift 2 ;;
	--net)      NET=$2; shift 2 ;;
	--client)   CLIENT=$2; shift 2 ;;
	-h|--help)  sed -n '2,9p' "$0"; exit 0 ;;
	*)          echo "unknown option: $1" >&2; exit 1 ;;
	esac
done
PORT=${ENDPOINT##*:}
HOST=${ENDPOINT%:*}

die() { echo "$*" >&2; exit 1; }

ifconfig awg9 create 2>/dev/null || die "no awg(4) here — install the kernel first"
trap 'ifconfig awg9 destroy 2>/dev/null || true' EXIT

key() { openssl rand -base64 32; }
# H values are uint32; awg-quick and the AmneziaWG apps all accept decimal
rnd() { od -An -N4 -tu4 /dev/random | tr -d ' \n' | sed 's/^0*//' | grep . || echo 0; }

SPRIV=$(key); CPRIV=$(key)

ifconfig awg9 awgkey "$SPRIV" || die "driver rejected the key"
SPUB=$(ifconfig awg9 | sed -n 's/.*awgpubkey //p')
[ -n "$SPUB" ] || die "could not read the public key back"

ifconfig awg9 awgkey "$CPRIV"
CPUB=$(ifconfig awg9 | sed -n 's/.*awgpubkey //p')
[ -n "$CPUB" ] || die "could not read the public key back"

H1=$(rnd); H2=$(rnd); H3=$(rnd); H4=$(rnd)
PSK=$(key)

cat <<EOF
# server: /etc/amnezia/amneziawg/vpn.conf   (chmod 600)
[Interface]
PrivateKey = $SPRIV
Address = $NET
ListenPort = $PORT
Jc = 4
Jmin = 10
Jmax = 50
S1 = 26
S2 = 33
H1 = $H1
H2 = $H2
H3 = $H3
H4 = $H4

[Peer]
PublicKey = $CPUB
PresharedKey = $PSK
AllowedIPs = $CLIENT

# client (AmneziaWG app or awg-quick)
[Interface]
PrivateKey = $CPRIV
Address = ${CLIENT%/*}/32
Jc = 4
Jmin = 10
Jmax = 50
S1 = 26
S2 = 33
H1 = $H1
H2 = $H2
H3 = $H3
H4 = $H4

[Peer]
PublicKey = $SPUB
PresharedKey = $PSK
Endpoint = $HOST:$PORT
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF
