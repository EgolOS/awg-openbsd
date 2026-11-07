#!/bin/sh
# AmneziaWG for OpenBSD — install script
# Run as root on OpenBSD 7.x or 8.x amd64
#
# Usage:
#   sh install.sh --tools-only   # patch + rebuild ifconfig, install awg-quick (no reboot)
#   sh install.sh --kernel-only  # build and install AWG kernel (requires reboot)
#   sh install.sh --all          # everything (default)

set -e

SCRIPT=$(readlink -f "$0")
REPODIR=$(dirname "$SCRIPT")
SRCDIR=/usr/src/sys
IFCFG_SRC=/usr/src/sbin/ifconfig
CDN=https://cdn.openbsd.org/pub/OpenBSD

# Nothing here may live at a predictable /tmp path: this script runs as root
# and an unprivileged user can pre-create a symlink at a fixed name. The work
# directory holds the release tarballs (~300 MB), so prefer /usr/src when it
# exists: on a VPS / is small and /tmp lives on it.
LOGFILE=$(mktemp /tmp/awg-install-log.XXXXXX) || { echo "mktemp failed" >&2; exit 1; }
chmod 600 "$LOGFILE"
if [ -d /usr/src ] && [ -w /usr/src ]; then
    WORK=$(mktemp -d /usr/src/.awg-install.XXXXXX) || exit 1
else
    WORK=$(mktemp -d /tmp/awg-install-work.XXXXXX) || exit 1
fi
trap 'rm -rf "$WORK"' EXIT

log()  { echo "==> $*"; echo "==> $*" >> "$LOGFILE"; }
die()  { echo "ERROR: $*" >&2; exit 1; }
step() { echo ""; echo "──── $* ────"; }

check_root() {
    [ "$(id -u)" -eq 0 ] || die "Run as root"
}

# Verify a downloaded release file against the release's signed SHA256 list.
# signify(1) and /etc/signify are part of a stock OpenBSD install, so a file
# that cannot be verified must stop the install, not warn.
verify_release_file() {		# verify_release_file <filename>
    local name=$1 ver key sig
    ver=$(uname -r)
    key="/etc/signify/openbsd-$(echo "$ver" | tr -d '.')-base.pub"
    [ -f "$key" ] || die "missing release key $key — cannot verify $name (install signify keys or fetch sources manually)"
    sig="$WORK/SHA256.sig"
    [ -f "$sig" ] || ftp -o "$sig" "$CDN/$ver/SHA256.sig" \
        || die "cannot fetch $CDN/$ver/SHA256.sig — refusing to build from unverified sources"
    ( cd "$WORK" && signify -C -p "$key" -x "$sig" ) > "$WORK/sigcheck.log" 2>&1 || true
    grep -q 'Signature Verified' "$WORK/sigcheck.log" \
        || die "$name: SHA256.sig did not verify against $key"
    grep -q "^$name: OK$" "$WORK/sigcheck.log" \
        || die "$name: signature check failed (see $WORK/sigcheck.log)"
    log "Verified $name against $key"
}

# ── 1. System packages ────────────────────────────────────────────────────────

install_packages() {
    step "System packages"

    if ! command -v bash >/dev/null 2>&1; then
        log "Installing bash"; pkg_add bash
    fi

    log "Packages OK"
}

# ── 2. Kernel sources ─────────────────────────────────────────────────────────

fetch_sys_sources() {
    step "Kernel sources"

    # Validate sources (files.rasops being empty is a known bad-extraction symptom)
    if [ ! -f "$SRCDIR/net/if_wg.c" ] || ! grep -q 'OpenBSD' "$SRCDIR/dev/rasops/files.rasops" 2>/dev/null; then
        local ver url
        ver=$(uname -r)
        url="$CDN/${ver}/sys.tar.gz"
        log "Fetching kernel sources from $url"
        mkdir -p /usr/src
        ftp -o "$WORK/sys.tar.gz" "$url" \
            || die "Failed to download $url — CDN only carries 7.7+. Download manually and extract to /usr/src"
        verify_release_file sys.tar.gz
        tar -C /usr/src -xzf "$WORK/sys.tar.gz" || die "Failed to extract sys.tar.gz"
        [ -f "$SRCDIR/net/if_wg.c" ] || die "sys.tar.gz extracted but $SRCDIR/net/if_wg.c not found"
        log "Kernel sources installed to /usr/src/sys"
    else
        log "Kernel sources already present"
    fi

    # After sysupgrade the old release's sources are still in place
    local srcver
    srcver=$(sed -n 's/^osr="\(.*\)"$/\1/p' "$SRCDIR/conf/newvers.sh")
    [ "$srcver" = "$(uname -r)" ] \
        || die "$SRCDIR is OpenBSD $srcver, system is $(uname -r): move $SRCDIR and $IFCFG_SRC aside and rerun to fetch matching sources"
}

# ── 3. ifconfig patch ─────────────────────────────────────────────────────────

install_ifconfig() {
    step "ifconfig — AWG support patch"

    local PATCH="$REPODIR/sbin/ifconfig/ifconfig_awg.patch"
    [ -f "$PATCH" ] || die "Patch not found: $PATCH"

    # If a kernel tree is present it must match the running release: the
    # ioctl ABI in the patch follows it. --tools-only does not download the
    # tree, so this is a check, not a requirement.
    if [ -f "$SRCDIR/conf/newvers.sh" ]; then
        local srcver
        srcver=$(sed -n 's/^osr="\(.*\)"$/\1/p' "$SRCDIR/conf/newvers.sh")
        [ "$srcver" = "$(uname -r)" ] \
            || log "WARNING: $SRCDIR is OpenBSD $srcver, system is $(uname -r)"
    fi

    # Fetch ifconfig sources from CDN if not present
    if [ ! -f "$IFCFG_SRC/ifconfig.c" ]; then
        local ver url
        ver=$(uname -r)
        url="$CDN/${ver}/src.tar.gz"
        log "Fetching userland sources from $url"
        ftp -o "$WORK/src.tar.gz" "$url" \
            || die "Failed to download $url"
        verify_release_file src.tar.gz
        tar -C /usr/src -xzf "$WORK/src.tar.gz" sbin/ifconfig \
            || die "Failed to extract sbin/ifconfig from src.tar.gz"
        [ -f "$IFCFG_SRC/ifconfig.c" ] || die "ifconfig.c not found after extraction"
        log "ifconfig sources extracted to $IFCFG_SRC"
    fi

    # Install if_awg.h into system include path (needed to compile ifconfig)
    cp "$REPODIR/src/if_awg.h" /usr/include/net/if_awg.h

    # Apply patch (idempotent: check if already patched)
    if grep -q 'awgversion' "$IFCFG_SRC/ifconfig.c" 2>/dev/null; then
        log "ifconfig.c already patched"
    else
        if grep -q 'A_AMNEZIAWG' "$IFCFG_SRC/ifconfig.c" 2>/dev/null; then
            # Patched by a release without AWG 3.1 support: start over
            [ -f "$IFCFG_SRC/ifconfig.c.orig" ] \
                || die "ifconfig.c has an old AWG patch and no ifconfig.c.orig to restore"
            log "Replacing old AWG patch in ifconfig.c"
            cp "$IFCFG_SRC/ifconfig.c.orig" "$IFCFG_SRC/ifconfig.c"
        else
            cp "$IFCFG_SRC/ifconfig.c" "$IFCFG_SRC/ifconfig.c.orig"
        fi
        log "Patching ifconfig.c"
        patch "$IFCFG_SRC/ifconfig.c" "$PATCH" \
            || die "patch failed — ifconfig.c may differ from expected version"
    fi

    # Build and install
    log "Building ifconfig"
    cd "$IFCFG_SRC"
    make || die "ifconfig build failed"
    # The binary is in obj/ if that exists
    install -c -s -o root -g bin -m 555 "$(make -V .OBJDIR)/ifconfig" /sbin/ifconfig
    install -c -o root -g bin -m 444 ifconfig.8 /usr/share/man/man8/ifconfig.8
    log "ifconfig installed to /sbin/ifconfig"
}

# ── 4. awg-quick ─────────────────────────────────────────────────────────────

install_awg_quick() {
    step "awg-quick"

    install -d -o root -g wheel -m 755 /usr/local/sbin
    install -m 755 -o root -g wheel "$REPODIR/sbin/awg-quick" /usr/local/sbin/awg-quick
    log "awg-quick installed to /usr/local/sbin/awg-quick"
}

# ── 5. AWG kernel driver ──────────────────────────────────────────────────────

# Build only: the artifact stays in the compile directory and /bsd is not
# touched. Used by CI and by anyone who wants to inspect a kernel before it
# becomes the boot kernel.
kernel_build() {
    step "AWG kernel driver"


    log "Copying driver files to $SRCDIR/net/"
    cp "$REPODIR/src/if_awg.h"    "$SRCDIR/net/if_awg.h"
    cp "$REPODIR/src/if_awg.c"    "$SRCDIR/net/if_awg.c"
    cp "$REPODIR/src/awg_noise.h" "$SRCDIR/net/awg_noise.h"
    cp "$REPODIR/src/awg_noise.c" "$SRCDIR/net/awg_noise.c"
    cp "$REPODIR/src/AWG.MP"      "$SRCDIR/arch/amd64/conf/AWG.MP"

    # Patch conf/files to register the pseudo-device (idempotent)
    if grep -q 'pseudo-device awg' "$SRCDIR/conf/files"; then
        log "conf/files already patched"
    else
        log "Patching $SRCDIR/conf/files"
        awk '
            /^pseudo-device wg: ifnet$/ { print; print "pseudo-device awg: ifnet"; next }
            /^file net\/if_wg\.c/ { print; print "file net/if_awg.c\t\t\tawg"; next }
            { print }
        ' "$SRCDIR/conf/files" > "$SRCDIR/conf/files.tmp" \
            && mv "$SRCDIR/conf/files.tmp" "$SRCDIR/conf/files"
        grep -q 'pseudo-device awg' "$SRCDIR/conf/files" \
            || die "conf/files patch failed — wg entry not found"
    fi

    # awg_noise.c came with AWG 3.1 support, add it to older installs too
    if ! grep -q 'net/awg_noise\.c' "$SRCDIR/conf/files"; then
        log "Registering net/awg_noise.c in $SRCDIR/conf/files"
        awk '
            /^file net\/if_awg\.c/ { print; print "file net/awg_noise.c\t\t\tawg"; next }
            { print }
        ' "$SRCDIR/conf/files" > "$SRCDIR/conf/files.tmp" \
            && mv "$SRCDIR/conf/files.tmp" "$SRCDIR/conf/files"
        grep -q 'net/awg_noise\.c' "$SRCDIR/conf/files" \
            || die "conf/files patch failed — if_awg.c entry not found"
    fi

    # The driver calls wg_cookie/blake2s/curve25519/chachapoly. Those files are
    # attached to "wg" (or "ipsec | crypto"), so a config with awg but without
    # wg links with undefined symbols. Attach them to awg as well.
    if ! grep -q 'wg_cookie\.c.*awg' "$SRCDIR/conf/files"; then
        log "Attaching shared crypto/cookie files to the awg attribute"
        awk '
            $1 == "file" && ($2 == "net/wg_cookie.c" || $2 == "crypto/blake2s.c" ||
                $2 == "crypto/curve25519.c" || $2 == "crypto/chachapoly.c") &&
                $0 !~ /awg/ { print $0 " | awg"; next }
            { print }
        ' "$SRCDIR/conf/files" > "$SRCDIR/conf/files.tmp" \
            && mv "$SRCDIR/conf/files.tmp" "$SRCDIR/conf/files"
        grep -q 'wg_cookie\.c.*awg' "$SRCDIR/conf/files" \
            || die "conf/files patch failed — wg_cookie.c entry not found"
    fi

    log "Configuring AWG.MP"
    cd "$SRCDIR/arch/amd64/conf"
    config AWG.MP

    log "Building kernel — this takes ~15 minutes"
    cd "$SRCDIR/arch/amd64/compile/AWG.MP"
    # Not `make | tee`: the pipeline reports tee's status, so a build that died
    # half-way looks like a success and the installer carries on to install a
    # kernel that was never produced. Log to the file, report on failure.
    log "Building kernel — this takes ~15 minutes (log: $LOGFILE)"
    if ! make -j"$(sysctl -n hw.ncpu)" >> "$LOGFILE" 2>&1; then
        tail -20 "$LOGFILE" >&2
        die "kernel build failed (full log: $LOGFILE)"
    fi
}

# Refuse a compiled kernel that cannot be the AWG kernel: wrong file type, a
# size that is not a kernel, or no awg(4) symbols in it. `make install` would
# happily put any of those on /bsd.
verify_kernel_artifact() {		# verify_kernel_artifact <path>
    local k=$1 size
    [ -f "$k" ] || die "kernel artifact missing: $k"
    # BSD head has no -c; dd+od is the portable check for 7f 45 4c 46.
    [ "$(dd if="$k" bs=4 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')" = 7f454c46 ] \
        || die "$k is not an ELF file — build failed and left something else"
    size=$(stat -f %z "$k")
    [ "$size" -gt 5242880 ] \
        || die "$k is $size bytes — too small to be a kernel"
    nm -n "$k" 2>/dev/null | grep -q ' awg_encap$' \
        || die "$k has no awg_encap symbol — the AWG driver is not linked in"
    log "Verified $k ($size bytes, awg symbols present)"
}

# config(8) puts the objects (and the linked bsd) under obj/; `make install`
# finds them there, but a script that copies the artifact must not assume it.
kernel_object_path() {
    echo "$SRCDIR/arch/amd64/compile/AWG.MP/obj/bsd"
}

build_kernel_only() {
    kernel_build
    verify_kernel_artifact "$(kernel_object_path)"
    log "Kernel built: $(kernel_object_path)"
    log "/bsd was not touched. Install with: sh $0 --kernel-only"
}

install_kernel() {
    local bsd

    kernel_build
    bsd=$(kernel_object_path)
    verify_kernel_artifact "$bsd"

    # Stage, then replace. A build that dies half-way must leave the running
    # kernel alone; the copy is verified again after the swap.
    log "Staging kernel to /bsd.awg-staging"
    cp "$bsd" /bsd.awg-staging || die "could not stage the kernel"
    chmod 700 /bsd.awg-staging
    chown root:wheel /bsd.awg-staging

    if [ -f /bsd ]; then
        log "Keeping the kernel being replaced as /bsd.awg-prev"
        cp -p /bsd /bsd.awg-prev || die "could not save /bsd"
        # The bootloader falls back to /obsd; keep it in step with the backup.
        ln -f /bsd /obsd 2>/dev/null || cp -p /bsd /obsd 2>/dev/null || true
    fi

    install -F -m 700 /bsd.awg-staging /bsd || die "could not install /bsd"
    rm -f /bsd.awg-staging
    cmp -s "$bsd" /bsd || die "/bsd does not match the built kernel after install"
    sha256 -h /var/db/kernel.SHA256 /bsd 2>/dev/null \
        || log "warning: could not update /var/db/kernel.SHA256 (syspatch will ask to rebuild it)"

    log "Kernel installed to /bsd — reboot to activate"
    log "If it does not boot: at the boot prompt, 'boot /bsd.awg-prev'; after boot, sh $0 --rollback"
}

rollback_kernel() {
    local failed

    step "Rollback to the previous kernel"
    [ -f /bsd.awg-prev ] \
        || die "no /bsd.awg-prev to roll back to — boot /obsd from the bootloader instead"

    if cmp -s /bsd /bsd.awg-prev; then
        log "/bsd already matches /bsd.awg-prev — nothing to do"
        return 0
    fi
    failed="/bsd.failed.$(date +%Y%m%d%H%M%S)"
    cp -p /bsd "$failed" 2>/dev/null && log "Current kernel kept as $failed"
    install -F -m 700 /bsd.awg-prev /bsd || die "could not restore /bsd"
    sha256 -h /var/db/kernel.SHA256 /bsd 2>/dev/null || true
    cmp -s /bsd.awg-prev /bsd || die "/bsd does not match the restored kernel"
    log "Previous kernel restored — reboot to activate it"
}

# Report what an install would do and stop. Nothing is written, so this is safe
# to run on a production box before a maintenance window.
check_plan() {
    local rc=0 key ver ifcfg_ver

    step "Pre-flight check"
    ver=$(uname -r)
    printf 'os            : %s %s (%s)\n' "$(uname -s)" "$ver" "$(uname -m)"
    [ "$(uname -m)" = amd64 ] || { printf 'arch          : unsupported (amd64 only)\n'; rc=1; }

    key="/etc/signify/openbsd-$(echo "$ver" | tr -d '.')-base.pub"
    if [ -f "$key" ]; then
        printf 'signify key   : %s\n' "$key"
    else
        printf 'signify key   : MISSING %s (source verification will fail)\n' "$key"
        rc=1
    fi

    if [ -f "$SRCDIR/net/if_wg.c" ]; then
        printf 'kernel source : %s\n' "$SRCDIR"
    else
        printf 'kernel source : not present, would be downloaded to %s\n' "$SRCDIR"
    fi

    if [ -f "$IFCFG_SRC/ifconfig.c" ]; then
        ifcfg_ver=$(sed -n 's/.*OpenBSD \([0-9.]*\).*/\1/p' "$IFCFG_SRC/ifconfig.c" | head -1)
        if [ -n "$ifcfg_ver" ] && [ "$ifcfg_ver" != "$ver" ]; then
            printf 'ifconfig src  : %s is from %s, running %s (patch would be wrong)\n' \
                "$IFCFG_SRC" "$ifcfg_ver" "$ver"
            rc=1
        else
            printf 'ifconfig src  : %s\n' "$IFCFG_SRC"
        fi
    else
        printf 'ifconfig src  : not present, would be downloaded\n'
    fi

    printf 'running kernel: %s\n' "$(uname -v)"
    if [ -f /bsd.awg-prev ]; then
        printf 'rollback      : /bsd.awg-prev present\n'
    else
        printf 'rollback      : none yet (the first install keeps the current /bsd)\n'
    fi
    printf 'disk free     : %s\n' "$(df -h / | awk 'NR==2 {print $4}')"

    printf '\nplan: %s\n' \
        'fetch+verify sources, copy src/ into the tree, patch conf/files, config AWG.MP,'
    printf '      %s\n' \
        'build, verify the artifact, stage it, keep /bsd as /bsd.awg-prev, install, reboot.'
    [ "$rc" -eq 0 ] || printf '\nresult: %s\n' 'a precondition above would fail — fix it before running --kernel-only'
    [ "$rc" -eq 0 ] && printf '\nresult: ready\n'
    exit "$rc"
}

# ── 6. Config directory ───────────────────────────────────────────────────────

setup_confdir() {
    if [ ! -d /etc/amnezia/amneziawg ]; then
        log "Creating /etc/amnezia/amneziawg"
        mkdir -p /etc/amnezia/amneziawg
    fi
    # The directory holds private keys; enforce 0700 even when it already
    # existed with a looser mode.
    chmod 700 /etc/amnezia/amneziawg
}

# ── 7. pf MSS clamping ────────────────────────────────────────────────────────

setup_pf_mss() {
    step "pf MSS clamping for awg interfaces"

    local PF_CONF=/etc/pf.conf
    local RULE='match on awg scrub (max-mss 1380)'

    if grep -q 'max-mss.*1380' "$PF_CONF" 2>/dev/null; then
        log "pf MSS clamp already present in $PF_CONF"
        return
    fi

    # Never write a firewall policy for the operator. Adding "block return /
    # pass" to a host that has no pf.conf would open it up; only edit a file
    # that already exists, and only when explicitly asked.
    if [ "$EDIT_PF" != yes ]; then
        log "Suggested rule (not applied; rerun with --pf to add it):"
        echo "        $RULE"
        return
    fi

    [ -f "$PF_CONF" ] || die "$PF_CONF does not exist — create a policy first, then add: $RULE"

    log "Adding MSS clamp rule to $PF_CONF"
    cp -p "$PF_CONF" "$PF_CONF.awg.bak"
    if grep -q '^set skip' "$PF_CONF"; then
        awk -v rule="$RULE" '
            /^set skip/ { print; found=1; next }
            found && !/^set skip/ { print "\n# Clamp TCP MSS to awg MTU (1420 - 40 = 1380)"; print rule; found=0 }
            { print }
        ' "$PF_CONF" > "$PF_CONF.tmp" && mv "$PF_CONF.tmp" "$PF_CONF"
    else
        { printf '# Clamp TCP MSS to awg MTU (1420 - 40 = 1380)\n%s\n\n' "$RULE"; cat "$PF_CONF"; } > "$PF_CONF.tmp" \
            && mv "$PF_CONF.tmp" "$PF_CONF"
    fi

    if pfctl -nf "$PF_CONF" 2>/dev/null; then
        pfctl -f "$PF_CONF" && log "pf reloaded with MSS clamp" || log "WARNING: pf reload failed — check $PF_CONF"
    else
        log "WARNING: pf syntax check failed — restoring $PF_CONF.awg.bak"
        cp -p "$PF_CONF.awg.bak" "$PF_CONF"
    fi
}

# ── main ──────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $0 [--tools-only | --kernel-only | --build-only | --rollback | --check | --all] [--pf]

  --check        Report what would be done and stop (nothing is changed)
  --tools-only   Patch ifconfig + install awg-quick (no reboot needed)
  --build-only   Build the kernel and stop; /bsd is not touched
  --kernel-only  Build and install AWG kernel (requires reboot)
  --all          Install everything: tools + kernel (default)
  --rollback     Put the previous /bsd back (see /bsd.awg-prev)
  --pf           Also add the MSS clamp rule to an existing /etc/pf.conf
                 (default: print the rule and leave the firewall alone)

The kernel is staged and verified before /bsd is replaced, and the kernel it
replaces is kept as /bsd.awg-prev so --rollback works without the bootloader.

After --all or --kernel-only: reboot to activate the AWG kernel.
Place your config in /etc/amnezia/amneziawg/<name>.conf, then:
  awg-quick up <name>
  awg-quick down <name>
EOF
    exit 0
}

MODE=all
EDIT_PF=no
for arg in "$@"; do
    case "$arg" in
        --tools-only)  MODE=tools ;;
        --kernel-only) MODE=kernel ;;
        --build-only)  MODE=build ;;
        --rollback)    MODE=rollback ;;
        --check)       MODE=check ;;
        --all)         MODE=all ;;
        --pf)          EDIT_PF=yes ;;
        -h|--help)     usage ;;
        *) die "Unknown option: $arg. Run $0 --help for usage." ;;
    esac
done

check_root
: > "$LOGFILE"

case "$MODE" in
    tools)
        install_packages
        install_ifconfig
        install_awg_quick
        setup_confdir
        setup_pf_mss
        ;;
    kernel)
        fetch_sys_sources
        install_kernel
        ;;
    build)
        fetch_sys_sources
        build_kernel_only
        ;;
    rollback)
        rollback_kernel
        ;;
    check)
        check_plan
        ;;
    all)
        install_packages
        fetch_sys_sources
        install_ifconfig
        install_awg_quick
        setup_confdir
        setup_pf_mss
        install_kernel
        ;;
esac

echo ""
echo "=========================================="
echo " AmneziaWG install complete"
echo "=========================================="
echo ""

if [ "$MODE" = "tools" ]; then
    echo "Tools installed. Place your config:"
    echo "  /etc/amnezia/amneziawg/<name>.conf"
    echo ""
    echo "Then: awg-quick up <name>"
else
    echo "REBOOT REQUIRED to load the AWG kernel."
    echo ""
    echo "After reboot:"
    echo "  uname -v                   # verify AWG.MP kernel"
    echo "  ifconfig awg0 create       # verify awg device"
    echo ""
    echo "Place your config:"
    echo "  /etc/amnezia/amneziawg/<name>.conf"
    echo ""
    echo "Then: awg-quick up <name>"
fi

echo ""
echo "Log: $LOGFILE"
