#!/usr/bin/env python3
"""Lab HTTP server with an atomically-bound, reported port (R1 review).

The original install_vm.py probed a free port with a throw-away socket, closed
that socket, and started `http.server` on the number it returned. Between the
close and the server's bind, a second worker could claim the same port
(classic TOCTOU), and the server's stderr was discarded, so a failed bind was
only visible much later. This helper starts the server that actually serves
the response file and reports the port its own socket bound, so the port is
reserved for exactly as long as the server lives.

`start_http_server()` is importable without pexpect so a unit test can drive
it with no VM and no installer.
"""
import subprocess
import sys

SERVING_PREFIX = "Serving HTTP on"


def start_http_server(directory, port=None, attempts=5, python=None):
    """Start `http.server` for `directory` and return (proc, live_port).

    port=None asks the kernel for a free ephemeral port on the very socket the
    server serves from (bind(0) is atomic, so the port is reserved the moment
    it is chosen). An explicit port is used as-is and, when busy, retried on
    port+1 ... port+attempts-1. The live port is read from the
    "Serving HTTP on <addr> port <n>" line the server prints *after* a
    successful bind, which doubles as the startup check (no sleep needed:
    readline blocks until the bind succeeded or the process died). The failed
    attempt's stderr is kept for the report, and every failed child is
    terminated and reaped so no orphan holds a port.
    """
    last_err = ""
    current = port
    for _ in range(max(1, attempts)):
        argv = [python or sys.executable, "-B", "-u", "-m", "http.server",
                str(0 if current is None else current),
                "--bind", "0.0.0.0", "--directory", str(directory)]
        # -u: the "Serving HTTP ..." line must reach the parent unbuffered,
        # otherwise the readiness readline waits for a full block.
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        out, err = proc.stdout, proc.stderr
        line = out.readline() if out else ""
        if line.startswith(SERVING_PREFIX):
            # "Serving HTTP on 0.0.0.0 port 41235 (http://0.0.0.0:41235/) ..."
            head = line[len(SERVING_PREFIX):].split(" port ", 1)
            if len(head) == 2:
                digits = head[1].strip().split(" ")[0]
                if digits.isdigit():
                    return proc, int(digits)
        # Bind failed (EADDRINUSE) or the server died before announcing:
        # keep the diagnostic, terminate and reap, then retry the next port.
        try:
            last_err = ((err.read() if err else "") or "").strip() or last_err
        except Exception:
            pass
        proc.terminate()
        proc.wait()
        if out is not None:
            out.close()
        if err is not None:
            err.close()
        if current is not None:
            current = int(current) + 1
    raise RuntimeError(
        f"http.server failed to start after {attempts} attempts: {last_err}")


def stop_http_server(proc):
    """Terminate and reap the server so the port is released for the next run."""
    if proc is None:
        return
    proc.terminate()
    proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


if __name__ == "__main__":
    import os
    d = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    p = sys.argv[2] if len(sys.argv) > 2 else None
    srv, live = start_http_server(d, p)
    print(f"HTTP_PORT={live}", flush=True)
    stop_http_server(srv)
