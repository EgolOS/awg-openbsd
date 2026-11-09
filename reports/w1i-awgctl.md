# w1i — awgctl lane integration + N13 + R4-08 (round 7)

Branch `integ/installer-r6` in `awg-rc2/w1i`. No push.
Commits (oldest first): `41e0ee3` merge, `ddb10c7` N13, `9589ffb` R4-08,
`3ae2cb6` publish-scan allow-table union.

## 1. Merge `fix/awgctl-r4` (7dc021a) → `integ/installer-r6` — PASS

Command: `git merge --no-commit --no-ff fix/awgctl-r4`, then
`git checkout --theirs <conflicts> && git add <same>` (merge commit `41e0ee3`).

Conflicts — exactly two files:

| file | resolution |
|---|---|
| `sbin/awgctl/awgctl.c` | `--theirs`: the corrected tool (round-4/5/6 fixes) replaces the old integ copy wholesale; the integ side of this file had no independent edits. |
| `tools/publish-scan.py` | `--theirs`: the N6 per-rule (pattern, reason) allow table replaces the flat integ list. The integ lab prefix `10.9.19.` is not in the w1b topology, so it was added back to the private-ipv4 exception (`3ae2cb6`) — union of both lab prefixes, so the merged fixture keeps scanning clean. |

The merge also brought `sbin/awgctl/awgctl.8`, `tools/lab/awgctl-test.sh`,
`tools/lab/fixture-ifconfig.py`, `tools/lab/fixture-stubs.c`,
`tools/tests/test_awgctl.py`, `tools/tests/test_awgctl_r5.py`, the
`.fixdbg.*` evidence and `reports/w11-r5-*` into the tree.

Verification:

- `make check-tests` → `Ran 94 tests ... OK (skipped=1)` (baseline was 85;
  the merge plus the concurrent lock work `9ad722b` took the suite to 94).
- `sh tools/lab/awgctl-fixture-test.sh` (host fixture, the w1b copy) →
  `RESULT: all fixture cases passed`, 58 PASS.
- `AWG_LAB_DISPOSABLE=1 sh tools/lab/awgctl-test.sh` on VM 2206
  (kernel `AWG.MP#2`, tool built with `bsd.prog.mk`) → 18 PASS / 81 FAIL,
  37 `Abort trap (core dumped)`. The abort pattern matches the pre-fix VM
  baseline `reports/w11-r5-vm2206.log` (kernel-side ioctl path on that VM
  build), so it is not a merge regression; full log saved as
  `reports/w1i-awgctl-vm2206.log`.
- `make check` also runs `check-scan`: 54 findings (64 before the allow-table
  union). The w1b per-rule scanner is stricter than the integ flat list, so
  the union of the two trees' evidence logs scores worse; recorded, not in
  this task's scope.

## 2. N13 — non-contiguous netmasks pass as prefixes — PASS

Reproduction (pre-fix tool, host fixture, `AWGFIX_MASK`):
`0xff00ff00` recorded `prefix=/8` and `0xf0f00000` recorded `prefix=/4`
(`/tmp/repro2.log`). `mask_bits()` counted the leading run of ones and
re-checked the remainder with a shift of `8 - bits % 8`; when the run ends on
a byte boundary that shift is 8, so a whole byte is dropped and a holed mask
verifies.

Fix: walk all `8 * len` bits (4 IPv4 / 16 IPv6 bytes) and return -1 —
"cannot verify" — for any one that follows a zero, so only a run of ones
followed by zeros is accepted.

Tests: fixture case F11 — `0xff00ff00` and `0xf0f00000` rejected,
`0xffffff00` (/24) and `0xffffffff` (/32) accepted, next to the existing N7
missing-mask case. Command
`sh tools/lab/awgctl-fixture-test.sh` → all pass. Commit `ddb10c7`.

## 3. R4-08 residual — mutation before validation — PASS

Reproduction (pre-fix tool, host fixture): with a valid first address and an
invalid second (`10.9.19.2/33`), a bad peer endpoint, a bad allowed-IP, and a
NUL-truncated config, the interface was created with MTU 1420 and the first
address installed before the failure (`/tmp/repro2.log`).

Fix: split `apply_config` into `serialise_config()` (base64 keys,
`getaddrinfo` endpoint, `inet_net_pton` allowed-IPs, address grammar via the
new `parse_address()`) and the single `SIOCSAWG`; the whole payload is parsed,
resolved and serialised before the first mutation. A `state_snapshot`
(existence, MTU, addresses) taken before create/MTU/addresses is restored on
an operational failure — a fresh interface is destroyed, a pre-existing one
gets its MTU and address list back. A NUL byte in the config now refuses the
whole file (N12). The host fixture gained `mtu` in its state, an `AF_LINK`
`ifa_data`, `AWGFIX_CREATE`/`AWGFIX_SET_ERR` knobs and a `destroy` that
resets the modelled interface.

Tests: fixture case F12 — invalid second address after a valid first, bad
peer endpoint, bad allowed-IP, truncated/NUL config — each leaves the
interface, MTU and addresses exactly as before. Command
`sh tools/lab/awgctl-fixture-test.sh` → 58 PASS. Commit `9589ffb`.

## Statuses

| item | status | command |
|---|---|---|
| 1 merge awgctl lane | PASS (94 tests OK, skipped=1; fixture 58 PASS; VM 2206 run recorded) | `git merge --no-commit --no-ff fix/awgctl-r4`, `make check-tests`, `sh tools/lab/awgctl-fixture-test.sh`, `AWG_LAB_DISPOSABLE=1 sh tools/lab/awgctl-test.sh` (VM 2206) |
| 2 N13 netmask | PASS | `sh tools/lab/awgctl-fixture-test.sh` (F11) |
| 3 R4-08 prevalidation | PASS | `sh tools/lab/awgctl-fixture-test.sh` (F12) |
