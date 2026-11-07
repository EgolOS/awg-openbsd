# Two-VM lab

Everything here was used to validate the driver. It is deliberately small: an
unattended OpenBSD install, two VMs on a private L2, and a build script that
compiles the kernel inside the guest.

## Pieces

| File | Purpose |
|---|---|
| `install.conf.example` | autoinstall(8) response file — copy to `install.conf`, set a password and an ssh key |
| `install_vm.py` | boots the ISO, answers `bsd.rd` over serial, runs the autoinstall |
| `start_vm.sh` | starts a VM: user-mode NIC for ssh/http + a private L2 socket between the pair |
| `build-in-vm.sh` | fetches `sys.tar.gz` + `driver.tgz` from the host, builds `AWG.MP` and the patched `ifconfig` |
| `interop-test.sh` | two VMs, tunnel over the LAN link: handshake, ping, 8 MiB transfer |
| `smoke-test.sh` | PASS/FAIL gate inside the guest: bounds, key handling, handshake over loopback, malformed input |
| `serial_read.py` | dumps the serial console to a file — the only way to read a panic after the fact |

## Safety boundary and current status

Only explicitly disposable, private lab VMs are authorized. The current pair is
2 vCPU / 1 GB each and runs AWG.MP#5. No performance baseline at 1 vCPU exists.
Individual scripts can destroy test interfaces; keep them away from production.

Use `AWG_LAB_DISPOSABLE=1 make check-vm` / `make check-interop`. The Makefile now
requires pinned host keys (`LAB_KNOWN_HOSTS`); obtain keys/fingerprints from the
local VM console and verify before populating that file. TOFU acceptance is at
most a deliberate disposable-loopback bootstrap, not a production SSH policy.
`A_SSH`/`B_SSH` remain trusted operator shell-command overrides (including quoted
key paths); never populate them from peers/config files. All metacharacter/path
overrides have not been exhaustively tested.

Interop refuses an existing awg0, requires PF **already disabled in the isolated
lab**, and leaves PF unchanged. It checks sender status, receiver bytes and SHA256.
The old OpenBSD-invalid `nc -l -p` listener and timer-only success are fixed.
This is not a benchmark, full protocol matrix, long soak or leak-freedom proof.

## Bring-up

On the host (needs `/dev/kvm`, `qemu`, `python3-pexpect`; a container with
`--device /dev/kvm --network=host -v $PWD:/lab` works fine):

```sh
cp tools/lab/install.conf.example /lab/install.conf   # set a password
curl -fLo /lab/install80.iso https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/install80.iso
curl -fLo /lab/SHA256.sig https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/SHA256.sig
# Use a release key from an already trusted OpenBSD installation.
(cd /lab && signify -C -p /etc/signify/openbsd-80-base.pub -x SHA256.sig install80.iso)
curl -fLo /lab/sys.tar.gz https://cdn.openbsd.org/pub/OpenBSD/8.0/sys.tar.gz
curl -fLo /lab/src.tar.gz https://cdn.openbsd.org/pub/OpenBSD/8.0/src.tar.gz
curl -fLo /lab/source-SHA256.sig https://cdn.openbsd.org/pub/OpenBSD/8.0/SHA256.sig
(cd /lab && signify -C -p /etc/signify/openbsd-80-base.pub -x source-SHA256.sig sys.tar.gz src.tar.gz)
# Existing lab helpers expect these names, only after verification:
cp /lab/sys.tar.gz /lab/sys80.tar.gz
cp /lab/src.tar.gz /lab/src80.tar.gz
DISKSIZE=40G VMNAME=obsd-a python3 tools/lab/install_vm.py
```

Then:

```sh
sh tools/lab/start_vm.sh obsd-a 2201 listen 9001 1 1024 01
sh tools/lab/start_vm.sh obsd-b 2202 connect 9001 2 1024 02
ssh -p 2201 lab@127.0.0.1            # add your key first, or use the console
doas sh build-in-vm.sh               # inside the guest, tree on /home
```

## Gotchas that cost time

- `set tty com0` in the bootloader, otherwise the installer talks to a screen
  nobody sees.
- The default 10 GB disk leaves `/usr` at 2.9 GB and the build fails with
  `No space left on device`. Use 40 GB, or move the tree to `/home/build`.
- Root ssh with a `*` password is refused even when the key is accepted; use a
  normal user and `doas`.
- Two clones share a MAC address and the L2 segment dies silently. Always pass
  a distinct MAC id.
- `-serial unix:...,server=on` discards output when nothing is attached — use
  `file:` if you want to capture a panic.
- A panic drops to `ddb`; killing qemu there can leave the filesystem
  inconsistent (`Floating point exception (core dumped)` from userland on the
  next boot). Rebuild the disk if that happens.
- A fresh install can end up with host keys that `sshd` refuses
  (`sshd: no hostkeys available`, `ssh-keygen -y` reports *invalid format*).
  Fix inside the guest: `rm -f /etc/ssh/ssh_host_* && ssh-keygen -A &&
  rcctl start sshd`. The install itself is fine; only the key files are bad.
- `ping` on OpenBSD has no `-W`; use `-w <seconds>` for a total deadline.
