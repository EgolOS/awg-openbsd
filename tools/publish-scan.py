#!/usr/bin/env python3
"""Publish gate: scan a git repository for secrets and personal data.

Pass 1 of the two-pass review.  Run with --history to also scan every blob
that ever existed in the repository.
"""
import re
import subprocess
import sys

PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("wireguard key (44 b64 chars + =)", re.compile(r"\b[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=\b")),
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
    r"|10\.0\.0\.|10\.8\.0\.|10\.8\.9\.|10\.9\.0\.|127\.0\.0\.1|0\.0\.0\.0"
    r"|255\.255\.255|YourPublicKey|PUBKEY|/home/aigeus/build)"
)


def scan(text, label, findings):
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW.search(line):
            continue
        for name, rx in PATTERNS:
            m = rx.search(line)
            if m:
                findings.append((label, lineno, name, line.strip()[:160]))


def main():
    history = "--history" in sys.argv
    findings = []
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    for f in files:
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
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
    if not findings:
        print("RESULT: clean")
        return 0
    for label, lineno, name, line in findings:
        print(f"{label}:{lineno}: [{name}] {line}")
    print(f"RESULT: {len(findings)} finding(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
