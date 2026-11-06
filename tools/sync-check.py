#!/usr/bin/env python3
"""Three-way sync check: stock if_wg.c (baseline) vs stock if_wg.c (new
release) vs our src/if_awg.c.

Run this when a new OpenBSD release lands, before touching the driver:

    tar xzf /tmp/sys81.tar.gz -C /tmp/sys81          # the new tree
    python3 tools/sync-check.py --base /tmp/sys80/sys/net/if_wg.c \\
                                --new  /tmp/sys81/sys/net/if_wg.c

Output says, per upstream change, whether it can be taken mechanically (we never
touched that function) or needs a human merge (we did).

Exit status: 0 = all upstream changes are mechanical, 1 = at least one conflict.

The baseline is the tree the driver was last ported from; `docs/MAINTENANCE.md`
records which one that is.
"""
import argparse
import difflib
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
OURS = HERE / "src" / "if_awg.c"

RENAMES = (
    (re.compile(r"\bif_wg\b"), "if_awg"),
    (re.compile(r"\bwg_cookie\b"), "awg_cookie"),
    (re.compile(r"\bWG_"), "AWG_"),
    (re.compile(r"\bwg_"), "awg_"),
    (re.compile(r"\bnoise_"), "awg_noise_"),
    (re.compile(r"\bwgattach\b"), "awgattach"),
    (re.compile(r"\bwg\("), "awg("),
)


def normalise(text):
    for rx, repl in RENAMES:
        text = rx.sub(repl, text)
    return text


def functions(path):
    lines = pathlib.Path(path).read_text(errors="replace").splitlines()
    out, name, buf = {}, None, []
    for line in lines:
        if name is None:
            m = re.match(r"^([a-z_][A-Za-z0-9_]*)\(", line)
            if m:
                name, buf = m.group(1), [line]
            continue
        buf.append(line)
        if line == "}":
            out[name] = "\n".join(buf)
            name = None
    if name:
        out[name] = "\n".join(buf)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="stock if_wg.c the port was last synced to")
    ap.add_argument("--new", required=True, help="stock if_wg.c of the release you are moving to")
    ap.add_argument("--ours", default=str(OURS))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    base = {normalise(k): normalise(v) for k, v in functions(args.base).items()}
    new = {normalise(k): normalise(v) for k, v in functions(args.new).items()}
    ours = functions(args.ours)

    added = sorted(set(new) - set(base))
    removed = sorted(set(base) - set(new))
    changed = sorted(n for n in set(base) & set(new) if base[n] != new[n])

    mechanical, conflicts = [], []
    for name in changed:
        local = ours.get(name)
        if local is None:
            conflicts.append((name, "missing locally"))
        elif local == base[name]:
            mechanical.append(name)
        elif local == new[name]:
            mechanical.append(name + " (already applied)")
        else:
            conflicts.append((name, "changed locally and upstream"))

    print(f"base: {args.base}")
    print(f"new : {args.new}")
    print(f"ours: {args.ours}")
    print()
    if not (added or removed or changed):
        print("upstream did not touch net/if_wg.c — nothing to merge")
        return 0

    if added:
        print("added upstream (copy in, then wire up the callers):")
        for n in added:
            print(f"  + {n}")
    if removed:
        print("removed upstream (delete locally, check callers):")
        for n in removed:
            print(f"  - {n}")
    if mechanical:
        print("changed upstream, untouched locally — take upstream verbatim:")
        for n in mechanical:
            print(f"  = {n}")
    if conflicts:
        print("CONFLICTS — merge by hand:")
        for n, why in conflicts:
            print(f"  ! {n} ({why})")
        if args.verbose:
            for n, _ in conflicts:
                print(f"\n--- {n} ---")
                for line in difflib.unified_diff(
                    base[n].splitlines(), new[n].splitlines(),
                    fromfile="base", tofile="new", lineterm="", n=2,
                ):
                    print(line)
    print()
    print(f"RESULT: {len(added)} added, {len(removed)} removed, "
          f"{len(mechanical)} mechanical, {len(conflicts)} conflict(s)")
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
