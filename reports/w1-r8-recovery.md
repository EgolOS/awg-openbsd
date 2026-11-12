# W1 / R8 — installer recovery: fail-closed restore, kernel.SHA256, honest outcomes

Tree: `awg-rc2/w1i`, branch `integ/installer-r6`, base `1bcff8d`. No push. VM 2205 not needed
(all gates run on the host through the PATH-shim harness).

## R4-02 (partial) — outcome per item

| # | Item | Status | Command |
|---|------|--------|---------|
| 1 | Fail closed before any write (recorded member missing/corrupt → non-zero, nothing written, member named) | **PASS** | `python3 -m unittest discover -s tools/tests -p 'test_installer_r8_recovery.py' -v` (historical: from other branch) |
| 2 | Restore every published member (kernel, ifconfig, if_awg.h, awgctl, /var/db/kernel.SHA256), hash-verified before "rolled back" | **PASS** | same suite (`test_rollback_restores_kernel_sha256_member`, `test_kernel_sha256_restored_after_publish_rollback`) |
| 3 | Three outcomes in exit status + message: `rolled back` (20) / `rollback failed (state: …)` (21) / `nothing to roll back` (22) | **PASS** | same suite (`test_second_signal_during_recovery_reports_rolled_back`, `test_recovery_without_manifest_reports_nothing_to_roll_back`, r7_commit `test_record_failure_reports_failed_rollback`) |
| 4 | Regression tests kept (header missing, awgctl missing, kernel.SHA256 restore, second signal) | **PASS** | `tools/tests/test_installer_r8_recovery.py` (6 tests) |
| — | `make check-tests` | **PASS** | 108 tests, 1 skipped, 0 failures (`r8-tests-after.log`) |
| — | `make check` | **PASS** | syntax/py/tests/scan/patch all OK (`r8-make-check.log`) |

## Reproduction (before the fix, HEAD 1bcff8d)

`python3 -m unittest discover -s tools/tests -p 'test_installer_r8_recovery.py' -v` (historical: from other branch)
→ `FAILED (failures=3, errors=1)` (evidence: `awg-rc2/evidence/r8-repro-before.log`):

- `test_restore_missing_header_backup_fails_closed` — FAIL: `AssertionError: 0 == 0` — the
  restore skipped the recorded header backup, removed the installed header and logged
  "Quadruple restored" with exit 0 (the reviewer's `verify-restore-missing-header.log`
  behaviour, reproduced in the sandbox harness).
- `test_restore_missing_awgctl_backup_fails_closed` — FAIL: same, exit 0 with the installed
  awgctl removed.
- `test_rollback_restores_kernel_sha256_member` — FAIL: `kernel.SHA256` kept the
  published-kernel value, not the recorded generation's.
- `test_recovery_without_manifest_reports_nothing_to_roll_back` — ERROR: with no manifest the
  restore fell back to the un-stamped base paths and reported a success (exit 0).

## The fix (install.sh)

1. **Fail closed** — `restore_generation_pair` derives header/awgctl *required* from the
   manifest hash being present (was hardcoded `no`), so "recorded but absent" fails before
   any member is written and names the member (`missing header backup <path>`).
2. **kernel.SHA256 is a generation member** — new `KERNEL_SHA256_BACKUP_BASE`
   (`/var/db/kernel.SHA256.awg-prev.<gen>`); `write_backup_pair` backs it up and records
   `backup_kernel_sha256_path`/`backup_kernel_sha256_sha256` (manifest schema 2 → 3);
   `restore_generation_pair` restores it last, hash-verified, in both the automatic recovery
   and `--rollback` paths (`--rollback-kernel-only` keeps the re-hash). `next_generation`
   and `prune_old_backups` know the new base.
3. **Honest outcomes** — `rollback_uncommitted`: 20 `rolled back` (only after every member's
   hash verifies), 21 `rollback failed (state: <RESTORE_STATE>)` where the state names how
   far the restore got (`nothing written`, `ifconfig restored`, …), 22 `nothing to roll back`
   when no generation is recorded. `publish_abort`/EXIT trap propagate 20/21/22.

## Files

- `install.sh` — restore path, backup pair, manifest schema 3, exit statuses, usage text.
- `tools/tests/test_installer_transaction.py` — harness seeds `kernel.SHA256` + backup base;
  backup-pair test pins schema 3 and the new keys (no existing assertion weakened).
- `tools/tests/test_installer_r8_recovery.py` — new, 6 tests.
- `evidence/r8-repro-before.log` — pre-fix reproduction output.
