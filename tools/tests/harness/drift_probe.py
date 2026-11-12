"""Drift probes for the E2 vector harness.

usage: python3 drift_probe.py <export-dir> <case> <outfile>

Each case mutates production source in a *private copy* of the export and
runs the vector suite there.  A conformance suite that is actually linked
to src/ must fail for every case; the exit status is appended to <outfile>.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = {
    # widen a message field without changing the line count
    "struct-width": (
        "src/if_awg.c",
        "\tuint8_t\t\t\tue[NOISE_PUBLIC_KEY_LEN];\n"
        "\tuint8_t\t\t\tes[NOISE_PUBLIC_KEY_LEN + NOISE_AUTHTAG_LEN];",
        "\tuint8_t\t\t\tue[NOISE_PUBLIC_KEY_LEN + 4];\n"
        "\tuint8_t\t\t\tes[NOISE_PUBLIC_KEY_LEN + NOISE_AUTHTAG_LEN];",
    ),
    # widen the receive window
    "counter-window": (
        "src/awg_noise.h",
        "#define COUNTER_BITS_TOTAL\t8192",
        "#define COUNTER_BITS_TOTAL\t4096",
    ),
    # shrink the junk buffer bound
    "junk-bound": (
        "src/if_awg.c",
        "#define AWG_MAX_JUNK_SIZE\t1500",
        "#define AWG_MAX_JUNK_SIZE\t9999",
    ),
    # change the responder discriminator
    "message-type": (
        "src/if_awg.c",
        "#define AWG_TYPE_RESPONSE\t2",
        "#define AWG_TYPE_RESPONSE\t7",
    ),
}


def main():
    export, case, outfile = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    relpath, old, new = CASES[case]
    with tempfile.TemporaryDirectory(prefix="e2-drift-") as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(export, tree, symlinks=True)
        path = tree / relpath
        text = path.read_text()
        if old not in text:
            outfile.write_text("case %s: mutation anchor not found\n" % case)
            return 3
        path.write_text(text.replace(old, new, 1))
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", "tools/tests", "-p", "test_e2_vectors.py"],
            cwd=tree, capture_output=True, text=True)
        body = "case %s (%s)\n%s\n%s\nrc=%d\n" % (
            case, relpath, proc.stdout[-4000:], proc.stderr[-2000:],
            proc.returncode)
        outfile.write_text(body)
        return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
