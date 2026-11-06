#!/bin/sh
# start_vm.sh NAME SSHPORT {listen|connect} LANPORT [SMP] [MEM]
set -e
LAB=${LAB:-/lab}
NAME=$1; SSHP=$2; MODE=$3; LANP=$4; SMP=${5:-1}; MEM=${6:-1024}; MACID=${7:-01}

if [ "$MODE" = listen ]; then
	LAN="socket,id=n1,listen=127.0.0.1:$LANP"
else
	LAN="socket,id=n1,connect=127.0.0.1:$LANP"
fi

exec qemu-system-x86_64 \
	-enable-kvm -cpu host -m "$MEM" -smp "$SMP" \
	-drive "file=$LAB/$NAME.qcow2,if=virtio,format=qcow2" \
	-netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$SSHP-:22" -device virtio-net-pci,netdev=n0 \
	-netdev "$LAN" -device "virtio-net-pci,netdev=n1,mac=52:54:00:00:00:$MACID" \
	-display none -serial "file:$LAB/serial-$NAME.log" \
	-monitor none -pidfile "$LAB/qemu-$NAME.pid" -daemonize
