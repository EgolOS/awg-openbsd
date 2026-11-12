# External and unverifiable references

Commit-shaped identifiers cited by this repository's documents that do **not**
resolve to a commit here. `tools/check-citations.py` (`make check-citations`)
resolves every cited hash with `git rev-parse --verify <sha>^{commit}`; a hash that
fails resolves only if this table declares it, and the table is enforced in both
directions:

1. an entry that *does* resolve in this repository fails the guard (cite the commit
   directly instead);
2. an entry cited from a file outside its `Citable only in` column fails the guard,
   so a phantom hash cannot be pasted into a new report;
3. an entry cited nowhere fails the guard, so the table cannot grow into a dumping
   ground;
4. `docs/` (release notes, security audit, engineering report, matrix) may not cite
   an `out-of-tree-workspace` entry at all — those are history of workspaces this
   repository never contained, and no gate, matrix row or release claim may rest on
   them;
5. an `external-evidence-branch`, `upstream-repo` or `content-hash` entry must name
   its provenance on the citing line itself (the token in the last column);
6. every accepted hit is printed as `external (unverifiable here)` and is **not**
   counted as current evidence.

| Identifier | Kind | Provenance | Citable only in | Required context token |
|---|---|---|---|---|
| `01098cd` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-integ.md` | — |
| `04d00d1` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w11-r4.md` | — |
| `0d73ac5f` | content-hash | prefix of the OpenBSD 8.0 source-tree sha256 in `tools/upstream/8.0.sha256` (not a git object) | `reports/w1.md` | `sha256` |
| `16ca687` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-r7-integration.md` | — |
| `191d8816` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `1a72a73` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w11-r5.md` | — |
| `1b8e339` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1.md` | — |
| `1bcff8d` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r8-prevalidation.md`, `reports/w1-r8-recovery.md` | — |
| `20c025a8` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `25dc854` | external-evidence-branch | `test/network-matrix` branch (declared separate evidence set; not in this repository) | `docs/RELEASE-NOTES.md`, `docs/TRACEABILITY.md` | `network-matrix` |
| `27eb5b0` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w11-r6.md` | — |
| `2eec623620fbb80bb11ec4a633fc0c0d91e2f7ea` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md` | — |
| `3373c61` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-r7-integration.md` | — |
| `3ae2cb6` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock`; `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md`, `reports/w1i-awgctl.md` | — |
| `3e43460` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w11-r6.md` | — |
| `41e0ee3` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock`; `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md`, `reports/w1i-awgctl.md` | — |
| `4569c4c67f3a57414969260cafbbd04694fbaae0` | upstream-repo | amnezia-vpn/amneziawg-linux-kernel-module (different repository) | `docs/ENGINEERING-REPORT.md` | `amneziawg-linux-kernel-module` |
| `4655d6d` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w11-r4.md` | — |
| `4a26481` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-proof.md` | — |
| `4bf4ce739ee24c597f05060aba35ca130d6c8ce8` | out-of-tree-workspace | workspace that workspace's native restore run, staged on the lab host — not retained, so the hash resolves in no ref of this repository | `reports/w1-r9-restore-native.md` | — |
| `62ad6a2` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-r7-integration.md` | — |
| `71a02b2` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-proof.md` | — |
| `720f80d` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-proof.md`, `reports/w1.md` | — |
| `74b4171` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `7691fe4` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w11-r6.md` | — |
| `7dc021a` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-awgctl.md` | — |
| `7f454c46` | content-hash | ELF magic of the built kernel artifact (not a git object) | `docs/ENGINEERING-REPORT.md` | `artifact` |
| `8770841a` | content-hash | prefix of the installed `/bsd` sha256 recorded in the installer manifest (not a git object) | `docs/SECURITY-AUDIT.md`, `docs/TRACEABILITY.md` | `sha256` |
| `8a00b97` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl`; `~/.hermes/cache/scratch/awg-rc2/w1b`, branch `fix/awgctl-r4` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md`, `reports/w11-r4.md`, `reports/w11-r6.md` | — |
| `8b3cc3e` | out-of-tree-workspace | workspace `git archive` of that workspace's Round-10 pin, staged on the lab host — not retained, so the hash resolves in no ref of this repository | `reports/w1-r10-native-suite.md` | — |
| `95779448c013deb29200ac50569229c7ec611b46` | out-of-tree-workspace | workspace that workspace's native restore run, staged on the lab host — not retained, so the hash resolves in no ref of this repository | `reports/w1-r9-restore-native.md` | — |
| `95786d3` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md` | — |
| `9589ffb` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-awgctl.md` | — |
| `965fc47` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md` | — |
| `9ad722b` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock`; `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md`, `reports/w1i-awgctl.md` | — |
| `9b31ab2` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-integ.md` | — |
| `abddf0e` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1.md` | — |
| `ac8567f` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `b1d09ad2a5eb5af0b174fe7aa55e7c284b92cda4` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1.md` | — |
| `b5928ef` | upstream-repo | amnezia-vpn/amneziawg-go (different repository) | `docs/ENGINEERING-REPORT.md`, `docs/TRACEABILITY.md` | `amneziawg-go` |
| `b5928efb6ca19f0153958460c3d141f04abc5c2e` | upstream-repo | amnezia-vpn/amneziawg-go (different repository) | `docs/ENGINEERING-REPORT.md` | `amneziawg-go` |
| `c7cfcd8` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `c970ab83` | content-hash | prefix of the built kernel artifact sha256 (not a git object) | `docs/ENGINEERING-REPORT.md` | `sha256` |
| `d07d9ad` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-r7-integration.md` | — |
| `d9e4d04` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md` | — |
| `ddb10c7` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-awgctl.md` | — |
| `e00ed8f6` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `e0e5fa0` | external-evidence-branch | `test/network-matrix` branch (not in this repository) | `docs/SECURITY-AUDIT.md` | `network-matrix` |
| `e774efb` | external-evidence-branch | `test/network-matrix` branch, earlier head (not in this repository) | `docs/RELEASE-NOTES.md` | `network-matrix` |
| `e8c8211` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1i-r7-integration.md` | — |
| `e94971ed` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `edb346c` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `ee0eab6` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1c`, branch `fix/installer-lock`; `~/.hermes/cache/scratch/awg-rc2/w1i`, branch `integ/installer-r6` — not retained, so the hash resolves in no ref of this repository | `reports/w1-n1-v2.md`, `reports/w1i-r7-integration.md` | — |
| `ee13b45` | out-of-tree-workspace | workspace `~/.hermes/cache/scratch/awg-rc2/w1`, branch `feat/awgctl` — not retained, so the hash resolves in no ref of this repository | `reports/w1-r4.md` | — |
| `fbe6c467057357c911c0b02316574b270ac599f7` | out-of-tree-workspace | workspace that workspace's native restore run, staged on the lab host — not retained, so the hash resolves in no ref of this repository | `reports/w1-r9-restore-native.md` | — |

Enforced by `tools/check-citations.py` (`make check-citations`), which fails when a
cited hash is missing from this table, when an entry is stale (it resolves here),
when an entry is cited from a file outside its `Citable only in` column, or when
an entry is cited nowhere at all.
