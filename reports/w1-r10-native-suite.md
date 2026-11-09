# W1-r10 — the host test suite does not complete on the OpenBSD laptop

Date: 2026-11-09. Host: `lab@<lab-host>`, OpenBSD 8.0 `AWG.MP#0`, disposable test box.
Tree: `git archive` of `8b3cc3e` (the Round-10 pin), staged at `/home/user/n8` — no source edits.

## What happened

`make check-tests` was started natively and killed by a 20-minute cap. The harness reported:

```
test_c_numeric_endpoints_skip_the_resolver (tools.tests.test_quick_r9_endpoint.QuickR9EndpointResolution)
  ... *** Signal SIGTERM in /home/user/n8 (Makefile:55 'check-tests')
   13m34.79s real     0m53.76s user     0m39.14s system
```

13.5 minutes of real time against 54 s of user CPU is waiting, not computing — the suite had reached
that one test and never left it. The same 114-test suite takes ~45 s on the Fedora host.

Narrowed to one test, with a 90-second cap:

```
$ cd /home/user/n8 && timeout 90 python3 -m unittest \
    tools.tests.test_quick_r9_endpoint.QuickR9EndpointResolution.test_c_numeric_endpoints_skip_the_resolver -v
test_c_numeric_endpoints_skip_the_resolver (...) ...
RC=124                     # killed by timeout; no leftover bash/route child afterwards
```

So it is a hang, not slowness: the suite reaches that test on OpenBSD and never leaves it.

## Mechanism (supported diagnosis — not an exhaustive native proof)

`sbin/awg-quick:791` starts the route monitor as

```
exec 19< <(exec route -n monitor)
```

The test harness does not install a `route` *program*; it defines a bash **function** (`route()` in
`tools/tests/test_quick_r9_endpoint.py`). `exec` replaces the shell — and a function is not a program —
so the name is resolved as an external command through `PATH`. On OpenBSD `/sbin/route` is there, in
the default `PATH`: the real monitor starts, runs until the interface goes away, and keeps fd 19 (and
therefore the captured pipe `subprocess.run(capture_output=True)` waits on) open. On the Fedora host
a `route` binary does exist (`/usr/sbin/route`, net-tools), but it is not a BSD `route`: `route -n
monitor` exits at once with status 3 and a usage message. A monitor that exits immediately closes
fd 19, the pipe drains, and the test passes — which is why nine rounds of green host gates never
saw it. The host case is therefore "the monitor dies at once", not "the monitor is missing".

Tests `c`, `d` and `e` in that module all take the same path; `c` is simply the first in alphabetical
order, so it is the one that appears stuck. The number of affected tests is therefore "the endpoint
tests that reach the monitor", not "one".

What this supports: the hang is a **fixture portability defect** — a mock that cannot survive `exec` —
not evidence that `awg-quick` misbehaves on OpenBSD. What it does **not** support: any claim that the
rest of the native suite would pass if this fixture were fixed. The native run says nothing about
native ioctl/pledge/unveil behaviour, a final kernel boot, client conformance or soak.

## Why it matters

- The release notes say native suite portability is unproven. This is the concrete reason — and it is
  narrower than "the suite is unreliable": one test's fixture does not survive an OpenBSD `PATH`.
- A green Fedora `make check` therefore says nothing about the host the product targets. That is a
  documentation fact, not a code verdict.

## Next step (not done here)

The fixture must stop relying on a shell function for a command the tool starts with `exec`: install a
real executable `route` shim in a private `PATH` directory and give it a bounded lifetime. Then re-run
the full suite natively and record the completion time. Until that is done the native claim stays
"not run" in the release notes.
