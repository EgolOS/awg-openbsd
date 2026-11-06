# Runbook: surviving `sysupgrade`

`sysupgrade` replaces `/bsd` with the stock kernel and reboots. On the next
boot you have working networking but no `awg(4)`, so tunnels stay down until
the patched kernel is back. Decide which of the two procedures you want
**before** the first upgrade.

## Option A — prebuilt kernel, seconds to restore (recommended)

Keep a kernel built for each release you run, outside `/`:

```
/root/awg/bsd-8.0      # built by install.sh build
/root/awg/bsd-8.1
```

After `sysupgrade`:

```
doas cp /root/awg/bsd-<version> /bsd
doas chown root:wheel /bsd
doas chmod 700 /bsd
doas reboot
```

Verify with `uname -v` (it prints the kernel ident, e.g. `AWG.MP#0`) and
`ifconfig awg0`.

Store the kernels on a partition `sysupgrade -k` does not touch, or copy them
back from another host — `sysupgrade` cleans `/usr/obj` and can move `/home`.

## Option B — rebuild on the host

```
doas pkg_add git    # once
git clone https://github.com/EgolOS/awg-openbsd /root/awg-openbsd
cd /root/awg-openbsd
doas ./install.sh build
```

On 1 vCPU this takes roughly 35–50 minutes and needs ~2.5 GB free under
`/usr` (the tree lives in `/usr/src` and `/usr/obj`). If `/usr` is tight, move
the build tree to `/home/build` and symlink, which is what the lab does.

## After either option

1. `uname -v` shows the AWG ident.
2. `doas ifconfig awg0` shows the expected parameters.
3. `awg-quick up <config>` and one ping across the tunnel.
4. Confirm the negative tests still pass — in particular that
   `ifconfig awg0 awgs1 65535` returns `EINVAL` rather than panicking. A
   kernel built from a stale tree will not have the bound.

## Upgrading the driver itself

```
cd /root/awg-openbsd && doas git pull && doas ./install.sh build && doas reboot
```

The driver is a copy of `if_wg.c`, so a new OpenBSD release can move code
under it. Never skip the boot test after an upgrade.
