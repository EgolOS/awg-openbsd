#!/usr/bin/env python3
"""Derive PASS/FAIL/PARTIAL counts from a runner log (R6/N4).

A report must not claim a verdict its own log contradicts, so the counts are
computed from the log text instead of typed by hand.  Recognised shapes:

  PASS: <text>            -> pass
  FAIL: <text>            -> fail
  NOTE: <text>            -> note (informational, never a pass)
  RESULT: all ... passed  -> summary line, cross-checked against the counts
  RESULT: N ... failed    -> summary line, cross-checked against the counts
  RESULT: <n> case(s) failed
  IDLE_RESULT/CHURN_RESULT present=P missing=M of T  -> partial (P of T)

Usage: python3 tools/lab/log-verdicts.py LOG [LOG ...]
Exit 0 when every log's own RESULT line agrees with the counted PASS/FAIL.
"""
import re
import sys

TS = r"^(?:\d{1,2}:\d{2}:\d{2}(?::\d{2})?\s+)?"
PASS = re.compile(TS + r"PASS:\s*(.*)$")
FAIL = re.compile(TS + r"FAIL:\s*(.*)$")
NOTE = re.compile(TS + r"NOTE:\s*(.*)$")
RESULT = re.compile(TS + r"RESULT:\s*(.*)$")
CHURN = re.compile(TS + r"(IDLE_RESULT|CHURN_RESULT)\s+present=(\d+)\s+missing=(\d+)\s+of\s+(\d+)")
SUMMARY = re.compile(r"^(?:(?P<np>\d+)\s+(?:laptop\s+)?case|all)\s*(?:s)?\s*(?:laptop\s+)?(?:check|case)")


def tally(path):
    npass = nfail = nnote = 0
    partial = []
    declared = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            m = PASS.match(line)
            if m:
                npass += 1
                continue
            m = FAIL.match(line)
            if m:
                nfail += 1
                continue
            m = NOTE.match(line)
            if m:
                nnote += 1
                continue
            m = CHURN.match(line)
            if m:
                present, missing, total = int(m.group(2)), int(m.group(3)), int(m.group(4))
                partial.append((m.group(1), present, missing, total))
                continue
            m = RESULT.match(line)
            if m:
                declared = m.group(1)
    return npass, nfail, nnote, partial, declared


def main(argv):
    if len(argv) < 2:
        print("usage: log-verdicts.py LOG [LOG ...]", file=sys.stderr)
        return 2
    bad = 0
    for path in argv[1:]:
        npass, nfail, nnote, partial, declared = tally(path)
        rows = [f"PASS={npass}", f"FAIL={nfail}", f"NOTE={nnote}"]
        for name, present, missing, total in partial:
            pct = (100.0 * present / total) if total else 0.0
            rows.append(f"{name}=PARTIAL {present}/{total} ({pct:.0f}% missing {missing})")
        agree = "agrees"
        if declared is not None:
            dm = re.match(r"^(\d+)\s+(?:laptop\s+)?(?:check|case)\(s\)\s+failed", declared)
            if dm:
                agree = "agrees" if int(dm.group(1)) == nfail else f"DISAGREES (log says {dm.group(1)})"
            elif declared.startswith("all"):
                agree = "agrees" if nfail == 0 else f"DISAGREES (log says all passed, counted {nfail})"
        print(f"{path}\t{'\t'.join(rows)}\tRESULT='{declared}'\t{agree}")
        if agree != "agrees":
            bad += 1
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
