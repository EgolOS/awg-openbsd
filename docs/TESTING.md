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

`tools/lab/smoke-test.sh` runs this list inside the VM and prints PASS/FAIL per
case, so it is the gate this matrix refers to: `doas sh tools/lab/smoke-test.sh`.
These are the cases that produced `panic: m_clget: request for 65807 byte
cluster` before the bounds were added.

| Input | Expected |
|---|---|
| `ifconfig awg0 awgs1 65535` | `EINVAL`, device unaffected |
| `ifconfig awg0 awgs2 65535` (and `S3`, `S4`) | `EINVAL` |
| `ifconfig awg0 awgjc 65535` | `EINVAL` |
| config with `S1=65535`, then a handshake | no panic, handshake dropped |
| `I1 = <r 65507>` | packet skipped, nothing fragmented |
| peer sends a 65 000-byte transport packet | padding clamped, no panic |
| truncated/oversized UDP datagrams to the port | dropped, no crash |

Record the console output: booting with a serial console attached
(`-serial file:...`) is what makes a panic legible after the fact.

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
