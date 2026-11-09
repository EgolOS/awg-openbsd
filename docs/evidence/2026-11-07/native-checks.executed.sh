#!/bin/sh
set -eu
cd /home/user/awg-assurance-1813 # scan:allow-fixture[sha1:8714274ee689c5f49e0d2e7c0a5276cfc84085c1]
# This is a disposable VM, not an installer run or base-tool replacement.
doas mkdir -p /root/awg-assurance-1813
doas chmod 700 /root/awg-assurance-1813
printf '[Interface]\nPrivateKey = dummy-not-a-real-key\nTable = off\n' > user.conf
chmod 600 user.conf
doas cp user.conf /root/awg-assurance-1813/trusted.conf
doas chmod 600 /root/awg-assurance-1813/trusted.conf
if doas /usr/local/bin/bash ./quick-functions.bash "$PWD/user.conf" >user.log 2>&1; then
    echo 'FAIL: user-owned config accepted'; exit 1
fi
echo 'PASS: actual doas wheel policy refuses user-owned 0600 config' # scan:allow-fixture
doas /usr/local/bin/bash ./quick-functions.bash /root/awg-assurance-1813/trusted.conf >trusted.log 2>&1
grep -q '^PARSED:trusted$' trusted.log
echo 'PASS: root-owned config in trusted ancestors accepted' # scan:allow-fixture
doas cp user.conf ./root-in-user-dir.conf
doas chown root:wheel ./root-in-user-dir.conf
if doas /usr/local/bin/bash ./quick-functions.bash "$PWD/root-in-user-dir.conf" >parent.log 2>&1; then
    echo 'FAIL: user-controlled parent accepted'; exit 1
fi
echo 'PASS: root-owned file under user-controlled ancestor refused' # scan:allow-fixture
ln -s user.conf link.conf
if doas /usr/local/bin/bash ./quick-functions.bash "$PWD/link.conf" >symlink.log 2>&1; then
    echo 'FAIL: symlink to user config accepted'; exit 1
fi
echo 'PASS: canonical symlink target ownership checked' # scan:allow-fixture
mkdir -p source-check stale
cp /usr/src/sbin/ifconfig/ifconfig.c.orig source-check/ifconfig.c
cp source-check/ifconfig.c stale/ifconfig.c
printf '\n/* stale */\n' >> stale/ifconfig.c
sh ./verify-source-native.sh source-check ./8.0.sha256
echo 'PASS: real OpenBSD source matches signed archive baseline' # scan:allow-fixture
if sh ./verify-source-native.sh stale ./8.0.sha256 >stale.log 2>&1; then
    echo 'FAIL: stale ifconfig source accepted'; exit 1
fi
echo 'PASS: stale ifconfig source rejected before build' # scan:allow-fixture
# Exercise OpenBSD patch syntax and build as the unprivileged lab user.
tar xzf ifconfig-verified.tgz
patch -C -F 0 sbin/ifconfig/ifconfig.c ifconfig_awg.patch
patch -F 0 sbin/ifconfig/ifconfig.c ifconfig_awg.patch
(cd sbin/ifconfig && make > ../../ifconfig-build.log 2>&1)
echo 'PASS: patched ifconfig builds unprivileged from verified 8.0 sources' # scan:allow-fixture
# Real native signify fixture, independent from the Linux binary.
mkdir signature; cd signature
signify -G -n -p key.pub -s key.sec
printf 'non-secret fixture\n' > sys.tar.gz
sha256 sys.tar.gz > SHA256
signify -S -e -s key.sec -m SHA256 -x SHA256.sig
sh ../verify-release.sh key.pub SHA256.sig sys.tar.gz
echo 'PASS: native signify accepts valid fixture' # scan:allow-fixture
printf 'corrupt\n' >> sys.tar.gz
if sh ../verify-release.sh key.pub SHA256.sig sys.tar.gz >/dev/null 2>&1; then
    echo 'FAIL: corrupt archive accepted'; exit 1
fi
echo 'PASS: native signify rejects corrupt fixture' # scan:allow-fixture
rm -f key.sec
