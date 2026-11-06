# Deploying on a VPS (openbsd.amsterdam)

Target used for the numbers in this document: 1 vCPU / 1 GB RAM / 50 GB disk,
OpenBSD 8.0-release with errata, GENERIC.MP, serial console through `vmctl(8)`.

The provider does not publish bandwidth figures. Treat the interface speed as
unknown until you measure it; the driver is not the bottleneck at 1 vCPU for
anything up to a few hundred Mbit/s.

## 0. Before you start

- A local build is much faster than building on a 1 vCPU VPS. If you can,
  build once per release on a faster machine and copy `/bsd` over (see
  `RUNBOOK-sysupgrade.md`), or accept a 35–50 minute build on the VPS.
- 120 MB of free space in `/usr` is not enough for the build; you need about
  2.5 GB. Either grow the disk or build in `/home/build` as the lab does.

## 1. First boot

```
vmctl console <vm>          # from the host, or the provider's console link
```

Log in as `root`, then:

```sh
# a normal user with doas beats root ssh (root ssh needs a password here)
useradd -m -G wheel aigeus
passwd aigeus

# key-based ssh only
mkdir -p /home/aigeus/.ssh && chmod 700 /home/aigeus/.ssh
echo '<your public key>' > /home/aigeus/.ssh/authorized_keys
chown -R aigeus:aigeus /home/aigeus/.ssh && chmod 600 /home/aigeus/.ssh/authorized_keys

echo 'permit persist :wheel' > /etc/doas.conf
chmod 600 /etc/doas.conf

# sshd: no root, keys only
sed -i 's/^#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
rcctl restart sshd
```

Check that you can log in as the new user **before** closing the console.

## 2. Keep the stock kernel

`sysupgrade` will overwrite `/bsd`, so keep a copy of what the provider shipped
and of every kernel you build:

```sh
doas mkdir -p /root/kernels
doas cp /bsd /root/kernels/bsd-stock-$(uname -r)
```

## 3. Build and install

`awg-quick` is bash, and OpenBSD ships ksh — install the dependency first, or
`awg-quick` fails with "No such file or directory" even though the file exists:

```sh
doas pkg_add bash
```

Then:

```sh
doas pkg_add git
git clone https://github.com/EgolOS/awg-openbsd /root/awg-openbsd
cd /root/awg-openbsd
doas ./install.sh --all          # tools + kernel, ~35-50 min on 1 vCPU
doas reboot
```

After the reboot, before anything else:

```sh
uname -v                          # AWG.MP#1 or later
strings /sbin/ifconfig | grep -c awgkey   # 3
doas ifconfig awg0 create && doas ifconfig awg0 destroy
```

If `ifconfig` does not know `awgkey`, the patched binary did not land — see the
`awgkey: bad value` note in `docs/TESTING.md`.

## 4. Firmware / panic behaviour

```sh
# a panic should reboot, not drop to the debugger nobody is watching
echo 'ddb.panic=0' >> /etc/sysctl.conf
doas sysctl ddb.panic=0
```

Keep the console reachable: with `ddb.panic=1` a panic leaves the machine in
`ddb`, which looks exactly like a dead VPS from the outside.

## 5. Firewall

`install.sh` prints the MSS rule and leaves `/etc/pf.conf` alone. A minimal
policy for a VPN endpoint:

```
set skip on lo
scrub in all

match on awg scrub (max-mss 1380)

block return log
pass in on egress proto udp to (egress) port 51820
pass in on egress proto tcp to (egress) port 22
pass out on egress inet from (awg:network) to any nat-to (egress)
```

```sh
doas pfctl -nf /etc/pf.conf && doas pfctl -f /etc/pf.conf
doas rcctl enable pf
```

Adjust the port to what the tunnel uses and keep `22` reachable; if you move
ssh to another port, change both the rule and `sshd_config` in one session over
the console, not over ssh.

## 6. Tunnel

```sh
doas mkdir -p /etc/amnezia/amneziawg && doas chmod 700 /etc/amnezia/amneziawg
# write the server config (see examples/server.conf.example)
doas cp server.conf /etc/amnezia/amneziawg/vpn.conf
doas chmod 600 /etc/amnezia/amneziawg/vpn.conf
doas awg-quick up vpn
```

`awg-quick` prints the interface state; verify with `doas ifconfig awg0` and
`doas awg-quick show vpn`. Handshake counters stay at zero until a client
connects — that is normal.

Enable at boot:

```sh
echo 'awg-quick up vpn' >> /etc/rc.local     # or a small rc.d script
```

## 7. Client side

Generate the client config with the obfuscation parameters that match the
server exactly (`Jc`/`Jmin`/`Jmax`, `S1`–`S4`, `H1`–`H4`, and in 3.1 the
`HeaderProtectionKey` and `I1`–`I5`). `tools/gen-config.sh` in this repository
prints a matching pair.

## 8. What to watch

| Symptom | Usual cause |
|---|---|
| `ping` works, large transfers stall | MSS clamping missing (`match on awg scrub`) |
| handshake never completes | parameters differ between the ends, or the UDP port is blocked |
| works for a while, then drops every packet | timing values set so `RejectAfterTime` is too small — the driver now rejects those |
| `ifconfig: SIOCSAWG: Invalid argument` on one `S` value | header protection needs all four `S` values in one command |
| machine disappears after a config change | old kernel without the bounds: reinstall from a current tree |

## 9. Backup and rollback

- Keep `/root/kernels/bsd-<release>` for every release you run; restoring is
  one `cp` and a reboot.
- Keep `/etc/amnezia/amneziawg/*.conf` in your password manager or an
  encrypted store, not in git.
- Rollback path: boot the stock kernel (`vmctl start -c -b /root/kernels/bsd-stock-8.0`),
  fix the build, boot again. The driver is a separate pseudo-device, so a
  stock kernel with no `awg` interfaces is a fully working machine.
