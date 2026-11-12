#!/usr/bin/env python3
"""Publish gate: scan a git repository for secrets and personal data.

Pass 1 of the two-pass review.  Run with --history to also scan every blob
that ever existed in the repository.

Round-6 (finding N6) redesign of the allow mechanism:

* ALLOW is a per-rule table of (pattern, reason) pairs.  An exception
  suppresses only *its own* rule's match on the line; every other rule is
  still evaluated.  The old single regex skipped the whole line, so a
  dummy private-key assignment passed silently as soon as the line also
  mentioned 127.0.0.1 (the bypass the reviewer reproduced).
* `absolute home path` is a dedicated rule with its own narrow exception
  list (the CI build home and the lab host home); nothing else can
  suppress it.
* `password assignment` requires a literal secret token at the end of the
  assignment, so report prose (`PASS: ...`) and C code
  (`char *secret = read_secret(x)`) are no longer findings.
* --self-test carries a negative case proving an allowlist entry cannot
  hide a different rule's match on the same line.

Round-8 (finding N6) second half of the redesign: the bare
`scan:allow-fixture` marker suppressed *every* match of its rule on the
line, so a real key on a line that already carried the marker was invisible.
The marker now pins the exact fixture shape it is attached to:

    scan:allow-fixture[sha1:<40 hex>]   (several shapes: sha1:a...,sha1:b...)

The payload is the sha1 of the matched text.  A same-rule match whose text
differs from every pinned shape is reported.  The bare marker stays accepted
as the legacy "all shapes of this rule" form; `--normalize-markers` rewrites
bare markers in the tracked tree into the hash-pinned form, so each marker
carries exactly the shapes the captured run produced.
"""
import base64
import hashlib
import os
import re
import subprocess
import sys

# (rule, matcher, per-rule exceptions [(pattern, reason), ...])
PATTERNS = [
    ("private key block",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     []),
    # 44 base64 chars ending in "=" — the trailing \b of the previous version
    # could never match after "=", so this rule never fired.
    # Exceptions are *public* peer keys (labelled awgpeer/awgpubkey, i.e. the
    # half of the keypair that is safe to publish) plus the disposable lab
    # fixtures; arbitrary `awgkey` output is never blanket-allowed.
    ("wireguard key (44 b64 chars + =)",
     re.compile(r"\b[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=(?![A-Za-z0-9+/=])"),
     [(re.compile(r"\b(awgpeer|awgpubkey|PublicKey|YourPublicKey|PUBKEY(?:_[AB])?|"
                  r"KEY_MATCH_[AB]|peer=|peer_of_a|peer_of_b|KEY0_PUB)\b"),
       "public peer key"),
      (re.compile(r"scan:allow-fixture"), "lab fixture")]),
    ("openai/anthropic style token",
     re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|"
                r"gho_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"),
     []),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), []),
    # A real assignment: keyword, separator, one literal token, end of line
    # (a trailing comment is tolerated).  Prose like `PASS: R4-07: world-...`
    # and calls like `char *secret = read_secret(val)` no longer qualify.
    ("password assignment",
     re.compile(r"(?i)\b(password|passwd|pass|secret|token|api[_-]?key)\s*[:=]\s*"
                r"(?:'([A-Za-z0-9+/=_.:@!-]{8,})'|\"([A-Za-z0-9+/=_.:@!-]{8,})\""
                r"|([A-Za-z0-9+/=_.:@-]{8,}))\s*(?:[A-Za-z0-9:._@!/-]{0,7}\s*)?(?:#.*)?$"),
     [(re.compile(r"scan:allow-fixture"), "lab fixture")]),
    # Upstream copyright attribution is preserved; the exception is anchored
    # to the copyright comment form, not to the address in general.
    ("email address",
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     [(re.compile(r'^\s*\+?\s*(?:\*|\.\\")?\s*Copyright \([cC]\) \d{4}(?:-\d{4})? .*\S+@\S+>?\s*$'),
       "upstream copyright attribution"),
      (re.compile(r"\blab@obsd-a\.my\.domain\b"), "lab host identity"),
      (re.compile(r"zx2c4\.com"), "upstream maintainer site")]),
    # Dedicated, non-bypassable: only these two homes are exempt, and only
    # for this rule (the old shared ALLOW regex let 127.0.0.1 hide a key).
    ("absolute home path",
     re.compile(r"/(home|Users)/[A-Za-z0-9._-]+"),
     [(re.compile(r"/home/build\b"), "CI build home"),
      (re.compile(r"/home/user\b"), "lab host home"),
      (re.compile(r"/home/awg2/sys\b"), "lab kernel tree"),
      (re.compile(r"scan:allow-fixture"), "lab fixture")]),
    ("private ipv4",
     re.compile(r"\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}"
                r"(\.\d{1,3})?\b"),
     [(re.compile(r"\b(10\.0\.0\.|10\.8\.0\.|10\.8\.9\.|10\.9\.0\.|10\.9\.9\.|10\.9\.19\.|"
                  r"10\.0\.2\.|10\.42\.|10\.66\.0\.|10\.77\.0\.|192\.168\.99\.|"
                  r"127\.0\.0\.1|0\.0\.0\.0|255\.255\.255)"),
       "lab/example topology"),
      (re.compile(r"scan:allow-fixture"), "lab fixture")]),
    ("mac address",
     re.compile(r"\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"),
     [(re.compile(r"\b52:54:00:"), "virtio lab MAC"),
      (re.compile(r"scan:allow-fixture"), "lab fixture")]),
]

# `scan:allow-fixture` with an optional [sha1:hex,...] payload pinning the
# exact fixture shapes this marker suppresses (round-8 N6).
FIXTURE_MARKER = re.compile(r"scan:allow-fixture(?:\[(sha1:[0-9a-f]{40}(?:,sha1:[0-9a-f]{40})*)\])?")


def _shape_hash(matched):
    """Identity of one fixture shape: sha1 of the matched text."""
    return hashlib.sha1(matched.encode("utf-8")).hexdigest()


def _stripped(line):
    """Line with the fixture marker (and its payload) removed: the marker is
    a comment, so matching it keeps the matched text stable while the pinned
    payload grows (R8/N6)."""
    return FIXTURE_MARKER.sub("", line)


def _marker_shapes(line):
    """Set of pinned hashes for the fixture marker on a line.  The bare
    marker is accepted (legacy form, rewritten by --normalize-markers) and
    suppresses nothing; only the [sha1:...] payload pins shapes (R8/N6)."""
    shapes = set()
    for m in FIXTURE_MARKER.finditer(line):
        if m.group(1):
            shapes.update(h.split(":", 1)[1] for h in m.group(1).split(","))
    return shapes


def _line_reports(line, rx, allow):
    """True when this line yields a finding for one (pattern, allow) rule:
    a non-fixture exception suppresses the line; the fixture marker suppresses
    only the shapes it pins (bare marker = legacy, no suppression)."""
    for rx_allow, _reason in allow:
        if not rx_allow.pattern.startswith(r"scan:allow-fixture") and rx_allow.search(line):
            return False
    pinned = _marker_shapes(line)
    sline = _stripped(line)
    if not pinned:
        return rx.search(sline) is not None
    return any(_shape_hash(m.group(0)) not in pinned for m in rx.finditer(sline))


def scan(text, label, findings):
    """One finding per (line, rule); the fixture marker suppresses only the
    shapes it pins (sha1 of the matched text); a bare marker pins nothing."""
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx, allow in PATTERNS:
            pinned = set()
            suppressed_by_other = False
            for rx_allow, _reason in allow:
                if rx_allow.pattern.startswith(r"scan:allow-fixture"):
                    pinned.update(_marker_shapes(line))
                elif rx_allow.search(line):
                    suppressed_by_other = True
                    break
            if suppressed_by_other:
                continue
            sline = _stripped(line)
            if not pinned:
                if rx.search(sline):
                    findings.append((label, lineno, name, line.strip()[:160]))
                continue
            for m in rx.finditer(sline):
                if _shape_hash(m.group(0)) in pinned:
                    continue
                findings.append((label, lineno, name, line.strip()[:160]))
                break


def normalize_markers():
    """Rewrite bare `scan:allow-fixture` markers into the hash-pinned form,
    pinning exactly the shapes each rule matched on that line (--history
    ignored: the working tree is rewritten)."""
    raw = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    files = [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]
    changed = 0
    for f in files:
        if os.path.isdir(f):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        out_lines = []
        touched = False
        for line in text.splitlines():
            if "scan:allow-fixture" not in line:
                out_lines.append(line)
                continue
            # One payload per rule that carries the fixture exception: the
            # shapes that rule matched on this line (merged with any payload
            # already pinned).  A marker written for one rule pins that
            # rule's shapes; other rules keep their own shapes in the same
            # marker payload, so a later same-rule match is still reported.
            shapes = set()
            sline = _stripped(line)
            for _name, rx, allow in PATTERNS:
                has_fixture = any(a.pattern.startswith(r"scan:allow-fixture")
                                  for a, _r in allow)
                if not has_fixture:
                    continue
                for m in rx.finditer(sline):
                    shapes.add(_shape_hash(m.group(0)))
            for m in FIXTURE_MARKER.finditer(line):
                if m.group(1):
                    shapes.update(h.split(":", 1)[1] for h in m.group(1).split(","))
            if not shapes:
                out_lines.append(line)
                continue
            payload = "sha1:" + ",sha1:".join(sorted(shapes))
            new = FIXTURE_MARKER.sub(lambda m: "scan:allow-fixture[%s]" % payload, line)
            if new != line:
                touched = True
            out_lines.append(new)
        if touched:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out_lines) + ("\n" if text.endswith("\n") else ""))
            changed += 1
            print(f"normalized: {f}")
    print(f"RESULT: {changed} file(s) rewritten")
    return 0


def self_test():
    """Guard the rules against silent rot and against the allowlist bypass.

    Fixtures are assembled at runtime so that scanning this repository does
    not report the scanner itself.
    """
    key = base64.b64encode(b"\x01" * 32).decode()
    tok = "sk-" + "abcdef0123456789abcdef"
    akia = "AKIA" + "IOSFODNN7EXAMPLE"
    email = "author@" + "example.org"
    home = "/home/" + "personal"
    cases = [
        ("wireguard key (44 b64 chars + =)", "PrivateKey = " + key, True),
        ("wireguard key (44 b64 chars + =)",
         "PrivateKey = <base64, generated with awg genkey>", False),
        ("openai/anthropic style token", "token = " + tok, True),
        ("aws access key", akia, True),
        # password rule: literal secret yes, prose and function calls no
        ("password assignment", "password = hunter2hunter2", True),
        ("password assignment", "pass: 's3cret-token'", True),
        ("password assignment", "PASS: R4-07: world-writable refused", False),
        ("password assignment", "char *secret = read_secret(val);", False),
        # N6 bypass: an allowlist entry for one rule must not hide another
        # rule's match on the same line (the old code dropped the line).
        ("__line__", "127.0.0.1 PrivateKey = " + key,
         ["wireguard key (44 b64 chars + =)"]),
        ("__line__", "10.99.9.9 PrivateKey = " + key,  # scan:allow-fixture[sha1:ce2c9d3d86bc2f7b8894d0939022f0787e48db48] lab fixture
         ["wireguard key (44 b64 chars + =)", "private ipv4"]),
        ("__line__", "scan:allow-fixture token = " + tok,
         ["openai/anthropic style token", "password assignment"]),
        # N6 (round 8): a hash-pinned marker suppresses only the shape it
        # pins; a *same-rule* match with a different shape is still reported.
        ("__line__", "scan:allow-fixture[sha1:" + _shape_hash(key) + "] " + key,
         []),
        ("__line__", "scan:allow-fixture[sha1:" + _shape_hash(key) + "] " + key + " " +
         base64.b64encode(b"\x02" * 32).decode(),
         ["wireguard key (44 b64 chars + =)"]),
        ("__line__", "scan:allow-fixture " + home,
         ["absolute home path"]),
        ("__line__", "Copyright (C) 2026 Author <" + email + ">", []),
        ("__line__", "+ * Copyright (c) 2019-2020 Author <" + email + ">", []),
        ("__line__", '.\\" Copyright (C) 2026 Author <' + email + ">", []),
        ("__line__", email, ["email address"]),
        ("__line__", "Copyright (C) 2026 " + tok + " Author <" + email + ">",
         ["openai/anthropic style token"]),
        ("__line__", "lab@obsd-a.my.domain " + home,
         ["absolute home path"]),
    ]
    bad = 0
    by_name = dict((n, (rx, allow)) for n, rx, allow in PATTERNS)
    for name, line, want in cases:
        if name == "__line__":
            got = [n for n, (rx, allow) in by_name.items()
                   if _line_reports(line, rx, allow)]
            ok = sorted(got) == sorted(want)
            if not ok:
                print(f"SELF-TEST FAIL: {name}: {line[:60]!r} -> {got}, "
                      f"expected {want}")
                bad += 1
            continue
        rx, _allow = by_name[name]
        got = bool(rx.search(line))
        if got != want:
            print(f"SELF-TEST FAIL: {name}: {line!r} -> {got}, expected {want}")
            bad += 1
    print("SELF-TEST: ok" if bad == 0 else f"SELF-TEST: {bad} failure(s)")
    return 0 if bad == 0 else 1


def main():
    if "--normalize-markers" in sys.argv:
        return normalize_markers()
    if "--self-test" in sys.argv:
        return self_test()
    history = "--history" in sys.argv
    findings = []
    problems = []
    # -z: git C-quotes non-ASCII names otherwise, and split() then invents
    # paths that do not exist (previously swallowed as OSError).
    # AWG_SOURCE_REPO: scan the real repo's tracked list when invoked from
    # a `git archive` export (the export has no .git); files exist in both.
    scan_dir = os.environ.get("AWG_SOURCE_REPO") or None
    raw = subprocess.run(["git", "ls-files", "-z"], cwd=scan_dir,
                         capture_output=True, check=True).stdout
    files = [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]
    for f in files:
        if os.path.isdir(f):   # e.g. the src/net -> . symlink
            continue
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
    counts = {}
    for label, lineno, name, line in findings:
        print(f"{label}:{lineno}: [{name}] {line}")
        counts[name] = counts.get(name, 0) + 1
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"COUNT: {n:3d} {name}")
    print(f"RESULT: {len(findings)} finding(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
