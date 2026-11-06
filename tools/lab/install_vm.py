#!/usr/bin/env python3
"""OpenBSD 8.0 unattended install into a qemu VM (lab only)."""
import os, sys, subprocess, time, pexpect

LAB = os.environ.get("LAB", "/lab")
NAME = os.environ.get("VMNAME", "obsd-a")
DISK = f"{LAB}/{NAME}.qcow2"
LOG = f"{LAB}/install-{NAME}.log"
ISO = f"{LAB}/install80.iso"

# 1. HTTP server for install.conf (guest reaches it at 10.0.2.2:8000)
http = subprocess.Popen([sys.executable, "-m", "http.server", "8000", "--bind", "0.0.0.0", "--directory", LAB],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)

if not os.path.exists(DISK):
    subprocess.run(["qemu-img", "create", "-f", "qcow2", DISK, (os.environ.get("DISKSIZE","10G"))], check=True)

cmd = [
    "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "1024", "-smp", "2",
    "-drive", f"file={DISK},if=virtio,format=qcow2",
    "-cdrom", ISO,
    "-netdev", "user,id=n0", "-device", "virtio-net-pci,netdev=n0",
    "-nographic", "-no-reboot",
]
print("RUN:", " ".join(cmd), flush=True)
child = pexpect.spawn(cmd[0], cmd[1:], timeout=180, encoding="utf-8", codec_errors="replace")
child.logfile = open(LOG, "w")


def step(pattern, action, timeout=240, label=""):
    child.timeout = timeout
    try:
        child.expect(pattern)
    except pexpect.TIMEOUT:
        print(f"TIMEOUT waiting {pattern!r} at {label}", flush=True)
        print(child.before[-1500:] if child.before else "", flush=True)
        raise
    if action is not None:
        child.send(action)
    print(f"ok: {label or pattern}", flush=True)


step(r"boot>", "set tty com0\n", 120, "bootloader: set tty com0")
step(r"boot>", "boot\n", 60, "boot bsd.rd on serial")
step(r"\(I\)nstall, \(U\)pgrade, \(A\)utoinstall or \(S\)hell", "a\n", 240, "autoinstall")
step(r"Response file location\?", "http://10.0.2.2:8000/install.conf\n", 120, "response file url")
# disk / sets proceed autonomously; wait for the final prompt
step(r"(CONGRATULATIONS|installation is complete|Press Enter to reboot|reboot)", "\n", 1800, "install finished")
time.sleep(5)
try:
    child.expect(pexpect.EOF, timeout=60)
except Exception:
    pass
child.close(force=True)
http.terminate()
print("DONE install", NAME, flush=True)
