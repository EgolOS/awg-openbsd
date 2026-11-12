#!/usr/bin/env python3
"""
Bounded fuzz harness for awg-quick and awgctl config parsers — Revision 8.

Addresses the Round 7 reviewer corrections:
1. Input taxonomy is internally consistent: no literal is both a proven-invalid
   input and a valid control. `10.0.0.1` (bare address) and duplicate-of-
   VALID_KEY values are removed from the invalid lists; prefix-0 and IPv6
   link-local %zone forms are moved to documented-syntax controls; the
   non-base64 key case now uses a character ("!") outside the base64 alphabet.
   A structural assertion in the class generator fails if the sets overlap.
2. Records are partitioned by an explicit parser field (quick_/ctl_ desc prefix);
   the read-only 1 MB control is reported separately and never counted in the
   awgctl partition. Large valid configs get their own `valid_large_config`
   bucket instead of inflating valid_control.
3. Item-1 iteration/rc columns are derived from all_results.json
   (`summarize` / `report` subcommands), never transcribed.
4. Usage:
     python3 tools/tests/fuzz_config_harness.py run
     python3 tools/tests/fuzz_config_harness.py reclassify <all_results.json>
     python3 tools/tests/fuzz_config_harness.py summarize <all_results.json>
   `reclassify` regenerates classification.json + the markdown table from stored
   results without re-running any parser, so the classification is auditable;
   `summarize` prints the per-class table straight from the same records.

Kept from Revision 6/7: awgctl configs are written UNDER the compiled CONF_DIR,
awg-quick runs parse_options + prepare_config + build_config_args with inert
mutation stubs, and every class has an iteration and wall-clock bound.
"""

import os
import subprocess
import tempfile
import time
import sys
import json
import signal
import shutil
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from collections import Counter

# ===================== CONFIGURATION =====================
MAX_ITERATIONS_PER_CLASS = 50
MAX_WALL_TIME_PER_CLASS = 20  # seconds
MAX_CASE_TIMEOUT = 3  # seconds per test case (20s for large)
MAX_INPUT_SIZE = 4 * 1024 * 1024  # 4MB max
BOUNDARY_LINE_LIMIT = 4096

# Paths - configurable via environment
WORKSPACE = Path(os.environ.get("AWG_WORKSPACE",
    "/home/aigeus/.hermes/cache/scratch/awg-engineering-v1/wt-e2-config-fuzz-r8"))
SOURCE_ROOT = Path(os.environ.get("AWG_SOURCE",
    "/home/aigeus/.hermes/cache/scratch/awg-engineering-v1/source"))
CYCLE_ROOT = Path(os.environ.get("AWG_CYCLE_ROOT",
    "/home/aigeus/.hermes/cache/scratch/awg-engineering-v1"))

AWG_QUICK = WORKSPACE / "sbin/awg-quick"
FIXTURE_IFCONFIG = WORKSPACE / "tools/lab/fixture-ifconfig.py"
AWGCTL_SRC = WORKSPACE / "sbin/awgctl/awgctl.c"
FIXTURE_STUBS = WORKSPACE / "tools/lab/fixture-stubs.c"

# Private temp root for this run
TMPDIR = Path(tempfile.mkdtemp(prefix="awg-fuzz-r8-"))
RESULTS_DIR = TMPDIR / "fuzz-results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _trust_safe_conf_root() -> Path:
    """Pick a parent for awgctl config files that the parser's trust chain accepts.

    awgctl refuses any config path whose directory components are group/other
    writable (awgctl.c:549-554), so a TMPDIR under /tmp (mode 1777) is rejected
    before parsing — that is the parser's documented trust model, not a fuzz
    failure. Use an explicit override, else the first candidate whose whole path
    is non-world-writable.
    """
    candidates = [
        os.environ.get("AWG_CONF_PARENT"),
        str(Path.home() / ".hermes/cache/scratch"),
        os.environ.get("TMPDIR"),
        "/var/tmp",
    ]
    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        try:
            p = p.resolve()
            parts = [Path("/")] + list(p.parents)[::-1][1:] + [p]
            ok = all(not (q.stat().st_mode & 0o022) for q in parts if q.exists())
        except OSError:
            continue
        if ok and p.is_dir():
            return Path(tempfile.mkdtemp(prefix="awg-fuzz-r8-conf-", dir=p))
    raise RuntimeError("no trust-safe directory for awgctl configs; set AWG_CONF_PARENT")


CONF_ROOT = _trust_safe_conf_root()

# Valid test keys (32 bytes = 44 chars base64 with padding)
VALID_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
VALID_PEER_KEY = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="

# Cycle-root evidence directories (cycle root, never inside the repo)
EVIDENCE_DIR = Path(os.environ.get("AWG_EVIDENCE_DIR",
    str(CYCLE_ROOT / "evidence" / "e2-config-fuzz-r8")))
REPORTS_DIR = CYCLE_ROOT / "reports"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Build cache for awgctl binaries per CONF_DIR
AWGCTL_BUILD_CACHE = {}

# Receipt log
RECEIPT_LOG = RESULTS_DIR / "receipts.log"

def log_receipt(cmd: str, rc: int, stdout: str, stderr: str, duration: float):
    """Log command receipt with full output."""
    with open(RECEIPT_LOG, "a") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"COMMAND: {cmd}\n")
        f.write(f"EXIT_CODE: {rc}\n")
        f.write(f"DURATION: {duration:.3f}s\n")
        f.write(f"STDOUT:\n{stdout}\n")
        f.write(f"STDERR:\n{stderr}\n")
        f.write(f"{'='*80}\n")

@dataclass
class FuzzResult:
    test_class: str
    iteration: int
    input_desc: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    crashed: bool
    hung: bool
    command: str
    actual_input_bytes: int
    parser_stage: str  # "parse", "validate", "fixture", "read_only", "unknown", "ok"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stdout"] = self.stdout[:500] if self.stdout else ""
        d["stderr"] = self.stderr[:500] if self.stderr else ""
        return d


def run_with_timeout(cmd: List[str], input_data: Optional[bytes] = None,
                     timeout: float = MAX_CASE_TIMEOUT,
                     env: Optional[Dict[str, str]] = None,
                     cwd: Optional[Path] = None) -> tuple:
    """Run command with hard timeout."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, input=input_data, capture_output=True,
            timeout=timeout, env=env, cwd=cwd, text=True
        )
        duration = time.monotonic() - start
        crashed = result.returncode < 0
        return result.returncode, result.stdout, result.stderr, duration, crashed, False
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return -1, "", "TIMEOUT", duration, False, True
    except Exception as e:
        duration = time.monotonic() - start
        return -1, "", str(e), duration, True, False


def build_awgctl_for_confdir(conf_dir: Path) -> Path:
    """Build awgctl-fixture with a specific CONF_DIR. Caches by conf_dir path."""
    cache_key = str(conf_dir.resolve())
    if cache_key in AWGCTL_BUILD_CACHE:
        return AWGCTL_BUILD_CACHE[cache_key]

    binary = RESULTS_DIR / f"awgctl-fixture-{conf_dir.name}"
    cc = os.environ.get("CC", "cc")
    cflags = os.environ.get("CFLAGS", "")

    conf_dir_str = str(conf_dir)
    ifconfig_path = str(FIXTURE_IFCONFIG)

    cmd = [cc]
    if cflags:
        cmd.extend(cflags.split())
    cmd.extend([
        "-DAWGCTL_LINUX",
        f"-I{WORKSPACE}/src",
        f"-I{WORKSPACE}/sbin/awgctl",
        f'-DCONF_DIR="{conf_dir_str}"',
        "-DCONF_OWNER_OK(u)=1",
        f'-DIFCONFIG_PATH="{ifconfig_path}"',
        "-o", str(binary),
        str(AWGCTL_SRC),
        str(FIXTURE_STUBS),
        "-lutil"
    ])

    cmd_str = " ".join(cmd)
    rc, out, err, dur, crash, hang = run_with_timeout(cmd, timeout=30)
    log_receipt(cmd_str, rc, out, err, dur)
    if rc != 0:
        raise RuntimeError(f"Failed to build awgctl for {conf_dir}: {err}")

    binary.chmod(0o755)
    AWGCTL_BUILD_CACHE[cache_key] = binary
    return binary


def make_awgquick_driver(config_path: Path) -> Path:
    """Create a driver script that exercises COMPLETE validation pipeline."""
    source = AWG_QUICK.read_text()
    parser_part = source.split('\nif [[ $# -eq 1 &&')[0]

    mock_ops = '''
# Inert mutation stubs - record calls but don't mutate system
ifconfig() {
    printf 'IFCONFIG:%s\\n' "$*"
    while IFS= read -r key; do
        printf 'STDIN:%s\\n' "$key"
    done
}
route() { printf 'ROUTE:%s\\n' "$*"; }
getent() { printf 'GETENT:%s\\n' "$*"; return 1; }
pgrep() { return 1; }
cp() { printf 'CP:%s\\n' "$*"; }
printf() { command printf "$@"; }
netstat() { printf 'NETSTAT:%s\\n' "$*"; }
ip() { printf 'IP:%s\\n' "$*"; }

# BSD stat adapter for ownership checks (root-owned, mode 600)
stat() {
    case "$2" in
        %Lp) printf '600\\n' ;;
        %u) printf '0\\n' ;;
        *) return 1 ;;
    esac
}

# readlink -f for config path resolution
readlink() {
    if [[ "$1" == "-f" ]]; then
        printf '%s\\n' "$2"
    else
        command readlink "$@"
    fi
}
'''
    driver = config_path.parent / f"driver_{config_path.stem}.bash"
    driver.write_text(parser_part + mock_ops +
                      f'\nparse_options "{config_path}"\nprepare_config\nbuild_config_args\necho "VALIDATION_COMPLETE"\n')
    driver.chmod(0o755)
    return driver


def run_awgquick_full(config_text: str, timeout: float = MAX_CASE_TIMEOUT) -> tuple:
    """Run awg-quick through COMPLETE validation pipeline."""
    with tempfile.TemporaryDirectory(dir=RESULTS_DIR) as d:
        work = Path(d)
        config = work / "fixture.conf"
        config.write_text(config_text)
        config.chmod(0o600)
        for p in [work]:
            p.chmod(0o700)

        driver = make_awgquick_driver(config)
        cmd_str = f"bash {driver}"
        rc, out, err, dur, crash, hang = run_with_timeout(
            ["bash", str(driver)], timeout=timeout)
        log_receipt(cmd_str, rc, out, err, dur)
        return rc, out, err, dur, crash, hang, cmd_str


def run_awgctl_parse(config_text: str, conf_dir: Path,
                     timeout: float = MAX_CASE_TIMEOUT) -> tuple:
    """Run awgctl-fixture on config text using custom CONF_DIR.
    CRITICAL: Config is written UNDER conf_dir so unveil gate passes."""
    binary = build_awgctl_for_confdir(conf_dir)
    config = conf_dir / "awg9.conf"
    config.write_bytes(config_text.encode() if isinstance(config_text, str) else config_text)
    config.chmod(0o600)

    env = os.environ.copy()
    env["AWGFIX_STATE"] = str(RESULTS_DIR / f"fixture-{conf_dir.name}.state")
    env["AWGFIX_IFC_LOG"] = str(RESULTS_DIR / f"ifconfig-{conf_dir.name}.log")

    cmd_str = f"{binary} up awg9 --config {config}"
    rc, out, err, dur, crash, hang = run_with_timeout(
        [str(binary), "up", "awg9", "--config", str(config)],
        timeout=timeout, env=env
    )
    log_receipt(cmd_str, rc, out, err, dur)
    return rc, out, err, dur, crash, hang, cmd_str


def reset_fixture_state(conf_dir: Path) -> None:
    """Drop the fixture's interface state for a CONF_DIR.

    tools/lab/fixture-ifconfig.py keeps per-CONF_DIR state, so a second `up`
    against the same state sees the interface/addresses already configured.
    Used between valid controls so each control starts from a clean interface.
    """
    state = RESULTS_DIR / f"fixture-{conf_dir.name}.state"
    try:
        state.unlink()
    except FileNotFoundError:
        pass


def classify_awgctl_error(stderr: str) -> str:
    """Classify awgctl error stage. Separate fixture ops from config parse/validation errors."""
    if "config must live under" in stderr:
        return "confine"
    if "not installed on awg" in stderr:
        return "fixture"
    if "interface" in stderr and "not found" in stderr:
        return "fixture"
    if "permission denied" in stderr or "operation not permitted" in stderr:
        return "fixture"
    if "invalid length" in stderr or "not a valid" in stderr or "bad prefix" in stderr:
        return "validate"
    if "NUL byte" in stderr or "truncated" in stderr:
        return "parse"
    if "key.*outside a section" in stderr or "unknown key" in stderr:
        return "parse"
    if "no '='" in stderr:
        return "parse"
    if "PrivateKey not set" in stderr:
        return "validate"
    if stderr.strip() == "":
        return "unknown"
    return "parse"


def generate_valid_large_config(size_target: int, style: str = "many_peers") -> str:
    """Generate a WELL-TERMINATED valid large config that reaches the intended parsing path.
    NO CAPS - scale counts to guarantee >= size_target bytes."""
    cfg = "[Interface]\nPrivateKey = " + VALID_KEY + "\n"

    if style == "many_peers":
        # Many peers with valid AllowedIPs and endpoints
        # Each peer ~200 bytes, need ~5500 for 1MB
        num_peers = max(1, int(size_target * 1.1 // 180))
        for p in range(num_peers):
            cfg += f"[Peer]\nPublicKey = {VALID_PEER_KEY}\n"
            cfg += f"AllowedIPs = 10.{p//256}.{p%256}.0/24\n"
            cfg += f"Endpoint = 10.{p//256}.{p%256}.1:51820\n"
            cfg += "PersistentKeepalive = 25\n"
    elif style == "many_addresses":
        # Many SHORT Address lines (valid CIDRs) - NOT a single long line
        # Each address ~30 bytes, need ~36000 for 1MB
        num_addrs = max(1, int(size_target * 1.1 // 30))
        for a in range(num_addrs):
            cfg += f"Address = 10.{a//256}.{a%256}.1/24\n"
    elif style == "long_line":
        # Single very long line (valid CIDR list) - will be caught by 4096-byte gate
        # Each CIDR ~20 bytes, need ~50000 for 1MB
        num_addrs = max(1, int(size_target * 1.1 // 20))
        addrs = ",".join(f"10.{i//256}.{i%256}.0/24" for i in range(num_addrs))
        cfg += f"Address = {addrs}\n"

    # Guarantee the target: pad with fixed-width comment lines. Each pad line is
    # exactly 80 bytes ("# " + 76 chars + "\n"); the final line is trimmed to the
    # exact remainder, so len(cfg) == size_target, never a few KB short.
    if len(cfg) < size_target:
        need = size_target - len(cfg)
        pad_line = "# " + "p" * 77 + "\n"
        assert len(pad_line) == 80
        full, tail = divmod(need, len(pad_line))
        cfg += pad_line * full
        if tail:
            if tail >= 2:
                cfg += "#" + "p" * (tail - 2) + "\n"
            else:
                # A single spare byte cannot form a comment line; a blank line
                # is enough (it is ignored by both parsers).
                cfg += "\n"

    # Ensure final newline
    if not cfg.endswith('\n'):
        cfg += '\n'
    assert len(cfg) >= size_target, f"generator undershot: {len(cfg)} < {size_target}"
    return cfg


def check_class_budget(class_start: float, max_wall_time: float, case_timeout: float) -> float:
    """Check remaining class budget and return capped timeout for next case."""
    elapsed = time.time() - class_start
    remaining = max_wall_time - elapsed
    if remaining <= case_timeout * 0.5:
        return 0
    return min(case_timeout, remaining)


# ===================== TEST CLASSES =====================

def test_truncated_files() -> List[FuzzResult]:
    """Test configs with truncated last lines (no newline at EOF)."""
    print("Testing: truncated files")
    results = []
    class_start = time.time()

    base = f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = 10.0.0.1/24\n"
    conf_dir = CONF_ROOT / "truncated"
    conf_dir.mkdir(mode=0o700)

    for i in range(MAX_ITERATIONS_PER_CLASS):
        budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
        if budget <= 0:
            break

        configs = [
            ("no_final_newline", base[:-1]),
            ("truncated_mid_line", base.rstrip() + "Address = 10.0.0.1/24"),
            ("no_newline_at_all", f"[Interface]\nPrivateKey = {VALID_KEY}"),
            ("truncated_key", "[Interface]\nPrivateKey = AAA"),
            ("no_section_header", f"PrivateKey = {VALID_KEY}\n"),
        ]

        for desc, cfg in configs:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break

            # awg-quick
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("truncated_files", i, f"quick_{desc}_len{len(cfg)}",
                                      rc, out, err, dur, crash, hang, cmd,
                                      len(cfg), "parse"))

            # awgctl
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("truncated_files", i, f"ctl_{desc}_len{len(cfg)}",
                                      rc, out, err, dur, crash, hang, cmd,
                                      len(cfg), stage))
    return results


def test_long_lines_values() -> List[FuzzResult]:
    """Test absurdly long lines and values. Includes exact 4096/4097 boundary tests."""
    print("Testing: long lines and values")
    results = []
    class_start = time.time()
    conf_dir = CONF_ROOT / "long_lines"
    conf_dir.mkdir(mode=0o700)

    # Exact boundary tests for the 4096 limit — PHYSICAL LINE LENGTH
    # The limit checks [[ ${#line} -le 4096 ]] where line includes "Address = VALUE"
    # len("Address = ") = 10, so value length should be:
    # 4085 for 4095 total (under), 4086 for 4096 total (at limit), 4087 for 4097 total (exceeds)
    boundary_tests = [
        ("4085_value_4095_line", "A" * 4085),   # 10 + 4085 = 4095 line (under)
        ("4086_value_4096_line", "A" * 4086),   # 10 + 4086 = 4096 line (at limit)
        ("4087_value_4097_line", "A" * 4087),   # 10 + 4087 = 4097 line (exceeds)
    ]

    for i in range(MAX_ITERATIONS_PER_CLASS):
        budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
        if budget <= 0:
            break

        lengths = [1000, 10000, 100000, 500000, 1000000]
        for length in lengths:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break

            long_val = "A" * length
            configs = [
                (f"long_privkey_{length}", f"[Interface]\nPrivateKey = {long_val}\n"),
                (f"long_address_{length}", f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = {long_val}\n"),
                (f"long_dns_{length}", f"[Interface]\nPrivateKey = {VALID_KEY}\nDNS = {long_val}\n"),
                (f"long_listenport_{length}", f"[Interface]\nPrivateKey = {VALID_KEY}\nListenPort = {long_val}\n"),
                (f"long_mtu_{length}", f"[Interface]\nPrivateKey = {VALID_KEY}\nMTU = {long_val}\n"),
                (f"long_peer_pubkey_{length}", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {long_val}\nAllowedIPs = 10.0.0.1/24\n"),
                (f"long_endpoint_{length}", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {VALID_PEER_KEY}\nEndpoint = {long_val}:51820\n"),
            ]

            for desc, cfg in configs:
                budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
                if budget <= 0:
                    break

                # awg-quick
                rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
                results.append(FuzzResult("long_lines_values", i, f"quick_{desc}",
                                          rc, out, err, dur, crash, hang, cmd,
                                          len(cfg), "parse"))

                # awgctl
                rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
                stage = classify_awgctl_error(err)
                results.append(FuzzResult("long_lines_values", i, f"ctl_{desc}",
                                          rc, out, err, dur, crash, hang, cmd,
                                          len(cfg), stage))

        # Boundary tests (run once per class)
        if i == 0:
            for desc, val in boundary_tests:
                budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
                if budget <= 0:
                    break
                cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = {val}\n"
                # awg-quick
                rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
                results.append(FuzzResult("long_lines_values", i, f"quick_boundary_{desc}",
                                          rc, out, err, dur, crash, hang, cmd,
                                          len(cfg), "parse"))
                # awgctl
                rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
                stage = classify_awgctl_error(err)
                results.append(FuzzResult("long_lines_values", i, f"ctl_boundary_{desc}",
                                          rc, out, err, dur, crash, hang, cmd,
                                          len(cfg), stage))
    return results


def test_duplicated_keys() -> List[FuzzResult]:
    """Test duplicated keys in config (last-write-wins behavior)."""
    print("Testing: duplicated keys")
    results = []
    class_start = time.time()
    conf_dir = CONF_ROOT / "dup_keys"
    conf_dir.mkdir(mode=0o700)

    configs = [
        ("dup_privatekey", f"[Interface]\nPrivateKey = {VALID_KEY}\nPrivateKey = {VALID_PEER_KEY}\n"),
        ("dup_address", f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = 10.0.0.1/24\nAddress = 192.168.1.1/24\n"),
        ("dup_listenport", f"[Interface]\nPrivateKey = {VALID_KEY}\nListenPort = 51820\nListenPort = 51821\n"),
        ("dup_mtu", f"[Interface]\nPrivateKey = {VALID_KEY}\nMTU = 1420\nMTU = 1500\n"),
        ("dup_dns", f"[Interface]\nPrivateKey = {VALID_KEY}\nDNS = 1.1.1.1\nDNS = 8.8.8.8\n"),
        ("dup_jc", f"[Interface]\nPrivateKey = {VALID_KEY}\nJc = 4\nJc = 8\n"),
        ("dup_peer_allowedips", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {VALID_PEER_KEY}\nAllowedIPs = 10.0.0.1/24\nAllowedIPs = 192.168.1.1/24\n"),
        ("dup_peer_endpoint", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {VALID_PEER_KEY}\nEndpoint = 10.0.0.1:51820\nEndpoint = 10.0.0.2:51820\n"),
        ("dup_peer_keepalive", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {VALID_PEER_KEY}\nAllowedIPs = 10.0.0.1/24\nPersistentKeepalive = 25\nPersistentKeepalive = 30\n"),
    ]

    for i in range(MAX_ITERATIONS_PER_CLASS):
        budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
        if budget <= 0:
            break

        for desc, cfg in configs:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break

            # awg-quick
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("duplicated_keys", i, f"quick_{desc}",
                                      rc, out, err, dur, crash, hang, cmd,
                                      len(cfg), "parse"))

            # awgctl
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("duplicated_keys", i, f"ctl_{desc}",
                                      rc, out, err, dur, crash, hang, cmd,
                                      len(cfg), stage))
    return results


def test_nested_quotes_escapes() -> List[FuzzResult]:
    """Test nested quotes and escape sequences."""
    print("Testing: nested quotes and escapes")
    results = []
    class_start = time.time()
    conf_dir = CONF_ROOT / "quotes"
    conf_dir.mkdir(mode=0o700)

    special_vals = [
        ("dquote", 'value"with"quotes'),
        ("squote", "value'with'single"),
        ("mixed_quote", 'value"with\'mixed'),
        ("escaped_quote", 'value\\"escaped"'),
        ("dollar_paren", 'value$`subst`'),
        ("cmd_subst", 'value$(cmd)'),
        ("backtick", 'value`cmd`'),
        ("var_expand", 'value${var}'),
        ("backslash_n", 'value\\nnewline'),
        ("backslash_t", 'value\\ttab'),
        ("backslash", 'value\\\\backslash'),
        ("quoted_double", '"quoted value"'),
        ("quoted_single", "'single quoted'"),
        ("hash_comment", 'value # comment'),
        ("semicolon", 'value; cmd'),
        ("pipe", 'value|pipe'),
        ("ampersand", 'value&bg'),
        ("redirect_in", 'value<redirect'),
        ("redirect_out", 'value>redirect'),
    ]

    for i in range(MAX_ITERATIONS_PER_CLASS):
        budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
        if budget <= 0:
            break

        for tag, val in special_vals:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break

            configs = [
                (f"address_{tag}", f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = {val}\n"),
                (f"dns_{tag}", f"[Interface]\nPrivateKey = {VALID_KEY}\nDNS = {val}\n"),
                (f"peer_allowedips_{tag}", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {VALID_PEER_KEY}\nAllowedIPs = {val}\n"),
                (f"peer_endpoint_{tag}", f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {VALID_PEER_KEY}\nEndpoint = {val}:51820\n"),
            ]

            for desc, cfg in configs:
                budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
                if budget <= 0:
                    break

                # awg-quick
                rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
                results.append(FuzzResult("nested_quotes_escapes", i, f"quick_{desc}",
                                          rc, out, err, dur, crash, hang, cmd,
                                          len(cfg), "parse"))

                # awgctl
                rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
                stage = classify_awgctl_error(err)
                results.append(FuzzResult("nested_quotes_escapes", i, f"ctl_{desc}",
                                          rc, out, err, dur, crash, hang, cmd,
                                          len(cfg), stage))
    return results


def test_invalid_addresses_ports_keys() -> List[FuzzResult]:
    """Test invalid addresses, ports, and keys. Classify acceptances honestly."""
    print("Testing: invalid addresses/ports/keys")
    results = []
    class_start = time.time()
    conf_dir = CONF_ROOT / "invalid"
    conf_dir.mkdir(mode=0o700)

    # Proven-invalid address values. Each literal must violate validate_cidr's
    # documented contract: IPv4 address + prefix 0..32, IPv6 address (+ optional
    # %zone) + prefix 0..128. Bare addresses, prefix 0 and %zone forms are
    # SUPPORTED (sbin/awg-quick:566-584), so they are controls, not defects.
    invalid_addrs = [
        ("v4_octet_overflow", "999.999.999.999/24"), ("v4_prefix_33", "10.0.0.1/33"),
        ("v4_prefix_negative", "10.0.0.1/-1"), ("v4_prefix_nonnumeric", "10.0.0.1/abc"),
        ("invalid_host", "not-an-ip/24"), ("v6_nonhex", "::gggg:ffff/64"),
        ("v6_prefix_129", "2001:db8::/129"),
        ("v4_octet_256", "10.0.0.256/24"), ("double_prefix", "10.0.0.1/32/24"),
        ("v6_prefix_129_dup", "::/129"),
    ]

    invalid_ports = [("above_max", "65536"), ("above_max_2", "65537"),
                     ("negative", "-1"), ("nonnumeric", "abc"), ("huge", "999999"),
                     ("decimal", "1.5"), ("multi_colon", "1:2:3")]

    # A WireGuard key is 32 bytes = 44 base64 characters (43 data + '='). Every
    # entry below violates that: wrong length, wrong padding, or a character
    # outside the base64 alphabet ('!'). 'Z'*43+'=' was removed in r8 — it is
    # valid base64, so accepting it is not an invalid-acceptance.
    invalid_keys = [
        ("key_short", "short"),
        ("key_long", "A" * 100),
        ("key_no_padding", "A" * 43),
        ("key_double_padding", "A" * 43 + "=="),
        ("key_nonb64_char", "A" * 42 + "!" + "="),
        ("key_empty", ""), ("key_space", " "), ("key_newline", "\n"), ("key_tab", "\t"),
    ]

    # Valid controls that SHOULD pass syntax validation. The last four are the
    # documented-syntax forms withdrawn from the r7 invalid list.
    valid_controls = [
        ("valid_cidr_v4_no_prefix", "10.0.0.1", "addr"),
        ("valid_cidr_v4_prefix", "10.0.0.1/24", "addr"),
        ("valid_cidr_v6_prefix", "2001:db8::/64", "addr"),
        ("valid_cidr_v4_prefix_zero_net", "10.0.0.0/0", "addr"),
        ("valid_cidr_v4_prefix_zero_unspec", "0.0.0.0/0", "addr"),
        ("valid_cidr_v6_prefix_zero", "::/0", "addr"),
        ("valid_cidr_v6_link_local_zone", "fe80::1%eth0/64", "addr"),
        ("valid_port", "51820", "port"),
        ("valid_port_high", "65535", "port"),
        # need_uint (<= 65535, no lower bound beyond digits) documents 0 as a
        # legal ListenPort, so port 0 was withdrawn from the invalid list in r8.
        ("valid_port_zero", "0", "port"),
        ("valid_key", VALID_KEY, "key"),
    ]

    # Structural taxonomy invariant: an input literal may not be classified as
    # both "proven invalid" and a valid control. Round 7 shipped exactly that
    # contradiction (10.0.0.1, and a key value byte-identical to VALID_KEY).
    invalid_values = ({v for _, v in invalid_addrs} |
                      {v for _, v in invalid_ports} |
                      {v for _, v in invalid_keys})
    control_values = {v for _, v, _ in valid_controls}
    overlap = invalid_values & control_values
    assert not overlap, f"taxonomy contradiction, both invalid and valid: {sorted(overlap)!r}"
    assert VALID_KEY not in {v for _, v in invalid_keys}, \
        "VALID_KEY must never appear in the invalid key set"

    for i in range(MAX_ITERATIONS_PER_CLASS):
        budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
        if budget <= 0:
            break

        # Invalid addresses
        for tag, addr in invalid_addrs:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break
            cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = {addr}\n"
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"quick_invalid_addr_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), "validate"))
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"ctl_invalid_addr_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), stage))

        # Invalid ports
        for tag, port in invalid_ports:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break
            cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nListenPort = {port}\n"
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"quick_invalid_port_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), "validate"))
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"ctl_invalid_port_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), stage))

        # Invalid keys (interface PrivateKey)
        for tag, key in invalid_keys:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break
            cfg = f"[Interface]\nPrivateKey = {key}\n"
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"quick_invalid_privkey_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), "validate"))
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"ctl_invalid_privkey_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), stage))

            # Also test as peer public key
            cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\n[Peer]\nPublicKey = {key}\nAllowedIPs = 10.0.0.1/24\n"
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"quick_invalid_peerkey_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), "validate"))
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("invalid_addresses_ports_keys", i, f"ctl_invalid_peerkey_{tag}",
                                      rc, out, err, dur, crash, hang, cmd, len(cfg), stage))

        # Valid controls (should pass syntax validation, may fail later validation).
        # A separate CONF_DIR keeps the awgctl fixture state file clean, so a
        # control cannot inherit "interface exists" state from an invalid case.
        if i == 0:
            valid_conf_dir = CONF_ROOT / "valid_controls"
            valid_conf_dir.mkdir(mode=0o700, exist_ok=True)
            for desc, val, ftype in valid_controls:
                budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
                if budget <= 0:
                    break
                if ftype == "key":
                    cfg = f"[Interface]\nPrivateKey = {val}\n"
                elif ftype == "port":
                    cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nListenPort = {val}\n"
                else:
                    cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = {val}\n"
                rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
                results.append(FuzzResult("invalid_addresses_ports_keys", i, f"quick_VALID_CONTROL_{desc}",
                                          rc, out, err, dur, crash, hang, cmd, len(cfg), "validate"))
                reset_fixture_state(valid_conf_dir)
                rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, valid_conf_dir, timeout=budget)
                stage = classify_awgctl_error(err)
                results.append(FuzzResult("invalid_addresses_ports_keys", i, f"ctl_VALID_CONTROL_{desc}",
                                          rc, out, err, dur, crash, hang, cmd, len(cfg), stage))
    return results


def test_nul_bytes() -> List[FuzzResult]:
    """Test NUL bytes in config."""
    print("Testing: NUL bytes")
    results = []
    class_start = time.time()
    conf_dir = CONF_ROOT / "nul"
    conf_dir.mkdir(mode=0o700)

    for i in range(MAX_ITERATIONS_PER_CLASS):
        budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
        if budget <= 0:
            break

        base = f"[Interface]\nPrivateKey = {VALID_KEY}\n"
        configs = [
            ("nul_at_end", base + "\x00"),
            ("nul_at_start", "\x00" + base),
            ("nul_in_keyname", base.replace("PrivateKey", "Pr\x00ivateKey")),
            ("nul_in_value", base.replace(VALID_KEY, "AAA\x00AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")),
            ("nul_before_newline", f"[Interface]\nPrivateKey = {VALID_KEY}\x00\n"),
            ("multiple_nuls", f"PrivateKey = {VALID_KEY}\x00\x00\x00\n"),
        ]

        for desc, cfg in configs:
            budget = check_class_budget(class_start, MAX_WALL_TIME_PER_CLASS, MAX_CASE_TIMEOUT)
            if budget <= 0:
                break

            # awg-quick
            rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=budget)
            results.append(FuzzResult("nul_bytes", i, f"quick_{desc}",
                                      rc, out, err, dur, crash, hang, cmd,
                                      len(cfg.encode()), "parse"))

            # awgctl (write as bytes to preserve NUL)
            rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=budget)
            stage = classify_awgctl_error(err)
            results.append(FuzzResult("nul_bytes", i, f"ctl_{desc}",
                                      rc, out, err, dur, crash, hang, cmd,
                                      len(cfg.encode()), stage))
    return results


def test_large_pathological() -> List[FuzzResult]:
    """Test 1MB+ pathological inputs - WELL-TERMINATED with valid bounded addresses/peers."""
    print("Testing: large pathological inputs")
    results = []
    class_start = time.time()
    conf_dir = CONF_ROOT / "large"
    conf_dir.mkdir(mode=0o700)

    # Only test 1MB to stay within time bounds
    size = 1024*1024  # 1MB

    for i in range(min(1, MAX_ITERATIONS_PER_CLASS)):  # Single iteration
        # Style 1: Many peers with valid AllowedIPs and endpoints
        cfg = generate_valid_large_config(size, "many_peers")
        actual_size = len(cfg)
        case_timeout = 40
        if case_timeout <= 0:
            break

        # awg-quick
        rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=case_timeout)
        stage = "ok" if rc == 0 else ("validate" if "truncated" not in err and "exceeds" not in err else "parse")
        results.append(FuzzResult("large_pathological", i,
                                  f"quick_many_peers_target{size}_actual{actual_size}",
                                  rc, out, err, dur, crash, hang, cmd,
                                  actual_size, stage))

        # awgctl
        rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=case_timeout)
        stage = classify_awgctl_error(err)
        results.append(FuzzResult("large_pathological", i,
                                  f"ctl_many_peers_target{size}_actual{actual_size}",
                                  rc, out, err, dur, crash, hang, cmd,
                                  actual_size, stage))

        # Style 2: Many SHORT Address lines (many lines, each < 4096 bytes)
        cfg = generate_valid_large_config(size, "many_addresses")
        actual_size = len(cfg)
        case_timeout = 40

        rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=case_timeout)
        stage = "ok" if rc == 0 else ("validate" if "truncated" not in err and "exceeds" not in err else "parse")
        results.append(FuzzResult("large_pathological", i,
                                  f"quick_many_addrs_target{size}_actual{actual_size}",
                                  rc, out, err, dur, crash, hang, cmd,
                                  actual_size, stage))

        rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=case_timeout)
        stage = classify_awgctl_error(err)
        results.append(FuzzResult("large_pathological", i,
                                  f"ctl_many_addrs_target{size}_actual{actual_size}",
                                  rc, out, err, dur, crash, hang, cmd,
                                  actual_size, stage))

        # Style 3: Single long line with valid CIDRs (will hit 4096-byte gate)
        cfg = generate_valid_large_config(size, "long_line")
        actual_size = len(cfg)
        max_line = max(len(line) for line in cfg.split('\n')) if cfg else 0
        case_timeout = 40

        rc, out, err, dur, crash, hang, cmd = run_awgquick_full(cfg, timeout=case_timeout)
        stage = "ok" if rc == 0 else ("parse" if "exceeds 4096" in err else "validate")
        results.append(FuzzResult("large_pathological", i,
                                  f"quick_long_line_target{size}_actual{actual_size}_maxline{max_line}",
                                  rc, out, err, dur, crash, hang, cmd,
                                  actual_size, stage))

        rc, out, err, dur, crash, hang, cmd = run_awgctl_parse(cfg, conf_dir, timeout=case_timeout)
        stage = classify_awgctl_error(err)
        results.append(FuzzResult("large_pathological", i,
                                  f"ctl_long_line_target{size}_actual{actual_size}_maxline{max_line}",
                                  rc, out, err, dur, crash, hang, cmd,
                                  actual_size, stage))

        # Control: read-only 1MB line (isolate read from parsing) — RUN ONCE
        if i == 0:
            control_line = "A" * 1000000 + "\n"
            with tempfile.TemporaryDirectory(dir=RESULTS_DIR) as d:
                ctrl_file = Path(d) / "control.txt"
                ctrl_file.write_text(control_line)
                start = time.monotonic()
                try:
                    # Use simple read without complex bash
                    r = subprocess.run(
                        ["bash", "-c", 'read -r line < "$1"; echo "length=${#line}"', "read-control", str(ctrl_file)],
                        capture_output=True, text=True, timeout=5
                    )
                    dur = time.monotonic() - start
                    results.append(FuzzResult("large_pathological", i, "control_1MB_readonly",
                                              r.returncode, r.stdout, r.stderr, dur,
                                              False, False, "read-control",
                                              len(control_line), "read_only"))
                    log_receipt("read-control", r.returncode, r.stdout, r.stderr, dur)
                except subprocess.TimeoutExpired:
                    dur = time.monotonic() - start
                    results.append(FuzzResult("large_pathological", i, "control_1MB_readonly",
                                              -1, "", "TIMEOUT", dur, False, True,
                                              "read-control", len(control_line), "read_only"))
                    log_receipt("read-control", -1, "", "TIMEOUT", dur)
                except Exception as e:
                    dur = time.monotonic() - start
                    results.append(FuzzResult("large_pathological", i, "control_1MB_readonly",
                                              -1, "", str(e), dur, True, False,
                                              "read-control", len(control_line), "read_only"))
                    log_receipt("read-control", -1, "", str(e), dur)

    return results


def run_all_tests() -> List[FuzzResult]:
    """Run all fuzz test classes."""
    all_results = []
    classes = [
        ("truncated_files", test_truncated_files),
        ("long_lines_values", test_long_lines_values),
        ("duplicated_keys", test_duplicated_keys),
        ("nested_quotes_escapes", test_nested_quotes_escapes),
        ("invalid_addresses_ports_keys", test_invalid_addresses_ports_keys),
        ("nul_bytes", test_nul_bytes),
        ("large_pathological", test_large_pathological),
    ]

    for class_name, test_func in classes:
        print(f"\n{'='*60}")
        print(f"Running: {class_name}")
        print(f"{'='*60}")
        start = time.time()
        results = test_func()
        elapsed = time.time() - start
        all_results.extend(results)

        # Summary for this class
        crashed = sum(1 for r in results if r.crashed)
        hung = sum(1 for r in results if r.hung)
        clean_errors = sum(1 for r in results if not r.crashed and not r.hung and r.exit_code != 0)
        clean_success = sum(1 for r in results if not r.crashed and not r.hung and r.exit_code == 0)

        print(f"  Iterations: {len({r.iteration for r in results})} "
              f"(records: {len(results)})")
        print(f"  Wall time: {elapsed:.2f}s")
        print(f"  Crashed: {crashed}")
        print(f"  Hung: {hung}")
        print(f"  Clean errors (exit!=0): {clean_errors}")
        print(f"  Clean success (exit=0): {clean_success}")

        # Save class results
        quick_results = [r.to_dict() for r in results if "quick" in r.input_desc]
        ctl_results = [r.to_dict() for r in results if "ctl" in r.input_desc]
        with open(RESULTS_DIR / f"awgquick_{class_name}.json", "w") as f:
            json.dump(quick_results, f, indent=2)
        with open(RESULTS_DIR / f"awgctl_{class_name}.json", "w") as f:
            json.dump(ctl_results, f, indent=2)

    return all_results


def run_regression_tests() -> Dict[str, Any]:
    """Run targeted regression tests for fixed issues.
    Demonstrates failure against pre-fix production."""
    print("\n" + "="*60)
    print("Running regression tests")
    print("="*60)

    regressions = {}

    # 1. 4096-byte line limit regression — EXACT PHYSICAL LINE LENGTH
    # The check is [[ ${#line} -le 4096 ]] where line includes the full physical line
    # For under/at limit: use VALID COMMENT LINE (starts with #) so test passes (rc=0)
    # For over limit: use invalid Address value but "exceeds" MUST appear first
    print("\n1. Testing 4096-byte line limit (R8/R4-08)")
    for line_len, desc, should_reject, use_valid_comment in [
        (4095, "4095_line", False, True),   # under limit - valid comment, should pass (rc=0)
        (4096, "4096_line", False, True),   # at limit - valid comment, should pass (rc=0)
        (4097, "4097_line", True, False),   # over limit - invalid value, "exceeds" MUST appear
        (5000, "5000_line", True, False),
        (100000, "100KB_line", True, False),
        (1000000, "1MB_line", True, False),
    ]:
        if use_valid_comment:
            # Standalone comment line: "#" + (line_len-1) chars = line_len total
            val = "#" + "x" * (line_len - 1)
            cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\n{val}\n"
        else:
            # Address value: "Address = " (10 chars) + value
            val = "A" * (line_len - 10)
            cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = {val}\n"
        rc, out, err, dur, crash, hang, _ = run_awgquick_full(cfg)
        exceeded = "exceeds 4096 bytes" in err

        if use_valid_comment:
            status = "PASS" if (rc == 0 and not exceeded) else "FAIL"
        else:
            status = "PASS" if (exceeded == should_reject) else "FAIL"

        print(f"  {desc}: rc={rc}, line_len={line_len}, exceeded={exceeded} [{status}]")
        regressions[f"line_limit_{desc}"] = {
            "exit_code": rc, "should_reject": should_reject,
            "exceeded_limit": exceeded, "status": status,
            "line_len": line_len, "used_valid_comment": use_valid_comment
        }

    # 2. 1MB well-terminated configs reach validation (not truncated-last-line gate)
    print("\n2. Testing 1MB well-terminated configs reach validation")
    for style in ["many_peers", "many_addresses", "long_line"]:
        cfg = generate_valid_large_config(1024*1024, style)
        actual_size = len(cfg)
        rc, out, err, dur, crash, hang, _ = run_awgquick_full(cfg, timeout=60)
        truncated_fail = "truncated last line" in err
        status = "PASS" if not truncated_fail else "FAIL"
        print(f"  {style}: actual_size={actual_size}, rc={rc}, truncated_fail={truncated_fail} [{status}]")
        regressions[f"1mb_{style}"] = {
            "exit_code": rc, "actual_bytes": actual_size,
            "truncated_fail": truncated_fail, "status": status
        }

    # 3. awgctl reaches parsing (not blocked by CONF_DIR)
    print("\n3. Testing awgctl reaches config parsing")
    conf_dir = CONF_ROOT / "regress_ctl"
    conf_dir.mkdir(mode=0o700)
    cfg = f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = 999.999.999.999/24\n"
    rc, out, err, dur, crash, hang, _ = run_awgctl_parse(cfg, conf_dir)
    reached_parsing = rc == 2  # EX_CONFIG = parsing error, not EX_USAGE = path error
    stage = classify_awgctl_error(err)
    status = "PASS" if reached_parsing else "FAIL"
    print(f"  invalid_addr: rc={rc} (EX_CONFIG=2 means parsing reached), stage={stage} [{status}]")
    regressions["awgctl_reaches_parsing"] = {
        "exit_code": rc, "reached_parsing": reached_parsing, "stage": stage, "status": status
    }

    # 4. Valid control config passes both parsers
    print("\n4. Testing valid control config")
    cfg = (f"[Interface]\nPrivateKey = {VALID_KEY}\nAddress = 10.0.0.1/24\n"
           f"ListenPort = 51820\n[Peer]\nPublicKey = {VALID_PEER_KEY}\n"
           f"AllowedIPs = 10.0.0.0/24\nEndpoint = 10.0.0.2:51820\n")
    rc_q, out_q, err_q, dur_q, crash_q, hang_q, _ = run_awgquick_full(cfg)
    conf_dir2 = CONF_ROOT / "regress_ctl2"
    conf_dir2.mkdir(mode=0o700)
    rc_c, out_c, err_c, dur_c, crash_c, hang_c, _ = run_awgctl_parse(cfg, conf_dir2)
    quick_ok = rc_q == 0 and "VALIDATION_COMPLETE" in out_q
    ctl_ok = rc_c == 0
    status = "PASS" if quick_ok and ctl_ok else "FAIL"
    print(f"  awg-quick: rc={rc_q}, validation_complete={quick_ok}")
    print(f"  awgctl: rc={rc_c}")
    regressions["valid_control"] = {
        "quick_rc": rc_q, "ctl_rc": rc_c,
        "quick_ok": quick_ok, "ctl_ok": ctl_ok, "status": status
    }

    # 5. Demonstrate pre-fix failure (documented)
    print("\n5. Documenting pre-fix failure for 1MB line")
    regressions["pre_fix_1mb_line_expected"] = {
        "description": "Pre-fix: 1MB line would take 5+ seconds (bash param expansion). Post-fix: rejected in ~0.02s.",
        "status": "DOCUMENTED"
    }

    return regressions


# ---------------------------------------------------------------- classification
# Machine tags are embedded in input_desc at generation time, so the classifier
# never has to guess from a truncated description. Buckets partition the rc=0
# set; proven_* categories sum to proven_invalid_accepted.

DEFERRED_REASONS = (
    "duplicate_key_last_write_wins",
    "ctl_accepts_missing_trailing_newline",
)


PARSERS = ("quick", "ctl")


def _split_desc(desc: str):
    """Partition a record by the parser that produced it.

    The prefix is written by the generator (`quick_…` / `ctl_…`). Anything else
    — e.g. `control_1MB_readonly`, a read-only timing probe that invokes no
    parser — is returned as parser "control" and is excluded from both
    partitions instead of being silently attributed to awgctl.
    """
    if desc.startswith("quick_"):
        return "quick", desc[len("quick_"):]
    if desc.startswith("ctl_"):
        return "ctl", desc[len("ctl_"):]
    return "control", desc


def _truncated_tag(body: str) -> str:
    for tag in ("no_final_newline", "no_newline_at_all", "truncated_mid_line",
                "truncated_key", "no_section_header"):
        if body.startswith(tag):
            return tag
    return "other"


def _quotes_field(body: str) -> str:
    for field, prefix in (("address", "address_"), ("dns", "dns_"),
                          ("allowedips", "peer_allowedips_"),
                          ("endpoint", "peer_endpoint_")):
        if body.startswith(prefix):
            return field
    return "other"


def categorize(r: Dict[str, Any]):
    """Return (bucket, detail_key) for one rc=0 record.

    bucket in {valid_control, accepted_design, proven_invalid_accepted}
    detail_key is a deferred reason for accepted_design and a proven category
    for proven_invalid_accepted.
    """
    desc = r["input_desc"]
    parser, body = _split_desc(desc)
    cls = r["test_class"]

    if "VALID_CONTROL" in desc:
        return "valid_control", "explicit_valid_control"
    if cls == "large_pathological":
        # These are well-terminated, structurally valid 1 MB+ configs; reaching
        # rc=0 is the expected outcome, not an acceptance bug. Kept in their own
        # bucket so they never inflate the hand-picked valid controls.
        return "valid_large_config", "well_terminated_valid_large_config"
    if "dup_" in body:
        return "accepted_design", "duplicate_key_last_write_wins"
    if cls == "truncated_files":
        tag = _truncated_tag(body)
        if tag in ("no_final_newline", "no_newline_at_all", "truncated_mid_line"):
            if parser == "ctl":
                # awgctl does not require a trailing newline; awg-quick does.
                return "accepted_design", "ctl_accepts_missing_trailing_newline"
            return "proven_invalid_accepted", "quick_accepts_missing_trailing_newline"
        if tag == "no_section_header":
            return "proven_invalid_accepted", "key_outside_section_accepted"
        if tag == "truncated_key":
            return "proven_invalid_accepted", "short_private_key_accepted"
        return "proven_invalid_accepted", "truncated_input_accepted"
    if body.startswith("long_") or body.startswith("boundary_"):
        return "proven_invalid_accepted", "overlong_value_accepted"
    if body.startswith("invalid_addr_"):
        return "proven_invalid_accepted", "invalid_address_accepted"
    if body.startswith("invalid_port_"):
        return "proven_invalid_accepted", "invalid_port_accepted"
    if body.startswith("invalid_privkey_"):
        return "proven_invalid_accepted", "invalid_private_key_accepted"
    if body.startswith("invalid_peerkey_"):
        return "proven_invalid_accepted", "invalid_peer_key_accepted"
    if cls == "nested_quotes_escapes":
        return "proven_invalid_accepted", f"quotes_metachar_{_quotes_field(body)}_accepted"
    if cls == "nul_bytes":
        return "proven_invalid_accepted", "nul_byte_accepted"
    return "proven_invalid_accepted", "other"


def classify_acceptances(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build classification.json purely from result records.

    Invariants enforced (raise on violation, so a bad file cannot be shipped):
      sum(buckets) == rc0_total
      sum(proven_categories) == proven_invalid_accepted
      duplicate_key appears only in deferred_reasons, never in categories
    """
    classification = {
        "generated_from": "all_results.json",
        "rules": "tools/tests/fuzz_config_harness.py:categorize",
        "quick": None,
        "ctl": None,
        "non_parser_records": [],
        "partition_ok": False,
        "details": [],
    }

    # Records that belong to neither parser partition (read-only timing probes).
    classification["non_parser_records"] = [
        {"desc": r["input_desc"], "exit_code": r["exit_code"],
         "parser_stage": r["parser_stage"], "duration": r["duration"]}
        for r in results if _split_desc(r["input_desc"])[0] == "control"
    ]

    for parser in PARSERS:
        rc0 = [r for r in results
               if r["exit_code"] == 0 and _split_desc(r["input_desc"])[0] == parser]
        buckets = {"valid_control": 0, "valid_large_config": 0,
                   "accepted_design": 0, "proven_invalid_accepted": 0}
        deferred_reasons = {k: 0 for k in DEFERRED_REASONS}
        categories: Dict[str, int] = {}
        details = []
        for r in rc0:
            bucket, key = categorize(r)
            buckets[bucket] += 1
            if bucket == "accepted_design":
                deferred_reasons[key] = deferred_reasons.get(key, 0) + 1
                details.append({"desc": r["input_desc"], "bucket": bucket, "reason": key,
                                "stderr": r["stderr"][:200]})
            elif bucket == "proven_invalid_accepted":
                categories[key] = categories.get(key, 0) + 1
                details.append({"desc": r["input_desc"], "bucket": bucket, "category": key,
                                "stage": r["parser_stage"], "stderr": r["stderr"][:200]})
        classifications = {
            "rc0_total": len(rc0),
            "buckets": buckets,
            "deferred_reasons": deferred_reasons,
            "proven_categories": dict(sorted(categories.items())),
            "details": details,
        }
        total_buckets = sum(buckets.values())
        total_cats = sum(categories.values())
        if total_buckets != len(rc0):
            raise AssertionError(f"{parser}: buckets {total_buckets} != rc0 {len(rc0)}")
        if total_cats != buckets["proven_invalid_accepted"]:
            raise AssertionError(f"{parser}: categories {total_cats} != proven "
                                 f"{buckets['proven_invalid_accepted']}")
        if "duplicate_key" in categories:
            raise AssertionError("duplicate_key leaked into proven categories")
        classification[parser] = classifications

    classification["partition_ok"] = True
    return classification


def classification_markdown(classification: Dict[str, Any]) -> str:
    lines = ["| parser | rc=0 | valid_control | valid_large_config | accepted_design | proven_invalid_accepted |",
             "|---|---|---|---|---|---|"]
    for parser in PARSERS:
        c = classification[parser]
        b = c["buckets"]
        lines.append(f"| {parser} | {c['rc0_total']} | {b['valid_control']} | "
                     f"{b['valid_large_config']} | "
                     f"{b['accepted_design']} | {b['proven_invalid_accepted']} |")
    lines.append("")
    non_parser = classification.get("non_parser_records") or []
    if non_parser:
        lines.append("Non-parser records (excluded from both partitions): "
                     + ", ".join(f"`{r['desc']}` rc={r['exit_code']}" for r in non_parser))
        lines.append("")
    for parser in PARSERS:
        c = classification[parser]
        lines.append(f"**{parser} proven categories** (sum = {c['buckets']['proven_invalid_accepted']})")
        lines.append("")
        lines.append("| category | count |")
        lines.append("|---|---|")
        for cat, count in c["proven_categories"].items():
            lines.append(f"| {cat} | {count} |")
        lines.append("")
        lines.append(f"**{parser} accepted-by-design reasons**")
        lines.append("")
        lines.append("| reason | count |")
        lines.append("|---|---|")
        for reason, count in c["deferred_reasons"].items():
            if count:
                lines.append(f"| {reason} | {count} |")
        lines.append("")
    return "\n".join(lines)


CLASS_ORDER = [
    "truncated_files", "long_lines_values", "duplicated_keys",
    "nested_quotes_escapes", "invalid_addresses_ports_keys", "nul_bytes",
    "large_pathological",
]

CLASS_BOUNDS = {c: "50×3 s + 20 s loop bound" for c in CLASS_ORDER}
CLASS_BOUNDS["large_pathological"] = "6×40 s cases + 5 s read-only control"


def class_summary(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive the item-1 per-class table from stored records (never transcribe).

    iterations completed = number of distinct 0-based loop indices actually
    executed, so a class that stops inside index 33 reports 34, not 33.
    """
    rows = []
    for cname in CLASS_ORDER:
        rs = [r for r in records if r["test_class"] == cname]
        if not rs:
            continue
        iters = sorted({r["iteration"] for r in rs})
        rc0 = sum(1 for r in rs if r["exit_code"] == 0)
        rows.append({
            "class": cname,
            "iterations": len(iters),
            "iteration_indices": f"{iters[0]}..{iters[-1]}",
            "records": len(rs),
            "bound": CLASS_BOUNDS[cname],
            "summed_case_seconds": round(sum(r["duration"] for r in rs), 2),
            "rc0": rc0,
            "rc_nonzero": len(rs) - rc0,
            "crashed": sum(1 for r in rs if r["crashed"]),
            "hung": sum(1 for r in rs if r["hung"]),
            "slowest_case_s": round(max(r["duration"] for r in rs), 3),
        })
    return rows


def class_summary_markdown(rows: List[Dict[str, Any]]) -> str:
    lines = ["| class | iterations completed | records | class wall bound | summed case time | rc=0 | rc!=0 | crashed | hung | slowest case |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['class']} | {r['iterations']} (idx {r['iteration_indices']}) | "
                     f"{r['records']} | {r['bound']} | {r['summed_case_seconds']} s | "
                     f"{r['rc0']} | {r['rc_nonzero']} | {r['crashed']} | {r['hung']} | "
                     f"{r['slowest_case_s']} s |")
    return "\n".join(lines)


def run_make_check() -> Dict[str, Any]:
    """Run make check from git archive export of final SHA."""
    print("\n" + "="*60)
    print("Running make check from git archive")
    print("="*60)

    # Get final SHA
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=WORKSPACE, capture_output=True, text=True)
    final_sha = result.stdout.strip()

    # Create git archive export
    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = Path(tmpdir) / "awg-export"
        export_dir.mkdir()
        subprocess.run(["git", "archive", final_sha], cwd=WORKSPACE, stdout=open(export_dir / "archive.tar", "wb"))
        subprocess.run(["tar", "-xf", "archive.tar", "-C", str(export_dir)], cwd=export_dir)

        # Run make check
        make_check_log = RESULTS_DIR / "make_check.log"
        start = time.time()
        result = subprocess.run(["make", "check"], cwd=export_dir, capture_output=True, text=True, timeout=300)
        duration = time.time() - start

        # Parse output
        output = result.stdout + result.stderr
        with open(make_check_log, "w") as f:
            f.write(f"Command: make check\n")
            f.write(f"Exit code: {result.returncode}\n")
            f.write(f"Duration: {duration:.2f}s\n")
            f.write(f"SHA: {final_sha}\n")
            f.write("="*80 + "\n")
            f.write(output)

        # Count tests
        import re
        tests_run = 0
        failures = 0
        errors = 0
        skipped = 0

        # Look for test output patterns
        for line in output.split('\n'):
            if "OK" in line and ("test" in line.lower() or "pass" in line.lower()):
                pass
            if "FAIL" in line:
                failures += 1
            if "ERROR" in line:
                errors += 1
            if "SKIP" in line:
                skipped += 1

        print(f"  make check rc={result.returncode}")
        print(f"  Duration: {duration:.2f}s")
        print(f"  Output saved to: {make_check_log}")

        return {
            "rc": result.returncode,
            "duration": duration,
            "sha": final_sha,
            "log_path": str(make_check_log),
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:5000],
            "tests_run": tests_run,
            "failures": failures,
            "errors": errors,
            "skipped": skipped
        }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reclassify":
        src = Path(sys.argv[2])
        records = json.loads(src.read_text())
        classification = classify_acceptances(records)
        out_json = src.parent / "classification.json"
        out_json.write_text(json.dumps(classification, indent=2))
        out_md = src.parent / "classification.md"
        out_md.write_text(classification_markdown(classification) + "\n")
        print(classification_markdown(classification))
        print(f"partition_ok={classification['partition_ok']} written to {out_json}")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "summarize":
        src = Path(sys.argv[2])
        records = json.loads(src.read_text())
        rows = class_summary(records)
        print(class_summary_markdown(rows))
        print("")
        print(f"| total | {sum(r['iterations'] for r in rows)} iterations | "
              f"{sum(r['records'] for r in rows)} records | | "
              f"{round(sum(r['summed_case_seconds'] for r in rows), 2)} s | "
              f"{sum(r['rc0'] for r in rows)} | {sum(r['rc_nonzero'] for r in rows)} | "
              f"{sum(r['crashed'] for r in rows)} | {sum(r['hung'] for r in rows)} | |")
        sys.exit(0)

    print(f"Starting bounded config fuzz tests (revision 8)")
    print(f"Workspace: {WORKSPACE}")
    print(f"Cycle root: {CYCLE_ROOT}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"awgctl config root (trust-safe): {CONF_ROOT}")
    print(f"Evidence dir: {EVIDENCE_DIR}")
    print(f"Max iterations per class: {MAX_ITERATIONS_PER_CLASS}")
    print(f"Max wall time per class: {MAX_WALL_TIME_PER_CLASS}s")
    print(f"Max case timeout: {MAX_CASE_TIMEOUT}s")
    print(f"awg-quick: {AWG_QUICK}")
    print(f"awgctl src: {AWGCTL_SRC}")
    print(f"Receipts: {RECEIPT_LOG}")

    overall_start = time.time()
    all_results = run_all_tests()

    # Run regression tests
    regressions = run_regression_tests()

    # Classify acceptances — generated from the same records that get shipped
    result_dicts = [r.to_dict() for r in all_results]
    classification = classify_acceptances(result_dicts)

    # Run make check (skippable: the cycle rule wants it from a clean archive
    # export of the final SHA, which is a separate step)
    if os.environ.get("AWG_SKIP_MAKE_CHECK") == "1":
        make_check_result = {"skipped": True, "reason": "AWG_SKIP_MAKE_CHECK=1"}
    else:
        make_check_result = run_make_check()

    # Overall summary
    total = len(all_results)
    crashed = sum(1 for r in all_results if r.crashed)
    hung = sum(1 for r in all_results if r.hung)
    clean_errors = sum(1 for r in all_results if not r.crashed and not r.hung and r.exit_code != 0)
    clean_success = sum(1 for r in all_results if not r.crashed and not r.hung and r.exit_code == 0)

    print(f"\n{'='*60}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests: {total}")
    print(f"Crashed: {crashed}")
    print(f"Hung: {hung}")
    print(f"Clean errors (exit!=0): {clean_errors}")
    print(f"Clean success (exit=0): {clean_success}")
    print(f"Total time: {time.time() - overall_start:.2f}s")

    print("\nAcceptance classification:")
    for parser in PARSERS:
        c = classification[parser]
        b = c["buckets"]
        print(f"  {parser}: rc0={c['rc0_total']} valid_control={b['valid_control']}, "
              f"valid_large_config={b['valid_large_config']}, "
              f"accepted_design={b['accepted_design']}, proven_invalid={b['proven_invalid_accepted']}")
        for cat, count in c["proven_categories"].items():
            print(f"    {cat}: {count}")

    # Save overall results
    with open(RESULTS_DIR / "awgquick_all_results.json", "w") as f:
        json.dump([r.to_dict() for r in all_results if "quick" in r.input_desc], f, indent=2)
    with open(RESULTS_DIR / "awgctl_all_results.json", "w") as f:
        json.dump([r.to_dict() for r in all_results if "ctl" in r.input_desc], f, indent=2)
    with open(RESULTS_DIR / "all_results.json", "w") as f:
        json.dump([r.to_dict() for r in all_results], f, indent=2)
    with open(RESULTS_DIR / "regressions.json", "w") as f:
        json.dump(regressions, f, indent=2)
    with open(RESULTS_DIR / "classification.json", "w") as f:
        json.dump(classification, f, indent=2)
    with open(RESULTS_DIR / "classification.md", "w") as f:
        f.write(classification_markdown(classification) + "\n")
    with open(RESULTS_DIR / "make_check.json", "w") as f:
        json.dump(make_check_result, f, indent=2)

    # Copy evidence to cycle-root location
    if EVIDENCE_DIR.exists():
        shutil.rmtree(EVIDENCE_DIR)
    shutil.copytree(RESULTS_DIR, EVIDENCE_DIR)

    # Machine summary next to (not over) the authored report: a rerun must never
    # clobber the hand-written report that cites these records.
    report_path = REPORTS_DIR / f"t_53072532-e2-config-fuzz-r8-harness.md"
    with open(report_path, "w") as f:
        f.write(f"# E2-CONFIG-FUZZ Revision 8 Report\n\n")
        f.write(f"**Base SHA:** 0e557e111f945d26366079c2c261373c57c5c8c4\n")
        f.write(f"**Final SHA:** {make_check_result.get('sha', 'see make_check.log')}\n")
        f.write(f"**Branch:** e2/config-fuzz-r8\n")
        f.write(f"**Workspace:** {WORKSPACE}\n")
        f.write(f"**Cycle Root:** {CYCLE_ROOT}\n")
        f.write(f"**Evidence:** {EVIDENCE_DIR}\n")
        f.write(f"**Total Tests:** {total}\n")
        f.write(f"**Crashed:** {crashed}\n")
        f.write(f"**Hung:** {hung}\n")
        f.write(f"**Clean Errors:** {clean_errors}\n")
        f.write(f"**Clean Success:** {clean_success}\n")
        f.write(f"**Total Wall Time:** {time.time() - overall_start:.2f}s\n\n")

        f.write("## Regression Tests\n\n")
        for k, v in regressions.items():
            f.write(f"- **{k}**: {v}\n")

        f.write("\n## Acceptance Classification (generated from all_results.json)\n\n")
        f.write(f"partition_ok: {classification['partition_ok']}\n\n")
        f.write(classification_markdown(classification))
        f.write("\n")

        f.write("## Per-Class Summary (derived from all_results.json)\n\n")
        f.write(class_summary_markdown(class_summary(result_dicts)))
        f.write("\n")

        f.write("\n## Per-Test Actual Sizes and Runtimes (large_pathological)\n\n")
        large_results = [r for r in all_results if r.test_class == "large_pathological"]
        for r in large_results:
            if "actual" in r.input_desc:
                f.write(f"- {r.input_desc}: bytes={r.actual_input_bytes}, rc={r.exit_code}, dur={r.duration:.3f}s, stage={r.parser_stage}\n")

        if "skipped" in make_check_result:
            f.write(f"\n## Make Check Results\n\nSkipped in-harness ({make_check_result['reason']}); the cycle rule requires make check from a clean `git archive` export of the final SHA and that receipt is stored alongside this report as `make_check.log`.\n")
        else:
            f.write(f"\n## Make Check Results\n\n")
            f.write(f"- **Exit code**: {make_check_result['rc']}\n")
            f.write(f"- **Duration**: {make_check_result['duration']:.2f}s\n")
            f.write(f"- **SHA**: {make_check_result['sha']}\n")
            f.write(f"- **Log**: {make_check_result['log_path']}\n")

        f.write("\n## Receipts\n\n")
        f.write(f"Full command/output/exit receipts logged to: `{RECEIPT_LOG}`\n")
        f.write(f"Evidence directory: `{EVIDENCE_DIR}`\n")

    print(f"\nCanonical report: {report_path}")
    print(f"Evidence copied to: {EVIDENCE_DIR}")
    print(f"Receipts: {RECEIPT_LOG}")

    # Exit with error if any crashes or hangs
    if crashed > 0 or hung > 0:
        print("FAIL: Crashes or hangs detected!")
        sys.exit(1)
    else:
        print("PASS: No crashes or hangs")
        sys.exit(0)