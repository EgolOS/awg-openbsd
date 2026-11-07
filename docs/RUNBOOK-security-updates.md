# Runbook: security updates with a custom AWG kernel

## What is and is not established

This is a maintenance policy, not a record that errata were installed. No
`syspatch`, `sysupgrade`, production kernel replacement or reboot was executed
in the current engineering pass. AWG-014 and AWG-029 remain blockers; consult
`RUNBOOK-sysupgrade.md` for pair backups, stop conditions and recovery limitations.

A base-system binary update does not prove the custom AWG kernel contains that
fix. A custom build from an old source tree remains old even when `syspatch -l`
lists a newer binary patch. Likewise `uname -v` identifies the running build,
not all of its source/errata contents. Installing a kernel file does not change
the running kernel until reboot.

## Read-only triage

Record `uname -r`, `uname -v`, `sysctl kern.version`, `syspatch -c` (available)
and `syspatch -l` (installed). Read the official errata page for that exact release,
then the authenticated source patch and its release-specific instructions.

Do not use example patch numbers, guessed future release names, or unsigned
`ftp ...patch | patch` recipes. Obtain and verify source patches with the trusted
release key and the **exact commands published by OpenBSD for that erratum**;
keep the verified diff, upstream identifier, hash, applicability decision and
reviewer result. Do not interpret a successful HTTPS fetch as signature verification.

## Assess impact against two independent upstreams

| Change surface | Required review |
|---|---|
| OpenBSD net/if_wg.c, net/wg_noise.c | Function-level comparison and minimal merge into AWG copies |
| wg_cookie, ChaCha/Blake2s/Curve25519 and shared helpers | Shared build dependency and crypto/security delta |
| net/if.c, mbuf, ART/SMR, task queues, sockets, ioctl ABI | Driver lifetime and integration review even if no AWG source file changed |
| sbin/ifconfig or shared AWG header | Rebuild/revalidate the kernel-management compatibility unit |
| Official AmneziaWG Go/Linux or supported client fix | Pin source/client revisions, compare wire semantics and execute affected interop |
| Unrelated subsystem | Document why not applicable; do not infer from filename alone |

Run `tools/upstream-diff.py --tree <verified-src>/sys --release 8.0` against the
supported baseline first. It also checks sibling `sbin/ifconfig/ifconfig.c`.
A changed/errata-patched source should report MISMATCH: review and record the new
provenance rather than silently updating a digest to make a gate green.
Use `tools/sync-check.py --base OLD/net/if_wg.c --new NEW/net/if_wg.c` for the
specific upstream delta. Candidate releases do not become supported by compiling.

## Build and acceptance order

1. Verify and record source archives, errata revisions, patch identities and the
   AWG commit in an isolated, clean build VM of the exact supported release.
2. Compile unprivileged where feasible. Privileged installation must not execute
   makefiles from writable/untrusted trees. The current installer still needs
   work here (AWG-014); `--build-only` is not an unprivileged sandbox.
3. Stage and validate the artifact and corresponding patched ifconfig/header.
4. Boot the new kernel in a disposable VM, verify identity, exercise negative
   input/lifecycle/concurrency gates and the relevant real-peer interoperability.
5. Rehearse same-release paired rollback and stock-kernel management recovery.
6. Only after human authorization and proven console access, schedule production
   maintenance; keep PF/network configuration unchanged unless separately approved.
7. After reboot verify build/source provenance, ABI and actual tunnel traffic.
   A source diff, build, boot and handshake are distinct evidence levels.

## Source identity after an erratum

The 8.0 manifest pins base-release ifconfig. If an authenticated erratum changes
that source, `install.sh` intentionally refuses the unrecorded digest. Review the
erratum, re-roll/check the AWG patch and record an explicit reviewed source baseline
before extending installer support. Do not suppress the hash check or restore an
unpatched `.orig` simply to install an older vulnerable utility.

## Advisory/backport record

Keep one entry per relevant change in `SECURITY-AUDIT.md` or its linked release
record: upstream ID/revision, affected functions, trust boundary and prerequisites,
applicability, minimal backport scope, disclosure status, actual tests and residual
risk. No speculative CVE or remote-exploit claim. Suspected exploitable defects go
through private disclosure; do not publish an exploit during routine maintenance.

Review official errata and both protocol upstreams during routine maintenance and
before every release. This document installs no background updater or new service.
