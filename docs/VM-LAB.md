# VM Lab — disposable OpenBSD 8.0 target with proven rescue path

Card: t_b5aa232e (awg-engineering-v1). Base: eng/integration-e1.

## Lab host

- **Host:** GMKtec Evo X2 mini-PC (Ryzen AI Max+ 395 "Strix Halo", 12 cores,
  128 GiB unified memory), Fedora KDE, **shared** with the normal desktop and
  the other engineering lanes (it is the project's primary workstation).
- **Hypervisor:** qemu/KVM. The CPU exposes AMD-V (32 `svm` flags in
  `/proc/cpuinfo`) and `/dev/kvm` is read/write, so KVM acceleration is used.
  `vmm(4)` is OpenBSD-only and not applicable on this Linux host, so the
  card's fallback rule selects **qemu with the OpenBSD 8.0 amd64 image**.
- **qemu build:** QEMU 11.1.2 AppImage (pkgforge-dev/QEMU-AppImage,
  anylinux-x86_64, SHA256 `c3d5a7cc1a7165c1c52c3cb4061213c56d9d8edd21d6b8ec0b04bdf92f28b00a`),
  extracted to `vm-lab/squashfs-root/bin` — no root package install needed.
- **Guest kernel independence:** the guest boots its own OpenBSD 8.0 `/bsd`
  from the qcow2 disk; nothing depends on the host's live kernel.

## Image source + checksum

| Item | Value |
|---|---|
| Media | `install80.iso` (OpenBSD/amd64 8.0 Install CD, 832 565 248 B) |
| URL | `https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/install80.iso` |
| SHA256 | `bc85d747c6ee4354d14fbfeee38087500386a2e9d86fa7b5ab13d840d902aa33` (matches CDN `SHA256`) |

Images are kept **outside the repo** in `vm-lab/` (cycle root
`/home/aigeus/.hermes/cache/scratch/awg-engineering-v1/vm-lab`). scan:allow-fixture[sha1:d1c7b3c65a8ace7b9e89e34725c5397c83fb8700]

## VM shape (create-openbsd-vm.sh defaults)

| Knob | Value |
|---|---|
| Disk | qcow2, 8 GiB virtual (`awg80-lab.qcow2`) |
| Memory / CPU | 2048 MiB / 2 vCPU (`-machine q35 -cpu max`) |
| Network | user-mode (slirp) netdev, ssh forwarded host `10022` → guest `22` |
| Serial console | `-nographic` (serial on stdio; every run tees to a `.log`) |
| Root password | `awglab` (lab-only) |

## Create

```sh
QEMU=<vm-lab>/squashfs-root/bin/qemu-system-x86_64 \
QEMU_IMG=<vm-lab>/squashfs-root/bin/qemu-img \
PYTHON=<vm-lab>/qemu-env/bin/python \
VM_DIR=<vm-lab> \
sh tools/vm/create-openbsd-vm.sh
```

The script (1) fetches + checksums the ISO, (2) creates the qcow2,
(3) runs `tools/vm/openbsd-install.py`, a pexpect driver that answers every
`install(8)` prompt over the serial console (i, vt220, us, vio0, dhcp,
autoconfig, SMP, vd0, W MBR, cd sets, all, timezone, root password, S), and
(4) snapshots the clean install. The whole serial transcript is stored next
to the disk as `<name>-serial-install.log`.

## Rescue path (proven)

1. **Snapshot/rollback:** `qemu-img snapshot -c pre-breakage awg80-lab.qcow2`
   after first boot; `qemu-img snapshot -a pre-breakage` restores it.
2. **Re-clone:** the snapshot-free disk can also be re-cloned from the ISO
   path above (fresh qcow2 + scripted install) — the media is cached.
3. **Out-of-band console:** the serial console (`-nographic`) is independent
   of the guest graphics stack and keeps printing across kernel panics and
   reboots; each boot's console output is captured to a log file.

Demonstration (see `evidence/vm-lab/`): boot 1 (install) → snapshot →
boot 2 (ssh login, `uname -a`, break `/etc/motd` + truncate `/etc/services`)
→ shutdown → `snapshot -a pre-breakage` → boot 3 (ssh login, `uname -a`,
verify restored files) → shutdown.

## Destroy

The VM is disposable: kill the qemu process and delete
`vm-lab/awg80-lab.qcow2` (plus the serial logs). The ISO and the QEMU
AppImage can be re-fetched; nothing on the host depends on them.
