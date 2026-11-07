# Testing

Every release is expected to pass the matrix below on a quiet machine. The
abbreviated form runs in a container with KVM; no hardware is needed.

## Lab harness

Two OpenBSD VMs on a private L2 segment:

| VM | Role | Resources | Notes |
|---|---|---|---|
| `obsd-a` | server | 1 vCPU / 1 GB | matches the target VPS |
| `obsd-b` | client | 2 vCPU / 1 GB | |

Both boot the `AWG.MP` kernel built by `install.sh build`. The client can also
run `amneziawg-go` from the OpenBSD port or a Linux/Android/Windows AmneziaWG
client for interoperability.

## Functional matrix

| Test | Expected |
|---|---|
| legacy 2.0: `awg0` up, ping across tunnel | replies, counters increase |
| 3.1: header protection key + `S3`/`S4` + `I1` | handshake completes |
| 3.1 ↔ `amneziawg-go` client | handshake completes, data flows |
| `awg-quick up/down` with a sample config | interface appears and disappears |
| `ifconfig awg0` | all parameters round-trip |
| `iperf3` (1 vCPU server) | record the number in the release notes |

## Negative tests (must not panic)

Four scripts, all run by `make check-vm` (or individually as root inside the VM).
Each prints PASS/FAIL per case and exits non-zero on the first failing class, so
they are the gate this matrix refers to.

| Script | What it covers |
|---|---|
| `tools/lab/smoke-test.sh` | lifecycle, key handling, one obfuscated handshake, malformed datagrams, 3.1 round-trip |
| `tools/lab/wire-test.sh` | receive path against zero-length, short, oversized (65 000 B) and structurally wrong datagrams; interface must stay usable |
| `tools/lab/matrix-test.sh` | every parameter at its bound, plus the interdependent rules (`jmin>jmax`, `H` range overlap, `S<12` with header protection, `reject <= keepalive+rekey`, `I1` descriptors) |
| `tools/lab/stress-test.sh` | 200 version switches, parameter churn and peer add/remove while traffic flows |

The malformed-input cases are the ones that produced
`panic: m_clget: request for 65807 byte cluster` before the bounds were added,
and a `panic: free: non-malloced addr ... type devbuf` in `awg_reset_3_1()`
(now `AWG-030` in `docs/SECURITY-AUDIT.md`).

| Input | Expected |
|---|---|
| `ifconfig awg0 awgs1 65535` | `EINVAL`, device unaffected |
| `ifconfig awg0 awgs2 65535` (and `S3`, `S4`) | `EINVAL` |
| `ifconfig awg0 awgjc 65535` | `EINVAL` |
| config with `S1=65535`, then a handshake | no panic, handshake dropped |
| `I1 = <r 65507>` | packet skipped, nothing fragmented |
| peer sends a 65 000-byte transport packet | padding clamped, no panic |
| truncated/oversized UDP datagrams to the port | dropped, no crash |
| `awgversion 3.1` … `awgversion legacy` with `I1` set | returns to legacy, no bad free |

Record the console output: booting with a serial console attached
(`tools/lab/start_vm.sh` writes `serial-<name>.log`) is what makes a panic
legible after the fact. A panic leaves the guest at the `ddb>` prompt, where
`show panic` and `trace` name the function that failed.

## Reproducing the panic (fixed)

```
ifconfig awg1 create
ifconfig awg1 awgkey <server-key> awgport 51999 awgversion legacy \
    awgs1 65535 awgs2 0
ifconfig awg1 inet 10.8.9.1/24 up
ifconfig awg1 awgpeer <peer-key> awgaip 10.8.9.2/32 awgendpoint 10.9.0.2 51998
ping -c 1 10.8.9.2      # pre-fix: kernel panics here
```

## Known quirks found while testing

- On a 3.1 interface with `HeaderProtectionKey`, changing one of `S1`/`S2`
  alone is refused (`SIOCSAWG: Invalid argument`) because `ifconfig` zeroes the
  companion field and the driver rejects `S < 12` with header protection on.
  Set all four S values in one command, as `awg-quick` does.
- Root ssh with a locked password is refused even when the key is accepted;
  use a normal user with `doas`.
- A panic drops the machine into `ddb`; hard-killing qemu there can leave the
  filesystem inconsistent. Rebuild the disk if userland starts core dumping.

## Interoperability

| Client | Status |
|---|---|
| AmneziaWG for Android/Windows (legacy 2.0) | verified |
| `amneziawg-go` (3.1) | verified |
| Linux kernel module (3.1) | verified on the wire, not yet with `HeaderProtectionKey` |

Anything not in this table is untested — say so in the release notes.
