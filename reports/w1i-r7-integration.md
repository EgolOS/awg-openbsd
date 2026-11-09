# R7 w1i scanner/evidence integration gate

Task: `t_a86e37d4`; branch: `integ/installer-r6`. Host-only integration, no push,
VM or SSH. This receipt does not claim a new native driver or installer run.

## Changes and provenance

- Integrated the applicable scanner and evidence-redaction changes from `e8c8211`
  using git objects in this worktree. Scanner source was taken from `16ca687`,
  then reconciled with the existing integration-specific topology exception and
  stricter home-path behavior. No sibling worktree was accessed or changed.
- Kept the per-rule exception mechanism and all three existing negative bypass
  cases. Added copyright-format and cross-rule regression cases, including the
  roff attribution form. Upstream legal attributions are unchanged.
- Kept personal home paths non-suppressible by fixture annotations: the additional
  home-path fixture exception from `16ca687` is not needed here and was not kept.
  Imported evidence uses placeholders instead of additional home exceptions.
- Removed the tracked `.fixdbg.8HvjUb/awgabrt` debug executable containing a local
  build path. Other debug files were left untouched; no scanner exclusion added.
- Annotated synthetic awgctl fixture keys on their own source lines. Heredoc
  configs use the parser's existing trailing-comment support; single-line
  `write_conf` inputs retain their original bytes. No fixture assertion changed.
- `3373c61` and `62ad6a2` concern network harnesses and evidence absent from this
  integration tree. Those unrelated additions were not imported for this gate.

## Shared-index receipt

`ee0eab6` is a mixed-index commit: the lock worker's report/orphan-test commit
also captured seven already-staged scanner/docs paths from this integration.
Its history is preserved, not rewritten. `d07d9ad` records that collision on the
installer report. The remaining integration-only paths are committed separately
with an explicit path-limited commit. Neither installer source/tests nor the
lock worker's reports were edited by this task.

## Verification

On the actual integrated working tree:

- Initial `make check-scan`: 54 findings (22 email, 18 key, 13 home, 1 private IP).
- `make check-tests`: exit 0, `Ran 94 tests in 33.307s`, `OK (skipped=1)`.
- `make check`: exit 0, `Ran 94 tests in 35.939s`, `OK (skipped=1)`;
  `SELF-TEST: ok`, `RESULT: clean`, `publish scan: clean`,
  `awg-openbsd.patch is up to date`, `generated patch: current`,
  `repository checks: OK`.
- `git diff --check` and `git diff --cached --check`: exit 0, no output.
- `sh tools/lab/awgctl-fixture-test.sh`: exit 0,
  `RESULT: all fixture cases passed`. This is a Linux host fixture, not native
  ioctl/pledge/unveil evidence.
- Content hashes of the initial installer/test paths, awgctl source/reports,
  `.r7repro2/` files and `awgctl-fixture` still match the task-entry snapshot:
  `Protected paths changed: []`.

Final post-commit gate output and exact HEAD are in the task completion handoff.
Full command logs are retained with that handoff. History scanning was not run;
removing a tracked artifact or redacting evidence does not scrub git history.
