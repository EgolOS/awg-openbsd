"""E2 — wire-format conformance vectors for the AmneziaWG driver.

Every expected value below has an origin that is independent of the code it
checks, and every actual value is produced by *production* source: the
harness in ``tools/tests/harness`` copies the message structs, the
junk/counter/KDF function bodies and the constants verbatim out of ``src/``
at build time (see ``harness/extract.py``).  Nothing here is computed by a
second copy of the logic under test.

Origins used below
------------------
LAYOUT   WireGuard protocol message formats (WireGuard whitepaper §5.4;
         wireguard-go ``device/message.go`` ``MessageInitiation`` /
         ``MessageResponse`` / ``MessageCookieReply`` / ``MessageTransport``):
         initiation 148 B, response 92 B, cookie reply 64 B, transport
         header 16 B, MAC region 32 B at the tail of the handshake messages.
TYPES    WireGuard message type discriminator values 1..4 (same source).
COUNTER  WireGuard upstream ``wg_noise.c`` AWGTEST counter sequence
         (``awg_noise_counter_test``), reproduced verbatim in this tree at
         ``src/awg_noise.c`` inside ``#ifdef AWGTEST``; expectations here are
         transcribed from the upstream test, not from the local function.
KDF      Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s KDF:
         sec = HMAC-BLAKE2s(ck, x); out1 = HMAC-BLAKE2s(sec, 0x01);
         out2 = HMAC-BLAKE2s(sec, out1 || 0x02);
         out3 = HMAC-BLAKE2s(sec, out2 || 0x03).
         Recomputed from scratch with Python ``hmac``/``hashlib``.
COOKIE   ``wg_cookie.c`` (OpenBSD net/wg_cookie.c, upstream-fetch copy)
         mac1_key = BLAKE2s("mac1----" || peer_public)   [UNKEYED]
         mac1     = BLAKE2s(mac1_key, msg[0 .. 116))            [KEYED, 16 B]
         mac2     = BLAKE2s(cookie, msg[0 .. 116) || mac1)      [KEYED, 16 B]
         i.e. mac2 hashes the message with the mac1 FIELD SUBSTITUTED; the
         raw i==i packet[0..132) yields 1f9027d2…, not the vector.
         Recomputed from scratch with Python ``hashlib.blake2s``.
BLAKE2S  RFC 7693 appendix A: BLAKE2s-256("") and BLAKE2s-256("abc").

If a check below fails, the vector and the implementation disagree; the
failure message prints both sides instead of adjusting the vector.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = ROOT / "tools" / "tests" / "harness"

EEXIST = 17
COUNTER_WINDOW_SIZE = 8128          # src/awg_noise.h:52
T_LIM = COUNTER_WINDOW_SIZE + 1      # src/awg_noise.c AWGTEST: T_LIM
REJECT_AFTER_MESSAGES = (1 << 64) - 1 - COUNTER_WINDOW_SIZE - 1


# ---------------------------------------------------------------------------
# expected vectors (independent authority, cited above)
# ---------------------------------------------------------------------------

LAYOUT = {
    # message sizes
    "layout.init.size": 148,
    "layout.resp.size": 92,
    "layout.cookie.size": 64,
    "layout.data.header": 16,
    # initiation field offsets: t, s_idx, ue[32], es[48], ets[28], macs[32]
    "layout.init.t": 0,
    "layout.init.s_idx": 4,
    "layout.init.ue": 8,
    "layout.init.es": 40,
    "layout.init.ets": 88,
    "layout.init.mac1": 116,
    "layout.init.mac2": 132,
    "layout.init.ue_len": 32,
    "layout.init.es_len": 48,
    "layout.init.ets_len": 28,
    # response: t, s_idx, r_idx, ue[32], en[16], macs[32]
    "layout.resp.ue": 12,
    "layout.resp.en": 44,
    "layout.resp.mac1": 60,
    "layout.resp.en_len": 16,
    # cookie reply: t, r_idx, nonce[24], encrypted_cookie[32]
    "layout.cookie.r_idx": 4,
    "layout.cookie.nonce": 8,
    "layout.cookie.ec": 32,
    "layout.cookie.nonce_len": 24,
    "layout.cookie.ec_len": 32,
    # transport: t, r_idx, counter[8], payload
    "layout.data.nonce_len": 8,
}

TYPES = {
    "layout.type.init": 1,
    "layout.type.resp": 2,
    "layout.type.cookie": 3,
    "layout.type.data": 4,
}

LIMITS = {
    "limits.junk.max": 64,          # src/if_awg.c AWG_MAX_JUNK
    "limits.junk.max_size": 1500,   # src/if_awg.c AWG_MAX_JUNK_SIZE
    # smallest transport message = header (16) + empty-payload tag (16)
    "limits.data.min_size": 32,
}

MAC_INPUT = {
    "mac.init.mac1_len": 116,
    "mac.init.mac2_len": 132,
    "mac.init.mac1_offset": 116,
    "mac.init.mac2_offset": 132,
}

# recomputed with hashlib.blake2s over the deterministic packet the harness
# builds (byte i == i, peer public key 00..1f, cookie a0..af).
#
# Cookie MACs are TRUNCATED to 16 bytes (COOKIE_MAC_SIZE), so every blake2s
# call below carries digest_size=16.  mac2 hashes the mac1 FIELD SUBSTITUTION
# required by wg_cookie.c cookie_macs_mac2(): the input is msg[0..116) ||
# mac1, not the raw i==i packet[0..132).  Literal reproduction:
#
#   pkt    = bytes(range(148))
#   mk     = blake2s(b"mac1----" + bytes(range(32))).digest()
#   mac1   = blake2s(pkt[:116], key=mk, digest_size=16).digest()
#   cookie = bytes(range(0xa0, 0xb0))
#   mac2   = blake2s(pkt[:116] + mac1, key=cookie, digest_size=16).digest()
#
# Dropping the substitution (blake2s(pkt[:132], key=cookie, digest_size=16))
# gives 1f9027d2095fc29ee4301d2a2c574f53 — the wrong value, asserted different
# in test_mac2_provenance so a later auditor cannot "fix" a valid vector.
MAC_KATS = {
    "mac.mac1_key":
        "4fb2527ac956001553dc1ad9b55171b7d9daa3de1cdbcd71451830170c7e6cac",
    "mac.mac1": "3a7d1b865f062f4af4677f1d88d7ea74",
    "mac.mac2": "f6a9e61375e8606d3bc7e6823f7588d4",
}

# Deliberately WRONG mac2: hashing the untouched i==i packet instead of
# substituting mac1.  Pinned here so the distinction is a checked vector.
NAIVE_MAC2_WRONG = "1f9027d2095fc29ee4301d2a2c574f53"

KDF_KATS = {
    "kdf.out1": "db87d400a747c3fbea14bb0fb3b326d5b5a199fa1a40a32776ff421ed5354599",
    "kdf.out2": "8c03cf7e8409712236b2ae3d938e83eb06b6700361d6ed88c6b3baf45eb6092d",
    "kdf.out3": "f5eaa7261cd657021f83ba104239df798cea3ccd754ff934fa531ecda13381f7",
}

BLAKE2S_KATS = {
    "blake2s.empty":
        "69217a3079908094e11121d042354a7c1f55b6482ca1a51e1b250dfd1ed0eef9",
}


def _counter_vectors():
    """Nonce sequence and expected return codes.

    Transcribed from the WireGuard upstream ``awg_noise_counter_test``
    (same sequence lives at ``src/awg_noise.c:1081-1157`` AWGTEST, cases
    1-49 — upstream ``net/wg_noise.c`` T(44)..T(49) are cases 44-49 here).
    """
    seq = [
        (0, 0), (1, 0), (1, EEXIST), (9, 0), (8, 0), (7, 0), (7, EEXIST),
        (T_LIM, 0), (T_LIM - 1, 0), (T_LIM - 1, EEXIST), (T_LIM - 2, 0),
        (2, 0), (2, EEXIST), (T_LIM + 16, 0), (3, EEXIST),
        (T_LIM + 16, EEXIST), (T_LIM * 4, 0),
        (T_LIM * 4 - (T_LIM - 1), 0), (10, EEXIST),
        (T_LIM * 4 - T_LIM, EEXIST), (T_LIM * 4 - (T_LIM + 1), EEXIST),
        (T_LIM * 4 - (T_LIM - 2), 0), (T_LIM * 4 + 1 - T_LIM, EEXIST),
        (0, EEXIST),
        (REJECT_AFTER_MESSAGES, EEXIST), (REJECT_AFTER_MESSAGES - 1, 0),
        (REJECT_AFTER_MESSAGES, EEXIST),
        (REJECT_AFTER_MESSAGES - 1, EEXIST),
        (REJECT_AFTER_MESSAGES - 2, 0),
        (REJECT_AFTER_MESSAGES + 1, EEXIST),
        (REJECT_AFTER_MESSAGES + 2, EEXIST),
        (REJECT_AFTER_MESSAGES - 2, EEXIST),
        (REJECT_AFTER_MESSAGES - 3, 0),
        (0, EEXIST),
    ]
    # case 35: fill the whole window from 1, then accept 0 once
    seq.append(("reset", None))
    seq.extend([(i, 0) for i in range(1, COUNTER_WINDOW_SIZE + 1)])
    seq.extend([(0, 0), (0, EEXIST)])
    # case 38: start at 2, wrap to 1
    seq.append(("reset", None))
    seq.extend([(i, 0) for i in range(2, COUNTER_WINDOW_SIZE + 2)])
    seq.extend([(1, 0), (0, EEXIST)])
    # case 41: strictly descending from the top of the window
    seq.append(("reset", None))
    seq.extend([(i, 0) for i in range(COUNTER_WINDOW_SIZE, -1, -1)])
    # case 42: descending, then 0 is out of window
    seq.append(("reset", None))
    seq.extend([(i, 0) for i in range(COUNTER_WINDOW_SIZE + 1, 1, -1)])
    seq.append((0, EEXIST))
    # cases 44-46: descending from W..1, then W+1 accepted, then 0 rejected
    seq.append(("reset", None))
    seq.extend([(i, 0) for i in range(COUNTER_WINDOW_SIZE, 0, -1)])
    seq.extend([(COUNTER_WINDOW_SIZE + 1, 0), (0, EEXIST)])
    # cases 47-49: same sweep, but 0 is accepted before the W+1 step
    seq.append(("reset", None))
    seq.extend([(i, 0) for i in range(COUNTER_WINDOW_SIZE, 0, -1)])
    seq.extend([(0, 0), (COUNTER_WINDOW_SIZE + 1, 0)])
    return seq


COUNTER_VECTORS = _counter_vectors()


# ---------------------------------------------------------------------------
# harness build / run
# ---------------------------------------------------------------------------

def _build(harness_dir):
    """Configure-free build; always from scratch so no stale binary runs."""
    subprocess.run(["make", "-C", str(harness_dir), "clean"], check=False,
                   capture_output=True)
    proc = subprocess.run(["make", "-C", str(harness_dir)], check=False,
                          capture_output=True, text=True)
    return proc


def _parse(stdout):
    values = {}
    numeric = ("layout.", "limits.", "junk.", "counter.", "mac.init.")
    for line in stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            if key.startswith(numeric):
                try:
                    values[key] = int(value)
                    continue
                except ValueError:
                    pass
            values[key] = value
    return values


class _Harness:
    values = None
    counter = None
    build_log = ""

    @classmethod
    def load(cls):
        if cls.values is not None:
            return
        proc = _build(HARNESS_DIR)
        cls.build_log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError("harness build failed (rc=%d):\n%s"
                               % (proc.returncode, cls.build_log))
        stdin = "\n".join(
            "reset" if nonce == "reset" else str(nonce)
            for nonce, _ in COUNTER_VECTORS) + "\n"
        run = subprocess.run([str(HARNESS_DIR / "e2_harness")], check=False,
                             input=stdin, capture_output=True, text=True)
        if run.returncode != 0:
            raise RuntimeError("harness run failed (rc=%d):\n%s"
                               % (run.returncode, run.stderr))
        cls.values = _parse(run.stdout)
        cls.counter = [int(line.split("=", 1)[1])
                       for line in run.stdout.splitlines()
                       if line.startswith("counter.rc=")]


def setUpModule():
    _Harness.load()


def check(name, expected, actual):
    """The single comparison used by every positive and negative vector."""
    if actual != expected:
        raise AssertionError(
            "%s mismatch\n  expected: %s\n  actual:   %s"
            % (name, expected, actual))


class E2VectorTest(unittest.TestCase):
    def check_group(self, expected_map):
        for key, value in expected_map.items():
            with self.subTest(key=key):
                self.assertIn(key, _Harness.values,
                              "harness did not emit %s" % key)
                check(key, value, _Harness.values[key])


# ---------------------------------------------------------------------------
# 1. handshake message layout
# ---------------------------------------------------------------------------

class TestHandshakeLayout(E2VectorTest):
    def test_message_layout(self):
        """WireGuard message sizes and field offsets (whitepaper §5.4)."""
        self.check_group(LAYOUT)

    def test_message_types(self):
        """Type discriminators 1..4, H1-H4 default ranges."""
        self.check_group(TYPES)

    def test_mac_input_region(self):
        """MAC1 covers the initiation up to mac1; MAC2 up to mac2."""
        self.check_group(MAC_INPUT)


# ---------------------------------------------------------------------------
# 2. mac1/mac2 cookie derivation
# ---------------------------------------------------------------------------

class TestCookieMacs(E2VectorTest):
    def test_mac1_key_derivation(self):
        self.check_group({"mac.mac1_key": MAC_KATS["mac.mac1_key"]})

    def test_mac1_known_answer(self):
        """KEYED BLAKE2s(mac1_key, msg[0..116)) over production bytes."""
        check("mac.mac1", MAC_KATS["mac.mac1"], _Harness.values["mac.mac1"])

    def test_mac2_known_answer(self):
        """KEYED BLAKE2s(cookie, msg[0..116) || mac1), 16 B."""
        check("mac.mac2", MAC_KATS["mac.mac2"], _Harness.values["mac.mac2"])

    def test_mac2_provenance(self):
        """Re-derive mac1/mac2 from scratch and pin the mac1 substitution.

        wg_cookie.c ``cookie_macs_mac2()`` hashes ``msg[0..116) || mac1``:
        the mac1 field inside the message is replaced by the mac1 value
        before mac2 is taken.  Hashing the untouched deterministic packet
        ``pkt[0..132)`` produces a *different* digest; this test asserts
        both facts with stdlib blake2s so the vector cannot be "corrected"
        to the naive value later.
        """
        pkt = bytes(range(148))
        mac1_key = hashlib.blake2s(b"mac1----" + bytes(range(32))).digest()
        mac1 = hashlib.blake2s(pkt[:116], key=mac1_key,
                               digest_size=16).digest()
        cookie = bytes(range(0xa0, 0xb0))
        substituted = hashlib.blake2s(pkt[:116] + mac1, key=cookie,
                                      digest_size=16).hexdigest()
        naive = hashlib.blake2s(pkt[:132], key=cookie,
                                digest_size=16).hexdigest()

        check("mac.mac1_key (python)", MAC_KATS["mac.mac1_key"],
              mac1_key.hex())
        check("mac.mac1 (python)", MAC_KATS["mac.mac1"], mac1.hex())
        check("mac.mac2 (python, mac1 substituted)",
              MAC_KATS["mac.mac2"], substituted)
        check("mac.mac2 naive construction (must differ)",
              NAIVE_MAC2_WRONG, naive)
        self.assertNotEqual(naive, MAC_KATS["mac.mac2"],
                            "naive mac2 construction unexpectedly matches")


# ---------------------------------------------------------------------------
# 3. junk packets at the configured bound
# ---------------------------------------------------------------------------

class TestJunkPackets(E2VectorTest):
    def test_configured_limits(self):
        self.check_group(LIMITS)

    def test_junk_at_bound(self):
        """jmin == jmax == 1500 emits exactly Jc packets of 1500 bytes."""
        v = _Harness.values
        check("junk.at_bound.n", 3, v["junk.at_bound.n"])
        for i in range(3):
            check("junk.at_bound.len.%d" % i, 1500,
                  v["junk.at_bound.len.%d" % i])
            # a junk packet is opaque random payload, never NULL or empty
            check("junk.at_bound.null.%d" % i, 0,
                  v["junk.at_bound.null.%d" % i])
            check("junk.at_bound.nz.%d" % i, 1,
                  v["junk.at_bound.nz.%d" % i])

    def test_junk_guards(self):
        """Bound is exactly the 1500-byte build buffer; bad configs send 0."""
        v = _Harness.values
        check("junk.over_bound.n", 0, v["junk.over_bound.n"])   # jmax=1501
        check("junk.jc_zero.n", 0, v["junk.jc_zero.n"])
        check("junk.jmin_zero.n", 0, v["junk.jmin_zero.n"])
        check("junk.inverted.n", 0, v["junk.inverted.n"])       # jmax<jmin

    def test_junk_random_range(self):
        """jmin/jmax range is honoured for every emitted packet."""
        v = _Harness.values
        check("junk.range.n", 4, v["junk.range.n"])
        for i in range(4):
            length = int(v["junk.range.len.%d" % i])
            self.assertTrue(100 <= length <= 200,
                            "packet %d length %d outside [100,200]"
                            % (i, length))


# ---------------------------------------------------------------------------
# 4. key and counter handling
# ---------------------------------------------------------------------------

class TestKeyAndCounter(E2VectorTest):
    def test_kdf_known_answers(self):
        """Noise KDF extract/expand against stdlib HMAC-BLAKE2s."""
        for key, value in KDF_KATS.items():
            with self.subTest(key=key):
                check(key, value, _Harness.values[key])

    def test_counter_reference_sequence(self):
        """Upstream WireGuard counter test: 49 cases + window sweeps."""
        expected = [code for _, code in COUNTER_VECTORS
                    if code is not None]
        actual = _Harness.counter
        self.assertEqual(len(actual), len(expected),
                         "harness returned %d counter results, expected %d"
                         % (len(actual), len(expected)))
        for index, (want, got) in enumerate(zip(expected, actual), 1):
            with self.subTest(case=index):
                check("counter case %d" % index, want, got)

    def test_counter_resets(self):
        self.assertEqual(_Harness.values.get("counter.reset"), "ok")


# ---------------------------------------------------------------------------
# 5. crypto provider self-check (the harness's own primitive)
# ---------------------------------------------------------------------------

class TestPrimitives(E2VectorTest):
    def test_blake2s_rfc7693(self):
        self.check_group(BLAKE2S_KATS)


# ---------------------------------------------------------------------------
# 6. negative controls — the harness must be able to fail
# ---------------------------------------------------------------------------

class TestNegativeControls(E2VectorTest):
    def test_mutated_expected_vector_fails_same_check(self):
        """A corrupted expected value must fail the very same comparison."""
        actual = _Harness.values["mac.mac1"]
        check("mac.mac1", MAC_KATS["mac.mac1"], actual)  # valid control

        mutated = ("0" if MAC_KATS["mac.mac1"][0] != "0" else "1") + \
            MAC_KATS["mac.mac1"][1:]
        self.assertNotEqual(mutated, MAC_KATS["mac.mac1"])
        with self.assertRaises(AssertionError):
            check("mac.mac1", mutated, actual)

    def test_mutated_layout_vector_fails_same_check(self):
        actual = int(_Harness.values["layout.init.size"])
        check("layout.init.size", 148, actual)
        with self.assertRaises(AssertionError):
            check("layout.init.size", 149, actual)

    def test_production_drift_is_detected(self):
        """Mutating src/ must change what the harness reports.

        A private copy of the tree gets two production edits
        (AWG_MAX_JUNK 64 -> 3, AWG_TYPE_RESPONSE 2 -> 7); the harness is
        rebuilt from that copy and the same checks must fail.
        """
        with tempfile.TemporaryDirectory(prefix="e2-drift-") as tmp:
            tmp = Path(tmp)
            shutil.copytree(ROOT / "src", tmp / "src", symlinks=True)
            shutil.copytree(HARNESS_DIR, tmp / "tools" / "tests" / "harness")
            src = tmp / "src" / "if_awg.c"
            text = src.read_text()
            mutated = text.replace("#define AWG_MAX_JUNK\t\t64",
                                   "#define AWG_MAX_JUNK\t\t3", 1)
            mutated = mutated.replace("#define AWG_TYPE_RESPONSE\t2",
                                      "#define AWG_TYPE_RESPONSE\t7", 1)
            self.assertNotEqual(text, mutated, "drift mutation did not apply")
            src.write_text(mutated)

            harness = tmp / "tools" / "tests" / "harness"
            proc = _build(harness)
            self.assertEqual(proc.returncode, 0,
                             "mutated tree failed to build:\n%s\n%s"
                             % (proc.stdout, proc.stderr))
            run = subprocess.run([str(harness / "e2_harness")], check=False,
                                 input="", capture_output=True, text=True)
            values = _parse(run.stdout)
            self.assertEqual(values["limits.junk.max"], 3)
            self.assertEqual(values["layout.type.resp"], 7)
            with self.assertRaises(AssertionError):
                check("limits.junk.max", LIMITS["limits.junk.max"],
                      int(values["limits.junk.max"]))
            with self.assertRaises(AssertionError):
                check("layout.type.resp", TYPES["layout.type.resp"],
                      int(values["layout.type.resp"]))

    def test_build_requires_production_source(self):
        """Without src/ the harness cannot build, so the suite cannot pass."""
        with tempfile.TemporaryDirectory(prefix="e2-nosrc-") as tmp:
            tmp = Path(tmp)
            shutil.copytree(HARNESS_DIR, tmp / "tools" / "tests" / "harness")
            proc = _build(tmp / "tools" / "tests" / "harness")
            self.assertNotEqual(proc.returncode, 0,
                                "harness built without production source")
            self.assertIn("src/if_awg.c",
                          (proc.stdout or "") + (proc.stderr or ""))


# ---------------------------------------------------------------------------
# 7. origin audit — every citation must point at real source text
# ---------------------------------------------------------------------------

#: (path, 1-based line, needle) — pinned at base 0e557e1
CITATIONS = [
    ("src/if_awg.c", 85, "#define AWG_MAX_JUNK"),
    ("src/if_awg.c", 86, "#define AWG_MAX_JUNK_SIZE"),
    ("src/if_awg.c", 125, "struct awg_pkt_initiation {"),
    ("src/if_awg.c", 184, "#define AWG_TYPE_INITIATION"),
    ("src/if_awg.c", 1782, "awg_send_junk(struct awg_softc *sc"),
    ("src/awg_noise.h", 49, "#define COUNTER_BITS_TOTAL"),
    ("src/awg_noise.h", 52, "#define COUNTER_WINDOW_SIZE"),
    ("src/awg_noise.h", 81, "struct awg_noise_counter {"),
    ("src/awg_noise.c", 766, "awg_noise_counter_recv(struct awg_noise_counter"),
    ("src/awg_noise.c", 811, "awg_noise_kdf(uint8_t *a"),
    ("src/awg_noise.c", 1081, "awg_noise_counter_test()"),
]


class TestOriginAudit(unittest.TestCase):
    def test_citations_point_at_real_lines(self):
        for relpath, lineno, needle in CITATIONS:
            with self.subTest(citation="%s:%d" % (relpath, lineno)):
                lines = (ROOT / relpath).read_text().splitlines()
                self.assertLessEqual(lineno, len(lines),
                                     "%s has only %d lines"
                                     % (relpath, len(lines)))
                self.assertIn(needle, lines[lineno - 1],
                              "%s:%d does not contain %r; actual: %r"
                              % (relpath, lineno, needle,
                                 lines[lineno - 1]))

    def test_extracted_blocks_are_tracked(self):
        """The manifest records exactly where each compiled block came from."""
        manifest = json.loads(
            (HARNESS_DIR / "gen" / "manifest.json").read_text())
        names = {block["name"] for block in manifest["blocks"]}
        for required in ("awg_send_junk", "awg_noise_counter_recv",
                         "awg_noise_kdf", "awg_pkt_initiation",
                         "awg_noise_counter"):
            self.assertIn(required, names)
        self.assertGreaterEqual(len(manifest["blocks"]), 20)


if __name__ == "__main__":
    unittest.main()
