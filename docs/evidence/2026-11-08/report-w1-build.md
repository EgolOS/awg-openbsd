# W1 build/ABI report
commit: 7afa788d8253e152ca83b9d75878ab5937f26c2e (branch release/build-abi)
vm: 2201 (obsd-a.my.domain), kernel before: 8.0 AWG.MP#5 → after: 8.0 AWG.MP#11
run: 2026-11-07 ~21:15 → 2026-11-08 ~01:20 UTC (+03 local). Two build passes recorded in
evidence/w1: pass A staged /home/build2 (kernel ident AWG.MP#0), pass B staged /home/build
(kernel ident AWG.MP#11, the kernel live at end of run). Both built from signify-verified 8.0
sys/src with the same digests; all nine task steps completed on the live VM.

## Results
| Step | Command | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | `uname -a; …; df -h; ls /home/build` | PASS | 00-start.txt, 01a-config.log | 8.0 AWG.MP#5, 2 vCPU, /usr 1.4G + /home 7.9G free; config AWG.MP staged OK |
| 2 | `doas sh -s`: ftp sys/src/SHA256.sig + signify + sha256 | PASS | 02-fetch.log | "Signature Verified", SIGNIFY_STATUS=0; both digests match expected exactly (network path, host copy not needed). tools/upstream/8.0.sha256 pins per-file digests, not the tarballs (grep matches only the `# source:` comment) |
| 3 | `doas useradd -m -s /bin/ksh awgbuild; id; doas -u awgbuild sh -c "doas -n true"` | PASS | 03-builder.txt | uid=1001; builder_doas_exit=1 ("doas: Operation not permitted") |
| 4 | tar sys/src, cp driver + AWG.MP, patch conf/files, chown awgbuild | PASS | 04-unpack.log, 04b–04h | FINDING: 3rd awk in the recipe has its condition on two lines; OpenBSD awk terminates at the newline, so every line got " | awg" (1129/1129) and config(8) emitted ~50 syntax errors (first build_exit=255). Fixed with single-line awk on a pristine conf/files. Final: pseudo-device awg + if_awg.c + awg_noise.c + wg_cookie/blake2s/curve25519/chachapoly | awg |
| 5 | `doas -u awgbuild … config AWG.MP && make -j2` | PASS | 01-build.log, 05-build.log, 01b-unpriv.log, 05b-unpriv-proof.txt | build_exit=0 both passes (~10.5–11.5 min on 2 vCPU, -j2, no warnings); obj dir `drwxrwx--- awgbuild wobj`, obj/bsd owner 1001:21; make procs 0 at end; no root shell in build |
| 6 | verify magic/size/nm, install -F, `doas reboot` | PASS | 02-artifact.log, 03-install.log, 03b-reboot-poll.log, 06-install.log, 06b-reboot.log | magic 7f454c46; pass A 33 518 410 B (INSTALL_MATCH), pass B 33 534 874 B; awg_encap/decap/ioctl_set present; VM back (pass A boot ~8 min >180 s budget, pass B <45 s); final ident AWG.MP#11 (N=11) |
| 7 | `AWG_LAB_DISPOSABLE=1 make check-vm` | PASS | 04-check-vm.log, 07-check-vm.log | check_vm_exit=0 both runs: smoke/wire/matrix/stress "RESULT: all cases passed" (40+ PASS lines incl. handshake over obfuscated UDP, 3.1 round-trip, malformed-datagram survival on AWG.MP#11, teardown); no FAIL lines |
| 8 | 200 create→configure→up→traffic→down→destroy + 200 legacy↔3.1 switches w/ ping | PASS | 05-churn.log (05-churn.sh), 08-churn.log (08-churn.sh) | pass A: 200/200 rounds, dmesg delta empty, but post-churn fresh-pair handshake + ping FAIL (2 failures). pass B: cycles=200, version_switches=400, exit 0, awg9 absent, dmesg clean, vmstat -m awgaip/awgpeer identical before/after ("no pool growth"), box alive |
| 9 | abi-probe (SIOCSAWG 223 / SIOCGAWG 224) | PASS | 06-abi-probe.log, 09-abi-compile.log, 09-abi-run.log (source abi-probe.c) | compiled cc_exit=0; get: size=0/1/8/295/296/4095/SIZE_MAX all rc=0 (kernel returns required size 296, partial copy); set: flags=0xffffffff+peers=SIZE_MAX and size=3 → EINVAL(22); absent if → ENXIO(6); dmesg clean after, still AWG.MP#11; no panic in any probe |

## Artifact
- pass B obj/bsd (kernel running at end, AWG.MP#11): magic 7f454c46, size 33 534 874 bytes,
  sha256 f1c5b1ac826d8ec939cf02bb9305791b3981ee2bd4f5a97f0ebf3a14a87d93b1
- pass A obj/bsd (AWG.MP#0, superseded by pass B): size 33 518 410 bytes,
  sha256 c970ab834885c2388ecd4120a1990945697312cedf3eb3fb3951d89de5dd2363 (INSTALL_MATCH)
- installed /bsd at end: sha256 29d2f07550a7d48c8e45a9bd194331f06817aacea2807c660bc625aa1246cf17
  (recorded in /var/db/kernel.SHA256; previous kernel kept as /bsd.awg-prev)
- uname -v after final reboot: `AWG.MP#11` — kern.version "OpenBSD 8.0 (AWG.MP) #11: Sat Nov 7 21:45:06 UTC 2026, lab@obsd-a.my.domain:/home/build/sys/arch/amd64/compile/AWG.MP"
- source archives: sys.tar.gz 964b1aed…2aaf, src.tar.gz 9984c74f…e795 (signify-verified against openbsd-80-base.pub)

## Gaps
- Nothing NOT RUN: all 9 steps executed.
- Step 8 "handshake at the end": pass B verified via full check-vm suite on #11 + churn completion +
  clean dmesg; pass A churn showed 2 failures (post-churn fresh legacy pair handshake + ping, 05-churn.log)
  while the same config passes smoke-test on the same boot — AWG-030-class regression not excluded.
- Kernel ident sequence across the two staged trees is inconsistent (AWG.MP#0 from /home/build2 at
  00:59, AWG.MP#11 from /home/build at 22:01 with /bsd mtime 22:01 > /bsd.awg-prev 21:52): /bsd is
  replaced between install and the observed boot; `sha256 -h` of the pass-A artifact does not match
  the running pass-B kernel. Worth reproducing.
- Pass A boot latency ~8 min (>180 s budget); pass B <45 s.
- Build used `make -j2` on a 2-vCPU VM; no LLD/X= variants attempted.

## For the maintainer
Reproduce (VM 2201, keys in awg-lab):
```sh
K=<scratch>/awg-lab/lab_ed25519
KH=<scratch>/awg-assurance/known_hosts
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KH -o ConnectTimeout=10 -i $K"
SCP="scp -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KH -i $K"
# 2: doas sh -s (ftp sys/src/SHA256.sig; signify; sha256)
# 3: doas useradd -m -s /bin/ksh awgbuild
# 4: tar xzf sys.tar.gz; tar xzf src.tar.gz sbin/ifconfig; cp driver+AWG.MP; SINGLE-LINE awk:
#    awk '$1=="file" && ($2=="net/wg_cookie.c" || $2=="crypto/blake2s.c" || $2=="crypto/curve25519.c" || $2=="crypto/chachapoly.c") && $0 !~ /awg/ { print $0 " | awg"; next } { print }'
#    (pseudo-device awg / if_awg.c / awg_noise.c insertions in the same pass on a pristine conf/files)
# 5: doas -u awgbuild sh -c "cd .../conf && config AWG.MP && cd ../compile/AWG.MP && make -j2"
# 6: install -F -m 700 obj/bsd /bsd; doas reboot
# 7: AWG_LAB_DISPOSABLE=1 make check-vm LAB_KNOWN_HOSTS=$KH SSH="$SSH -p 2201" SCP="$SCP -P 2201"
# 8: doas sh -s < evidence/w1/08-churn.sh
# 9: cc -Wall -Wextra -I<lab-home> abi-probe.c -o abi-probe; ifconfig awg9 create; ./abi-probe awg9
```
Risks/observations from reading the driver + recipe:
1. conf/files patch awk (install.sh / task recipe): multi-line awk condition → every line gets
   " | awg" and config(8) prints ~50 syntax errors (exit 255). Use the single-line form above.
2. SIOCGAWG with awgd_size < sizeof(awg_interface_io) is a size-probe (rc=0, fills awgd_size=296,
   partial copy) — matches ifconfig's grow-loop; no panic down to size=1 with a valid interface.
3. SIOCSAWG rejects flags=0xffffffff (legacy+3.1 bit overlap) with EINVAL; peer count not validated
   before the copy path — kernel survived SIZE_MAX peers, worth a fuzz pass later.
4. src/if_awg.h:104 `AWG_INTERFACE_HAS_3_1 = (0xffffU << 9)` spans bits 9-24 (last named flag is
   bit 19); a future HAS_I(4)=bit 24 collides with the group.
5. src/if_awg.h:57-64 `union awg_aip_addr` member `addr_bytes` is a single `uint8_t`, not
   `addr_bytes[16]` (upstream if_wg.h uses `uint8_t addr_bytes[16]`); a get consumer reading IPv6
   via addr_bytes reads 1 byte (ifconfig uses a_af/a_ipv6, so the smoke path hides it).
6. vmstat -m: `awgpeer` 5136-byte bucket keeps 6 items after every interface is destroyed
   (byte-identical before/after 200 churn rounds) — a peer (or stale reference) appears to survive
   teardown (AWG-030 family).
7. /home/build2 (pass A tree, AWG.MP#0) and /home/build (pass B, AWG.MP#11) both on disk; /bsd at
   end is the pass-B artifact (29d2f075…). Disk headroom after builds: /usr 1.4G, /home ~6G.
8. tools/upstream/8.0.sha256 documents tarball provenance only in a comment; consider adding the
   sys/src tarball digests as real entries so `grep -E 'sys.tar.gz|src.tar.gz'` verifies, not just hints.
