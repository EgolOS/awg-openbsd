#!/usr/bin/env python3
"""Scripted OpenBSD 8.0 amd64 installer driver over the qemu serial console.

Spawns qemu-system with -nographic (serial stdio), answers the install(8)
prompts with a tolerant regex state machine, exits to the shell and powers
the guest off. The full serial transcript is written to --serial-log so the
out-of-band console is captured with the run.

Usage:
  openbsd-install.py --qemu QEMU --iso ISO --disk DISK --serial-log LOG \
      [--mem 2048] [--cpus 2] [--ssh-port 10022] [--root-pass awglab]
"""
import argparse
import re
import sys
import time

import pexpect


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--qemu", required=True)
    p.add_argument("--iso", required=True)
    p.add_argument("--disk", required=True)
    p.add_argument("--serial-log", required=True)
    p.add_argument("--mem", default="2048")
    p.add_argument("--cpus", default="2")
    p.add_argument("--ssh-port", default="10022")
    p.add_argument("--root-pass", default="awglab")
    p.add_argument("--datadir", default="", help="qemu -L pc-bios directory")
    p.add_argument("--timeout", type=int, default=600)
    return p.parse_args()


# Ordered (regex, answer). First match wins; answers are sent with Enter.
PROMPTS = [
    (r"\(I\)nstall.{0,60}\(S\)hell\?", "i"),
    (r"Terminal type\?", ""),
    (r"Keyboard layout\?", ""),
    (r"Shall we go to \(S\)hell.{0,60}after completion\?", "s"),
    (r"Which network interface do you wish to configure", ""),
    (r"Configure IPv4 address.{0,40}\[y\]", ""),
    (r"IPv4 address for \w+\?", ""),
    (r"Configure IPv6 address.{0,40}\[y\]", ""),
    (r"IPv6 address for \w+\?", ""),
    (r"Disable SMP kernel on MP cpu\?", ""),
    (r"Which disk is the root disk", ""),
    (r"Use \(W\)hole disk", ""),
    (r"Location of sets\?.*\[cd", ""),
    (r"Set\(s\).{0,80}\[all\]", ""),
    (r"Timezone \(", ""),
    (r"Enter password for root", None),   # filled from --root-pass
    (r"Re-enter password", None),
    (r"Exit to \(S\)hell", "S"),
]
PATTERNS = [re.compile(rx) for rx, _ in PROMPTS]


def main():
    a = parse_args()
    cmd = [
        a.qemu,
        "-L", a.datadir or "pc-bios",
        "-accel", "kvm",
        "-machine", "q35",
        "-cpu", "host",
        "-m", a.mem,
        "-smp", a.cpus,
        "-cdrom", a.iso,
        "-boot", "order=dc",
        "-drive", f"if=virtio,file={a.disk}",
        "-netdev", f"user,id=n0,hostfwd=tcp::{a.ssh_port}-:22",
        "-device", "virtio-net-pci,netdev=n0",
        "-nographic",
    ]
    log = open(a.serial_log, "w", buffering=1)
    child = pexpect.spawn(" ".join(cmd), encoding="utf-8", timeout=a.timeout,
                          dimensions=(40, 120))
    child.logfile_read = log
    t0 = time.time()
    while child.isalive():
        idx = child.expect_list(
            [pexpect.TIMEOUT, pexpect.EOF] + PATTERNS, timeout=a.timeout)
        if idx == 0:  # TIMEOUT: keep waiting on the same prompt set
            continue
        if idx == 1:  # EOF
            break
        answer = PROMPTS[idx - 2][1]
        if answer is None:
            answer = a.root_pass
        child.sendline(answer)
        print(f"[{time.time()-t0:7.1f}s] answered prompt #{idx-2}: "
              f"{PROMPTS[idx-2][0]}", flush=True)
        if "Exit to" in PROMPTS[idx - 2][0]:
            break
    # Exit to shell, then power off cleanly (preserves the qcow2 for snapshot).
    child.sendline("shutdown -p now")
    child.expect(pexpect.EOF, timeout=a.timeout)
    child.close()
    print(f"installer exitstatus={child.exitstatus} rc={child.exitstatus == 0}")
    sys.exit(0 if child.exitstatus == 0 else 1)


if __name__ == "__main__":
    main()
