#!/usr/bin/env python3
"""Validate comparison provenance, schemas and the exact cause of a failure."""
import argparse
import json
from pathlib import Path
import re
import sys


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def payload(text):
    require(bool(text) and text.endswith(";"), "empty/malformed canonical render")
    result = {}
    for field in text[:-1].split(";"):
        key, sep, value = field.partition("=")
        require(sep and re.fullmatch(r"[a-z][a-z0-9]*", key), "malformed canonical field")
        require(key not in result, f"duplicate canonical key: {key}")
        result[key] = value
    return result


def difference(a, b):
    require(a.keys() == b.keys(), "canonical schema mismatch")
    return {k for k in a if a[k] != b[k]}


def one(lines, prefix):
    matches = [(i, line) for i, line in enumerate(lines) if line.startswith(prefix)]
    require(len(matches) == 1, f"expected exactly one {prefix!r}, got {len(matches)}")
    return matches[0]


def comparison(lines, case, failed):
    pi, pre = one(lines, f"PRE {case}")
    require(pre.startswith(f"PRE {case}="), f"malformed PRE: {case}")
    ci, record = one(lines, f"COMPARE {case}")
    prefix = f"COMPARE {case} pre="
    require(record.startswith(prefix), f"malformed comparison: {case}")
    halves = record[len(prefix):].split(" post=")
    require(len(halves) == 2, f"malformed pre/post: {case}")
    a, b = map(payload, halves)
    require(payload(pre.split("=", 1)[1]) == a, f"PRE not bound to comparison: {case}")
    difference(a, b)
    if case == "F13a":
        conclusion = "FAIL: F13a: the state changed (" if failed else "PASS: F13a: the pre-state is restored (canonical)"
    else:
        conclusion = "FAIL: F13c: the address list is not the pre-state (" if failed else "PASS: F13c: MTU and the address list are restored (canonical)"
    fi, line = one(lines, conclusion)
    require(pi < ci < fi, f"PRE/comparison/conclusion order: {case}")
    if failed:
        require(line.endswith(")"), f"malformed failure: {case}")
        inline = line[len(conclusion):-1]
        if case == "F13c":
            require(inline.startswith("pre="), "missing F13c inline pre")
            parts = inline[4:].split(" post=")
            require(len(parts) == 2 and payload(parts[0]) == a, "F13c inline pre mismatch")
            inline = parts[1]
        require(payload(inline) == b, f"inline post mismatch: {case}")
    return a, b


def runs(clean, injected, key, value, blind=False):
    cl, il = Path(clean).read_text().splitlines(), Path(injected).read_text().splitlines()
    for lines, passes, fails in ((cl, 74, 0), (il, 72, 2)):
        require(sum(x.startswith("PASS:") for x in lines) == passes and
                sum(x.startswith("FAIL:") for x in lines) == fails, "unexpected fixture counts")
        one(lines, "BUILD: awgctl-fixture OK")
    require([x.split(":", 2)[1].strip() for x in il if x.startswith("FAIL:")] == ["F13a", "F13c"], "wrong failure locations")
    for case in ("F13a", "F13c"):
        ca, cb = comparison(cl, case, False)
        a, b = comparison(il, case, True)
        require(not difference(ca, cb), f"clean comparison changed: {case}")
        require(a == ca, f"injected PRE differs from clean witness: {case}")
        if blind:
            require("ispec2" not in a and "ispec2" not in b, "adversary is not I2 blind")
        else:
            require("ispec2" in a and "ispec2" in b, "missing I2 key")
        changed = difference(a, b)
        require(changed == {key}, f"{case} wrong cause: {','.join(sorted(changed)) or 'none'}; expected {key}")
        require(b[key] == value, f"{case} wrong injected post value")
    print(f"records: built; clean 74/0; injected 72/2; cause: {key}")


def loss(log, mtu_only=False):
    lines = Path(log).read_text().splitlines()
    one(lines, "BUILD: awgctl-fixture OK")
    require(sum(x.startswith("PASS:") for x in lines) == 73, "loss control must have 73 PASS")
    require([x.split(":", 2)[1].strip() for x in lines if x.startswith("FAIL:")] == ["F13c"], "address loss must fail F13c only")
    a, b = comparison(lines, "F13c", True)
    if mtu_only:
        require(difference(a, b) == {"mtu"} and b["mtu"] == "1419", "wrong-MTU control not MTU-only")
        print("records: F13c only; cause: mtu; no secondary loss")
        return
    require(difference(a, b) == {"addrs"}, "secondary-loss control has unrelated difference")
    before, after = a["addrs"].split(), b["addrs"].split()
    secondary = "10:fd00::2/64"
    require(secondary in before and before.index(secondary) > 0, "missing pre secondary")
    require(after == [x for x in before if x != secondary], "post is not exactly the secondary loss")
    print("records: F13c only; cause: addrs; secondary 10:fd00::2/64 actually lost")


def counters(receipt, log):
    before, after = json.loads(Path(receipt).read_text())
    require(difference(before, after) == {"tx", "rx", "handshake"}, "counter control changed unrelated raw state")
    require(all(int(after[k]) == int(before[k]) + 1 for k in ("tx", "rx", "handshake")), "counters did not advance")
    lines = Path(log).read_text().splitlines()
    one(lines, "BUILD: awgctl-fixture OK")
    require(sum(x.startswith("PASS:") for x in lines) == 74 and not any(x.startswith("FAIL:") for x in lines), "counter control not green")
    for case in ("F13a", "F13c"):
        a, b = comparison(lines, case, False)
        require(not difference(a, b), "counter control changed a comparison")
    print("records: real tx/rx/handshake advanced; projected comparisons unchanged; 74/0")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("probe", "runs", "mtu", "loss", "loss-mtu", "counters"))
    p.add_argument("paths", nargs="+")
    args = p.parse_args()
    try:
        if args.mode == "probe":
            a, b = (payload(Path(x).read_text().replace("\n", ";")) for x in args.paths[:2])
            require("ispec2" in a and "ispec2" in b, "probe missing I2")
            require(difference(a, b) == {"ispec2"}, "probe is not I2-only sensitive")
            require(a["ispec2"] == "<b 0x01>" and b["ispec2"] == args.paths[2], "probe wrong I2 values")
            print("records: extracted comparator I2-only probe passed")
        elif args.mode == "counters":
            counters(args.paths[0], args.paths[1])
        elif args.mode in ("loss", "loss-mtu"):
            loss(args.paths[0], args.mode == "loss-mtu")
        else:
            runs(args.paths[0], args.paths[1], "mtu" if args.mode == "mtu" else "ispec2",
                 args.paths[2], args.mode == "mtu")
    except (Invalid, OSError, TypeError, IndexError) as e:
        print(f"records: FAIL — {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
