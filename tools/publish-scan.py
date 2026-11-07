#!/usr/bin/env python3
"""Publish gate: scan a git repository for secrets and personal data.

Pass 1 of the two-pass review.  Run with --history to also scan every blob
that ever existed in the repository.
"""
import base64
import re
import subprocess
import sys

PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # 44 base64 chars ending in "=" — the trailing \b of the previous version
    # could never match after "=", so this rule never fired.
    ("wireguard key (44 b64 chars + =)", re.compile(r"\b[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=(?![A-Za-z0-9+/=])")),
    ("openai/anthropic style token", re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("password assignment", re.compile(r"(?i)\b(pass(word|wd)?|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\"$<{]{6,}")),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("absolute home path", re.compile(r"/(home|Users)/[A-Za-z0-9._-]+")),
    ("private ipv4", re.compile(r"\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}(\.\d{1,3})?\b")),
    ("mac address", re.compile(r"\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")),
]
# Lines that look like the pattern but are declarations, docs, or examples.
ALLOW = re.compile(
    r"(AWG_MAX_|MAXMCLBYTES|placeholder|<server-key>|<peer-key>|example\.(com|org)"
    r"|10\.0\.0\.|10\.8\.0\.|10\.8\.9\.|10\.9\.0\.|10\.9\.9\.|10\.0\.2\.|10\.66\.0\.|192\.168\.99\.|127\.0\.0\.1|0\.0\.0\.0"
    r"|255\.255\.255|YourPublicKey|PUBKEY|/home/build"
    # upstream copyright headers and the WireGuard protocol string
    r"|Copyright \(C\)|zx2c4\.com|noconroy|SPDX-License-Identifier"
    # explicit, greppable opt-out for deterministic lab fixtures
    r"|scan:allow-fixture)"
)


def scan(text, label, findings):
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW.search(line):
            continue
        for name, rx in PATTERNS:
            m = rx.search(line)
            if m:
                findings.append((label, lineno, name, line.strip()[:160]))


def self_test():
    """Guard the rules against silent rot (one did: the 44-char key rule).

    Fixtures are assembled at runtime so that scanning this repository does
    not report the scanner itself.
    """
    key = base64.b64encode(b"\x01" * 32).decode()
    tok = "sk-" + "abcdef0123456789abcdef"
    akia = "AKIA" + "IOSFODNN7EXAMPLE"
    cases = [
        ("wireguard key (44 b64 chars + =)", "PrivateKey = " + key, True),
        ("wireguard key (44 b64 chars + =)",
         "PrivateKey = <base64, generated with awg genkey>", False),
        ("openai/anthropic style token", "token = " + tok, True),
        ("aws access key", akia, True),
    ]
    bad = 0
    for name, line, want in cases:
        rx = dict(PATTERNS)[name]
        got = bool(rx.search(line))
        if got != want:
            print(f"SELF-TEST FAIL: {name}: {line!r} -> {got}, expected {want}")
            bad += 1
    print("SELF-TEST: ok" if bad == 0 else f"SELF-TEST: {bad} failure(s)")
    return 0 if bad == 0 else 1


def main():
    if "--self-test" in sys.argv:
        return self_test()
    history = "--history" in sys.argv
    findings = []
    problems = []
    # -z: git C-quotes non-ASCII names otherwise, and split() then invents
    # paths that do not exist (previously swallowed as OSError).
    raw = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    files = [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            problems.append(f"{f}: {exc}")
            continue
        scan(text, f, findings)
    if history:
        blobs = subprocess.run(
            ["git", "rev-list", "--all", "--objects"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        seen = set()
        for entry in blobs:
            sha = entry.split()[0]
            if sha in seen:
                continue
            seen.add(sha)
            kind = subprocess.run(["git", "cat-file", "-t", sha], capture_output=True, text=True).stdout.strip()
            if kind != "blob":
                continue
            text = subprocess.run(["git", "cat-file", "-p", sha], capture_output=True, text=True).stdout
            scan(text, f"history:{sha[:8]}", findings)

    print(f"files scanned: {len(files)}  history: {history}")
    for p in problems:
        print(f"NOT SCANNED: {p}")
    if not findings:
        print("RESULT: clean" if not problems else f"RESULT: clean ({len(problems)} file(s) not scanned)")
        return 0 if not problems else 2
    for label, lineno, name, line in findings:
        print(f"{label}:{lineno}: [{name}] {line}")
    print(f"RESULT: {len(findings)} finding(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
