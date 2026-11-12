#!/usr/bin/env python3
"""
Mechanical matrix validator for docs/TRACEABILITY.md.

Fails closed on structural problems, so the matrix cannot silently rot:

  * every requirement table row must have the exact required columns and no
    empty required cell;
  * requirement IDs must be unique;
  * the ID inventory must be exactly one row per source requirement
    (G-1..G-14, L-1..L-10, U-1..U-11, I-1..I-10, K-1..K-13, N-1..N-6,
     X-1..X-6, S-1..S-6, P-1..P-2, PKG-1..PKG-4, CI-1..CI-6, and exactly the
    AWG-0xx IDs that SECURITY-AUDIT.md defines);
  * the Gate Status must be one of the four allowed values, and the evidence
    cell must be consistent with it (proven = verifiable current artifact;
    fixture-only = named host-level fixture; untested = explicit gap);
  * an AWG cross-reference row must carry the same (weakest) status as the
    requirement rows it maps to;
  * the Summary Counts section must exist and match the mechanically derived
    counts.

Usage:
  python3 tools/check_matrix_totals.py            # validate, exit 0/1
  python3 tools/check_matrix_totals.py --generate # print a fresh summary
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TRACEABILITY_FILE = REPO_ROOT / "docs" / "TRACEABILITY.md"
AUDIT_FILE = REPO_ROOT / "docs" / "SECURITY-AUDIT.md"

VALID_STATUSES = ["proven", "fixture-only", "untested", "not applicable"]
STATUS_RANK = {"proven": 3, "fixture-only": 2, "untested": 1, "not applicable": 0}

# One row per source requirement. Numbering must start at 1 and be contiguous.
EXPECTED_DOMAIN_IDS = {
    "G": 14,
    "L": 10,
    "U": 11,
    "I": 10,
    "K": 13,
    "N": 6,
    "X": 6,
    "S": 6,
    "P": 2,
    "PKG": 4,
    "CI": 6,
}

STATUS_LABELS = {
    "proven": "Master Directive Gates",
    "fixture-only": "RELEASE-NOTES Limitations",
}

STATUS_MARKERS = {
    "fixture-only": ("fixture", "shim", "host parser", "compiled-c", "host-level"),
    "untested": (
        "none", "not run", "not implemented", "historical", "stub", "outline",
        "manual", "no automated", "no evidence", "not measured", "open",
        "deferred", "proposed", "not re-run", "unexecuted",
    ),
}

# Phrases that must never appear in a `proven` evidence cell.
PROVEN_BANNED = (
    "historical", "not final pin", "manual test", "no automated", "stub",
    "outline", "not run", "none", "no execution", "not measured",
)

COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
EVIDENCE_PATH_RE = re.compile(r"docs/evidence/[^\s`|)]+")
TEST_PATH_RE = re.compile(r"tools/tests/test_[a-z0-9_]+\.py")
# FILE:LINE and FILE:LINE-LINE anchors, e.g. RELEASE-NOTES.md:59, src/if_awg.c:2707-2718
ANCHOR_RE = re.compile(
    r"(?<![\w/.-])((?:docs/)?[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:md|c|h|sh|py))"
    r":(\d+)(?:\s*[–-]\s*(\d+))?"
)
# Words too generic to anchor a row to a source line.
ANCHOR_STOPWORDS = {
    "which", "their", "there", "these", "those", "where", "while", "without",
    "must", "shall", "have", "from", "this", "that", "with", "when", "also",
    "only", "into", "over", "each", "both", "does", "been", "being", "other",
    "than", "then", "them", "they", "claim", "claims",
}
CORE_DOC_RE = re.compile(
    r"docs/(SECURITY-AUDIT|RELEASE-NOTES|ENGINEERING-REPORT|TESTING|TRACEABILITY)\.md"
)
MAKE_RE = re.compile(r"make\s+check(?:-\w+)*")
AWG_ID_RE = re.compile(r"\bAWG-(\d{3})\b")
REQ_REF_RE = re.compile(r"(?<!AWG-)\b(G|L|U|I|K|N|X|S|P|PKG|CI)-(\d+)\b")
DOMAIN_ID_RE = re.compile(r"^(G|L|U|I|K|N|X|S|P|PKG|CI)-(\d+)$")


class MatrixError(Exception):
    pass


def domain_prefix(req_id):
    match = DOMAIN_ID_RE.match(req_id)
    return match.group(1) if match else None


def parse_traceability():
    """Parse requirement tables.

    Returns (rows, errors). rows are dicts with id, status, group, line,
    requirement, proves, evidence.
    """
    lines = TRACEABILITY_FILE.read_text().splitlines()
    rows = []
    errors = []
    table = None  # None or "standard"/"awg"

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        is_row = stripped.startswith("|")
        if not is_row:
            table = None
            continue
        if "| Requirement |" in stripped and "| Gate Status |" in stripped:
            table = "awg" if "AWG-ID" in stripped else "standard"
            continue
        if stripped.startswith("|---") or set(stripped) <= set("|-: "):
            continue
        if table is None:
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if table == "standard":
            if len(cells) != 5:
                errors.append(
                    f"line {lineno}: malformed requirement row ({len(cells)} columns, "
                    f"expected 5): {stripped[:90]}"
                )
                continue
            req_id, requirement, proves, evidence, status = cells
        else:
            if len(cells) != 7:
                errors.append(
                    f"line {lineno}: malformed AWG row ({len(cells)} columns, "
                    f"expected 7): {stripped[:90]}"
                )
                continue
            req_id, _sev, _state, requirement, proves, evidence, status = cells

        if not req_id or req_id in ("—", "-"):
            errors.append(f"line {lineno}: row without a requirement ID")
            continue

        rows.append({
            "id": req_id,
            "status": status,
            "requirement": requirement,
            "proves": proves,
            "evidence": evidence,
            "line": lineno,
            "table": table,
        })
    return rows, errors


def audit_register_ids():
    """AWG IDs actually defined by the security register (source of truth)."""
    ids = set()
    for line in AUDIT_FILE.read_text().splitlines():
        match = re.match(r"^#{1,4}\s*AWG-(\d{3})\b", line.strip())
        if match:
            ids.add("AWG-" + match.group(1))
    return ids


def validate_structure(rows, errors):
    seen = {}
    for row in rows:
        req_id = row["id"]
        if req_id in seen:
            errors.append(
                f"line {row['line']}: duplicate requirement ID {req_id} "
                f"(first at line {seen[req_id]})"
            )
        else:
            seen[req_id] = row["line"]

        if row["status"] not in VALID_STATUSES:
            errors.append(
                f"line {row['line']}: {req_id} has invalid status '{row['status']}' "
                f"(allowed: {', '.join(VALID_STATUSES)})"
            )
            continue

        for field in ("requirement", "proves", "evidence"):
            if not row[field] or row[field] in ("—", "-"):
                errors.append(
                    f"line {row['line']}: {req_id} has an empty '{field}' cell"
                )

    # Exactly one row per source requirement, contiguous numbering.
    counts = {}
    for row in rows:
        prefix = domain_prefix(row["id"])
        if prefix:
            counts.setdefault(prefix, set()).add(int(DOMAIN_ID_RE.match(row["id"]).group(2)))
    for prefix, expected in EXPECTED_DOMAIN_IDS.items():
        found = counts.get(prefix, set())
        missing = sorted(set(range(1, expected + 1)) - found)
        extra = sorted(found - set(range(1, expected + 1)))
        if missing:
            errors.append(
                f"{prefix}: missing source IDs {', '.join(f'{prefix}-{n}' for n in missing)}"
            )
        if extra:
            errors.append(
                f"{prefix}: unexpected IDs {', '.join(f'{prefix}-{n}' for n in extra)} "
                f"(source inventory has {expected})"
            )

    expected_awg = audit_register_ids()
    found_awg = {row["id"] for row in rows if row["id"].startswith("AWG-")}
    for missing in sorted(expected_awg - found_awg):
        errors.append(f"AWG: register entry {missing} has no matrix row")
    for extra in sorted(found_awg - expected_awg):
        errors.append(f"AWG: matrix row {extra} is not in the SECURITY-AUDIT.md register")
    if not expected_awg:
        errors.append("AWG: no register entries parsed from docs/SECURITY-AUDIT.md")


def validate_evidence_semantics(rows, errors):
    for row in rows:
        status = row["status"]
        evidence = row["evidence"].lower()
        proves = row["proves"].lower()
        if status == "proven":
            verifiable = (
                TEST_PATH_RE.search(row["evidence"])
                or EVIDENCE_PATH_RE.search(row["evidence"])
                or CORE_DOC_RE.search(row["evidence"])
                or MAKE_RE.search(row["evidence"])
                or COMMIT_RE.search(row["evidence"])
            )
            if not verifiable:
                errors.append(
                    f"line {row['line']}: {row['id']} is 'proven' but cites no "
                    f"verifiable artifact (test path, docs/evidence path, core doc, "
                    f"make target or commit)"
                )
            for banned in PROVEN_BANNED:
                if banned in evidence:
                    errors.append(
                        f"line {row['line']}: {row['id']} is 'proven' but its evidence "
                        f"contains '{banned}'"
                    )
            # "Nothing marked proven on fixture evidence alone": a row whose own
            # evidence names a host-level fixture cannot be proven.
            for marker in STATUS_MARKERS["fixture-only"]:
                if marker in evidence:
                    errors.append(
                        f"line {row['line']}: {row['id']} is 'proven' but its evidence "
                        f"names a host-level fixture ('{marker}'); fixture evidence "
                        f"is 'fixture-only', never 'proven'"
                    )
        elif status == "fixture-only":
            if not any(marker in evidence for marker in STATUS_MARKERS["fixture-only"]):
                errors.append(
                    f"line {row['line']}: {row['id']} is 'fixture-only' but the evidence "
                    f"does not name the host-level fixture (fixture/shim/compiled-C/host-level)"
                )
        elif status == "untested":
            if not any(marker in evidence for marker in STATUS_MARKERS["untested"]):
                errors.append(
                    f"line {row['line']}: {row['id']} is 'untested' but the evidence "
                    f"does not state the gap (none/historical/stub/outline/manual/not run/...)"
                )
            if not proves or proves in ("—", "-", "not run"):
                # 'not run' is acceptable only when the evidence states the gap.
                if proves != "not run":
                    errors.append(
                        f"line {row['line']}: {row['id']} is 'untested' without saying "
                        f"what would prove it"
                    )


def validate_awg_mapping(rows, errors):
    by_id = {row["id"]: row for row in rows if domain_prefix(row["id"])}
    for row in rows:
        if not row["id"].startswith("AWG-"):
            continue
        if row["status"] not in VALID_STATUSES:
            continue
        refs = REQ_REF_RE.findall(row["requirement"])
        if not refs:
            continue
        targets = []
        for prefix, number in refs:
            ref_id = f"{prefix}-{number}"
            if ref_id not in by_id:
                errors.append(
                    f"line {row['line']}: {row['id']} maps to {ref_id}, which has no row"
                )
                continue
            targets.append(by_id[ref_id])
        if not targets:
            continue
        weakest = min(targets, key=lambda r: STATUS_RANK.get(r["status"], -1))
        if row["status"] != weakest["status"]:
            errors.append(
                f"line {row['line']}: {row['id']} is '{row['status']}' but its weakest "
                f"mapped requirement {weakest['id']} is '{weakest['status']}'"
            )


def _resolve_anchor_file(path: str):
    """Map a FILE:LINE anchor target to a path in this repository, or None."""
    candidate = REPO_ROOT / path
    if candidate.exists():
        return candidate
    candidate = REPO_ROOT / "docs" / path
    if candidate.exists():
        return candidate
    return None


def _subject_tokens(text: str):
    tokens = re.findall(r"[a-z0-9][a-z0-9+_-]*", text.lower())
    return sorted({t for t in tokens if len(t) >= 5 and t not in ANCHOR_STOPWORDS})


def validate_anchors(rows, errors):
    """Every FILE:LINE anchor must resolve, and a row's own anchor must be on topic.

    A cited line number that drifts off the sentence it is supposed to date is
    exactly how the matrix rots: the row still 'has evidence', but the evidence
    is a different limitation. So a row anchor into a markdown source must land
    on a line that shares at least a third of the row's own subject words.
    """
    lines_by_file = {}
    whole_file_text = TRACEABILITY_FILE.read_text()

    def load(path):
        if path not in lines_by_file:
            lines_by_file[path] = path.read_text().splitlines()
        return lines_by_file[path]

    for match in ANCHOR_RE.finditer(whole_file_text):
        rel, start, end = match.group(1), int(match.group(2)), match.group(3)
        resolved = _resolve_anchor_file(rel)
        if resolved is None:
            errors.append(f"anchor {rel}:{start}: file not found in this repository")
            continue
        lines = load(resolved)
        last = int(end) if end else start
        if start < 1 or last > len(lines) or start > last:
            errors.append(
                f"anchor {rel}:{match.group(2)}{'-' + end if end else ''}: out of range "
                f"({len(lines)} lines in {resolved.relative_to(REPO_ROOT)})"
            )

    for row in rows:
        cited = f"{row['evidence']} {row['proves']}"
        for match in ANCHOR_RE.finditer(cited):
            rel, start, end = match.group(1), int(match.group(2)), match.group(3)
            resolved = _resolve_anchor_file(rel)
            if resolved is None or resolved.suffix != ".md":
                continue
            lines = load(resolved)
            last = int(end) if end else start
            if last > len(lines) or start < 1 or start > last:
                continue  # already reported by the whole-file pass
            span = " ".join(lines[start - 1:last]).lower()
            tokens = _subject_tokens(row["requirement"])
            if len(tokens) < 3:
                continue
            hits = [t for t in tokens if t in span]
            needed = max(3, len(tokens) // 3)
            # A row whose requirement names the capability while its anchor names
            # the limitation that withholds it shares few words by construction;
            # one distinctive (>= 8 character) subject word is enough there.
            if len(hits) < needed and not any(len(h) >= 8 for h in hits):
                errors.append(
                    f"line {row['line']}: {row['id']} anchors {rel}:{start} "
                    f"({resolved.relative_to(REPO_ROOT)}) but that line shares only "
                    f"{len(hits)}/{len(tokens)} subject words ({', '.join(hits) or 'none'}); "
                    f"the anchor cites the wrong text"
                )


def validate_evidence_index(errors):
    """Each Evidence Index row must name a commit that really contains the artifact."""
    lines = TRACEABILITY_FILE.read_text().splitlines()
    in_index = False
    for lineno, line in enumerate(lines, 1):
        if line.startswith("## Evidence Index"):
            in_index = True
            continue
        if in_index and line.startswith("## "):
            break
        if not in_index or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        path_match = re.search(r"`([A-Za-z0-9_./-]+\.(?:log|json|sh|md|txt))`", cells[1])
        sha_match = re.fullmatch(r"`([0-9a-f]{7,40})`", cells[2]) or re.search(
            r"`([0-9a-f]{7,40})`", cells[2]
        )
        if not path_match or not sha_match:
            continue
        path, sha = path_match.group(1), sha_match.group(1)
        if subprocess.run(
            ["git", "cat-file", "-t", sha], cwd=REPO_ROOT, capture_output=True, text=True
        ).returncode != 0:
            # The commit does not exist here at all: that is a citation failure,
            # reported by tools/check-citations.py (make check-citations).
            continue
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}:{path}"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            errors.append(
                f"line {lineno}: Evidence Index says {path} is in {sha}, but "
                f"`git cat-file -e {sha}:{path}` exits {proc.returncode}: the artifact "
                f"is not in that commit"
            )


def compute_counts(rows):
    counts = {status: 0 for status in VALID_STATUSES}
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] += 1
    return counts


def parse_summary():
    lines = TRACEABILITY_FILE.read_text().splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("## Summary Counts"):
            start = idx
            break
    if start is None:
        raise MatrixError("missing '## Summary Counts' section")
    summary = {}
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip().replace("**", "") for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        label, value = cells[0], cells[1]
        if not value.isdigit():
            continue
        if label in VALID_STATUSES:
            summary[label] = int(value)
        elif "total" in label.lower():
            summary["total"] = int(value)
    missing = [s for s in VALID_STATUSES if s not in summary]
    if missing:
        raise MatrixError(f"Summary Counts missing rows: {', '.join(missing)}")
    if "total" not in summary:
        raise MatrixError("Summary Counts missing a total row")
    return summary


def generate_summary(rows):
    counts = compute_counts(rows)
    total = len(rows)
    breakdown = []
    for prefix, label in [
        ("G", "G"), ("L", "L"), ("U", "Userspace"), ("I", "Installer"),
        ("K", "Kernel"), ("N", "Native"), ("X", "Interop"), ("S", "Stress"),
        ("P", "Performance"), ("PKG", "Packaging"), ("CI", "CI"),
    ]:
        n = sum(1 for r in rows if domain_prefix(r["id"]) == prefix)
        breakdown.append(f"{n} {label}")
    awg = sum(1 for r in rows if r["id"].startswith("AWG-"))
    return (
        "## Summary Counts\n\n"
        "| Gate Status | Count |\n"
        "|-------------|-------|\n"
        f"| proven | {counts['proven']} |\n"
        f"| fixture-only | {counts['fixture-only']} |\n"
        f"| untested | {counts['untested']} |\n"
        f"| not applicable | {counts['not applicable']} |\n"
        f"| **Total requirement rows** | **{total}** |\n\n"
        f"> **Note:** The {total} requirement rows comprise: {', '.join(breakdown)}, "
        f"{awg} AWG. Counts are mechanically derived by "
        f"`tools/check_matrix_totals.py` from the tables above; `make check-matrix` "
        f"fails if this block is stale.\n"
    )


def main():
    rows, errors = parse_traceability()
    if not rows:
        print("ERROR: no requirement rows parsed from docs/TRACEABILITY.md")
        return 1

    validate_structure(rows, errors)
    validate_evidence_semantics(rows, errors)
    validate_awg_mapping(rows, errors)
    validate_anchors(rows, errors)
    validate_evidence_index(errors)

    counts = compute_counts(rows)
    print(f"Total requirement rows parsed: {len(rows)}")
    groups = {}
    for row in rows:
        prefix = domain_prefix(row["id"]) or "AWG"
        groups[prefix] = groups.get(prefix, 0) + 1
    print("Source group breakdown: " + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())))
    print("Status counts: " + ", ".join(f"{s}={counts[s]}" for s in VALID_STATUSES))

    if errors:
        print(f"\nVALIDATION FAILED ({len(errors)} problem(s)):")
        for error in errors:
            print(f"  - {error}")
        return 1

    try:
        summary = parse_summary()
    except MatrixError as exc:
        print(f"\nVALIDATION FAILED: {exc}")
        return 1

    mismatches = [
        f"{status}: summary={summary[status]}, actual={counts[status]}"
        for status in VALID_STATUSES
        if summary[status] != counts[status]
    ]
    if summary["total"] != len(rows):
        mismatches.append(f"total: summary={summary['total']}, actual={len(rows)}")
    if mismatches:
        print("\nVALIDATION FAILED: Summary Counts is stale:")
        for mismatch in mismatches:
            print(f"  - {mismatch}")
        return 1

    print("\nVALIDATION PASSED: structure, inventory, evidence semantics and counts match")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--generate":
        parsed, _ = parse_traceability()
        print(generate_summary(parsed), end="")
    else:
        sys.exit(main())
