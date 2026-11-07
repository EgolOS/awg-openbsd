#!/usr/bin/env python3
"""Compare the AWG driver with the stock OpenBSD tree it was ported from.

Two jobs, both needed to keep a copied driver honest:

1.  Verify that the tree you are diffing against is the recorded upstream
    baseline (`tools/upstream/<release>.sha256`). A different tree means the
    divergence report describes something else.
2.  Report, function by function, which parts of `src/if_awg.c` and
    `src/awg_noise.c` are unchanged copies, which were modified, and which are
    AWG-only. That list is the merge surface for the next release.

    python3 tools/upstream-diff.py --tree /usr/src/sys
    python3 tools/upstream-diff.py --tree /home/build/sys --release 8.0 --json

Exit status: 0 = baseline matches and nothing unexpected, 1 = baseline
mismatch (a changed tree), 2 = usage error.
"""
import argparse
import difflib
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
MANIFEST_DIR = HERE / "tools" / "upstream"

# Same rename map the port used: stock text is normalised into our namespace so
# that an unchanged function compares equal.
RENAMES = (
    (re.compile(r"\bif_wg\b"), "if_awg"),
    (re.compile(r"\bwg_cookie\b"), "awg_cookie"),
    (re.compile(r"\bWG_"), "AWG_"),
    (re.compile(r"\bwg_"), "awg_"),
    (re.compile(r"\bnoise_"), "awg_noise_"),
    (re.compile(r"\bwgattach\b"), "awgattach"),
    (re.compile(r"\bwg\("), "awg("),
)

PAIRS = (
    ("net/if_wg.c", "src/if_awg.c"),
    ("net/wg_noise.c", "src/awg_noise.c"),
)


def normalise(text):
    for rx, repl in RENAMES:
        text = rx.sub(repl, text)
    return text


def functions(text):
    """Split a C file into top-level function bodies by brace depth.

    Returns {name: body}. Deliberately simple: `name(` at column 0 starts a
    function, the matching line `}` at column 0 ends it — the layout both files
    use.
    """
    out, name, buf = {}, None, []
    for line in text.splitlines():
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


def load_manifest(release):
    path = MANIFEST_DIR / f"{release}.sha256"
    if not path.is_file():
        sys.exit(f"no manifest for release {release}: {path}")
    entries = {}
    meta = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*(\w+):\s*(.+)", line)
            if m:
                meta[m.group(1)] = m.group(2).strip()
            continue
        digest, rel = line.split(None, 1)
        entries[rel.strip()] = digest
    return meta, entries


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True,
                    help="path to an unpacked OpenBSD sys/ tree (e.g. /usr/src/sys)")
    ap.add_argument("--release", default=None,
                    help="baseline release to check against (default: newest manifest)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    tree = pathlib.Path(args.tree)
    if not (tree / "net" / "if_wg.c").is_file():
        sys.exit(f"{tree} does not look like an OpenBSD sys/ tree")

    available = sorted(p.stem for p in MANIFEST_DIR.glob("*.sha256"))
    release = args.release or (available[-1] if available else None)
    if release is None:
        sys.exit(f"no baseline manifests under {MANIFEST_DIR}")

    meta, entries = load_manifest(release)

    baseline = []
    bad = 0
    for rel, want in sorted(entries.items()):
        path = tree / rel
        if not path.is_file():
            baseline.append({"file": rel, "state": "missing"})
            bad += 1
            continue
        got = sha256_file(path)
        state = "ok" if got == want else "mismatch"
        if state == "mismatch":
            bad += 1
        baseline.append({"file": rel, "state": state, "want": want, "got": got})

    report = {
        "release": release,
        "tree": str(tree),
        "meta": meta,
        "baseline": baseline,
        "functions": {},
    }

    for stock_rel, ours_rel in PAIRS:
        stock_path = tree / stock_rel
        ours_path = HERE / ours_rel
        if not stock_path.is_file() or not ours_path.is_file():
            continue
        stock = {normalise(k): normalise(v)
                 for k, v in functions(stock_path.read_text(errors="replace")).items()}
        ours = functions(ours_path.read_text(errors="replace"))
        identical = sorted(n for n in set(stock) & set(ours) if stock[n] == ours[n])
        modified = sorted(n for n in set(stock) & set(ours) if stock[n] != ours[n])
        # How much hand-merging each changed function needs: number of lines
        # that differ, which is a better triage signal than a binary flag.
        delta = {}
        for name in modified:
            sm = difflib.SequenceMatcher(None, stock[name].splitlines(),
                                         ours[name].splitlines())
            changed = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2
                          in sm.get_opcodes() if op != "equal")
            delta[name] = changed
        added = sorted(set(ours) - set(stock))
        removed = sorted(set(stock) - set(ours))
        report["functions"][ours_rel] = {
            "stock": stock_rel,
            "identical": identical,
            "modified": modified,
            "modified_changed_lines": delta,
            "awg_only": added,
            "upstream_only": removed,
        }

    if args.json:
        print(json.dumps(report, indent=2))
        return 1 if bad else 0

    print(f"baseline: OpenBSD {release}"
          f"{' (' + meta['recorded'] + ')' if 'recorded' in meta else ''}")
    print(f"tree    : {tree}")
    print()
    print("== baseline files")
    for row in baseline:
        if row["state"] == "ok":
            print(f"  ok       {row['file']}")
        elif row["state"] == "missing":
            print(f"  MISSING  {row['file']}")
        else:
            print(f"  MISMATCH {row['file']}")
            print(f"           expected {row['want']}")
            print(f"           actual   {row['got']}")
    if bad:
        print()
        print(f"{bad} file(s) do not match the recorded baseline — this tree is"
              " not the one the port was made from, so the function report below"
              " describes a different upstream.")
    print()
    print("== function-level divergence (stock normalised to the awg namespace)")
    for ours_rel, info in report["functions"].items():
        print(f"  {info['stock']} vs {ours_rel}")
        print(f"    identical copies : {len(info['identical'])}")
        print(f"    modified         : {len(info['modified'])}")
        for name in info["modified"]:
            print(f"      ~ {name} ({info['modified_changed_lines'][name]} lines)")
        print(f"    awg-only         : {len(info['awg_only'])}")
        for name in info["awg_only"]:
            print(f"      + {name}")
        if info["upstream_only"]:
            print(f"    upstream-only    : {len(info['upstream_only'])}")
            for name in info["upstream_only"]:
                print(f"      - {name}")
        print()
    deltas = [(n, c) for info in report["functions"].values()
              for n, c in info["modified_changed_lines"].items()]
    tiny = sum(1 for _, c in deltas if c <= 3)
    total_mod = sum(len(i["modified"]) for i in report["functions"].values())
    if total_mod:
        print(f"{tiny} of {total_mod} modified functions differ by three lines or"
              " fewer — those stay cheap to re-apply at the next release; the rest"
              " are the real merge work.")
    print()
    print("Merge rule: a function in `identical copies` can be replaced by the new"
          " upstream body verbatim; anything under `modified` is the hand-merge"
          " surface for the next release.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
