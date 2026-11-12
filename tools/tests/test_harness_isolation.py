"""E2-HARNESS-PARALLEL R1: outputs land in private per-run roots.

The R1 review found that the audit missed three sinks that wrote into shared
or source-tree locations: the junk-snapshot evidence files (fixed sibling
`evidence/kernel-junk/harness_{patched,prepatch}.c`), the installer
simulation's `make` shim (objects in `sbin/awgctl/obj`), and `make check-py`
(Python bytecode in `tools/**/__pycache__`). Two workers running the suite at
once overwrote each other's artifacts there.

These tests are the behavioural regression for that fix: they run the real
sinks twice under two private TMPDIRs and assert (a) the outputs are disjoint,
(b) the source tree gains nothing, and (c) the lab HTTP server reserves the
port it reports (bind(0) is atomic, so no close-before-spawn window).
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "tools" / "lab"
SHIM = ROOT / "tools" / "tests" / "shims" / "make"
PY = os.environ.get("PYTHON", sys.executable)


def git_status_untracked():
    """Untracked paths (including ignored files) of this worktree."""
    r = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                        "--untracked-files=all"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return {line[3:].rstrip("/") for line in r.stdout.splitlines() if line}


def install_sh_function(name):
    """Extract one shell function's body from install.sh (same trick as the
    installer transaction tests: the real script text is the unit)."""
    src = (ROOT / "install.sh").read_text().splitlines()
    start = None
    for i, line in enumerate(src):
        if line.startswith(name + "() {"):
            start = i
            break
    if start is None:
        raise AssertionError(f"{name}() not found in install.sh")
    out = []
    for line in src[start:]:
        out.append(line)
        if line == "}" and len(out) > 1:
            break
    return "\n".join(out)


class JunkEvidenceRoot(unittest.TestCase):
    """The junk-snapshot harness writes its evidence to a TMPDIR-derived root."""

    def run_module(self, tmpdir):
        env = dict(os.environ, TMPDIR=tmpdir, PYTHONDONTWRITEBYTECODE="1")
        env.pop("AWG_EVIDENCE_DIR", None)
        r = subprocess.run([PY, "-B", "-m", "unittest",
                            "tools.tests.test_junk_torn_snapshot"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_two_runs_get_disjoint_evidence_roots(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.run_module(a)
            self.run_module(b)
            fa = Path(a) / "awg-evidence" / "kernel-junk"
            fb = Path(b) / "awg-evidence" / "kernel-junk"
            for d in (fa, fb):
                self.assertTrue((d / "harness_patched.c").is_file(),
                                f"missing evidence under {d}")
                self.assertTrue((d / "harness_prepatch.c").is_file(),
                                f"missing evidence under {d}")
            # The two runs compiled the same tree, so the bytes match; the
            # paths must be distinct files under their own private roots.
            self.assertEqual((fa / "harness_patched.c").read_text(),
                             (fb / "harness_patched.c").read_text())
            self.assertNotEqual(fa.resolve(), fb.resolve())
            # The fixed sibling sink of the pre-fix harness is not used.
            sibling = ROOT.parent / "evidence" / "kernel-junk"
            if sibling.is_dir():
                before = {p.name for p in sibling.iterdir()}
                self.run_module(a)
                self.assertEqual({p.name for p in sibling.iterdir()}, before)

    def test_evidence_dir_override_wins(self):
        with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as e:
            env = dict(os.environ, TMPDIR=t, AWG_EVIDENCE_DIR=e,
                       PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run([PY, "-B", "-m", "unittest",
                                "tools.tests.test_junk_torn_snapshot"],
                               cwd=ROOT, env=env, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((Path(e) / "harness_patched.c").is_file())
            self.assertFalse((Path(t) / "awg-evidence").exists())


class SourceTreeCleanliness(unittest.TestCase):
    """Test/build outputs leave the checkout clean (ignored files included)."""

    def test_check_py_writes_no_bytecode(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, TMPDIR=t)
            r = subprocess.run(["make", "check-py"], cwd=ROOT, env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            caches = sorted(p.relative_to(ROOT).as_posix()
                            for p in ROOT.glob("tools/**/__pycache__"))
            self.assertEqual(caches, [], f"bytecode in source tree: {caches}")

    def test_make_shim_honours_objdir(self):
        with tempfile.TemporaryDirectory() as t:
            w = Path(t)
            obj = w / "obj"
            cmdfile = w / "cmdfile"
            env = dict(os.environ, CMDFILE=str(cmdfile), TMPDIR=t)
            before = git_status_untracked()
            r = subprocess.run([shutil.which("sh") or "/bin/sh", str(SHIM),
                                f"OBJDIR={obj}", "CPPFLAGS=-I" + str(ROOT / "src")],
                               cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in ("ifconfig", "bsd", "awgctl", "probe.s"):
                self.assertTrue((obj / name).exists(), f"{name} not in OBJDIR")
            # Every artifact went to OBJDIR: the source tree gains nothing.
            self.assertEqual(git_status_untracked(), before)
            # `-V .OBJDIR` answers the requested directory, not $PWD/obj.
            v = subprocess.run([shutil.which("sh") or "/bin/sh", str(SHIM),
                                "-V", ".OBJDIR", f"OBJDIR={obj}"],
                               cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(v.returncode, 0, v.stderr)
            self.assertEqual(v.stdout.strip(), str(obj))

    def test_installer_awgctl_build_uses_per_run_objdir(self):
        """install_awgctl() runs the builder with OBJDIR under $TMPDIR."""
        body = install_sh_function("install_awgctl")
        with tempfile.TemporaryDirectory() as t:
            w = Path(t)
            cmdfile = w / "cmdfile"
            script = f'''
set -u
REPODIR="{ROOT}"
CMDFILE="{cmdfile}"
step() {{ :; }}
log() {{ :; }}
die() {{ echo "die: $1" >&2; exit 1; }}
hand_tree_to_builder() {{ :; }}
builder_make() {{ ( cd "$1" && shift && sh "{SHIM}" "$@" ); }}
{body}
install_awgctl
echo "ARTIFACT=$AWGCTL_ARTIFACT"
'''
            (w / "run.sh").write_text(script)
            env = dict(os.environ, CMDFILE=str(cmdfile), TMPDIR=str(w))
            before = git_status_untracked()
            r = subprocess.run([shutil.which("sh") or "/bin/sh", str(w / "run.sh")],
                               cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            recorded = cmdfile.read_text()
            self.assertIn("OBJDIR=", recorded,
                          f"builder_make did not pass an OBJDIR: {recorded}")
            art = [ln for ln in r.stdout.splitlines()
                   if ln.startswith("ARTIFACT=")]
            self.assertTrue(art, r.stdout)
            artifact = art[0][len("ARTIFACT="):].strip()
            self.assertTrue(artifact.startswith(str(w)),
                            f"artifact {artifact} not under the private TMPDIR {w}")
            self.assertTrue(Path(artifact).is_file())
            self.assertFalse(Path(artifact).parent == ROOT / "sbin" / "awgctl")
            self.assertEqual(git_status_untracked(), before)

    def test_status_stays_clean_across_two_fixture_runs(self):
        """Two fixture runs in one tree leave the same untracked set."""
        before = git_status_untracked()
        state = f"{LAB}/fixture-ifconfig.py"
        for mtu in ("1500", "1460"):
            with tempfile.TemporaryDirectory() as t:
                env = dict(os.environ, TMPDIR=t)
                env.pop("AWGFIX_STATE", None)
                r = subprocess.run([PY, "-B", state, "awg9", "mtu", mtu],
                                   cwd=ROOT, env=env, capture_output=True,
                                   text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"mtu={mtu}",
                              (Path(t) / "awgctl-fixture.state").read_text())
        self.assertEqual(git_status_untracked(), before)


class HttpPortAllocation(unittest.TestCase):
    """The lab HTTP server reserves the port it reports (no TOCTOU window)."""

    def setUp(self):
        sys.path.insert(0, str(LAB))
        import http_server
        self.mod = http_server

    def tearDown(self):
        sys.path.remove(str(LAB))

    def fetch(self, port, path="/"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
            data = s.recv(65536)
        return data.split(b"\r\n", 1)[0].decode()

    def test_ephemeral_bind_is_live_and_reserved(self):
        with tempfile.TemporaryDirectory() as d:
            proc, port = self.mod.start_http_server(d)
            try:
                self.assertIn("200", self.fetch(port))
                # The port is held by the server's own socket: a competing
                # bind fails, which is exactly the pre-fix TOCTOU window.
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.bind(("0.0.0.0", port))
                    self.fail("a competing bind took the server's port")
                except OSError:
                    pass
                finally:
                    s.close()
            finally:
                self.mod.stop_http_server(proc)
            self.assertIsNotNone(proc.poll(), "server not reaped")
            # After the stop the port is free again.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", port))
            s.close()

    def test_occupied_port_is_retried_and_survives(self):
        with tempfile.TemporaryDirectory() as d:
            holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            holder.bind(("0.0.0.0", 0))
            holder.listen(1)
            busy = holder.getsockname()[1]
            proc, port = self.mod.start_http_server(d, port=busy)
            try:
                self.assertEqual(port, busy + 1,
                                 "server did not fall back to the next port")
                self.assertIn("200", self.fetch(port))
                # The original holder still owns its port (accept queue works).
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("127.0.0.1", busy))
                s.close()
            finally:
                self.mod.stop_http_server(proc)
                holder.close()

    def test_failed_attempts_are_reaped(self):
        """Every failed start is terminated; no orphan keeps a port."""
        with tempfile.TemporaryDirectory() as d:
            holders = []
            first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            first.bind(("0.0.0.0", 0))
            first.listen(1)
            holders.append(first)
            busy = first.getsockname()[1]
            # Occupy the two ports after it as well, so the walk has to take
            # three failed binds before it lands on busy+3.
            for offset in (1, 2):
                h = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                h.bind(("0.0.0.0", busy + offset))
                h.listen(1)
                holders.append(h)
            proc, port = self.mod.start_http_server(d, port=busy, attempts=4)
            try:
                self.assertEqual(port, busy + 3,
                                 "retries did not walk busy..busy+2, busy+3")
                self.assertIn("200", self.fetch(port))
                self.assertIsNone(proc.poll(), "live server exited")
            finally:
                self.mod.stop_http_server(proc)
                self.assertIsNotNone(proc.poll())
                for h in holders:
                    h.close()


if __name__ == "__main__":
    unittest.main()
