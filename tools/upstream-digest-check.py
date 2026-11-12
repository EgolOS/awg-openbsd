#!/usr/bin/env python3
"""Fail when the recorded upstream digest no longer matches the stock sources.

The repository records the SHA-256 of the stock OpenBSD files the port was
made from (`tools/upstream/<release>.sha256`) and vendors the stock
`kern_pledge.c` (`src/kern_pledge.c.orig`) next to the patched copy and the
generated patch artifact (`awg-openbsd.patch`). Moving the baseline to a new
release replaces the stock file and the patch artifact; the recorded digest
manifest must be refreshed in the same step. When only one side is updated,
the release ships a patch that was not applied to the tree its digest
describes. This check catches that drift without a network fetch.

Checks, in order (all repo-only):
  1. sha256(src/kern_pledge.c.orig) == manifest `kern/kern_pledge.c`
  2. sha256(src/kern_pledge.c)      == manifest `kern/kern_pledge.c.awg`
  3. applying the `sys/kern/kern_pledge.c` hunk of awg-openbsd.patch to the
     stock file reproduces src/kern_pledge.c byte-for-byte, so the patch
     artifact, the stock file and the recorded digest describe one edit.

Usage:
    python3 tools/upstream-digest-check.py [--release 8.0] [--repo DIR] [--json]

Exit status: 0 = consistent, 1 = drift, 2 = usage/missing input.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Manifest entry -> vendored file that carries that exact content.
VENDORED = {
    "kern/kern_pledge.c": "src/kern_pledge.c.orig",
    "kern/kern_pledge.c.awg": "src/kern_pledge.c",
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path):
    entries = {}
    meta = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            parts = line.lstrip("#").strip()
            if ":" in parts:
                k, v = parts.split(":", 1)
                meta[k.strip()] = v.strip()
            continue
        digest, rel = line.split(None, 1)
        entries[rel.strip()] = digest
    return meta, entries


def extract_hunk(patch_text, suffix="kern_pledge.c"):
    """Return the `---/+++` section whose +++ path ends with suffix."""
    lines = patch_text.splitlines(keepends=True)
    out, collecting = [], False
    for line in lines:
        if line.startswith("--- "):
            if collecting:
                break
            header = line
            collecting = True
            out.append(line)
            continue
        if collecting and line.startswith("+++ ") and suffix in line:
            out.append(line)
            continue
        if collecting and line.startswith("+++ ") and suffix not in line:
            collecting = False
            out = []
            continue
        if collecting:
            out.append(line)
    return "".join(out)


def apply_hunk(stock_path, hunk_text, workdir):
    """Apply hunk to a copy of stock_path with patch -F 0; return result bytes."""
    hunk = workdir / "kern_pledge.hunk"
    target = workdir / "kern_pledge.c"
    hunk.write_text(hunk_text)
    target.write_bytes(stock_path.read_bytes())
    rc = subprocess.run(["patch", "-p0", "-F", "0", str(target), str(hunk)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc.returncode != 0:
        return None
    return target.read_bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(HERE.parent),
                    help="repository root (default: parent of tools/)")
    ap.add_argument("--release", default=None,
                    help="manifest release to check (default: newest)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    repo = pathlib.Path(args.repo).resolve()
    manifest_dir = repo / "tools" / "upstream"
    available = sorted(p.stem for p in manifest_dir.glob("*.sha256"))
    release = args.release or (available[-1] if available else None)
    if release is None:
        sys.exit(f"no baseline manifests under {manifest_dir}")
    manifest = manifest_dir / f"{release}.sha256"
    if not manifest.is_file():
        sys.exit(f"no manifest for release {release}: {manifest}")
    patch_path = repo / "awg-openbsd.patch"
    if not patch_path.is_file():
        sys.exit(f"missing patch artifact: {patch_path}")

    meta, entries = load_manifest(manifest)
    results = []
    failed = 0

    for entry, vendored in VENDORED.items():
        vpath = repo / vendored
        want = entries.get(entry)
        if want is None:
            results.append({"check": f"digest:{entry}", "state": "missing-entry"})
            failed += 1
            continue
        if not vpath.is_file():
            results.append({"check": f"digest:{entry}", "state": "missing-file",
                            "file": vendored})
            failed += 1
            continue
        got = sha256_file(vpath)
        ok = got == want
        failed += 0 if ok else 1
        results.append({"check": f"digest:{entry}", "file": vendored,
                        "state": "ok" if ok else "mismatch",
                        "want": want, "got": got})

    hunk = extract_hunk(patch_path.read_text(errors="replace"))
    stock = repo / VENDORED["kern/kern_pledge.c"]
    patched = repo / VENDORED["kern/kern_pledge.c.awg"]
    if not hunk:
        results.append({"check": "hunk:awg-openbsd.patch", "state": "missing-hunk"})
        failed += 1
    elif not stock.is_file() or not patched.is_file():
        results.append({"check": "hunk:awg-openbsd.patch", "state": "missing-input"})
        failed += 1
    else:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out = apply_hunk(stock, hunk, pathlib.Path(td))
        if out is None:
            results.append({"check": "hunk:awg-openbsd.patch",
                            "state": "hunk-rejected"})
            failed += 1
        else:
            ok = out == patched.read_bytes()
            failed += 0 if ok else 1
            results.append({"check": "hunk:awg-openbsd.patch",
                            "state": "ok" if ok else "mismatch"})

    report = {"release": release, "manifest": str(manifest),
              "recorded": meta.get("recorded", ""), "checks": results,
              "failed": failed}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"upstream digest check: OpenBSD {release}"
              f"{' (recorded ' + meta['recorded'] + ')' if 'recorded' in meta else ''}")
        for r in results:
            if r["state"] == "ok":
                print(f"  ok       {r['check']}")
            else:
                print(f"  FAIL     {r['check']} — {r['state']}")
                if "want" in r:
                    print(f"           expected {r['want']}")
                    print(f"           actual   {r['got']}")
        if failed:
            print(f"{failed} check(s) failed — the recorded upstream digest does "
                  "not match the vendored stock/patch; refresh the manifest and "
                  "regenerate the patch (see docs/UPSTREAM-TRACKING.md).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
