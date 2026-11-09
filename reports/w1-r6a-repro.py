#!/usr/bin/env python3
"""N5 repro: run the REAL install.sh against a sandbox root, keep the tree.

Usage: repro_n5.py [--tools-only|--kernel-only|--all] [with_sys_header] [with_stale_root]
Prints exit code, stdout, the recorded command log (cmdfile) and the sandbox tree.
"""
import os
import sys
import subprocess
from pathlib import Path

# The repo root, resolved from this file: a hard-coded worktree path pinned the
# repro to one checkout (and the publish scan flags absolute home paths).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'tests'))
import test_installer_r6_staging as T

def main():
    args = sys.argv[1:]
    mode = args[0] if args else '--tools-only'
    sys_header = (args[1] != 'no') if len(args) > 1 else True
    stale = (len(args) > 2 and args[2] == 'stale')
    w = (Path(sys.argv[0]).parent / 'sandbox').resolve()
    if w.exists():
        subprocess.run(['rm', '-rf', str(w)], check=True)
    w.mkdir()
    T.InstallerR6Staging.sandbox(None, w, with_sys_header=sys_header, with_stale_root=stale)
    T.InstallerR6Staging.tree(None, w)
    env = T.InstallerR6Staging.env(None, w)
    (w / 'cmdfile').write_text('')
    shx = ['-x'] if os.environ.get('SHX') else []
    r = subprocess.run(['sh'] + shx + [str(ROOT / 'install.sh'), mode], env=env,
                       capture_output=True, text=True, cwd=str(w))
    print('EXIT', r.returncode)
    print('--- stdout ---')
    print(r.stdout)
    print('--- stderr ---')
    print(r.stderr)
    print('--- cmdfile ---')
    print((w / 'cmdfile').read_text())
    print('--- tree ---')
    for p in sorted(w.rglob('*')):
        print(p.relative_to(w))
    print('--- log tail ---')
    logs = sorted(w.glob('awg-install-log.*'))
    if logs:
        print(logs[0].read_text()[-2000:])

main()
