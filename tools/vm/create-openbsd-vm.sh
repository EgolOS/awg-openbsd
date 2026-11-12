#!/bin/sh
# create-openbsd-vm.sh — reproducible disposable OpenBSD 8.0 lab VM.
#
# Lab host: GMKtec Evo X2 (Ryzen AI Max+ 395, AMD-V, /dev/kvm rw) using
# qemu/KVM. vmm(4) is OpenBSD-only, so qemu with the OpenBSD 8.0 amd64
# install80.iso is the chosen path. Nothing depends on the laptop's live
# kernel: the guest boots its own /bsd from an 8 GiB qcow2 disk.
#
# Steps: fetch+verify install media, create qcow2, scripted install over the
# serial console, first boot, qcow2 snapshot (rescue point).
#
# All knobs are env-overridable; images live outside the repo (VM_DIR).
set -eu

: "${VM_DIR:=${PWD}/vm-lab}"
: "${VM_NAME:=awg80-lab}"
: "${QEMU:=qemu-system-x86_64}"
: "${QEMU_IMG:=qemu-img}"
: "${PYTHON:=python3}"
: "${ISO_URL:=https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/install80.iso}"
: "${ISO_SHA256:=bc85d747c6ee4354d14fbfeee38087500386a2e9d86fa7b5ab13d840d902aa33}"
: "${DISK_SIZE:=8G}"
: "${VM_MEM:=2048}"
: "${VM_CPUS:=2}"
: "${SSH_PORT:=10022}"
: "${ROOT_PASS:=awglab}"
: "${SNAPSHOT:=pre-breakage}"
: "${QEMU_DATA_DIR:=}"

ISO="${VM_DIR}/install80.iso"
DISK="${VM_DIR}/${VM_NAME}.qcow2"
SERIAL="${VM_DIR}/${VM_NAME}-serial-install.log"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "${VM_DIR}"

# 1. Install media: download once, verify against the published checksum.
if [ ! -f "${ISO}" ]; then
    echo "fetching ${ISO_URL}"
    curl -fsSL -o "${ISO}.part" "${ISO_URL}"
    echo "${ISO_SHA256}  ${ISO}.part" | sha256sum -c -
    mv "${ISO}.part" "${ISO}"
else
    echo "verifying cached ${ISO}"
    echo "${ISO_SHA256}  ${ISO}" | sha256sum -c -
fi

# 2. Disk: fresh qcow2 (8 GiB virtual).
"${QEMU_IMG}" create -f qcow2 "${DISK}" "${DISK_SIZE}"

# 3. Scripted install: qemu -nographic, serial stdio drives install(8).
"${PYTHON}" "${SCRIPT_DIR}/openbsd-install.py" \
    --qemu "${QEMU}" --iso "${ISO}" --disk "${DISK}" \
    --serial-log "${SERIAL}" --mem "${VM_MEM}" --cpus "${VM_CPUS}" \
    --ssh-port "${SSH_PORT}" --root-pass "${ROOT_PASS}" \
    ${QEMU_DATA_DIR:+--datadir "${QEMU_DATA_DIR}"}

# 4. Rescue point: internal qcow2 snapshot of the clean install.
#    qemu releases the image lock slightly after power-off; retry briefly.
i=0
until "${QEMU_IMG}" snapshot -c "${SNAPSHOT}" "${DISK}" 2>/dev/null; do
    i=$((i+1)); [ "$i" -ge 30 ] && exit 1
    sleep 1
done
"${QEMU_IMG}" snapshot -l "${DISK}"

echo "VM created: ${DISK} (snapshot ${SNAPSHOT}, ssh host port ${SSH_PORT})"
