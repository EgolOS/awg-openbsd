#!/bin/sh
# gen-config.sh [--endpoint host:port] [--net 10.8.0.1/24] [--client 10.8.0.2/32]
#               [--output pair.conf | --stdout]
# Writes a legacy IPv4 tunnel pair to an exclusive 0600 file (default pair.conf).
# IPv6 endpoints use [address]:port. --stdout deliberately exports plaintext.
# Requires awg(4) and the patched ifconfig with awgkey - (stdin).
set +x
set -eu
umask 077
ENDPOINT=${ENDPOINT:-vpn.example.com:51820}
NET=${NET:-10.8.0.1/24}
CLIENT=${CLIENT:-10.8.0.2/32}
OUT=pair.conf
STDOUT=0 EXPLICIT_OUT=0 CREATED=0 OUTPUT_CREATED=0 COMPLETE=0

die() { echo "$*" >&2; exit 1; }
while [ "$#" -gt 0 ]; do
    case "$1" in
    --endpoint|--net|--client|--output)
        [ "$#" -ge 2 ] || die 'option requires a value'
        case "$1" in
        --endpoint) ENDPOINT=$2 ;; --net) NET=$2 ;; --client) CLIENT=$2 ;;
        --output) OUT=$2; EXPLICIT_OUT=1 ;;
        esac
        shift 2 ;;
    --stdout) STDOUT=1; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) die 'unknown option (see --help)' ;;
    esac
done
[ "$STDOUT$EXPLICIT_OUT" != 11 ] || die '--stdout and --output are mutually exclusive'

valid_ip() {
    awk -v value="$1" '
    function v4(v, a,n,i) {
        n=split(v,a,"."); if(n!=4) return 0
        for(i=1;i<=n;i++) if(a[i]!~/^[0-9]+$/ || length(a[i])>3 || a[i]+0>255) return 0
        return 1
    }
    function v6(v, a,b,n,m,i,j,total) {
        n=split(v,a,"::"); if(n>2) return 0
        total=0
        for(i=1;i<=n;i++) {
            if(a[i]=="") continue
            m=split(a[i],b,":")
            for(j=1;j<=m;j++) {
                if(index(b[j],".")) {
                    if(i!=n || j!=m || !v4(b[j])) return 0
                    total+=2
                } else {
                    if(b[j]!~/^[0-9a-fA-F]+$/ || length(b[j])>4) return 0
                    total++
                }
            }
        }
        return n==2 ? total<8 : total==8
    }
    BEGIN { exit !(index(value,":") ? v6(value) : v4(value)) }' </dev/null
}
check_cidr() {
    case "$2" in *[!0-9./]*|*/*/*|*.) die "$1 must be an IPv4 address/prefix" ;; esac
    case "$2" in */*) ;; *) die "$1 needs a prefix" ;; esac
    addr=${2%/*}; prefix=${2##*/}
    valid_ip "$addr" || die "$1 has an invalid IPv4 address"
    case "$prefix" in ''|*[!0-9]*) die "$1 has an invalid prefix" ;; esac
    [ "${#prefix}" -le 2 ] && [ "$prefix" -le 32 ] || die "$1 prefix exceeds 32"
}
check_cidr net "$NET"
check_cidr client "$CLIENT"
PORT=${ENDPOINT##*:}; HOST=${ENDPOINT%:*}
case "$ENDPOINT" in

    \[*\]:*) literal=${HOST#\[}; literal=${literal%\]}
        valid_ip "$literal" || die 'invalid IPv6 endpoint'
        case "$literal" in *:*) ;; *) die 'brackets require an IPv6 literal' ;; esac ;;
    *:*:*) die 'IPv6 endpoints require [address]:port' ;;
    *:*) case "$HOST" in ''|*[!0-9A-Za-z._-]*|-*) die 'invalid endpoint host' ;; esac ;;
    *) die 'endpoint requires host:port' ;;
esac
case "$PORT" in ''|*[!0-9]*) die 'invalid endpoint port' ;; esac
[ "${#PORT}" -le 5 ] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die 'endpoint port outside 1..65535'

cleanup() {
    if [ "$CREATED" = 1 ]; then ifconfig awg9 destroy >/dev/null 2>&1 || true; fi
    if [ "$OUTPUT_CREATED" = 1 ] && [ "$COMPLETE" = 0 ]; then rm -f -- "$OUT"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if [ "$STDOUT" = 0 ]; then
    [ -n "$OUT" ] && [ ! -L "$OUT" ] || die 'invalid output path or symlink'
    set -C
    exec 3> "$OUT" || die 'output already exists or cannot be created'
    set +C
    OUTPUT_CREATED=1
fi
# Never borrow an existing interface: even rekeying it would disrupt its owner.
ifconfig awg9 create 2>/dev/null || die 'awg9 unavailable (busy or awg(4) not installed)'
CREATED=1
key() { openssl rand -base64 32; }
rnd() {
    value=$(od -An -N4 -tu4 /dev/random) || return 1
    value=$(printf '%s' "$value" | tr -d ' \n')
    case "$value" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$value"
}
SPRIV=$(key); CPRIV=$(key); PSK=$(key)
printf '%s\n' "$SPRIV" | ifconfig awg9 awgkey -
SPUB=$(ifconfig awg9 | sed -n 's/.*awgpubkey //p')
[ -n "$SPUB" ] || die 'could not read server public key'
printf '%s\n' "$CPRIV" | ifconfig awg9 awgkey -
CPUB=$(ifconfig awg9 | sed -n 's/.*awgpubkey //p')
[ -n "$CPUB" ] || die 'could not read client public key'
pick_h() {
    tries=0
    while [ "$tries" -lt 32 ]; do
        candidate=$(rnd) || return 1
        collision=0
        for seen do [ "$seen" != "$candidate" ] || collision=1; done
        if [ "$collision" = 0 ]; then printf '%s\n' "$candidate"; return 0; fi
        tries=$((tries + 1))
    done
    die 'random H collision limit exceeded'
}
H1=$(pick_h); H2=$(pick_h "$H1"); H3=$(pick_h "$H1" "$H2"); H4=$(pick_h "$H1" "$H2" "$H3")
emit() {
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
Address = $CLIENT
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

}
if [ "$STDOUT" = 1 ]; then emit; else emit >&3; fi
COMPLETE=1
[ "$STDOUT" = 1 ] || echo "configuration written to $OUT (0600)" >&2
