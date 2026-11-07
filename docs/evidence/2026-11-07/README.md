# Evidence export notes

These are results from the local 2026-11-07 engineering pass, not release artifacts.
Public copies append the scanner's existing `scan:allow-fixture` marker to reviewed
PASS test-result lines (the broad password rule otherwise mistakes `PASS:` for a
password assignment). No secret-pattern rule was disabled or weakened. The markers
are annotations, not original process output. The native executed script has only
comments added; its commands are unchanged. Paths in the compiled-ifconfig log and
baseline JSON are redacted to `<lab-work>` / `<verified-src>`; hashes and outcomes
are unchanged. Unannotated originals and independent-review reports are retained
privately outside this repository. No generated secret configuration is retained.

`check-interop.log` is the genuine failed sender run; `check-interop-rerun.log`
ended at the transfer and was terminated by timeout; `check-interop-final.log`
is the successful corrected run. Do not interpret the unfinished log as PASS.
`check-vm-final.log` reports the existing AWG.MP#5 kernel, not a newly built kernel.
