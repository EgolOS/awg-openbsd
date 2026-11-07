# Runbook: upgrade and recovery of the kernel/ifconfig pair

## Status and stop conditions

**AWG-029 remains a release blocker.** `install.sh --rollback` restores only the
kernel; it does not restore `/sbin/ifconfig`. The manual pair procedure below is
an operator procedure, **NOT REHEARSED in this engineering pass**. The previous
kernel-only rehearsal is historical evidence, not a successful paired rollback.

Do not replace/reboot a remote production kernel without an independently tested
provider console or rescue path. An SSH connection through the VPN being replaced
is not recovery access. If SSH is the only access path, **stop before installation**.
No production reboot, deployment or PF modification was executed for this work.

## Before any same-release update

1. Confirm the exact supported source baseline in `UPSTREAM-TRACKING.md` and the
   current support matrix in `ENGINEERING-REPORT.md`. 7.9 compile-only history and
   a hypothetical next release are not installation approval.
2. Review AWG-014: do not run root builds in a source/repository tree another user
   or group can alter. Source verification does not prevent a later writable-tree
   substitution. Prefer an isolated build VM and unprivileged compilation.
3. Verify the independent management path, console credentials and stock-kernel
   recovery with the provider before scheduling downtime.
4. Keep a private, same-release recovery **pair**, not just a kernel. As root in a
   maintenance window, a proposed backup procedure is:

```sh
umask 077
backup=/root/awg-recovery/$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup"
cp -p /bsd "$backup/bsd"
cp -p /sbin/ifconfig "$backup/ifconfig"
cp -p /usr/include/net/if_awg.h "$backup/if_awg.h"  # if already installed
sha256 "$backup/bsd" "$backup/ifconfig" > "$backup/SHA256"
uname -r > "$backup/release"
uname -v > "$backup/kernel-ident"
```

Record the AWG Git commit, exact kernel/userland source and errata revisions,
header identity and verified archive/patch hashes beside that pair. Keep tunnel
secrets separately in encrypted storage. Backups on the same failed disk are not
a rescue plan; securely retain an independent copy. A first stock install may not
have `if_awg.h`; record that absence rather than inventing a backup.

## Build/stage without implying installation safety

`sh install.sh --check` reports preconditions (it creates temporary log/work files,
but does not replace base tools/kernel or alter PF). `--build-only` builds/verifies
the kernel without installing `/bsd`, **but still modifies source/build trees and
runs root make**. Neither mode currently closes AWG-014.

`--tools-only` and `--all` replace ifconfig; they are not a paired transaction.
Only in a disposable lab, after taking the pair backup and resolving the build
trust boundary, rehearse the documented installer flags:

```sh
# Disposable lab only, from a reviewed checkout:
doas sh install.sh --check
doas sh install.sh --build-only
# Inspect artifact/provenance, then independently authorize installation:
doas sh install.sh --all
# Reboot only with console access and a maintenance window.
```

No command named `install.sh build` exists. Do not copy kernels from a different
OpenBSD release. A successful link is not a boot or runtime interoperability test.

## Verify after the authorized reboot

- Record `uname -r`, `uname -v` and `sysctl kern.version`; verify the expected AWG
  identity, source provenance and matching ifconfig/header, not just a filename.
- Create/configure/destroy a spare AWG interface without touching active tunnels.
- Bring up the intended tunnel from its trusted root-owned configuration and
  verify a fresh handshake plus real bidirectional data/counters.
- Verify IPv4 and IPv6 policies independently. An IPv4 ping is not an IPv6 leak test.
- Keep management SSH, routing and PF policy intact. Destructive `make check-vm`
  and malformed-input scripts belong only in disposable guests.

## Proposed same-release manual paired rollback

If the new kernel fails to boot, use the console's `boot /bsd.awg-prev` or a known
good stock kernel/rescue image first. Booting stock can restore base networking,
not the VPN: stock has no `awg(4)`. Use independent management access.

From a working/rescue system, verify the recorded backup checksums and that its
**release equals the installed base userland release**, then restore both files:

```sh
# Root, known-good same-release backup selected explicitly:
backup=/root/awg-recovery/<verified-backup>
# Compare freshly computed hashes to the private recorded manifest first.
sha256 "$backup/bsd" "$backup/ifconfig"
install -c -o root -g bin -m 555 "$backup/ifconfig" /sbin/ifconfig.awg-restore
mv -f /sbin/ifconfig.awg-restore /sbin/ifconfig
install -F -o root -g wheel -m 700 "$backup/bsd" /bsd
sha256 -h /var/db/kernel.SHA256 /bsd
cmp "$backup/ifconfig" /sbin/ifconfig
cmp "$backup/bsd" /bsd
```

Restore matching AWG headers/build metadata if further builds will be performed.
Reboot only after confirming the management path; repeat the post-boot checks.
Cross-release OS rollback requires a complete matching base system or reinstall,
not this binary-pair recipe. Do not assume a custom kernel alone can roll back an OS.

## What sysupgrade changes

`sysupgrade` replaces the kernel and base ifconfig and can leave old `/usr/src`
behind. Stock networking and AWG tunnelling are different outcomes. Verify the new
release support matrix **before** upgrading; there is no supported 8.1 recipe here.
The installer rejects a mismatching kernel release and ifconfig source digest;
an errata-modified userland source also requires reviewed manifest/patch provenance.

## Required disposable-VM rehearsals still NOT RUN

Fresh install, same-release update, actual sysupgrade, interrupted/full-disk
installation, old/new ABI combinations, paired restore and console/rescue recovery.
Until these pass with retained logs, do not mark this runbook verified or ship an RC.
