"""Optional KAT of actual awg_hp_init + exact OpenBSD ChaCha implementation.

OPENBSD_SYS_TREE must point at a verified release tree. Only the kernel-only
sys/systm.h include is replaced by userspace standard headers; cipher code and
production awg_hp_init are copied verbatim, never reimplemented. Not kernel fuzzing.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class HeaderProtectionVector(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('OPENBSD_SYS_TREE') and shutil.which('cc'),
                         'requires verified OPENBSD_SYS_TREE and userspace C compiler')
    def test_ietf_counter_zero_keystream(self):
        header = (Path(os.environ['OPENBSD_SYS_TREE']) / 'crypto/chacha_private.h').read_text()
        expected_header = next(line.split()[0] for line in
            (ROOT / 'tools/upstream/8.0.sha256').read_text().splitlines()
            if line.endswith('  crypto/chacha_private.h'))
        self.assertEqual(hashlib.sha256(header.encode()).hexdigest(), expected_header)
        self.assertIn('#include <sys/systm.h>', header)
        header = header.replace('#include <sys/systm.h>',
                                '#include <sys/types.h>\n#include <stdint.h>\n#include <string.h>\n#include <stdio.h>')
        source = (ROOT / 'src/if_awg.c').read_text()
        match = re.search(r'(?ms)^int\nawg_hp_init\(.*?^\}', source)
        assert match is not None
        harness = header + '''
#define AWG_HPK_NONCE_LEN 12
#define AWG_HPK_LEN 32
struct awg_softc { int sc_awg_has_hpk; uint8_t sc_awg_hpk[32]; };
''' + match.group() + '''
int main(void) {
    struct awg_softc sc = { .sc_awg_has_hpk = 1 };
    chacha_ctx ctx; uint8_t nonce[12], zeros[64] = {0}, out[64];
    for (int i=0;i<32;i++) sc.sc_awg_hpk[i] = i;
    for (int i=0;i<12;i++) nonce[i] = i+1;
    if (!awg_hp_init(&sc, &ctx, nonce)) return 1;
    chacha_encrypt_bytes(&ctx, zeros, out, sizeof(out));
    return fwrite(out, 1, sizeof(out), stdout) != sizeof(out);
}
'''
        with tempfile.TemporaryDirectory() as d:
            w = Path(d); (w / 'kat.c').write_text(harness)
            subprocess.run(['cc', '-std=c99', '-D_DEFAULT_SOURCE', '-Wall', '-Werror',
                            str(w / 'kat.c'), '-o', str(w / 'kat')], check=True, capture_output=True)
            actual = subprocess.check_output([str(w / 'kat')])
            # Deterministic public test key, not a credential. OpenSSL's first
            # state word is counter zero, followed by the same 96-bit nonce.
            expected = subprocess.check_output(['openssl', 'enc', '-chacha20',
                         '-K', bytes(range(32)).hex(),
                         '-iv', (bytes(4) + bytes(range(1,13))).hex()], input=bytes(64))
            self.assertEqual(len(actual), 64)
            self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
