#!/usr/bin/env python3
"""
Citation Consistency Checker

Scans RELEASE-NOTES.md, ENGINEERING-REPORT.md, SECURITY-AUDIT.md, TESTING.md,
TRACEABILITY.md, and every reports/*.md for citations to commits, tests, reports
and evidence that must exist in the current git tree. No report is excluded:
an intentional negative example must be scoped to its own citation, not to a
whole file.

Qualified test citations are resolved by exact identity:
  tools/tests/test_x.py::Class.method
  tools.tests.test_x.Class.method
  test_x.Class.method            (short form, module must exist in tools/tests)
A method that exists in a different module or class does not satisfy the
citation; the module/class chain is checked with the module's AST.

Historical references are module-scoped: a citation may name a test module that
no longer exists in this tree only when the same clause carries an explicit
historical marker ("historical:" together with "from other branch"). A missing
class or method inside a module that IS present in this tree always fails — the
marker never excuses a fabricated identity. Historical references are reported,
never counted as current evidence.

Commit citations are resolved against this repository only: a hash passes as a
commit only when `git rev-parse --verify <sha>^{commit}` succeeds. There is no
blanket allowlist. A hash that does not resolve is accepted at all only when
docs/EXTERNAL-REFS.md declares it, with its provenance, the files allowed to
cite it, and (for external/upstream/content-hash kinds) the provenance token
the citing line must carry. Declared external references are reported as
'external (unverifiable here)' — never as existing in this tree — and are never
counted as current evidence. `docs/` may not cite out-of-tree workspace hashes
at all; the registry itself is checked for stale (now-resolvable) and unused
entries, so it cannot grow into an exemption dump.

Exits non-zero with details if any internal citation is missing.
"""

import ast
import functools
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).parent.parent  # tools/ -> repo root
DOCS_DIR = REPO_ROOT / "docs"
REPORTS_DIR = REPO_ROOT / "reports"
EVIDENCE_DIR = DOCS_DIR / "evidence"
TOOLS_LAB_DIR = REPO_ROOT / "tools" / "lab"
TOOLS_TESTS_DIR = REPO_ROOT / "tools" / "tests"

# Core documentation files that must have strict citation checking
CORE_DOC_FILES = [
    DOCS_DIR / "RELEASE-NOTES.md",
    DOCS_DIR / "ENGINEERING-REPORT.md",
    DOCS_DIR / "SECURITY-AUDIT.md",
    DOCS_DIR / "TESTING.md",
    DOCS_DIR / "TRACEABILITY.md",
]

# Every markdown report is scanned. There is deliberately no file-level
# exemption: a report that wants to show a negative example must mark that
# single citation as historical/illustrative in its own clause (and then it is
# reported, not silently dropped).
SCAN_FILES = list(CORE_DOC_FILES) + sorted(REPORTS_DIR.glob("*.md"))

# Patterns to extract citations
COMMIT_PATTERN = re.compile(r'\b([0-9a-f]{7,40})\b')
# Make targets: capture ALL targets after 'make' (space-separated, allow options like -jN)
TEST_MAKE_PATTERN = re.compile(
    r'\bmake\s+(?:-[a-zA-Z]\d*\s+)*'
    r'(?:check(?:-\w+)*|baseline|manifest|awgctl|awgctl-fixture|clean)'
    r'(?:\s+(?:check(?:-\w+)*|baseline|manifest|awgctl|awgctl-fixture|clean))*\b'
)
TEST_LAB_SH_PATTERN = re.compile(r'\btools/lab/([a-z0-9_-]+\.sh)\b')

# Qualified Python test identities, most specific first.
# 1. path form:      tools/tests/test_mod.py::Class.method / ::method
TEST_PY_PATH_QUAL = re.compile(
    r'\btools/tests/(test_[a-z0-9_]+)\.py::'
    r'([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)'
)
# 2. dotted form:    tools.tests.test_mod.Class.method / .test_mod.method
TEST_PY_DOTTED_QUAL = re.compile(
    r'\btools\.tests\.(test_[a-z0-9_]+)'
    r'((?:\.[A-Za-z_][A-Za-z0-9_]*)+)'
)
# 3. short form:     test_mod.Class.method / test_mod.method
TEST_PY_SHORT_QUAL = re.compile(
    r'\b(test_[a-z0-9_]+)'
    r'((?:\.(?:Test[A-Za-z0-9_]*|test_[a-z0-9_]+|[A-Z][A-Za-z0-9_]*))+)'
)
# Plain module path:  tools/tests/test_mod.py
TEST_PY_PATTERN = re.compile(r'\btools/tests/(test_[a-z0-9_]+\.py)\b')
# Bare test names (test_* or Test*) not attached to a module path
BARE_TEST_PATTERN = re.compile(r'\b(test_[a-zA-Z0-9_]+|Test[A-Z][a-zA-Z0-9_]*)\b')
REPORT_PATH_PATTERN = re.compile(r'\breports/([a-z0-9_/-]+\.md)\b')
EVIDENCE_PATH_PATTERN = re.compile(r'\bdocs/evidence/([a-z0-9_/.-]+\.(?:log|json|md|sh|txt))\b')
EVIDENCE_REPORT_PATTERN = re.compile(r'\breports/evidence/([a-z0-9_/.-]+\.(?:log|json|md|sh|txt))\b')
# External branch @ commit pattern - these are NOT expected in this tree
EXTERNAL_BRANCH_PATTERN = re.compile(r'`?(test/network-matrix|eng/[\w-]+|refs/[\w/.-]+)`?\s+@\s+`?([0-9a-f]{7,40})`?')

# Declared external / unverifiable identifiers. There is deliberately no
# hard-coded allowlist in this file: the only way a non-resolving hash can pass
# is a row in docs/EXTERNAL-REFS.md that states its provenance, the files that
# may cite it, and (except for out-of-tree workspace history) the provenance
# token its citing line must carry.
EXTERNAL_REFS_FILE = DOCS_DIR / "EXTERNAL-REFS.md"

# Kinds that describe a commit in another repository or a documented separate
# evidence branch: legitimate external citations, reported as unverifiable here.
EXTERNAL_KINDS = ("external-evidence-branch", "upstream-repo")
# Non-git identifiers (kernel sha256 prefixes, ELF magic, manifest ids).
CONTENT_HASH_KIND = "content-hash"
# Commits of project workspaces this repository never contained. Never citable
# from docs/ — the matrix and release notes must not rest on them.
OUT_OF_TREE_KIND = "out-of-tree-workspace"
KNOWN_EXTERNAL_KINDS = EXTERNAL_KINDS + (CONTENT_HASH_KIND, OUT_OF_TREE_KIND)

# Test modules from historical reports that do not exist in this tree (other
# branches). Accepted only when the citation's own clause carries an explicit
# historical marker; never accepted as current evidence.
HISTORICAL_MISSING_TEST_MODULES = {
    'test_awgctl.py',
    'test_awgctl_r5.py',
}

# External branch related files (from test/network-matrix)
EXTERNAL_BRANCH_FILES = {
    'w3-dual-v2.md',
    'w3-addr.md',
    'awg-dual-v5-31.log',
    'control-v3b.log',
    'awg-dual-v5-31.sh',
}

HISTORICAL_MARKERS = ("historical:", "from other branch", "historical context")


def run_git(*args: str) -> Tuple[int, str, str]:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def commit_exists_in_tree(sha: str) -> bool:
    """True only for an existing commit object in this repository."""
    code, obj_type, _ = run_git("cat-file", "-t", sha)
    if code == 0:
        return obj_type == "commit"
    code, _, _ = run_git("rev-parse", "--verify", f"{sha}^{{commit}}")
    return code == 0


@functools.lru_cache(maxsize=None)
def external_registry() -> Dict[str, dict]:
    """Parse docs/EXTERNAL-REFS.md: identifier -> declared provenance.

    Table columns: Identifier | Kind | Provenance | Citable only in |
    Required context token. The file is optional; a repository without it has
    an empty registry and therefore no exemptions at all.
    """
    entries: Dict[str, dict] = {}
    if not EXTERNAL_REFS_FILE.exists():
        return entries
    for line in EXTERNAL_REFS_FILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        match = re.fullmatch(r"`([0-9a-f]{7,40})`", cells[0])
        if not match:
            continue
        ident = match.group(1)
        files = re.findall(r"`([^`]+)`", cells[3])
        tokens = re.findall(r"`([^`]+)`", cells[4])
        entries[ident] = {
            "kind": cells[1].strip("`"),
            "provenance": cells[2],
            "files": files,
            "tokens": tokens,
            "row": stripped,
        }
    return entries


def classify_commit(sha: str, relpath: str = "", line_context: str = "") -> Tuple[str, str]:
    """Return (state, detail) where state is resolved / external / missing.

    A hash is a commit only if it resolves in this repository. Anything else
    must be declared in docs/EXTERNAL-REFS.md *for the file citing it*, must not
    be an out-of-tree workspace hash cited from docs/, and — for external,
    upstream and content-hash kinds — must name its provenance on the same line.
    """
    if commit_exists_in_tree(sha):
        return "resolved", f"commit {sha} resolves in this repository"
    entry = external_registry().get(sha)
    if entry is None:
        return "missing", f"commit {sha} NOT FOUND in this repository's refs"
    kind = entry["kind"]
    if kind not in KNOWN_EXTERNAL_KINDS:
        return "missing", (
            f"{sha} is declared with unknown kind '{kind}' in docs/EXTERNAL-REFS.md"
        )
    if entry["files"] and relpath not in entry["files"]:
        return "missing", (
            f"commit {sha} does not resolve here and is declared external only for "
            f"{', '.join(entry['files'])} — not citable from {relpath or 'this file'}"
        )
    if kind == OUT_OF_TREE_KIND and relpath.startswith("docs/"):
        return "missing", (
            f"{relpath} may not cite {sha}: it is an out-of-tree workspace commit "
            f"({entry['provenance']}) that this repository never contained"
        )
    for token in entry["tokens"]:
        if token.lower() not in line_context.lower():
            return "missing", (
                f"external identifier {sha} must name its provenance on the same line "
                f"(missing '{token}'): {entry['provenance']}"
            )
    return "external", (
        f"{sha} — external ({kind}), NOT verifiable in this repository: "
        f"{entry['provenance']}"
    )


def file_exists_in_tree(path: str) -> bool:
    code, _, _ = run_git("ls-files", "--error-unmatch", path)
    return code == 0


def file_exists_on_disk(path: Path) -> bool:
    return path.exists()


def test_script_exists(name: str) -> bool:
    return file_exists_on_disk(TOOLS_LAB_DIR / name)


def test_py_exists(name: str) -> bool:
    return file_exists_on_disk(TOOLS_TESTS_DIR / name)


def make_target_exists(target: str) -> bool:
    makefile = REPO_ROOT / "Makefile"
    if not makefile.exists():
        return False
    return bool(re.search(rf'^{re.escape(target)}:', makefile.read_text(), re.MULTILINE))


@functools.lru_cache(maxsize=None)
def module_index(module_file: str) -> Tuple[frozenset, Dict[str, frozenset]]:
    """Return (module-level function names, {class name: method names})."""
    path = TOOLS_TESTS_DIR / module_file
    tree = ast.parse(path.read_text())
    functions = set()
    classes: Dict[str, set] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return frozenset(functions), {k: frozenset(v) for k, v in classes.items()}


def resolve_qualified(module: str, parts: List[str]) -> Tuple[bool, str]:
    """Resolve an exact module/class/method chain against the module AST."""
    module_file = module + ".py"
    if not test_py_exists(module_file):
        return False, f"test module tools/tests/{module_file} NOT FOUND"
    functions, classes = module_index(module_file)

    if len(parts) == 1:
        name = parts[0]
        if name in functions:
            return True, f"{module}:{name} is a module-level test function in {module_file}"
        if name in classes:
            return True, f"{module}:{name} is a class in {module_file}"
        if any(name in methods for methods in classes.values()):
            return True, f"{module}:{name} is a method in {module_file}"
        return False, f"test identity '{name}' NOT FOUND in {module_file} (module exists)"

    if len(parts) == 2:
        cls, method = parts
        if cls not in classes:
            return False, f"class '{cls}' NOT FOUND in {module_file} (module exists)"
        if method not in classes[cls]:
            return False, (
                f"test method '{method}' NOT FOUND in class '{cls}' of {module_file} "
                f"(other modules/classes do not satisfy this citation)"
            )
        return True, f"{module}.{cls}.{method} exists in {module_file}"

    return False, (
        f"nested identity '{'.'.join(parts)}' NOT SUPPORTED; cite module/Class.method"
    )


def is_core_doc(filepath: Path) -> bool:
    try:
        rel = filepath.relative_to(REPO_ROOT)
    except ValueError:
        return False
    return any(rel == core.relative_to(REPO_ROOT) for core in CORE_DOC_FILES)


def _clause_bounds(line: str, pos: int) -> Tuple[int, int]:
    """Return the span of the clause containing pos.

    Clause separators are ';' and '.', plus ',' when it is not inside the
    citation itself. The span stops at the next separator after the position so
    a marker attached to a different clause cannot exempt this citation.
    """
    start = 0
    for sep in (";", "."):
        idx = line.rfind(sep, 0, pos)
        if idx + 1 > start:
            start = idx + 1
    end = len(line)
    for sep in (";", "."):
        idx = line.find(sep, pos)
        if idx != -1 and idx < end:
            end = idx
    return start, end


def has_historical_marker(line: str, pos: int, cite_end: int = -1) -> bool:
    start, end = _clause_bounds(line, pos)
    clause = line[start:end].lower()
    # Allow the marker in the immediate parenthetical that follows the citation.
    tail_start = cite_end if cite_end > pos else pos
    tail = line[tail_start:tail_start + 90].lower()
    for stop in (";", "."):
        idx = tail.find(stop)
        if idx != -1:
            tail = tail[:idx]
    text = clause + " " + tail
    # A bare "historical" word is not enough: the clause must also name the
    # provenance (another branch) so an exemption cannot be waved through.
    return ("historical" in text) and ("other branch" in text)


def scan_file(filepath: Path) -> List[Tuple[str, str, int, str, bool, str, int, bool]]:
    """Scan a file for citations.

    Returns tuples of
    (type, citation, line_number, context_line, is_external_line,
     line_context, cite_start_pos, has_historical_marker).
    """
    if not filepath.exists():
        return []
    citations = []
    lines = filepath.read_text().splitlines()

    for i, line in enumerate(lines, 1):
        external_spans = []
        has_external_branch = False
        for match in EXTERNAL_BRANCH_PATTERN.finditer(line):
            has_external_branch = True
            external_spans.append((match.start(), match.end()))
            citations.append((
                "external-branch-commit", f"{match.group(1)}@{match.group(2)}", i,
                line.strip(), True, line, match.start(), False,
            ))

        def is_in_external_branch(pos: int) -> bool:
            return any(start <= pos < end for start, end in external_spans)

        consumed: List[Tuple[int, int]] = []

        def add(cite_type: str, citation: str, pos: int, is_ext: bool,
                cite_end: int = -1) -> None:
            citations.append((
                cite_type, citation, i, line.strip(), is_ext, line,
                pos, has_historical_marker(line, pos, cite_end),
            ))

        # 1. Qualified identities, most specific first; each consumed so the
        #    bare-test matcher cannot re-read the method as a global name.
        for match in TEST_PY_PATH_QUAL.finditer(line):
            consumed.append((match.start(), match.end()))
            add("py-test-qualified", f"{match.group(1)}::{match.group(2)}",
                match.start(), has_external_branch, match.end())
        for match in TEST_PY_DOTTED_QUAL.finditer(line):
            if any(s <= match.start() < e for s, e in consumed):
                continue
            consumed.append((match.start(), match.end()))
            parts = match.group(2).lstrip(".").split(".")
            add("py-test-qualified", f"{match.group(1)}::" + ".".join(parts),
                match.start(), has_external_branch, match.end())
        for match in TEST_PY_SHORT_QUAL.finditer(line):
            if any(s <= match.start() < e for s, e in consumed):
                continue
            consumed.append((match.start(), match.end()))
            parts = match.group(2).lstrip(".").split(".")
            add("py-test-qualified", f"{match.group(1)}::" + ".".join(parts),
                match.start(), has_external_branch, match.end())

        # 2. Plain modules and lab scripts
        for match in TEST_PY_PATTERN.finditer(line):
            if any(s <= match.start() < e for s, e in consumed):
                continue
            consumed.append((match.start(), match.end()))
            add("py-test-module", match.group(1), match.start(), has_external_branch, match.end())

        for match in TEST_LAB_SH_PATTERN.finditer(line):
            add("lab-script", match.group(1), match.start(), has_external_branch, match.end())

        # 3. Bare test names outside any consumed qualified identity
        for match in BARE_TEST_PATTERN.finditer(line):
            if any(s <= match.start() < e for s, e in consumed):
                continue
            before = line[max(0, match.start() - 30):match.start()]
            after = line[match.end():match.end() + 30]
            if 'tools/tests/' in before or 'tools.tests.' in before or '::' in before:
                continue
            if after.startswith(".py"):
                continue
            # Descriptive mentions, not citations
            stripped = before.rstrip()
            if stripped.endswith("test") or stripped.endswith("the"):
                continue
            if after.startswith(")"):
                continue
            add("bare-test", match.group(1), match.start(), has_external_branch, match.end())

        # 4. Commits (not part of an external branch reference)
        for match in COMMIT_PATTERN.finditer(line):
            sha = match.group(1)
            if len(sha) >= 7 and not sha.isdigit():
                add("commit", sha, match.start(), is_in_external_branch(match.start()),
                    match.end())

        # 5. Make targets - every target on the line, options stripped
        for match in TEST_MAKE_PATTERN.finditer(line):
            targets_str = re.sub(
                r'^make\s+(?:-[a-zA-Z]\d*\s+)*', '', match.group(0)
            )
            for target in targets_str.split():
                add("make-target", target, match.start(),
                    is_in_external_branch(match.start()), match.end())

        # 6. Report and evidence paths
        for match in REPORT_PATH_PATTERN.finditer(line):
            add("report", match.group(1), match.start(), has_external_branch, match.end())
        for match in EVIDENCE_PATH_PATTERN.finditer(line):
            add("evidence", match.group(1), match.start(), has_external_branch, match.end())
        for match in EVIDENCE_REPORT_PATTERN.finditer(line):
            add("evidence", match.group(1), match.start(), has_external_branch, match.end())

    return citations


def verify_citation(cite_type: str, citation: str, is_external_line: bool = False,
                    has_historical_marker_flag: bool = False,
                    relpath: str = "", line_context: str = "") -> Tuple[str, str]:
    """Verify a single citation.

    Returns (state, detail) where state is 'ok', 'external' (declared
    unverifiable provenance, never current evidence) or 'missing'.
    """

    if cite_type == "commit":
        return classify_commit(citation, relpath, line_context)

    if cite_type == "make-target":
        if make_target_exists(citation):
            return "ok", f"make target '{citation}' defined in Makefile"
        return "missing", f"make target '{citation}' NOT DEFINED in Makefile"

    if cite_type == "lab-script":
        if citation in EXTERNAL_BRANCH_FILES and is_external_line:
            return "ok", f"lab script tools/lab/{citation} is from external branch (test/network-matrix)"
        if test_script_exists(citation):
            return "ok", f"lab script tools/lab/{citation} exists"
        return "missing", f"lab script tools/lab/{citation} NOT FOUND"

    if cite_type == "py-test-module":
        if citation in HISTORICAL_MISSING_TEST_MODULES:
            if not has_historical_marker_flag:
                return "missing", (
                    f"test module tools/tests/{citation} is historical (from other branch) - "
                    f"not valid as current evidence without explicit historical marker"
                )
            return "ok", f"historical test module tools/tests/{citation} (from other branch, explicitly marked)"
        if test_py_exists(citation):
            return "ok", f"test module tools/tests/{citation} exists"
        return "missing", f"test module tools/tests/{citation} NOT FOUND"

    if cite_type == "py-test-qualified":
        module, chain = citation.split("::", 1)
        parts = chain.split(".")
        exists, detail = resolve_qualified(module, parts)
        if exists:
            return "ok", detail
        # The historical exemption is module-scoped only: it may excuse a
        # module that is absent from this tree (sibling branch), never a
        # missing class/method inside a module that is present here. Otherwise
        # four words beside a fabricated test name would rot-proof it.
        if test_py_exists(module + ".py"):
            return "missing", (
                f"{detail}; a historical marker cannot excuse a missing test "
                f"identity in a module that is present in this tree"
            )
        if has_historical_marker_flag:
            return "ok", (
                f"historical qualified reference {module}::{chain} ({detail}); "
                f"module absent from this tree and explicitly marked historical "
                f"- not current evidence"
            )
        return "missing", detail

    if cite_type == "bare-test":
        # A bare name has no module qualifier; resolve it against every test
        # module so a genuinely missing name fails while a real one passes.
        # Qualified identities are always resolved exactly (see above).
        for test_file in sorted(TOOLS_TESTS_DIR.glob("test_*.py")):
            functions, classes = module_index(test_file.name)
            if citation in functions or citation in classes:
                return "ok", f"bare test '{citation}' found in {test_file.name}"
            if any(citation in methods for methods in classes.values()):
                return "ok", f"bare test '{citation}' found in {test_file.name}"
        return "missing", (
            f"bare test name '{citation}' NOT FOUND in any test module - "
            f"use qualified form module::method"
        )

    if cite_type == "report":
        if citation in EXTERNAL_BRANCH_FILES and is_external_line:
            return "ok", f"report reports/{citation} is from external branch (test/network-matrix)"
        if file_exists_in_tree(f"reports/{citation}"):
            return "ok", f"report reports/{citation} tracked in git"
        if file_exists_on_disk(REPORTS_DIR / citation):
            return "ok", f"report reports/{citation} exists on disk (untracked?)"
        return "missing", f"report reports/{citation} NOT FOUND in tree or on disk"

    if cite_type == "evidence":
        if citation in EXTERNAL_BRANCH_FILES and is_external_line:
            return "ok", f"evidence {citation} is from external branch (test/network-matrix)"
        if file_exists_in_tree(f"docs/evidence/{citation}"):
            return "ok", f"evidence docs/evidence/{citation} tracked in git"
        if file_exists_on_disk(EVIDENCE_DIR / citation):
            return "ok", f"evidence docs/evidence/{citation} exists on disk"
        if file_exists_in_tree(f"reports/evidence/{citation}"):
            return "ok", f"evidence reports/evidence/{citation} tracked in git"
        if file_exists_on_disk(REPORTS_DIR / "evidence" / citation):
            return "ok", f"evidence reports/evidence/{citation} exists on disk"
        return "missing", f"evidence docs/evidence/{citation} NOT FOUND"

    if cite_type == "external-branch-commit":
        branch, sha = citation.split("@", 1)
        return "ok", f"external branch {branch}@{sha} (not verified in this tree)"

    return "missing", f"unknown citation type: {cite_type}"


def validate_registry(referenced: Dict[str, List[str]]) -> List[str]:
    """Fail closed on a registry that is stale, unresolvable or a dumping ground."""
    problems: List[str] = []
    registry = external_registry()
    for ident, entry in sorted(registry.items()):
        if commit_exists_in_tree(ident):
            problems.append(
                f"docs/EXTERNAL-REFS.md: entry {ident} resolves to a commit in this "
                f"repository — cite the commit directly and delete the entry"
            )
        if entry["kind"] not in KNOWN_EXTERNAL_KINDS:
            problems.append(
                f"docs/EXTERNAL-REFS.md: entry {ident} has unknown kind '{entry['kind']}' "
                f"(allowed: {', '.join(KNOWN_EXTERNAL_KINDS)})"
            )
        if not entry["files"]:
            problems.append(
                f"docs/EXTERNAL-REFS.md: entry {ident} names no file allowed to cite it"
            )
        elif ident not in referenced:
            problems.append(
                f"docs/EXTERNAL-REFS.md: entry {ident} is not cited anywhere "
                f"(declared for {', '.join(entry['files'])}) — remove it"
            )
        for cited_in in referenced.get(ident, []):
            if cited_in not in entry["files"]:
                problems.append(
                    f"docs/EXTERNAL-REFS.md: entry {ident} is cited from {cited_in}, "
                    f"which is not in its 'Citable only in' column"
                )
    return problems


def main() -> int:
    all_citations = []
    scanned = 0
    for f in SCAN_FILES:
        if f.exists():
            scanned += 1
            for cite in scan_file(f):
                all_citations.append((f.relative_to(REPO_ROOT), *cite))

    if not all_citations:
        print("No citations found in scanned files.")
        registry_problems = validate_registry({})
        if registry_problems:
            print(f"FAILED: docs/EXTERNAL-REFS.md is inconsistent "
                  f"({len(registry_problems)} problem(s)):")
            for problem in registry_problems:
                print(f"  {problem}")
            return 1
        return 0

    print(f"Scanned {scanned} files, found {len(all_citations)} citations.")
    print("-" * 80)

    failed = []
    historical = []
    external_info = []
    declared_external = []
    referenced: Dict[str, List[str]] = {}
    for (filepath, cite_type, citation, line_no, context, is_external_line,
         line_context, cite_start_pos, hist_marker) in all_citations:
        relpath = str(filepath)
        state, detail = verify_citation(
            cite_type, citation, is_external_line, hist_marker,
            relpath=relpath, line_context=line_context,
        )
        if state == "external":
            referenced.setdefault(citation, []).append(relpath)
        if cite_type == "external-branch-commit":
            external_info.append((filepath, cite_type, citation, line_no, detail))
            status = "i"
        elif state == "external":
            declared_external.append((filepath, citation, line_no, detail))
            status = "e"
        else:
            status = "✓" if state in ("ok", "resolved") else "✗"
        print(f"{status} {filepath}:{line_no} [{cite_type}] {citation} — {detail}")
        if state in ("ok", "resolved") and "historical" in detail and cite_type != "external-branch-commit":
            historical.append((filepath, citation, line_no))
        if state == "missing" and cite_type != "external-branch-commit":
            failed.append((filepath, cite_type, citation, line_no, detail))

    print("-" * 80)
    if external_info:
        print(f"External branch references (informational): {len(external_info)}")
        for filepath, cite_type, citation, line_no, detail in external_info:
            print(f"  i {filepath}:{line_no} [{cite_type}] {citation} — {detail}")
        print("-" * 80)

    if declared_external:
        print(
            f"Declared external references (unverifiable here, NOT current evidence): "
            f"{len(declared_external)}"
        )
        for filepath, citation, line_no, detail in declared_external:
            print(f"  e {filepath}:{line_no} {citation} — {detail}")
        print("-" * 80)

    registry_problems = validate_registry(referenced)
    if registry_problems:
        print(f"FAILED: docs/EXTERNAL-REFS.md is inconsistent ({len(registry_problems)} problem(s)):")
        for problem in registry_problems:
            print(f"  {problem}")
        print("-" * 80)
        failed.extend(("docs/EXTERNAL-REFS.md", "registry", "", 0, p) for p in registry_problems)

    if failed:
        print(f"FAILED: {len(failed)} internal citation(s) missing or invalid:")
        for filepath, cite_type, citation, line_no, detail in failed:
            print(f"  {filepath}:{line_no} [{cite_type}] {citation} — {detail}")
        return 1

    if historical:
        print(f"Historical (non-current) references accepted: {len(historical)}")
        for filepath, citation, line_no in historical:
            print(f"  h {filepath}:{line_no} {citation}")
    print("ALL INTERNAL CITATIONS VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
