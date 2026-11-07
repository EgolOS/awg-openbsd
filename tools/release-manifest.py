#!/usr/bin/env python3
"""Release manifest for an awg-openbsd build.

Collects the provenance of one release into a JSON document: which OpenBSD
release the kernel was built for, which driver commit produced it, the sha256
and size of the kernel artefact, the sha256 of the generated awg-openbsd.patch
and of the userspace tools (awg-quick, the ifconfig patch), the recorded test
results, the build host and a UTC timestamp. The JSON goes to stdout and, with
--out, to a file.

Exit status: 0 when every test result is "pass", 1 when at least one is "fail"
(usage errors also exit 1).

Usage:
    python3 release-manifest.py --kernel /path/to/bsd --version 1.0 \
        --release 8.0 --commit deadbeef --tests smoke=pass
    python3 release-manifest.py --self-test

Stdlib only, Python 3.8+.
"""
import argparse
import hashlib
import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# Files whose hashes pin the artefact set: the generated patch is the
# documented way to apply the driver, the two tool files are what userspace
# runs. Paths are relative to the repository root.
PATCH_REL = "awg-openbsd.patch"
TOOLS_REL = ("sbin/awg-quick", "sbin/ifconfig/ifconfig_awg.patch")

# Fallback repository root: the current directory, so the tool works when it is
# run from the checkout root or with --repo. No absolute personal path here.
FALLBACK_REPO = os.getcwd()


def default_repo():
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, PATCH_REL)):
        return here
    if os.path.isfile(os.path.join(FALLBACK_REPO, PATCH_REL)):
        return FALLBACK_REPO
    return here

REQUIRED_KEYS = (
    "schema_version",
    "version",
    "openbsd_release",
    "arch",
    "driver_commit",
    "kernel",
    "uname_version",
    "patch",
    "tools",
    "tests",
    "build_host",
    "timestamp_utc",
)


def sha256_file(path):
    """Streaming sha256 of a file, so a 300 MB kernel costs no memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_tests(pairs):
    """Turn ['smoke=pass', 'wire=fail'] into {'smoke': 'pass', ...}."""
    tests = {}
    for pair in pairs:
        name, sep, result = pair.partition("=")
        if not sep or not name or result not in ("pass", "fail"):
            raise ValueError(
                "bad --tests value {0!r}: expected name=pass or name=fail".format(pair)
            )
        if name in tests:
            raise ValueError("duplicate test name {0!r}".format(name))
        tests[name] = result
    return tests


def build_manifest(args):
    """Assemble the manifest dict from parsed arguments."""
    kernel_sha256 = sha256_file(args.kernel)
    kernel_size = os.path.getsize(args.kernel)

    repo = args.repo
    patch_path = os.path.join(repo, PATCH_REL)
    patch = {"path": PATCH_REL, "sha256": sha256_file(patch_path)}

    tools = []
    for rel in TOOLS_REL:
        tools.append({"name": os.path.basename(rel), "path": rel,
                      "sha256": sha256_file(os.path.join(repo, rel))})

    host = args.build_host if args.build_host else socket.gethostname()
    arch = args.arch if args.arch else platform.machine()

    return {
        "schema_version": SCHEMA_VERSION,
        "version": args.version,
        "openbsd_release": args.release,
        "arch": arch,
        "driver_commit": args.commit,
        "kernel": {
            "path": args.kernel,
            "sha256": kernel_sha256,
            "size": kernel_size,
        },
        "uname_version": args.uname_v if args.uname_v else None,
        "patch": patch,
        "tools": tools,
        "tests": parse_tests(args.tests),
        "build_host": host,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def run_self_test():
    """Build manifests from throwaway fixtures and assert the contract."""
    import tempfile

    failures = []

    def check(label, ok):
        if ok:
            print("ok   {0}".format(label))
        else:
            print("FAIL {0}".format(label))
            failures.append(label)

    parser = build_parser()
    with tempfile.TemporaryDirectory() as tmp:
        kernel = os.path.join(tmp, "bsd")
        with open(kernel, "w") as fh:
            fh.write("ELF fake kernel\n")
        expected_kernel_sha = sha256_file(kernel)
        patch = os.path.join(tmp, PATCH_REL)
        os.makedirs(os.path.dirname(os.path.join(tmp, "sbin", "ifconfig")))
        with open(patch, "w") as fh:
            fh.write("--- /dev/null\n+++ if_awg.h\n")
        for idx, rel in enumerate(TOOLS_REL):
            tool = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(tool), exist_ok=True)
            with open(tool, "w") as fh:
                fh.write("#tool {0}\n".format(idx))

        argv = ["--kernel", kernel, "--version", "test", "--release", "8.0",
                "--commit", "deadbeef", "--tests", "smoke=pass", "--repo", tmp]
        args = parser.parse_args(argv)
        manifest = build_manifest(args)
        text = json.dumps(manifest, indent=2, sort_keys=True)
        print(text)
        reparsed = json.loads(text)

        check("stdout is a JSON object", isinstance(reparsed, dict))
        check("all required keys present",
              all(key in reparsed for key in REQUIRED_KEYS))
        check("schema_version is {0}".format(SCHEMA_VERSION),
              reparsed["schema_version"] == SCHEMA_VERSION)
        check("kernel sha256 is the real 64-hex digest",
              reparsed["kernel"]["sha256"] == expected_kernel_sha
              and len(reparsed["kernel"]["sha256"]) == 64)
        check("kernel size matches the file",
              reparsed["kernel"]["size"] == os.path.getsize(kernel))
        check("openbsd_release/version/commit echoed",
              reparsed["openbsd_release"] == "8.0"
              and reparsed["version"] == "test"
              and reparsed["driver_commit"] == "deadbeef")
        check("patch sha256 recorded", len(reparsed["patch"]["sha256"]) == 64)
        check("both tool hashes recorded",
              len(reparsed["tools"]) == len(TOOLS_REL)
              and all(len(t["sha256"]) == 64 for t in reparsed["tools"]))
        check("tests parsed", reparsed["tests"] == {"smoke": "pass"})
        check("timestamp is UTC ISO 8601",
              reparsed["timestamp_utc"].endswith("+00:00"))
        check("all-pass manifest exits 0", exit_code_for(manifest) == 0)

        # Same invocation with a failing test result must exit non-zero.
        argv_fail = ["--kernel", kernel, "--version", "test", "--release", "8.0",
                     "--commit", "deadbeef", "--tests", "smoke=fail", "--repo", tmp]
        manifest_fail = build_manifest(parser.parse_args(argv_fail))
        check("a fail test result exits non-zero",
              exit_code_for(manifest_fail) != 0)

        # --out writes the same JSON to a file.
        out = os.path.join(tmp, "manifest.json")
        argv_out = argv + ["--out", out]
        args_out = parser.parse_args(argv_out)
        manifest_out = build_manifest(args_out)
        with open(out, "w") as fh:
            fh.write(json.dumps(manifest_out, indent=2, sort_keys=True))
        with open(out) as fh:
            check("--out file parses as JSON",
                  json.load(fh)["kernel"]["sha256"] == expected_kernel_sha)

    if failures:
        print("self-test: {0} failure(s)".format(len(failures)), file=sys.stderr)
        return 1
    print("self-test: all checks passed")
    return 0


def exit_code_for(manifest):
    """0 when every test passed, 1 when any failed."""
    return 1 if any(v == "fail" for v in manifest["tests"].values()) else 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Emit the release manifest JSON for an awg-openbsd build.")
    parser.add_argument("--kernel", help="path to the built kernel (e.g. bsd)")
    parser.add_argument("--version", help="driver/artefact version string")
    parser.add_argument("--release", help="OpenBSD release, e.g. 8.0")
    parser.add_argument("--commit", help="driver commit sha")
    parser.add_argument("--uname-v", dest="uname_v", default=None,
                        help="uname -v string of the built kernel, e.g. AWG.MP#5")
    parser.add_argument("--tests", action="append", default=[],
                        metavar="NAME=pass|fail",
                        help="test result, repeatable (e.g. --tests smoke=pass)")
    parser.add_argument("--arch", default=None,
                        help="target arch (default: platform.machine())")
    parser.add_argument("--build-host", dest="build_host", default=None,
                        help="build host name (default: socket.gethostname())")
    parser.add_argument("--repo", default=default_repo(),
                        help="repository root holding awg-openbsd.patch and sbin/ "
                             "(default: directory of this script, falling back to "
                             "the awg-openbsd checkout)")
    parser.add_argument("--out", default=None,
                        help="also write the JSON to this file")
    parser.add_argument("--self-test", action="store_true",
                        help="run the built-in self-test and exit")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    missing = [flag for flag, value in (("--kernel", args.kernel),
                                        ("--version", args.version),
                                        ("--release", args.release),
                                        ("--commit", args.commit))
               if not value]
    if missing:
        print("error: missing required argument(s): {0}".format(", ".join(missing)),
              file=sys.stderr)
        return 2
    try:
        manifest = build_manifest(args)
    except (OSError, ValueError) as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return 2
    text = json.dumps(manifest, indent=2, sort_keys=True)
    print(text)
    if args.out:
        try:
            with open(args.out, "w") as fh:
                fh.write(text + "\n")
        except OSError as exc:
            print("error: cannot write {0}: {1}".format(args.out, exc),
                  file=sys.stderr)
            return 2
    return exit_code_for(manifest)


if __name__ == "__main__":
    sys.exit(main())
