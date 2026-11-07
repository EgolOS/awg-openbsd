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
| `smoke-test.sh` | PASS/FAIL gate inside the guest: bounds, key handling, handshake over loopback, malformed input |
| `serial_read.py` | dumps the serial console to a file — the only way to read a panic after the fact |

## Bring-up

On the host (needs `/dev/kvm`, `qemu`, `python3-pexpect`; a container with
`--device /dev/kvm --network=host -v $PWD:/lab` works fine):

```sh
cp tools/lab/install.conf.example /lab/install.conf   # set a password
curl -O https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/install80.iso -o /lab/install80.iso
curl -O https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/SHA256.sig -o /lab/SHA256.sig
curl -O https://cdn.openbsd.org/pub/OpenBSD/8.0/amd64/sys.tar.gz -o /lab/sys80.tar.gz
curl -O https://cdn.openbsd.org/pub/OpenBSD/8.0/src.tar.gz  -o /lab/src80.tar.gz
signify -C -p /etc/signify/openbsd-80-base.pub -x /lab/SHA256.sig   # verify the ISO first
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
