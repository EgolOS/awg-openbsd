#!/usr/bin/env python3
"""Permanent negative controls, all through the unchanged make check-cgate."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "tools/lab"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"derivation expected one occurrence: {old!r}")
    return text.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    work = (args.output or Path(tempfile.mkdtemp(prefix="gate-controls."))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    original = (LAB / "awgctl-fixture-test.sh").read_text()
    found = re.findall(r"^canon_state\(\) \{\n.*?^\}", original, re.M | re.S)
    if len(found) != 1:
        raise ValueError("expected one real comparator")
    function = found[0]
    cases = {}

    def add(name, suite=None, env=None, reason="", green=False):
        if suite is not None:
            path = work / (name + ".sh")
            path.write_text(suite)
            env = dict(env or {}, AWGCTL_GATE_SUITE=str(path))
        cases[name] = (env or {}, reason, green)

    def logged(name, transform, reason):
        # Change instrumentation only: the real suite still builds, snapshots
        # and asserts without modification. Preserve its original exit status.
        transform_path = work / (name + ".py")
        transform_path.write_text("import os,sys\nlines=open(sys.argv[1]).read().splitlines()\n" + transform + "\nprint('\\n'.join(lines))\n")
        raw = shlex.quote(str(work / (name + ".raw")))
        derived = ("#!/bin/sh\n" + function + "\nsh " + shlex.quote(str(LAB / "awgctl-fixture-test.sh")) +
                   " > " + raw + " 2>&1\nstatus=$?\n${PYTHON:-python3} " + shlex.quote(str(transform_path)) + " " + raw + "\nexit \"$status\"\n")
        add(name, derived, reason=reason)

    add("green", reason="PASS — stable and sensitive", green=True)
    for order in ("before", "after"):
        transform = ("out=[]\nfor line in lines:\n"
                     "    shadow=line.replace('mtu=1420;', 'mtu=1419;')\n"
                     "    if line.startswith('PRE F13a='):\n"
                     f"        out.extend({'[shadow,line]' if order == 'before' else '[line,shadow]'})\n"
                     "    else: out.append(line)\nlines=out")
        logged("shadow-pre-" + order, transform, "expected exactly one 'PRE F13a'")
    logged("unique-wrong-pre", "lines=[x.replace('mtu=1420;', 'mtu=1419;') if x.startswith('PRE F13a=') else x for x in lines]", "PRE not bound to comparison")
    logged("missing-pre", "lines=[x for x in lines if not x.startswith('PRE F13a=')]", "expected exactly one 'PRE F13a'")
    logged("literal-post-pre", "lines=['PRE F13a=post=$(canon_state \"$STATE\")' if x.startswith('PRE F13a=') else x for x in lines]", "empty/malformed canonical render")
    logged("duplicate-comparison", "lines=[y for x in lines for y in ([x,x] if x.startswith('COMPARE F13a ') else [x])]", "expected exactly one 'COMPARE F13a'")
    logged("duplicate-failure", "lines=[y for x in lines for y in ([x,x] if x.startswith('FAIL: F13a: the state changed') else [x])]", "unexpected fixture counts")
    logged("inline-pre-mismatch", "lines=[x.replace('pre=family=10;mtu=1420;', 'pre=family=10;mtu=1419;') if x.startswith('FAIL: F13c:') else x for x in lines]", "F13c inline pre mismatch")
    logged("clean-witness-mismatch", "if os.environ.get('AWGFIX_MUTATE_ISPEC2'):\n    lines=[x.replace('mtu=1420;', 'mtu=1419;') for x in lines]", "injected PRE differs from clean witness")
    logged("schema-mismatch", "if os.environ.get('AWGFIX_MUTATE_ISPEC2'):\n    lines=[x.replace('ispec2=<b 0xdead>;', '') for x in lines]", "canonical schema mismatch")
    logged("wrong-post-i2", "if os.environ.get('AWGFIX_MUTATE_ISPEC2'):\n    lines=[x.replace('ispec2=<b 0xdead>;', 'ispec2=<b 0xbeef>;') for x in lines]", "wrong injected post value")
    logged("common-failure", "lines.append('FAIL: F13a: common clean/injected failure')", "clean run not green")
    add("missing-extraction", original.replace("canon_state", "hidden_canon_state"), reason="comparator extraction failed")
    add("empty-render", replace_once(original, function, "canon_state() {\n    :\n}"), reason="empty/malformed canonical render")
    add("malformed-render", replace_once(original, function, "canon_state() {\n    printf 'not canonical\\n'\n}"), reason="malformed canonical field")
    add("nonzero-render", replace_once(original, function, function[:-1] + "return 1\n}"), reason="extracted-function I2-only render probe failed")
    blind = replace_once(function, "\t' \"$1\"", "\t' \"$1\" | grep -v '^ispec2='")
    mtu_fixture = work / "mtu-ifconfig.py"
    mtu_fixture.write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import sys
rc = subprocess.call([sys.executable, ''' + repr(str(LAB / "fixture-ifconfig.py")) + '''] + sys.argv[1:])
if os.environ.get("AWGFIX_MUTATE_ISPEC2") and len(sys.argv) > 2 and sys.argv[2] == "mtu":
    state = Path(os.environ["AWGFIX_STATE"])
    state.write_text(state.read_text().replace("mtu=1420\\n", "mtu=1419\\n"))
sys.exit(rc)
''')
    add("i2-blind-mtu", replace_once(original, function, blind),
        env={"AWGCTL_GATE_IFCONFIG": str(mtu_fixture)}, reason="probe missing I2")
    add("failed-compiler", env={"CC": "false"}, reason="fixture build failed")
    add("wrong-count", env={"AWGCTL_GATE_PASS": "73"}, reason="clean run not green")

    # Adversary overrides must not be accepted simply because they exit 1.
    # These cases must run the mandatory-control branch, not skip it.
    add("adversary-missing", env={"AWGCTL_GATE_ADVERSARY": str(work / "absent.sh")}, reason="mandatory adversary missing")
    generic = work / "generic-nonzero.sh"
    generic.write_text("#!/bin/sh\nexit 1\n")
    add("adversary-generic-nonzero", env={"AWGCTL_GATE_ADVERSARY": str(generic)}, reason="did not prove an executed MTU-only rejection")
    receipt_only = work / "receipt-only.sh"
    receipt_only.write_text("#!/bin/sh\nprintf '%s\\n' 'guard-adversary: VERIFIED built; clean=74/0; injected=72/2; cause=mtu; guard=rejected'\nexit 1\n")
    add("adversary-receipt-only", env={"AWGCTL_GATE_ADVERSARY": str(receipt_only)}, reason="did not prove an executed MTU-only rejection")
    failed_cc = work / "adversary-compiler.sh"
    failed_cc.write_text("#!/bin/sh\nCC=false sh " + shlex.quote(str(LAB / "guard-adversary.sh")) + "\n")
    add("adversary-failed-compiler", env={"AWGCTL_GATE_ADVERSARY": str(failed_cc)}, reason="did not prove an executed MTU-only rejection")
    adv = (LAB / "guard-adversary.sh").read_text()
    adv = replace_once(adv, 'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)', 'here=' + shlex.quote(str(LAB)))
    for name, derived in (
        ("adversary-missing-fixture", replace_once(adv, 'real_ifconfig="$here/fixture-ifconfig.py"', 'real_ifconfig="/nonexistent/r14-ifconfig.py"')),
        ("adversary-derivation-failure", replace_once(adv, 'if ($0 == pat)', 'if (0)')),
    ):
        path = work / (name + ".sh")
        path.write_text(derived)
        add(name, env={"AWGCTL_GATE_ADVERSARY": str(path)}, reason="did not prove an executed MTU-only rejection")
    add("adversary-no-mtu-fault", env={"ADVERSARY_MTU": "1420"}, reason="did not prove an executed MTU-only rejection")

    selected = args.case or list(cases)
    results = []
    for name in selected:
        overrides, reason, green = cases[name]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AWGCTL_GATE_", "AWGFIX_", "ADVERSARY_"))}
        env.update(AWGFIX_ROUTES=str(work / (name + ".routes")))
        if name != "green" and not name.startswith("adversary-"):
            env["AWGCTL_GATE_SKIP_ADVERSARY"] = "1"
        env.update(overrides)
        run = subprocess.run(["make", "check-cgate"], cwd=ROOT, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (work / (name + ".log")).write_text(run.stdout)
        ok = (run.returncode == 0 if green else run.returncode != 0) and reason in run.stdout
        results.append({"case": name, "make_rc": run.returncode, "expected": "green" if green else "reject", "reason": reason, "ok": ok})
        print(f"{'PASS' if ok else 'FAIL'}: {name}: make check-cgate rc={run.returncode}", flush=True)
    (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"logs: {work}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
