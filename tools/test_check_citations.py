#!/usr/bin/env python3
"""
Regression tests for check-citations.py

Tests the specific bypasses and false negatives identified in R1-2:
1. Reports are scanned
2. Exact test method validation (not just module existence)
3. Commit object type validation (must be commit, not tag/tree)
4. Typo commit exemptions removed (valid hex but not in tree)
5. Multi-hyphen make target parsing
6. External branch references properly excluded per-citation (not line-wide)
7. Report with broken citation detected

Additional tests for R3-1 bypasses:
8. Bare test in report (nonexistent) -> FAIL
9. Historical module in report without historical marker -> FAIL
10. Mixed external/internal citations on same line -> internal ones FAIL

Additional tests for R6-1 bypasses:
11. Bare test with sentence-ending period -> FAIL
12. Dotted qualified test name -> FAIL (treated as bare test)
13. Bare test with parentheses -> FAIL
14. Make target with options (-j2) -> FAIL
"""

import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

def run_checker(repo_path: Path) -> tuple[int, str]:
    """Run check-citations.py on a repo and return (exit_code, output)."""
    result = subprocess.run(
        [sys.executable, "tools/check-citations.py"],
        cwd=repo_path,
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout + result.stderr

def create_test_repo() -> tuple[Path, str]:
    """Create a temporary repo with test files."""
    tmpdir = Path(tempfile.mkdtemp(prefix="checker-test-"))
    (tmpdir / "docs").mkdir()
    (tmpdir / "reports").mkdir()
    (tmpdir / "tools" / "lab").mkdir(parents=True)
    (tmpdir / "tools" / "tests").mkdir(parents=True)
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "w1").mkdir(parents=True)
    
    # Create a minimal Makefile with all targets
    (tmpdir / "Makefile").write_text("""\
check: check-cgate check-vm check-interop check-scan check-patch check-citations check-syntax check-py check-tests check-oracle

check-cgate:
	@echo "check-cgate"

check-vm:
	@echo "check-vm"

check-interop:
	@echo "check-interop"

check-scan:
	@echo "check-scan"

check-patch:
	@echo "check-patch"

check-citations:
	@echo "check-citations"

check-syntax:
	@echo "check-syntax"

check-py:
	@echo "check-py"

check-tests:
	@echo "check-tests"

check-oracle:
	@echo "check-oracle"
""")
    
    # Create minimal test files
    (tmpdir / "tools" / "lab" / "smoke-test.sh").write_text("#!/bin/sh\necho smoke\n")
    (tmpdir / "tools" / "lab" / "awgctl-test.sh").write_text("#!/bin/sh\necho awgctl\n")
    (tmpdir / "tools" / "lab" / "awg-dual-v5-31.sh").write_text("#!/bin/sh\necho external\n")
    (tmpdir / "tools" / "tests" / "test_quick_parser.py").write_text("""\
class QuickParser:
    def test_all_secret_keys_use_stdin_in_argument_order(self):
        pass
    def test_lists_do_not_expand_filenames(self):
        pass
    def test_user_owned_config_is_refused(self):
        pass
""")
    (tmpdir / "tools" / "tests" / "test_gen_config.py").write_text("""\
class GeneratorSecurity:
    def test_default_output_private_and_stdin_keys(self):
        pass
""")
    (tmpdir / "tools" / "tests" / "test_installer_transaction.py").write_text("""\
class InstallerTransaction:
    def test_root_build_refused_without_flag(self):
        pass
""")
    (tmpdir / "tools" / "tests" / "test_awgctl_r9_restore.py").write_text("""\
class AwgctlRestore:
    def test_failed_up_keeps_a_down_interface_down(self):
        pass
""")
    (tmpdir / "tools" / "tests" / "test_installer_r5_lock.py").write_text("""\
class LockOwnership:
    def test_three_reclaimers_barrier_exactly_one_wins(self):
        pass
""")
    
    # Create evidence files
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "make-check.log").write_text("make check passed\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "check-vm-final.log").write_text("check-vm passed\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "all-keys-native.log").write_text("keys ok\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "native-checks.log").write_text("native checks ok\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "generator-native.log").write_text("generator ok\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "w1-pledge-hunk.log").write_text("pledge hunk ok\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "w1" / "01-build.log").write_text("build ok\n")
    (tmpdir / "docs" / "evidence" / "2026-11-07" / "w1" / "01b-unpriv.log").write_text("unpriv build ok\n")
    
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmpdir, capture_output=True)
    
    # Create a commit to reference
    (tmpdir / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmpdir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add README"], cwd=tmpdir, capture_output=True)
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmpdir, capture_output=True, text=True
    ).stdout.strip()
    
    return tmpdir, commit_sha

def test_reports_are_scanned():
    """Test that reports/*.md files are scanned for citations."""
    print("Test 1: Reports are scanned...")
    repo, commit = create_test_repo()
    
    # Create a report with a valid citation (commit SHA)
    (repo / "reports" / "test-report.md").write_text(f"See commit {commit}\n")
    
    # Copy checker
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0, got {code}: {output}"
    assert "ALL INTERNAL CITATIONS VERIFIED" in output, f"Expected verification, got: {output}"
    print("  PASS: Reports are scanned and valid citations pass")

def test_nonexistent_test_method_detected():
    """Test that nonexistent test methods are detected (not just module existence)."""
    print("Test 2: Nonexistent test method detected...")
    repo, commit = create_test_repo()
    
    # Create TRACEABILITY.md with a nonexistent test method (full path format)
    (repo / "docs" / "TRACEABILITY.md").write_text(f"""\
| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| T-1 | Test requirement | tools/tests/test_quick_parser.py::test_NONE_EXISTENT | tools/tests/test_quick_parser.py::test_NONE_EXISTENT @ {commit} | proven |
""")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for nonexistent method, got {code}: {output}"
    assert "NOT FOUND" in output and "test_NONE_EXISTENT" in output, f"Expected method not found error: {output}"
    print("  PASS: Nonexistent test method correctly detected")

def test_commit_object_type_validated():
    """Test that commit object type is validated (must be commit, not tag/tree)."""
    print("Test 3: Commit object type validated...")
    repo, commit = create_test_repo()
    
    # Create an annotated tag (creates a tag object, not a commit)
    subprocess.run(["git", "tag", "-a", "test-tag", "-m", "Test tag"], cwd=repo, capture_output=True)
    tag_sha = subprocess.run(
        ["git", "rev-parse", "test-tag"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    
    # Verify it's a tag object, not a commit
    obj_type = subprocess.run(
        ["git", "cat-file", "-t", tag_sha], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert obj_type == "tag", f"Expected tag object, got {obj_type}"
    
    # Create TRACEABILITY.md referencing the tag SHA
    (repo / "docs" / "TRACEABILITY.md").write_text(f"""\
| # | Requirement | What Proves It | Concrete Evidence | Gate Status |
|---|-------------|----------------|-------------------|-------------|
| T-1 | Test requirement | some test | {tag_sha} | proven |
""")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    # The tag SHA should fail because it's not a commit object
    assert code == 1, f"Expected exit 1 for tag SHA, got {code}: {output}"
    assert "NOT FOUND" in output, f"Expected tag SHA not found error: {output}"
    print("  PASS: Tag SHA correctly rejected (not a commit)")

def test_typo_commit_not_exempted():
    """Test that typo commits (valid hex but not in tree) are not exempted."""
    print("Test 4: Typo commit not exempted...")
    repo, commit = create_test_repo()
    
    # Create a valid hex commit SHA that doesn't exist in the tree
    # Modify last few chars to create a valid but non-existent SHA
    typo_sha = commit[:-4] + "abcd"  # Valid hex, but not in tree
    
    # Put the typo SHA directly in the text
    (repo / "docs" / "TRACEABILITY.md").write_text(f"Broken commit reference: {typo_sha}\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for typo commit, got {code}: {output}"
    assert "NOT FOUND" in output, f"Expected typo commit not found error: {output}"
    print("  PASS: Typo commit correctly rejected")

def test_multi_hyphen_make_targets():
    """Test that multi-hyphen make targets are fully parsed."""
    print("Test 5: Multi-hyphen make targets parsed...")
    repo, commit = create_test_repo()
    
    # Create TRACEABILITY.md with multi-hyphen make targets in plain text
    (repo / "docs" / "TRACEABILITY.md").write_text(f"""\
make check-cgate
make check-vm
make check-interop
make check-scan
""")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0 for multi-hyphen targets, got {code}: {output}"
    # Check that all targets were found in output (they appear as verified citations)
    assert "check-cgate" in output, f"Expected check-cgate in output"
    assert "check-vm" in output, f"Expected check-vm in output"
    assert "check-interop" in output, f"Expected check-interop in output"
    assert "check-scan" in output, f"Expected check-scan in output"
    print("  PASS: Multi-hyphen make targets correctly parsed")

def test_external_branch_refs_excluded():
    """Test that external branch references are properly excluded per-citation.
    Known external branch files (in EXTERNAL_BRANCH_FILES) on a line with external branch ref are allowed.
    Unknown files on a line with external branch ref are NOT allowed just because of line-wide exemption."""
    print("Test 6: External branch references excluded per-citation...")
    repo, commit = create_test_repo()
    
    # Create TRACEABILITY.md with:
    # 1. Known external branch report on same line as external branch ref -> should PASS
    # 2. Known external branch lab-script on same line as external branch ref -> should PASS
    # 3. UNKNOWN report on same line as external branch ref -> should FAIL
    # 4. UNKNOWN lab-script on same line as external branch ref -> should FAIL
    (repo / "docs" / "TRACEABILITY.md").write_text(f"""\
See reports/w3-dual-v2.md on test/network-matrix @ {commit}
See tools/lab/awg-dual-v5-31.sh on test/network-matrix @ {commit}
See reports/unknown-report.md on test/network-matrix @ {commit}
See tools/lab/unknown-script.sh on test/network-matrix @ {commit}
""")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    # Known external files should pass, unknown should fail
    assert code == 1, f"Expected exit 1 for unknown external files, got {code}: {output}"
    assert "w3-dual-v2.md" in output and "is from external branch" in output, f"Known external report should pass: {output}"
    assert "awg-dual-v5-31.sh" in output and "is from external branch" in output, f"Known external lab-script should pass: {output}"
    assert "unknown-report.md" in output and "NOT FOUND" in output, f"Unknown external report should fail: {output}"
    assert "unknown-script.sh" in output and "NOT FOUND" in output, f"Unknown external lab-script should fail: {output}"
    print("  PASS: External branch references correctly excluded per-citation")

def test_report_with_broken_citation_detected():
    """Test that a report with a broken citation is detected."""
    print("Test 7: Report with broken citation detected...")
    repo, commit = create_test_repo()
    
    # Create a report with a broken commit citation (valid hex but not in tree)
    broken_sha = "deadbeef1234567890abcdef1234567890abcd"  # 40-char valid hex
    (repo / "reports" / "broken-report.md").write_text(f"See commit {broken_sha}\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for broken citation in report, got {code}: {output}"
    assert broken_sha in output, f"Expected broken commit in output: {output}"
    print("  PASS: Broken citation in report correctly detected")

def test_bare_test_in_report_detected():
    """Test R3-1: bare test name in report (nonexistent) is detected and fails."""
    print("Test 8: Bare test in report detected...")
    repo, commit = create_test_repo()
    
    # Create a report with a bare test name that doesn't exist
    (repo / "reports" / "bare-test-report.md").write_text(f"Passed test_REVIEW_NONEXISTENT\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for nonexistent bare test in report, got {code}: {output}"
    assert "test_REVIEW_NONEXISTENT" in output and "NOT FOUND" in output, f"Expected bare test not found error: {output}"
    print("  PASS: Nonexistent bare test in report correctly detected")

def test_historical_module_without_marker_detected():
    """Test R3-1: historical module (test_awgctl.py) without historical marker fails."""
    print("Test 9: Historical module without marker detected...")
    repo, commit = create_test_repo()
    
    # Create a report citing test_awgctl.py WITHOUT historical marker
    (repo / "reports" / "historical-module-report.md").write_text(f"Current evidence tools/tests/test_awgctl.py\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for historical module without marker, got {code}: {output}"
    assert "test_awgctl.py" in output and "historical" in output and "not valid as current evidence" in output, f"Expected historical module rejected error: {output}"
    print("  PASS: Historical module without marker correctly rejected")

def test_historical_module_with_marker_allowed():
    """Test that historical module WITH explicit historical marker is allowed."""
    print("Test 10: Historical module with marker allowed...")
    repo, commit = create_test_repo()
    
    # Create a report citing test_awgctl.py WITH historical marker
    (repo / "reports" / "historical-module-report.md").write_text(f"Historical: tools/tests/test_awgctl.py from other branch\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0 for historical module with marker, got {code}: {output}"
    assert "historical test module" in output, f"Expected historical module allowed message: {output}"
    print("  PASS: Historical module with explicit marker correctly allowed")

def test_mixed_external_internal_citations():
    """Test R3-1: mixed external/internal citations on same line - internal ones should fail."""
    print("Test 11: Mixed external/internal citations...")
    repo, commit = create_test_repo()
    
    # Create TRACEABILITY.md with mixed external reference and missing internal paths
    # The external branch ref is valid, but the internal script/report don't exist
    (repo / "docs" / "TRACEABILITY.md").write_text(f"""\
test/network-matrix @ {commit} ; current tools/lab/review-missing.sh and reports/review-missing.md
""")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    # The external branch commit is informational, but the internal script/report should fail
    assert code == 1, f"Expected exit 1 for missing internal files on mixed line, got {code}: {output}"
    assert "review-missing.sh" in output and "NOT FOUND" in output, f"Expected missing lab-script error: {output}"
    assert "review-missing.md" in output and "NOT FOUND" in output, f"Expected missing report error: {output}"
    print("  PASS: Mixed external/internal citations correctly validated per-citation")

def test_bare_test_with_period_detected():
    """Test R6-1: bare test name with sentence-ending period is detected and fails."""
    print("Test 12: Bare test with period detected...")
    repo, commit = create_test_repo()
    
    # Create a report with a bare test name ending with period that doesn't exist
    (repo / "reports" / "bare-test-period.md").write_text(f"Passed test_REVIEW_NONEXISTENT.\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for nonexistent bare test with period, got {code}: {output}"
    assert "test_REVIEW_NONEXISTENT" in output and "NOT FOUND" in output, f"Expected bare test not found error: {output}"
    print("  PASS: Nonexistent bare test with period correctly detected")

def test_dotted_qualified_test_name_detected():
    """Test R6-1: dotted qualified test name is detected as bare test and fails."""
    print("Test 13: Dotted qualified test name detected...")
    repo, commit = create_test_repo()
    
    # Create a report with a dotted qualified test name that doesn't exist
    (repo / "reports" / "dotted-test.md").write_text(f"Passed tools.tests.test_quick_parser.QuickParser.test_REVIEW_NONEXISTENT\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for dotted qualified test, got {code}: {output}"
    assert "test_REVIEW_NONEXISTENT" in output and "NOT FOUND" in output, f"Expected bare test not found error: {output}"
    print("  PASS: Dotted qualified test name correctly detected as bare test")

def test_bare_test_with_parens_detected():
    """Test R6-1: bare test name with parentheses is detected and fails."""
    print("Test 14: Bare test with parentheses detected...")
    repo, commit = create_test_repo()
    
    # Create a report with a bare test name with parentheses that doesn't exist
    (repo / "reports" / "bare-test-parens.md").write_text(f"Passed test_REVIEW_NONEXISTENT()\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for bare test with parentheses, got {code}: {output}"
    assert "test_REVIEW_NONEXISTENT" in output and "NOT FOUND" in output, f"Expected bare test not found error: {output}"
    print("  PASS: Bare test with parentheses correctly detected")

def test_make_target_with_options_detected():
    """Test R6-1: make target with options (-j2) is detected and fails."""
    print("Test 15: Make target with options detected...")
    repo, commit = create_test_repo()
    
    # Create a report with a make target with options that doesn't exist
    (repo / "reports" / "make-opts.md").write_text(f"Passed make -j2 check-REVIEW-NONEXISTENT\n")
    
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for make target with options, got {code}: {output}"
    assert "check-REVIEW-NONEXISTENT" in output and "NOT DEFINED" in output, f"Expected make target not defined error: {output}"
    print("  PASS: Make target with options correctly detected")

def test_demo_report_is_not_exempt():
    """Test R7-2: no report filename is exempt from scanning."""
    print("Test 16: Demonstration report is not exempt...")
    repo, commit = create_test_repo()

    (repo / "reports" / "t_fd03a54f-demonstration.md").write_text(
        "Current commit deadbeef1234567890abcdef1234567890abcd\n"
    )
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")

    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for broken commit in demo report, got {code}: {output}"
    assert "deadbeef1234567890abcdef1234567890abcd" in output, f"Expected commit failure: {output}"
    print("  PASS: Demonstration report citations are checked")


def test_dotted_identity_wrong_module_detected():
    """Test R7-2: a real method cited under the wrong module fails."""
    print("Test 17: Dotted identity with wrong module detected...")
    repo, commit = create_test_repo()

    (repo / "reports" / "wrong-module.md").write_text(
        "Passed tools.tests.test_gen_config.GeneratorSecurity.test_user_owned_config_is_refused\n"
    )
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")

    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for wrong module identity, got {code}: {output}"
    assert "NOT FOUND in class 'GeneratorSecurity'" in output, f"Expected class/method miss: {output}"
    print("  PASS: Method under the wrong module is rejected")


def test_dotted_identity_wrong_class_detected():
    """Test R7-2: a real method cited under the wrong class in the right module fails."""
    print("Test 18: Dotted identity with wrong class detected...")
    repo, commit = create_test_repo()

    (repo / "reports" / "wrong-class.md").write_text(
        "Passed tools.tests.test_quick_parser.GeneratorSecurity.test_user_owned_config_is_refused\n"
    )
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")

    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for wrong class identity, got {code}: {output}"
    assert "class 'GeneratorSecurity' NOT FOUND" in output, f"Expected class miss: {output}"
    print("  PASS: Method under the wrong class is rejected")


def test_dotted_identity_correct_positive():
    """Test R7-2: the correct module/class/method identity passes."""
    print("Test 19: Correct dotted identity accepted...")
    repo, commit = create_test_repo()

    (repo / "reports" / "correct-dotted.md").write_text(
        "Passed tools.tests.test_quick_parser.QuickParser.test_user_owned_config_is_refused\n"
    )
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")

    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0 for correct identity, got {code}: {output}"
    assert "ALL INTERNAL CITATIONS VERIFIED" in output, f"Expected verification: {output}"
    print("  PASS: Correct qualified identity accepted")


def test_short_qualified_missing_method_detected():
    """Test R7-2: short module.method form with a missing method fails."""
    print("Test 20: Short qualified missing method detected...")
    repo, commit = create_test_repo()

    (repo / "reports" / "short-missing.md").write_text(
        "Failed test_installer_r5_lock.test_two_reclaimers_barrier_exactly_one_wins\n"
    )
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")

    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for missing short-qualified method, got {code}: {output}"
    assert "NOT FOUND in test_installer_r5_lock.py" in output, f"Expected method miss: {output}"
    print("  PASS: Short qualified method miss rejected")


def test_historical_qualified_reference_scoped():
    """R8-1: the historical exemption is module-scoped only.

    A marker may excuse a module absent from the tree, never a missing
    class/method inside a module that is present.
    """
    print("Test 21: Historical qualified reference is module-scoped...")
    repo, commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    report = repo / "reports" / "historical-qualified.md"

    # Missing module + explicit provenance: accepted (reported as historical).
    report.write_text(
        "The old name tools/tests/test_awgctl_r5.py::Anything.test_gone "
        "(historical: from other branch) no longer exists here\n"
    )
    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0 for historical missing module, got {code}: {output}"
    assert "historical qualified reference" in output, f"Expected historical acceptance: {output}"

    # Missing module without provenance: rejected.
    report.write_text(
        "The old name tools/tests/test_awgctl_r5.py::Anything.test_gone "
        "no longer exists here\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 without provenance, got {code}: {output}"

    # Missing method inside a PRESENT module: rejected even with provenance.
    report.write_text(
        "Failed test_installer_r5_lock.test_two_reclaimers_barrier_exactly_one_wins "
        "(historical: from other branch)\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for missing method in present module, got {code}: {output}"
    assert "cannot excuse a missing test identity" in output, f"Expected scoped failure: {output}"
    print("  PASS: Historical exemption is module-scoped")


def test_historical_marker_does_not_excuse_unknown_path_qualified():
    """R8-1 probe NEW-R8-B: path-qualified fabrication with a marker fails."""
    print("Test 22: Historical marker does not excuse a path-qualified unknown name...")
    repo, commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    (repo / "reports" / "historical-path.md").write_text(
        "Old name tools/tests/test_quick_parser.py::test_never_existed_anywhere "
        "(historical: from other branch)\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1, got {code}: {output}"
    assert "cannot excuse a missing test identity" in output, f"Expected scoped failure: {output}"
    print("  PASS: Path-qualified unknown name still fails with a marker")


def _write_registry(repo: Path, rows) -> None:
    """Write a docs/EXTERNAL-REFS.md table into a fixture repository."""
    lines = [
        "# External and unverifiable references",
        "",
        "| Identifier | Kind | Provenance | Citable only in | Required context token |",
        "|---|---|---|---|---|",
    ]
    lines.extend(rows)
    (repo / "docs" / "EXTERNAL-REFS.md").write_text("\n".join(lines) + "\n")


def test_out_of_tree_hash_not_allowlisted():
    """R9-1: a hash that resolves in no ref fails, with no allowlist to hide behind."""
    print("Test 23: Phantom commit hash is not allowlisted...")
    repo, _commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    (repo / "reports" / "phantom.md").write_text(
        "Allowlisted non-commit citations: 9ced40e, 71a02b2, 25dc854, 9a8310a, 4a26481.\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for phantom hashes, got {code}: {output}"
    for sha in ("9ced40e", "71a02b2", "25dc854", "9a8310a", "4a26481"):
        assert f"{sha} NOT FOUND" in output, f"Expected {sha} rejected: {output}"
    print("  PASS: Phantom hashes rejected without a registry entry")


def test_external_hash_not_citable_from_other_file():
    """R9-1: a declared external hash is citable only from its registered files."""
    print("Test 24: External hash is scoped to its registered file...")
    repo, _commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    _write_registry(repo, [
        "| `25dc854` | external-evidence-branch | `test/network-matrix` branch | "
        "`docs/RELEASE-NOTES.md` | `network-matrix` |"
    ])
    (repo / "reports" / "pasted.md").write_text(
        "Pasted evidence: the `test/network-matrix` set at `25dc854`.\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for an out-of-scope external hash, got {code}: {output}"
    assert "not citable from reports/pasted.md" in output, f"Expected scope failure: {output}"
    print("  PASS: External hash rejected outside its registered file")


def test_external_hash_declared_passes_as_external():
    """R9-1: a declared, in-scope external hash passes as external, not as verified."""
    print("Test 25: Declared external hash passes as external/informational...")
    repo, _commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    _write_registry(repo, [
        "| `25dc854` | external-evidence-branch | `test/network-matrix` branch | "
        "`docs/RELEASE-NOTES.md` | `network-matrix` |"
    ])
    (repo / "docs" / "RELEASE-NOTES.md").write_text(
        "The separate evidence set is on `test/network-matrix` at `25dc854`.\n"
    )
    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0 for a declared external hash, got {code}: {output}"
    assert "external (external-evidence-branch), NOT verifiable in this repository" in output, \
        f"Expected explicit external reporting: {output}"
    assert "exists in tree" not in output, f"External hash must never be called verified: {output}"
    print("  PASS: Declared external hash reported as unverifiable, not verified")


def test_out_of_tree_hash_forbidden_in_core_docs():
    """R9-1: docs/ may not rest on a workspace commit this repository never had."""
    print("Test 26: Core docs cannot cite an out-of-tree workspace hash...")
    repo, _commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    _write_registry(repo, [
        "| `9ced40e` | out-of-tree-workspace | workspace `awg-rc2/w1`, not retained | "
        "`docs/RELEASE-NOTES.md` | — |"
    ])
    (repo / "docs" / "RELEASE-NOTES.md").write_text(
        "Reviewed source: branch `feat/awgctl`, base `9ced40e`.\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for an out-of-tree hash in docs/, got {code}: {output}"
    assert "may not cite 9ced40e" in output, f"Expected core-doc refusal: {output}"
    print("  PASS: Core docs refuse out-of-tree workspace hashes")


def test_registry_stale_entry_fails():
    """R9-1: a registry entry that resolves in this repository is a stale exemption."""
    print("Test 27: Registry entry that resolves is rejected...")
    repo, commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    _write_registry(repo, [
        f"| `{commit[:7]}` | out-of-tree-workspace | workspace claimed unretained | "
        "`docs/RELEASE-NOTES.md` | — |"
    ])
    (repo / "docs" / "RELEASE-NOTES.md").write_text(
        f"Recorded at `{commit[:7]}` for this tree.\n"
    )
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for a stale registry entry, got {code}: {output}"
    assert "resolves to a commit in this repository" in output, f"Expected stale entry: {output}"
    print("  PASS: Stale registry entry rejected")


def test_registry_unused_entry_fails():
    """R9-1: registry entries must be cited, so the table cannot become a dumping ground."""
    print("Test 28: Registry entry cited nowhere is rejected...")
    repo, _commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    _write_registry(repo, [
        "| `deadbee` | out-of-tree-workspace | workspace not retained | "
        "`docs/RELEASE-NOTES.md` | — |"
    ])
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 for an unused registry entry, got {code}: {output}"
    assert "is not cited anywhere" in output, f"Expected unused-entry failure: {output}"
    print("  PASS: Unused registry entry rejected")


def test_external_hash_must_name_provenance():
    """R9-1: an upstream external hash must name its repository on the citing line."""
    print("Test 29: External hash must carry its provenance token...")
    repo, _commit = create_test_repo()
    shutil.copy(REPO_ROOT / "tools" / "check-citations.py", repo / "tools" / "check-citations.py")
    _write_registry(repo, [
        "| `b5928ef` | upstream-repo | amnezia-vpn/amneziawg-go | "
        "`docs/TRACEABILITY.md` | `amneziawg-go` |"
    ])
    doc = repo / "docs" / "TRACEABILITY.md"
    doc.write_text("Interop baseline pinned at `b5928ef` for this run.\n")
    code, output = run_checker(repo)
    assert code == 1, f"Expected exit 1 without provenance token, got {code}: {output}"
    assert "must name its provenance" in output, f"Expected provenance requirement: {output}"
    doc.write_text("Official `amneziawg-go` client at `b5928ef` was not run here.\n")
    code, output = run_checker(repo)
    assert code == 0, f"Expected exit 0 with provenance token, got {code}: {output}"
    assert "external (upstream-repo)" in output, f"Expected external reporting: {output}"
    print("  PASS: External hash requires and accepts its provenance token")


def main():
    tests = [
        test_reports_are_scanned,
        test_nonexistent_test_method_detected,
        test_commit_object_type_validated,
        test_typo_commit_not_exempted,
        test_multi_hyphen_make_targets,
        test_external_branch_refs_excluded,
        test_report_with_broken_citation_detected,
        test_bare_test_in_report_detected,
        test_historical_module_without_marker_detected,
        test_historical_module_with_marker_allowed,
        test_mixed_external_internal_citations,
        test_bare_test_with_period_detected,
        test_dotted_qualified_test_name_detected,
        test_bare_test_with_parens_detected,
        test_make_target_with_options_detected,
        test_demo_report_is_not_exempt,
        test_dotted_identity_wrong_module_detected,
        test_dotted_identity_wrong_class_detected,
        test_dotted_identity_correct_positive,
        test_short_qualified_missing_method_detected,
        test_historical_qualified_reference_scoped,
        test_historical_marker_does_not_excuse_unknown_path_qualified,
        test_out_of_tree_hash_not_allowlisted,
        test_external_hash_not_citable_from_other_file,
        test_external_hash_declared_passes_as_external,
        test_out_of_tree_hash_forbidden_in_core_docs,
        test_registry_stale_entry_fails,
        test_registry_unused_entry_fails,
        test_external_hash_must_name_provenance,
    ]
    
    failed = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed.append(test.__name__)
        except Exception as e:
            print(f"  ERROR: {e}")
            failed.append(test.__name__)
    
    if failed:
        print(f"\n{len(failed)}/{len(tests)} tests FAILED: {failed}")
        return 1
    else:
        print(f"\nAll {len(tests)} tests PASSED")
        return 0

if __name__ == "__main__":
    sys.exit(main())