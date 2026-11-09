#!/bin/sh
# awgctl lab fixture — legacy config with one peer (VM 2205 only).
KEY0='AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='	# scan:allow-fixture[sha1:de49f82911b9d304f1518e8855db8f9cab08b006]
KEY1='AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI='	# scan:allow-fixture[sha1:35e3bbf9261d562486ce87cec758ee0e03293c36]
CONFDIR=/etc/amnezia/amneziawg
mkdir -p "$CONFDIR"
chmod 700 "$CONFDIR"
cat > "$CONFDIR/awg9.conf" <<-EOF
	[Interface]
	PrivateKey = $KEY0
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
	PublicKey = $KEY1
	Endpoint = 127.0.0.1:51821
	AllowedIPs = 10.9.19.2/32,fd00::2/128 # scan:allow-fixture[sha1:9c0214d26dcb5755b8a26890d52f74b389cca6ab]
	PersistentKeepalive = 25
	EOF
chmod 600 "$CONFDIR/awg9.conf"
echo FIXTURE_OK
