"""Baseline paths are relative to sys/, except userland's sibling sbin/."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class UpstreamManifest(unittest.TestCase):
    def test_userland_resolves_beside_sys_tree(self):
        spec = importlib.util.spec_from_file_location('upstream_diff', ROOT / 'tools/upstream-diff.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            w = Path(d); tree = w / 'sys'; (tree / 'net').mkdir(parents=True)
            (tree / 'net/if_wg.c').write_text('/* fixture */\n')
            userland = w / 'sbin/ifconfig'; userland.mkdir(parents=True)
            data = b'/* source fixture */\n'; (userland / 'ifconfig.c').write_bytes(data)
            manifests = w / 'manifests'; manifests.mkdir()
            (manifests / '8.0.sha256').write_text(hashlib.sha256(data).hexdigest()+'  sbin/ifconfig/ifconfig.c\n')
            with patch.object(module, 'MANIFEST_DIR', manifests), patch.object(sys, 'argv',
                    ['upstream-diff', '--tree', str(tree), '--release', '8.0', '--json']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
                (userland / 'ifconfig.c').unlink()
                self.assertEqual(module.main(), 1)


if __name__ == '__main__':
    unittest.main()
