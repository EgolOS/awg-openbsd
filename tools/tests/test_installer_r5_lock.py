"""R5 N1 + R6 N1: the run lock must be exclusive under reclamation.

Round-4 shipped a mkdir lock whose cleanup (`finish`) removed the lock
directory unconditionally, so a *refused* contender deleted the live owner's
lock and a third run entered. Round-5 bound the lock to an owner token
(pid + start time + nonce) with compare-and-rename stale reclaim. Round-6
(R6-N1) found that the delayed reclaimer renamed *before* comparing the owner,
so a third process mkdir'd in the rename gap and the rename-back nested the
moved directory inside the new one — two simultaneous holders, reproduced with
a three-process barrier and with a paused (SIGSTOP) initializer.

The lock is now one regular file carrying `gen=<N> pid=<P> start=<S>
nonce=<X>`. A new generation is published through an exclusive-create CAS slot
named after the observed generation and renamed over the canonical name, so
the name is never temporarily free and reclaimers of one generation collide
(exactly one wins, the losers refuse). The recorded pid start time is compared
so a recycled pid is not mistaken for the old owner.

The tests run the real EXIT trap, not only the acquisition function.
"""
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'install.sh').read_text()

PRELUDE = '\n'.join([
    ': "${AWG_BUILD_USER:=_awgbuild}"',
    ': "${LOCK_INITIALISE_GRACE:=30}"',
    'log()  { echo "==> $*"; echo "==> $*" >> "$LOGFILE"; }',
    'die()  { echo "ERROR: $*" >&2; exit 1; }',
    'step() { echo ""; echo "---- $* ----"; }',
])

# mkdir shim: an optional barrier file is awaited first (MK_WAIT), so a test
# can pin two processes at a chosen point.
SHIM_MKDIR = ('#!/usr/bin/env python3\nimport os,sys,time\n'
              'a=sys.argv[1:]\n'
              'w=os.environ.get("MK_WAIT","")\n'
              'if w:\n'
              '    t=time.time()\n'
              '    while not os.path.exists(w) and time.time()-t < 10: time.sleep(0.005)\n'
              'if a and a[0]=="-p": a=a[1:]\n'
              'for p in a:\n'
              '    try: os.mkdir(p)\n'
              '    except FileExistsError:\n'
              '        sys.exit(1)\n')
# mv shim: MV_SYNC/<name>-ready is created, then MV_SYNC/<name>-go is awaited
# before the rename, for the rename whose destination contains MV_SYNC_ON
# (default "lock" — the canonical lock name). Used as the barrier at the
# reclamation point (the CAS slot -> canonical name rename).
SHIM_MV = ('#!/usr/bin/env python3\nimport os,sys,time,shutil\n'
           's=os.environ.get("MV_SYNC","")\n'
           'n=os.environ.get("MV_NAME","mv")\n'
           'on=os.environ.get("MV_SYNC_ON","lock")\n'
           'a=[x for x in sys.argv[1:] if x not in ("-f","-T")]\n'
           'if s and a[1].endswith(on):\n'
           '    os.makedirs(s, exist_ok=True)\n'
           '    open(os.path.join(s,n+"-ready"),"w").close()\n'
           '    t=time.time()\n'
           '    while not os.path.exists(os.path.join(s,n+"-go")) and time.time()-t < 10:\n'
           '        time.sleep(0.005)\n'
           'if os.path.islink(a[1]):\n'
           '    os.unlink(a[1])\n'
           'elif os.path.isdir(a[1]):\n'
           '    shutil.rmtree(a[1])\n'
           'elif os.path.lexists(a[1]):\n'
           '    os.unlink(a[1])\n'
           'os.rename(a[0], a[1])\n')
SHIM_PS = ('#!/bin/sh\nif [ "$1" = "-p" ] && [ "$2" = "1" ]; then\n'
           '  case "$3" in -o) echo "2026-11-08T10:00:00" ;; esac\n  exit 0\nfi\nexec /bin/ps "$@"\n')
SHIM_ID = '#!/bin/sh\necho 502\n'
# BSD stat(1) semantics with real values: %z size, %m mtime (the CAS slot's
# freshness is what the grace measures, so a fixed epoch is not enough).
SHIM_STAT = ('#!/usr/bin/env python3\nimport os,sys\n'
             'a=sys.argv[1:]\n'
             "if a and a[0]=='-f': a=a[1:]\n"
             'f=a[0]; p=a[1] if len(a)>1 else "."\n'
             'st=os.stat(p)\n'
             'print(st.st_size if f=="%z" else int(st.st_mtime))\n')


def extract(name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\)\s*\{.*?^\}', SOURCE)
    assert match, 'install.sh needs a %s() function' % name
    return match.group()


LOCK_HELPERS = ['new_lock_token', 'proc_start_time', 'lock_token_start',
                'lock_token_gen', 'lock_owner_live', 'lock_mv_t', 'publish_lock_owner',
                'read_lock_owner', 'lock_token_pid', 'lock_record_age',
                'release_lock', 'verify_bsd_final', 'verify_lock_held']


def verify_held_sleep(secs):
    """acquire, sleep, then re-verify the lock is still ours (R6-N1: a
    reclaim that lands during the work must be observed)."""
    return ('acquire_lock\n'
            'sleep ' + str(secs) + '\n'
            'verify_lock_held\n')


def functions(*names):
    """The named install.sh functions, their helpers, and the real EXIT trap.

    install.sh defines the lock helpers before acquire_lock and finish calls
    release_lock, so an extracted subset must carry them for the shell to
    resolve the calls.
    """
    ordered = list(LOCK_HELPERS) + list(names)
    seen, uniq = set(), []
    for n in ordered:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return '\n'.join(extract(n) for n in uniq) + '\ntrap finish EXIT\n'


class LockOwnership(unittest.TestCase):

    def run_script(self, w, body, shims=None, env=None):
        for name, text in (shims or {}).items():
            p = w / name
            p.write_text(text)
            p.chmod(0o700)
        script = w / 'run.sh'
        script.write_text(PRELUDE + '\n' + body + '\n')
        (w / 'work').mkdir(exist_ok=True)
        (w / 'bsd').write_bytes(b'kernel-bytes\n')
        full = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'),
                    BSD_FILE=str(w / 'bsd'), LOCKFILE=str(w / 'lock'), WORK=str(w / 'work'),
                    PATH=str(w) + ':' + os.environ['PATH'])
        # BSD stat(1) semantics: GNU stat -f means "filesystem", so the harness
        # supplies the OpenBSD answers for the fields install.sh reads.
        if 'stat' not in (shims or {}):
            p = w / 'stat'
            p.write_text(SHIM_STAT)
            p.chmod(0o700)
        if env:
            full.update(env)
        return subprocess.run(['sh', str(script)], env=full,
                              capture_output=True, text=True)

    def holder(self, w, gen='0'):
        """A lock generation owned by a live process (pid 1, the ps shim's
        recorded start): a gen-<N> directory with the owner token, published
        through the canonical symlink (R6-N1 shape (a))."""
        gdir = w / ('gen-%s' % gen)
        gdir.mkdir(exist_ok=True)
        token = 'pid=1 start=2026-11-08T10:00:00 nonce=deadbeef'
        (gdir / 'owner').write_text(token + '\n')
        lock = w / 'lock'
        if lock.is_symlink() or lock.exists():
            lock.unlink()
        os.symlink(str(gdir), str(lock))
        return token

    def lock_record(self, w):
        """The owner token of the published generation (symlink -> gen dir,
        with the legacy plain-file shape accepted)."""
        lock = w / 'lock'
        if lock.is_symlink():
            target = os.readlink(str(lock))
            if target.startswith('gen='):
                return target
            return (Path(target) / 'owner').read_text().strip()
        return lock.read_text().strip()

    # (a) a refused contender leaves the holder's lock and token untouched
    def test_refused_contender_keeps_holders_lock(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            owner_token = self.holder(w)
            body = functions('acquire_lock', 'finish')
            r = self.run_script(w, body + 'acquire_lock\n',
                                {'mkdir': SHIM_MKDIR, 'ps': SHIM_PS})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('holds', r.stderr)
            self.assertTrue((w / 'lock').is_symlink(),
                            "the refused run must not remove the live owner's lock")
            self.assertEqual(self.lock_record(w), owner_token,
                             "the holder's owner token must be unchanged")
            self.assertFalse(list(w.glob('lock.cas.*')),
                             'a refused run must not leave reclaim slots')

    # (b) three reclaimers of the same dead generation, barrier at the
    # reclamation point: exactly one wins (R6-N1). A parks in the CAS-slot
    # rename, B and C collide on the same CAS slot and refuse.
    def test_three_reclaimers_barrier_exactly_one_wins(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'gen-7').mkdir()
            (w / 'gen-7' / 'owner').write_text('pid=999999 start=x nonce=cafe\n')
            os.symlink(str(w / 'gen-7'), str(w / 'lock'))
            os.utime(w / 'lock', (1762600000, 1762600000))  # stale: past the grace
            for name, text in (('mkdir', SHIM_MKDIR), ('mv', SHIM_MV), ('ps', SHIM_PS),
                               ('stat', SHIM_STAT)):
                p = w / name
                p.write_text(text)
                p.chmod(0o700)
            script = w / 'acquire.sh'
            script.write_text(PRELUDE + '\n' + functions('acquire_lock', 'finish')
                              + verify_held_sleep(1) + '\n')
            (w / 'work').mkdir(exist_ok=True)
            (w / 'bsd').write_bytes(b'kernel-bytes\n')
            sync = w / 'sync'
            os.makedirs(sync, exist_ok=True)
            env = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'),
                       BSD_FILE=str(w / 'bsd'), LOCKFILE=str(w / 'lock'), WORK=str(w / 'work'),
                       MV_SYNC_ON='owner',
                       PATH=str(w) + ':' + os.environ['PATH'])
            # A parks in PRIVATE token preparation; B and C race for the
            # immutable public successor while A cannot publish yet.
            a = subprocess.Popen(['sh', str(script)], env=dict(env, MV_SYNC=str(sync),
                                                                MV_NAME='a'),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            t0 = time.time()
            while not (sync / 'a-ready').exists() and time.time() - t0 < 10:
                time.sleep(0.005)
            b = subprocess.Popen(['sh', str(script)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            c = subprocess.Popen(['sh', str(script)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.3)  # let B and C hit the CAS slot while A is parked
            (sync / 'a-go').write_text('')
            ao, ae = a.communicate(timeout=30)
            bo, be = b.communicate(timeout=30)
            co, ce = c.communicate(timeout=30)
            rcs = {'a': a.returncode, 'b': b.returncode, 'c': c.returncode}
            winners = [k for k in rcs if rcs[k] == 0]
            self.assertEqual(len(winners), 1,
                             'exactly one run may acquire (%r)' % rcs)
            # The published generation is the winner's: the winner's pid and
            # the generation *after* the observed one (7 -> 8), as recorded in
            # the shared log (the winner releases the lock at exit).
            log = (w / 'LOGFILE').read_text()
            self.assertIn('Lock acquired', log)
            self.assertIn('generation 8', log, 'the reclaim must publish gen 8')
            self.assertIn('(pid %s)' % {'a': a, 'b': b, 'c': c}[winners[0]].pid, log,
                          'the canonical lock must name the winner process')
            # Immutable history remains; only generation 8 was admitted and
            # its owner published the matching release marker.
            self.assertEqual((w / 'lock').readlink(), w / 'gen-7')
            self.assertIn('gen=8 ', os.readlink(w / 'lock.claim-8'))
            self.assertEqual(os.readlink(w / 'lock.claim-8.released'),
                             os.readlink(w / 'lock.claim-8'))

    # (c) a paused initializer (SIGSTOP longer than the grace) must not
    # overwrite a newer owner: the second run refuses while the first is
    # stopped, and the first keeps its generation when it resumes.
    def test_sigstop_initializer_past_grace_one_holder(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            script = w / 'acquire.sh'
            # Real ps (no shim): a SIGSTOPped pid is found by ps -p, so the
            # second run sees the stopped owner as live and refuses.
            # 3 s sleep (not 1 s): BSD wakes the parent ~100 ms after the
            # sleep child exits, so a 1 s sleep releases before the 1.3 s
            # grace elapses and the second run sees no lock at all.
            script.write_text(PRELUDE + '\n' + functions('acquire_lock', 'finish')
                              + verify_held_sleep(3) + '\n')
            (w / 'work').mkdir()
            (w / 'bsd').write_bytes(b'kernel-bytes\n')
            # BSD stat(1) semantics for the pre-exit /bsd check; ps stays real.
            stat_shim = w / 'stat'
            stat_shim.write_text(SHIM_STAT)
            stat_shim.chmod(0o700)
            env = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'),
                       BSD_FILE=str(w / 'bsd'), LOCKFILE=str(w / 'lock'), WORK=str(w / 'work'),
                       AWG_LOCK_GRACE='1', PATH=str(w) + ':' + os.environ['PATH'])
            a = subprocess.Popen(['sh', str(script)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            t0 = time.time()
            while not (w / 'lock').is_symlink() and time.time() - t0 < 10:
                time.sleep(0.005)
            self.assertTrue((w / 'lock').is_symlink(), 'the victim must have taken the lock')
            token_a = self.lock_record(w)
            a.send_signal(19)  # SIGSTOP
            time.sleep(1.3)    # longer than the 1 s grace
            b = subprocess.run(['sh', str(script)], env=env,
                               capture_output=True, text=True)
            held_while_stopped = self.lock_record(w)
            a.send_signal(18)  # SIGCONT
            ao, ae = a.communicate(timeout=30)
            self.assertNotEqual(b.returncode, 0, 'the second run must refuse')
            self.assertIn('holds', b.stderr)
            self.assertEqual(a.returncode, 0, ae)
            self.assertEqual(held_while_stopped, token_a,
                             'the paused initializer keeps its own generation')
            self.assertEqual(os.readlink(w / 'lock.released'), token_a,
                             'the run releases only its own token')

    # (d) a run killed mid-way: the next run reclaims its lock
    def test_sigkill_midway_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            script = w / 'acquire.sh'
            script.write_text(PRELUDE + '\n' + functions('acquire_lock', 'finish')
                              + 'acquire_lock\nsleep 5\n')
            (w / 'work').mkdir()
            (w / 'bsd').write_bytes(b'kernel-bytes\n')
            env = dict(os.environ, LOGFILE=str(w / 'LOGFILE'), CMDFILE=str(w / 'CMDFILE'),
                       BSD_FILE=str(w / 'bsd'), LOCKFILE=str(w / 'lock'), WORK=str(w / 'work'),
                       PATH=str(w) + ':' + os.environ['PATH'])
            victim = subprocess.Popen(['sh', str(script)], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            t0 = time.time()
            while not (w / 'lock').is_symlink() and time.time() - t0 < 10:
                time.sleep(0.005)
            self.assertTrue((w / 'lock').is_symlink(), 'the victim must have taken the lock')
            victim.kill()
            victim.communicate(timeout=30)
            second = self.run_script(w, functions('acquire_lock', 'finish') + 'acquire_lock\n', {})
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn('Lock acquired', second.stdout + second.stderr)
            # The reclaimed lock is the second run's own generation (the dead
            # victim's token is gone), and the EXIT trap releases it.
            self.assertNotIn('999999', (w / 'LOGFILE').read_text(),
                             'the reclaimed lock carries the new owner, not the killed one')
            self.assertEqual(os.readlink(w / 'lock.claim-2.released'),
                             os.readlink(w / 'lock.claim-2'))

    # (e) an exit before acquisition leaves the lock untouched
    def test_usage_exit_does_not_touch_lock(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            owner_token = self.holder(w)
            r = self.run_script(w, functions('usage', 'finish') + 'usage\n',
                                {'mkdir': SHIM_MKDIR})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((w / 'lock').is_symlink(),
                            'usage must not remove the lock of a running install')
            self.assertEqual(self.lock_record(w), owner_token)
            self.assertFalse(list(w.glob('lock.cas.*')),
                             'usage must not create a lock generation')

    def test_unknown_option_exit_does_not_touch_lock(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            owner_token = self.holder(w)
            r = self.run_script(w, functions('die', 'finish') + 'die "Unknown option: --bogus"\n',
                                {'mkdir': SHIM_MKDIR})
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((w / 'lock').is_symlink())
            self.assertEqual(self.lock_record(w), owner_token)

    def test_root_check_exit_does_not_touch_lock(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            owner_token = self.holder(w)
            r = self.run_script(w, functions('check_root', 'finish') + 'check_root\n',
                                {'mkdir': SHIM_MKDIR, 'id': SHIM_ID})
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((w / 'lock').is_symlink())
            self.assertEqual(self.lock_record(w), owner_token)

    # a fresh lock with no owner record is "initialising", not stale
    def test_missing_owner_record_is_initialising(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'lock').write_text('')
            r = self.run_script(w, functions('acquire_lock', 'finish') + 'acquire_lock\n',
                                {'mkdir': SHIM_MKDIR,
                                 'stat': '#!/bin/sh\necho "$(date +%s)"\n'})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('initialising', r.stderr)
            self.assertFalse(list(w.glob('lock.cas.*')))

    # a recycled pid (same pid, different start time) is stale, not the owner
    def test_recycled_pid_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / 'lock').write_text('gen=3 pid=1 start=2020-01-01T00:00:00Z nonce=cafe format=r7\n')
            r = self.run_script(w, functions('acquire_lock', 'finish') + 'acquire_lock\n',
                                {'mkdir': SHIM_MKDIR, 'ps': SHIM_PS})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Lock acquired', r.stdout + r.stderr)
            self.assertIn('generation 4', (w / 'LOGFILE').read_text(),
                          'the recycled-pid generation is replaced by the next one')

    def test_owner_record_is_published_atomically(self):
        """A complete token must be atomically visible, not a partial file."""
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            r = self.run_script(w, functions('acquire_lock', 'finish') + 'acquire_lock\n', {})
            self.assertEqual(r.returncode, 0, r.stderr)
            token = os.readlink(w / 'lock')
            self.assertRegex(token, r'^gen=1 pid=\d+ start=\S+ nonce=[0-9a-f]{32} format=r7$')
            self.assertEqual(token, os.readlink(w / 'lock.released'))


if __name__ == '__main__':
    unittest.main()
