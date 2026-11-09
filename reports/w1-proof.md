# W1e — awgctl: clean-tree proof (kernel pledge hunk + peer count)

Date: 2026-11-08 (Europe/Istanbul). Lab VM **2205** (qemu, 1 vCPU, 1 GB).
Branch `feat/awgctl`, commits `4a26481` (worker) and `71a02b2` + this report (orchestrator).
Base: `origin/main` `8d77caa` merged in (`720f80d`).

Every item below is backed by a command that was actually run. The log is
`docs/evidence/2026-11-08/w1-pledge-hunk.log`.

## 1. Clean-tree install — PASS

The guest's `/usr/src/sys/kern/kern_pledge.c` was the **stock 8.0 file** before the run.
`doas sh install.sh --all` from a fresh copy of the worktree then:

- built `ifconfig` as `_awgbuild` (`==> building as _awgbuild`, `ifconfig installed to /sbin/ifconfig`);
- applied the pledge hunk through `install_pledge_hunk` (`kern/kern_pledge.c already carries SIOCSAWG/SIOCGAWG` on the second pass, i.e. the step is idempotent);
- built the kernel, verified the artifact (`31394656 bytes, awg symbols present`), staged it and installed `/bsd`, recording the pair in `/var/db/awg-install/manifest` (`installed_bsd_sha256=8770841a405729f257219e9d4698260492c3836150c8a0c8e6ec5c87a09fc398`).

`uname -v` after reboot: **AWG.MP#2**. Rebooted a second time after the repair in §4: same.

## 2. Pledge — PASS

`doas awgctl show awg0` on a freshly created legacy interface exits **0** and prints the
legacy parameter set. The pre-fix binary aborted here (`Abort trap (core dumped)`, rc 134);
the ktrace in `reports/w1.md` shows `PLDG ioctl, "tty", errno 1` immediately before the abort.
Shipped promise set: `stdio rpath inet route tty dns proc exec`.

## 3. Peer count and full replacement — PASS

| step | command | `ifconfig awg0 \| grep -c awgpeer` |
|---|---|---|
| up (2-peer config) | `awgctl up awg0 --config …/awg2.conf` | **2** |
| add third peer | `awgctl add awg0 peer <pub3> allowed-ips 10.77.0.4/32` | **3** |
| del third peer | `awgctl del awg0 peer <pub3>` | **2** |
| up again, same file | `awgctl up awg0 --config …/awg2.conf` | **2** |

Before `4a26481` all four steps reported success while installing **0** peers.

## 4. Two defects found by the orchestrator while running this proof

- **AWG-040 (fixed here, `71a02b2`)** — `open_trusted()` walked the config path's ancestors with
  `stat(2)` *after* `unveil(NULL, NULL)`, where a parent outside the unveiled subtree returns
  `ENOENT`; every `up` failed with `/etc/amnezia: No such file or directory` although the file
  was present, root-owned, mode 0600. The check now matches what the kernel enforces: path must
  start with `CONF_DIR "/"`, `CONF_DIR` is stat'ed for ownership/mode, the file is verified on
  its descriptor. A config outside the directory is refused with an explicit message.
- **AWG-041 (open)** — `install.sh` has **no host lock**. Two minutes after the run that
  installed `/bsd` (manifest hash recorded, `installed_at` 10:28:02Z), `/bsd` was **0 bytes**
  and `/bsd.awg-prev` plus `/bsd.awg-staging` were gone. Three sessions were issuing commands
  to that guest in the window, so the writer was not isolated — which is the finding: with no
  lock there is no way to attribute or prevent it. The guest kept running (kernel in memory);
  only a reboot would have exposed the damage. Repaired from `…/compile/AWG.MP/obj/bsd`, hash checked
  against the manifest, then rebooted twice to confirm. Proposed fix is in the register.

Both were hit *because* the orchestrator and a respawned worker ran installers on the same
guest at the same time — an operational mistake on my side that the missing lock turned into
data loss.

## 5. Still open in this workstream

- `awgctl` is not installed by `install.sh --all` (only `ifconfig` and `awg-quick` are); the
  binary was built and installed by hand for this proof. Decide whether it ships.
- `set`/`add` still accept a literal secret on argv (the `-` stdin form exists but is optional).
- The peer-count proof is loopback-only: no handshake with a real peer was attempted here
  (that is the `t_e4388050` job).
- `tools/lab/awgctl-test.sh` was not run to completion in this pass; the four commands above
  are the evidence that replaced it.
