"""Real generator with a kernel-boundary mock; fixtures are not usable keys."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GeneratorSecurity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / 'bin'; self.bin.mkdir()
        mock = '''#!/bin/sh
printf '%s\\n' "$*" >> "$LOG"
case "$2" in
create) [ "${BUSY:-0}" = 0 ] ;;
destroy) : ;;
awgkey) [ "$3" = - ] || exit 1
        read -r key
        printf '%s\\n' "$key" >> "$INPUTS"
        [ "${KEY_FAIL:-0}" = 0 ] ;;
*) : ;;
esac
'''
        # Status has no $2, and the public key does not carry private input.
        mock = mock.replace('case "$2" in', 'case "${2:-status}" in').replace('*) : ;;', "status) printf '\\tawgpubkey dummy-public\\n' ;;\n*) : ;;")
        for name, body in [('ifconfig', mock), ('openssl', '#!/bin/sh\nprintf "fixture-secret-never-in-argv\\n"\n')]:
            p = self.bin / name; p.write_text(body); p.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin)+':'+os.environ['PATH'],
                        LOG=str(self.work / 'argv'), INPUTS=str(self.work / 'stdin'))

    def run_gen(self, *args, trace=False, **extra):
        return subprocess.run(['sh'] + (['-x'] if trace else []) +
                              [str(ROOT / 'tools/gen-config.sh'), *args],
                              cwd=self.work, env=dict(self.env, **extra),
                              capture_output=True, text=True, timeout=10)

    def test_default_output_private_and_stdin_keys(self):
        r = self.run_gen()
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.work / 'pair.conf'
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('fixture-secret', r.stdout + r.stderr)
        self.assertNotIn('fixture-secret', (self.work / 'argv').read_text())
        self.assertEqual((self.work / 'stdin').read_text().splitlines(),
                         ['fixture-secret-never-in-argv'] * 2)
        self.assertIn('awg9 destroy', (self.work / 'argv').read_text())

    def test_h_values_are_distinct(self):
        r = self.run_gen()
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.work / 'pair.conf').read_text()
        values = [line.split('=', 1)[1].strip() for line in text.splitlines()
                  if line.startswith(('H1 =', 'H2 =', 'H3 =', 'H4 ='))][:4]
        self.assertEqual(len(set(values)), 4)

    def test_entropy_collision_retries_are_bounded(self):
        p = self.bin / 'od'; p.write_text('#!/bin/sh\nprintf "1\\n"\n'); p.chmod(0o700)
        r = self.run_gen()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('collision limit', r.stderr)
        self.assertFalse((self.work / 'pair.conf').exists())

    def test_xtrace_disabled_before_key_generation(self):
        r = self.run_gen(trace=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('fixture-secret', r.stdout + r.stderr)

    def test_preexisting_interface_untouched(self):
        r = self.run_gen(BUSY='1')
        self.assertNotEqual(r.returncode, 0)
        calls = (self.work / 'argv').read_text()
        self.assertNotIn('awgkey', calls)
        self.assertNotIn('destroy', calls)

    def test_existing_output_untouched(self):
        p = self.work / 'pair.conf'; p.write_text('keep')
        self.assertNotEqual(self.run_gen().returncode, 0)
        self.assertEqual(p.read_text(), 'keep')
        self.assertFalse((self.work / 'argv').exists())

    def test_symlink_output_untouched(self):
        p = self.work / 'victim'; p.write_text('keep')
        (self.work / 'pair.conf').symlink_to(p)
        self.assertNotEqual(self.run_gen().returncode, 0)
        self.assertEqual(p.read_text(), 'keep')

    def test_partial_failure_cleans_interface_and_output(self):
        self.assertNotEqual(self.run_gen(KEY_FAIL='1').returncode, 0)
        self.assertIn('destroy', (self.work / 'argv').read_text())
        self.assertFalse((self.work / 'pair.conf').exists())

    def test_explicit_export_and_ipv6_endpoint(self):
        r = self.run_gen('--stdout', '--endpoint', '[2001:db8::1]:51820')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Endpoint = [2001:db8::1]:51820', r.stdout)
        self.assertIn('PrivateKey = fixture-secret', r.stdout)
        self.assertFalse((self.work / 'pair.conf').exists())

    def test_invalid_inputs_refused_before_kernel_calls(self):
        for args in [('--endpoint',), ('--endpoint', 'host:70000'),
                     ('--endpoint', '[::::]:123'), ('--net', '999.1.1.1/24'),
                     ('--net', '10.0.0.1/128'), ('--client', '10.0.0.2/32\nPostUp = bad')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_gen(*args).returncode, 0)
                self.assertFalse((self.work / 'argv').exists())


if __name__ == '__main__':
    unittest.main()
