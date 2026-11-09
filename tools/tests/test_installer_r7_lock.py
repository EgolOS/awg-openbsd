"""R7 behavioral lock tests: real shell, real process liveness, no flock."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import unittest

from test_installer_r5_lock import SHIM_STAT

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'install.sh').read_text()


def extract(name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\)\s*\{.*?^\}', SOURCE)
    assert match, name
    return match.group()


def lock_functions():
    section = SOURCE[SOURCE.index('LOCK_OWNED=no'):SOURCE.index('# R4-01: before exiting')]
    return '\n'.join(re.findall(r'(?ms)^\w+\(\)\s*\{.*?^\}', section))


def wait_for(predicate):
    end = time.monotonic() + 10
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError('barrier timeout')
        time.sleep(.005)


def evidence(kind, data):
    directory = os.environ.get('R7_EVIDENCE_DIR')
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        with (path / (kind + '.jsonl')).open('a') as stream:
            stream.write(json.dumps(data) + '\n')


class R7Lock(unittest.TestCase):
    def setup_run(self, w):
        (w / 'state').mkdir()
        (w / 'work').mkdir()
        (w / 'bsd').write_text('kernel\n')
        (w / 'stat').write_text(SHIM_STAT)
        (w / 'stat').chmod(0o700)
        script = w / 'run.sh'
        funcs = lock_functions()
        for boundary in ('generation-mkdir', 'token-temp', 'token-rename', 'before-cas', 'after-cas'):
            marker = '# lock-boundary: ' + boundary
            funcs = funcs.replace(marker, marker + '\n'
                + '    if [ "${PAUSE_AT:-}" = "' + boundary + '" ]; then\n'
                + '        echo $$ > "$WORK/$NAME.ready"\n'
                + '        while [ ! -e "$WORK/$NAME.resume" ]; do sleep .01; done\n'
                + '    fi')
        script.write_text('set -e\nLOCK_OWNED=no\nLOCK_INITIALISE_GRACE=30\n'
                          'log() { echo "$*"; }\ndie() { echo "$*" >&2; exit 1; }\n'
                          + funcs + '\n'
                          + extract('verify_bsd_final') + '\n'
                          + extract('usage') + '\n' + extract('check_root')
                          + '\ntrap finish EXIT\n'
                          + 'case "$ACTION" in\n'
                          + 'refuse) acquire_lock ;;\n'
                          + 'usage) usage ;;\nroot) id() { echo 502; }; check_root ;;\n'
                          + 'signal) kill -TERM $$ ;;\n'
                          + '*) acquire_lock\n'
                          + 'echo $$ > "$WORK/$NAME.admitted"\n'
                          + 'while [ ! -e "$WORK/$NAME.go" ]; do sleep .01; done\n'
                          + 'verify_lock_held ;;\nesac\n')
        return dict(os.environ, PATH=str(w) + ':' + os.environ['PATH'], WORK=str(w / 'work'),
                    LOCKFILE=str(w / 'state' / 'lock'), BSD_FILE=str(w / 'bsd'),
                    LOGFILE=str(w / 'log'), ACTION='hold', NAME='a'), script

    def spawn(self, env, script, name, **extra):
        return subprocess.Popen(['sh', str(script)], env=dict(env, NAME=name, **extra),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def snapshot(self, w):
        result = []
        for p in [w] + sorted(w.rglob('*')):
            s = p.lstat()
            result.append((str(p.relative_to(w)), s.st_ino, s.st_mtime_ns,
                           s.st_mode, os.readlink(p) if p.is_symlink() else None))
        return result

    def test_five_schedule_exclusivity(self):
        repeats = int(os.environ.get('R7_RACE_REPEATS', '1'))
        schedules = ('fresh', 'dead', 'reclaim-v-fresh', 'paused-initializer', 'eight-way')
        for repeat in range(repeats):
            for schedule in schedules:
                with self.subTest(repeat=repeat, schedule=schedule), tempfile.TemporaryDirectory() as d:
                    w = Path(d)
                    env, script = self.setup_run(w)
                    procs = {}
                    try:
                        if schedule in ('dead', 'reclaim-v-fresh', 'eight-way'):
                            (w / 'state' / 'lock').write_text('gen=7 pid=99999999 start=x nonce=dead\n')
                        if schedule in ('reclaim-v-fresh', 'paused-initializer'):
                            boundary = 'before-cas' if schedule == 'reclaim-v-fresh' else 'generation-mkdir'
                            procs['a'] = self.spawn(env, script, 'a', PAUSE_AT=boundary)
                            wait_for(lambda: (w / 'work' / 'a.ready').exists())
                            procs['a'].send_signal(signal.SIGSTOP)
                            procs['c'] = self.spawn(env, script, 'c')
                            wait_for(lambda: (w / 'work' / 'c.admitted').exists())
                            procs['d'] = self.spawn(env, script, 'd')
                            procs['d'].wait(timeout=10)
                            self.assertNotEqual(procs['d'].returncode, 0)
                            self.assertIsNone(procs['c'].poll(), 'C must still be alive when D refuses')
                            (w / 'work' / 'a.resume').touch()
                            procs['a'].send_signal(signal.SIGCONT)
                            procs['a'].wait(timeout=10)
                            self.assertNotEqual(procs['a'].returncode, 0)
                        else:
                            names = list('abcdefgh') if schedule == 'eight-way' else ['c', 'd']
                            for name in names:
                                procs[name] = self.spawn(env, script, name, PAUSE_AT='before-cas')
                            wait_for(lambda: all((w / 'work' / (n + '.ready')).exists() for n in names))
                            for name in names:
                                (w / 'work' / (name + '.resume')).touch()
                            wait_for(lambda: any((w / 'work' / (n + '.admitted')).exists() for n in names))
                            # The winner cannot release until the test explicitly opens its gate.
                            wait_for(lambda: sum(p.poll() is not None for p in procs.values()) == len(names) - 1)
                        admitted = [n for n in procs if (w / 'work' / (n + '.admitted')).exists()]
                        self.assertEqual(len(admitted), 1, (schedule, admitted))
                        self.assertIsNone(procs[admitted[0]].poll())
                        self.assertFalse(all((w / 'work' / (n + '.admitted')).exists() for n in ('c', 'd')),
                                         'C and D must not both be admitted alive')
                        evidence('barrier', {'repeat': repeat, 'schedule': schedule,
                            'admitted': admitted, 'alive': [n for n, p in procs.items() if p.poll() is None],
                            'pids': {n: p.pid for n, p in procs.items()}})
                        (w / 'work' / (admitted[0] + '.go')).touch()
                        for name, p in procs.items():
                            out, err = p.communicate(timeout=10)
                            if name == admitted[0]:
                                self.assertEqual(p.returncode, 0, (out, err))
                    finally:
                        for p in procs.values():
                            if p.poll() is None:
                                p.send_signal(signal.SIGCONT)
                                p.kill()
                            p.communicate(timeout=10)
        print(f'R7 barrier: {repeats * len(schedules)} schedules, zero double-admissions', flush=True)

    def test_sigkill_five_boundaries_two_retries(self):
        for boundary in ('generation-mkdir', 'token-temp', 'token-rename', 'before-cas', 'after-cas'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as d:
                w = Path(d)
                env, script = self.setup_run(w)
                p = self.spawn(env, script, 'victim', PAUSE_AT=boundary)
                try:
                    wait_for(lambda: (w / 'work' / 'victim.ready').exists())
                    p.kill()
                    p.communicate(timeout=10)
                    for name in ('retry1', 'retry2'):
                        r = self.spawn(env, script, name, ACTION='refuse')
                        out, err = r.communicate(timeout=10)
                        self.assertEqual(r.returncode, 0, (boundary, name, out, err))
                        self.assertIn('Lock acquired', out)
                        evidence('crash', {'boundary': boundary, 'retry': name,
                                          'returncode': r.returncode, 'stdout': out, 'stderr': err})
                finally:
                    if p.poll() is None:
                        p.kill()
                    p.communicate(timeout=10)

    def test_stopped_published_owner_not_reclaimed(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env, script = self.setup_run(w)
            p = self.spawn(env, script, 'victim', PAUSE_AT='after-cas')
            try:
                wait_for(lambda: (w / 'work' / 'victim.ready').exists())
                p.send_signal(signal.SIGSTOP)
                before = self.snapshot(w / 'state')
                for name in ('c', 'd'):
                    r = self.spawn(env, script, name, ACTION='refuse', AWG_LOCK_GRACE='0')
                    out, err = r.communicate(timeout=10)
                    self.assertNotEqual(r.returncode, 0, (out, err))
                    self.assertIn('holds', err)
                    self.assertEqual(before, self.snapshot(w / 'state'))
                (w / 'work' / 'victim.resume').touch()
                (w / 'work' / 'victim.go').touch()
                p.send_signal(signal.SIGCONT)
                out, err = p.communicate(timeout=10)
                self.assertEqual(p.returncode, 0, (out, err))
            finally:
                if p.poll() is None:
                    p.send_signal(signal.SIGCONT)
                    p.kill()
                p.communicate(timeout=10)

    def test_orphan_generation_does_not_wedge(self):
        for leftover in ('mkdir', 'pid', 'owner-temp', 'owner', 'pending-cas'):
            with self.subTest(leftover=leftover), tempfile.TemporaryDirectory() as d:
                w = Path(d)
                env, script = self.setup_run(w)
                g = w / 'state' / 'gen-7'
                g.mkdir()
                (g / 'owner').write_text('gen=7 pid=99999999 start=x nonce=dead\n')
                (w / 'state' / 'lock').symlink_to(g)
                orphan = w / 'state' / 'gen-8'
                orphan.mkdir()
                if leftover == 'pid':
                    (orphan / 'pid').write_text('99999998\n')
                if leftover == 'owner-temp':
                    (orphan / 'owner.tmp').write_text('gen=8 pid=99999998 start=x nonce=orphan\n')
                if leftover in ('owner', 'pending-cas'):
                    (orphan / 'owner').write_text('gen=8 pid=99999998 start=x nonce=orphan\n')
                if leftover == 'pending-cas':
                    (w / 'state' / 'lock.cas.8').symlink_to(orphan)
                for name in ('retry1', 'retry2'):
                    p = self.spawn(env, script, name, ACTION='refuse')
                    out, err = p.communicate(timeout=10)
                    self.assertEqual(p.returncode, 0, (out, err))
                    self.assertIn('Lock acquired', out)

    def test_readonly_refusal_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            env, script = self.setup_run(w)
            g = w / 'state' / 'gen-7'
            g.mkdir()
            start = subprocess.check_output(['ps', '-p', str(os.getpid()), '-o', 'lstart='],
                                            text=True).strip('\n')
            start = re.sub(' +', '_', start)
            (g / 'owner').write_text(f'gen=7 pid={os.getpid()} start={start} nonce=abc\n')
            (w / 'state' / 'lock').symlink_to(g)
            before = self.snapshot(w / 'state')
            for action in ('refuse', 'usage', 'root', 'signal'):
                p = self.spawn(env, script, action, ACTION=action)
                out, err = p.communicate(timeout=10)
                if action == 'refuse':
                    self.assertNotEqual(p.returncode, 0, (out, err))
                    self.assertIn('holds', err)
                self.assertEqual(before, self.snapshot(w / 'state'), action)
                evidence('snapshot', {'action': action, 'before': before,
                                      'after': self.snapshot(w / 'state')})


if __name__ == '__main__':
    unittest.main()
