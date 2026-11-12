#!/usr/bin/env python3
"""Generate the E2 conformance translation unit from the production sources.

Nothing in this directory re-states production logic.  Every struct
definition, constant and function body that e2_harness compiles is copied
*verbatim* out of ``src/`` at build time, and emitted with ``#line``
directives that point back at the production file and line.  Two
consequences matter for the vectors in ``tools/tests/test_e2_vectors.py``:

* if ``src/`` is missing, extraction fails and the suite errors out;
* if production renames a field, resizes a message struct, changes a
  constant or edits the junk / counter / KDF logic, the compiled copy
  changes with it and the expected values in the test suite stop matching.

The generated files are build products (``gen/`` is not committed).
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# source slicing helpers
# --------------------------------------------------------------------------


class ExtractError(Exception):
    pass


class Source:
    def __init__(self, root, relpath):
        self.relpath = relpath
        path = root / relpath
        if not path.is_file():
            raise ExtractError("missing production source: %s" % relpath)
        self.lines = path.read_text().splitlines()

    def find(self, needle, count=1):
        hits = [i for i, line in enumerate(self.lines) if needle in line]
        if len(hits) != count:
            raise ExtractError(
                "%s: expected %d occurrence(s) of %r, found %d"
                % (self.relpath, count, needle, len(hits))
            )
        return hits

    def line(self, needle):
        return self.lines[self.find(needle)[0]]

    def block(self, start_needle, end_needle):
        """Return (start_idx, end_idx_inclusive) from start marker to end."""
        start = self.find(start_needle)[0]
        for end in range(start, len(self.lines)):
            if end_needle in self.lines[end]:
                return start, end
        raise ExtractError(
            "%s: no %r after line %d" % (self.relpath, end_needle, start + 1)
        )

    def function(self, name, rettype):
        """Return (start_idx, end_idx_inclusive) for a function definition.

        The opening brace is located on the signature line or the one that
        follows it; braces are then balanced to the closing one.
        """
        cand = [
            i
            for i, line in enumerate(self.lines)
            if re.match(r"^%s\(" % re.escape(name), line)
        ]
        if len(cand) != 1:
            raise ExtractError(
                "%s: expected exactly one definition of %s(), found %d"
                % (self.relpath, name, len(cand))
            )
        start = cand[0]
        depth = 0
        seen_open = False
        for idx in range(start, len(self.lines)):
            code = _strip_strings_and_comments(self.lines[idx])
            depth += code.count("{")
            if "{" in code:
                seen_open = True
            depth -= code.count("}")
            if seen_open and depth == 0:
                return start, idx
        raise ExtractError("%s: unbalanced braces in %s()" % (self.relpath, name))


def _strip_strings_and_comments(line):
    line = re.sub(r'/\*.*?\*/', "", line)
    line = re.sub(r'//.*$', "", line)
    line = re.sub(r'"(\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(\\.|[^'\\])*'", "''", line)
    return line


def emit_block(out, src, first, last, comment):
    text = "\n".join(src.lines[first : last + 1])
    out.append("/* ---- verbatim from %s:%d-%d (%s) ---- */" % (
        src.relpath, first + 1, last + 1, comment))
    out.append('#line %d "%s"' % (first + 1, src.relpath))
    out.append(text)
    out.append('#line 1 "gen/%s"' % Path(out.name if hasattr(out, "name") else "e2_harness.c").name)
    out.append("")


# --------------------------------------------------------------------------
# extraction plan
# --------------------------------------------------------------------------

#: single lines copied out of the production text
DEFINE_NEEDLES = {
    "src/awg_noise.h": [
        "#define NOISE_PUBLIC_KEY_LEN",
        "#define NOISE_SYMMETRIC_KEY_LEN",
        "#define NOISE_TIMESTAMP_LEN",
        "#define NOISE_AUTHTAG_LEN",
        "#define NOISE_HASH_LEN",
        "#define COUNTER_BITS_TOTAL",
        "#define COUNTER_BITS\t\t",
        "#define COUNTER_NUM",
        "#define COUNTER_WINDOW_SIZE",
        "#define REJECT_AFTER_MESSAGES",
    ],
    "src/if_awg.c": [
        "#define AWG_MAX_JUNK\t",
        "#define AWG_MAX_JUNK_SIZE",
        "#define AWG_MIN_DATA_SIZE",
        "#define AWG_TYPE_INITIATION",
        "#define AWG_TYPE_RESPONSE",
        "#define AWG_TYPE_COOKIE",
        "#define AWG_TYPE_DATA",
    ],
}

#: struct definitions copied out of the production text
STRUCT_NEEDLES = {
    "src/if_awg.c": [
        "struct awg_pkt_initiation {",
        "struct awg_pkt_response {",
        "struct awg_pkt_cookie {",
        "struct awg_pkt_data {",
    ],
    "src/awg_noise.h": [
        "struct awg_noise_counter {",
    ],
}

#: (file, function, return type as written in the source)
FUNCTIONS = {
    "src/if_awg.c": [("awg_send_junk", "void")],
    "src/awg_noise.c": [
        ("awg_noise_counter_recv", "static int"),
        ("awg_noise_kdf", "static void"),
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="repository root")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = {"blocks": [], "root": str(root)}

    def record(relpath, first, last, kind, name):
        text = "\n".join(Source(root, relpath).lines[first : last + 1])
        manifest["blocks"].append({
            "file": relpath,
            "first_line": first + 1,
            "last_line": last + 1,
            "kind": kind,
            "name": name,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        })

    # ---- defines ---------------------------------------------------------
    lines = []
    for relpath, needles in DEFINE_NEEDLES.items():
        src = Source(root, relpath)
        lines.append("/* ---- #define block from %s ---- */" % relpath)
        lines.append('#line 1 "%s"' % relpath)
        for needle in needles:
            idx = src.find(needle, count=1)[0]
            lines.append(src.lines[idx])
            record(relpath, idx, idx, "define", needle.strip())
        lines.append("")
    (outdir / "defines.inc").write_text("\n".join(lines) + "\n")

    # ---- structs ---------------------------------------------------------
    lines = []
    for relpath, needles in STRUCT_NEEDLES.items():
        src = Source(root, relpath)
        for needle in needles:
            first, last = src.block(needle, "};")
            emit_block(lines, src, first, last, needle.split()[1])
            record(relpath, first, last, "struct", needle.split()[1])
    (outdir / "structs.inc").write_text("\n".join(lines) + "\n")

    # ---- function bodies -------------------------------------------------
    for relpath, funcs in FUNCTIONS.items():
        src = Source(root, relpath)
        for name, rettype in funcs:
            first, last = src.function(name, rettype)
            out = [
                "/* ---- verbatim from %s:%d-%d (%s) ---- */" % (
                    relpath, first + 1, last + 1, name),
                '#line %d "%s"' % (first + 1, relpath),
                "%s %s" % (rettype, "\n".join(src.lines[first : last + 1])),
                "",
            ]
            (outdir / ("%s.inc" % name)).write_text("\n".join(out) + "\n")
            record(relpath, first, last, "function", name)

    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    total = len(manifest["blocks"])
    print("extracted %d blocks from %d production files"
          % (total, len({b["file"] for b in manifest["blocks"]})))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExtractError as exc:
        print("extract: %s" % exc, file=sys.stderr)
        sys.exit(2)
