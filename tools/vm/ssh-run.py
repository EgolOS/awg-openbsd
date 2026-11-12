#!/usr/bin/env python3
"""ssh-run.py — run a command over ssh with password auth (no sshpass needed).

Usage: ssh-run.py --host 127.0.0.1 --port 10022 --user root --pass PW --cmd CMD
Prints the command's stdout/stderr; exits with the remote exit status.
"""
import argparse
import sys

import pexpect


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default="22")
    p.add_argument("--user", default="root")
    p.add_argument("--password", default="awglab")
    p.add_argument("--cmd", required=True)
    p.add_argument("--timeout", type=int, default=60)
    a = p.parse_args()
    child = pexpect.spawn(
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-p {a.port} {a.user}@{a.host} {a.cmd!r}",
        encoding="utf-8", timeout=a.timeout)
    i = child.expect([r"[Pp]assword:", pexpect.EOF], timeout=a.timeout)
    if i == 0:
        child.sendline(a.password)
    child.expect(pexpect.EOF, timeout=a.timeout)
    out = child.before or ""
    child.close()
    sys.stdout.write(out)
    sys.exit(child.exitstatus if child.exitstatus is not None else 1)


if __name__ == "__main__":
    main()
