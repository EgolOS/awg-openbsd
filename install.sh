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
SRCDIR=${SRCDIR:-/usr/src/sys}
IFCFG_SRC=${IFCFG_SRC:-/usr/src/sbin/ifconfig}
CDN=https://cdn.openbsd.org/pub/OpenBSD

# AWG-014: root fetches and verifies, an unprivileged account compiles, root
# installs. AWG-029: /bsd and /sbin/ifconfig are backed up and restored as a pair.
AWG_BUILD_USER=${AWG_BUILD_USER:-_awgbuild}
BUILD_USER=""
ALLOW_ROOT_BUILD=no
INSTALL_DB=${INSTALL_DB:-/var/db/awg-install}
MANIFEST=$INSTALL_DB/manifest
# R4-01: one exclusive lock serialises every run against the shared paths
# (/bsd, /sbin/ifconfig, the backup pair). Overridable for tests.
LOCKFILE=${AWG_LOCK_FILE:-$INSTALL_DB/lock}
BSD_FILE=${BSD_FILE:-/bsd}
# R5: the kernel integrity DB is overridable so tests run against a sandboxed
# root instead of the live host's /var/db/kernel.SHA256.
KERNEL_SHA256=${KERNEL_SHA256:-/var/db/kernel.SHA256}
# R8-R4-02: the kernel integrity DB is a published member — it is backed up
# per generation and restored together with kernel, tool and header.
KERNEL_SHA256_BACKUP_BASE=${KERNEL_SHA256_BACKUP_BASE:-/var/db/kernel.SHA256.awg-prev}
KERNEL_SHA256_BACKUP=$KERNEL_SHA256_BACKUP_BASE
BSD_STAGING=${BSD_STAGING:-/bsd.awg-staging}
IFCFG_FILE=${IFCFG_FILE:-/sbin/ifconfig}
# R4-02: backups are generation-stamped; the bases are overridable for tests.
BSD_BACKUP_BASE=${BSD_BACKUP_BASE:-/bsd.awg-prev}
IFCFG_BACKUP_BASE=${IFCFG_BACKUP_BASE:-/sbin/ifconfig.awg-prev}
IFAWG_BACKUP_BASE=${IFAWG_BACKUP_BASE:-/usr/include/net/if_awg.h.awg-prev}
AWGCTL_BACKUP_BASE=${AWGCTL_BACKUP_BASE:-/usr/local/sbin/awgctl.awg-prev}
BSD_BACKUP=$BSD_BACKUP_BASE
IFCFG_BACKUP=$IFCFG_BACKUP_BASE
IFAWG_HEADER=${IFAWG_HEADER:-/usr/include/net/if_awg.h}
IFAWG_BACKUP=$IFAWG_BACKUP_BASE
AWGCTL_FILE=${AWGCTL_FILE:-/usr/local/sbin/awgctl}
AWGCTL_BACKUP=$AWGCTL_BACKUP_BASE
# R6: the awg-quick install target is overridable for the sandboxed test suite.
AWG_QUICK_FILE=${AWG_QUICK_FILE:-/usr/local/sbin/awg-quick}
# R6: likewise the config directory, so the sandbox does not touch /etc.
AWG_CONF_DIR=${AWG_CONF_DIR:-/etc/amnezia/amneziawg}
# R4-14 (R6): the awgctl manpage ships with the binary; the target is
# overridable for the sandboxed test suite.
AWGCTL_MAN_DIR=${AWGCTL_MAN_DIR:-/usr/share/man/man8}
ROLLBACK_KERNEL_ONLY=no
BACKUP_PAIR_DONE=no
GENERATION=0
BACKUP_GEN=0
PUBLISH_STARTED=no

# Nothing here may live at a predictable /tmp path: this script runs as root
# and an unprivileged user can pre-create a symlink at a fixed name. The work
# directory holds the release tarballs (~300 MB), so prefer /usr/src when it
# exists: on a VPS / is small and /tmp lives on it.
LOGFILE=$(mktemp /tmp/awg-install-log.XXXXXX) || { echo "mktemp failed" >&2; exit 1; }
chmod 600 "$LOGFILE"
if [ -d /usr/src ] && [ -w /usr/src ]; then
    WORK=$(mktemp -d /usr/src/.awg-install.XXXXXX) || exit 1
else
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/awg-install-work.XXXXXX") || exit 1
fi
# R6-N5: the staged header lives in a private directory created *after*
# mktemp, in the <net/…> layout the sources include. R5 expanded the path
# during variable init — before WORK existed — so every run wrote a root-level
# /if_awg.h; the WIP's `-I$WORK` with a flat $WORK/if_awg.h missed it too,
# because `#include <net/if_awg.h>` resolves $STAGE_DIR/net/if_awg.h.
STAGE_DIR=$WORK/stage
IFAWG_STAGED=$STAGE_DIR/net/if_awg.h
mkdir -m 700 -p "$STAGE_DIR/net" || { echo "cannot create $STAGE_DIR" >&2; exit 1; }

log()  { echo "==> $*"; echo "==> $*" >> "$LOGFILE"; }
die()  { echo "ERROR: $*"; echo "ERROR: $*" >> "$LOGFILE"; exit 1; }
step() { echo ""; echo "──── $* ────"; }

# Stage the driver header the run ships, in the private $STAGE_DIR. Every
# publishing mode calls this before it publishes, so the header that is
# installed is the one this run staged (R6-N5) — never a root-level leftover.
stage_header() {
    [ -f "$REPODIR/src/if_awg.h" ] \
        || die "cannot stage $REPODIR/src/if_awg.h: not found in the repo"
    cp "$REPODIR/src/if_awg.h" "$IFAWG_STAGED" \
        || die "cannot stage $REPODIR/src/if_awg.h to $IFAWG_STAGED"
    chmod 600 "$IFAWG_STAGED"
    log "Staged driver header in $IFAWG_STAGED"
}

# R7: immutable, append-only claims. The exclusive symlink creation is the
# acquisition point. A predecessor has exactly one successor slot; claims and
# release markers are never renamed, removed or reused. No live history GC.
LOCK_OWNED=no
LOCK_TOKEN=""
LOCK_GEN=0
LOCK_SLOT=""
LOCK_INITIALISE_GRACE=${AWG_LOCK_GRACE:-30}

new_lock_token() {         # new_lock_token <generation>
    local nonce start
    nonce=$(od -An -N16 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')
    [ "${#nonce}" -eq 32 ] || return 1
    start=$(proc_start_time "$$")
    [ -n "$start" ] || return 1
    printf 'gen=%s pid=%s start=%s nonce=%s format=r7' "$1" "$$" "$start" "$nonce"
}

proc_start_time() {        # proc_start_time <pid>
    local s
    s=$(LC_ALL=C TZ=UTC0 ps -p "$1" -o lstart= 2>/dev/null | tr -s ' ' '_')
    printf '%s' "$s"
}

lock_token_start() {       # lock_token_start <token>
    local rest
    case "$1" in
        *start=*) rest=${1#*start=}; printf '%s' "${rest%% *}" ;;
        *) printf '' ;;
    esac
}

lock_token_gen() {         # lock_token_gen <token>
    local rest
    case "$1" in
        gen=*) rest=${1#gen=}; printf '%s' "${rest%% *}" ;;
        *) printf '' ;;
    esac
}

lock_token_pid() {         # lock_token_pid <token>
    local rest
    case "$1" in
        *pid=*) rest=${1#*pid=}; printf '%s' "${rest%% *}" ;;
        *) printf '' ;;
    esac
}

lock_owner_live() {        # lock_owner_live <token>
    local pid want have
    pid=$(lock_token_pid "$1")
    case "$pid" in ''|0|*[!0-9]*) return 0 ;; esac
    want=$(lock_token_start "$1")
    have=$(proc_start_time "$pid")
    if [ -n "$have" ]; then
        { [ -z "$want" ] || [ "$want" = "$have" ]; } && return 0
        # Legacy timestamps did not pin locale/TZ: a different rendering is
        # not proof of PID reuse. Only R7 tokens have comparable timestamps.
        case "$1" in *' format=r7') return 1 ;; esac
        kill -0 "$pid" 2>/dev/null && return 0
        return 1
    fi
    # A failed ps/kill is not proof of death. Require a successful full
    # process table with the PID absent; any error fails closed.
    kill -0 "$pid" 2>/dev/null && return 0
    have=$(LC_ALL=C ps -ax -o pid= 2>/dev/null) || return 0
    printf '%s\n' "$have" | awk -v p="$pid" '$1 == p { found=1 } END { exit !found }'
}

lock_mv_t() {
    [ "$(uname -s 2>/dev/null)" = Linux ] && printf '%s' '-T'
    return 0
}

read_lock_owner() {        # atomic token link, or read-only legacy root
    local target
    if [ -L "$1" ]; then
        target=$(readlink "$1" 2>/dev/null) || return 1
        case "$target" in
            gen=*\ pid=*\ start=*\ nonce=*) printf '%s\n' "$target"; return 0 ;;
        esac
    fi
    if [ -f "$1" ]; then
        sed -n '1p' "$1" 2>/dev/null
    elif [ -f "$1/owner" ]; then
        sed -n '1p' "$1/owner" 2>/dev/null
    fi
}

lock_record_age() {
    local now mt
    now=$(date +%s 2>/dev/null)
    mt=$(stat -f %m "$1" 2>/dev/null) || return 1
    printf '%s' "$((now - mt))"
}

publish_lock_owner() {     # <slot> <token>; exclusive, never overwrite
    local slot=$1 token=$2 flags
    flags=-h
    [ "$(uname -s)" = Linux ] && flags=-T
    [ ! -d "$slot" ] || return 1
    ln -s "$flags" "$token" "$slot" 2>/dev/null || return 1
    [ "$(readlink "$slot" 2>/dev/null)" = "$token" ]
}

acquire_lock() {
    local slot observed gen next prep parent
    slot=$LOCKFILE
    gen=0
    parent=$(dirname "$LOCKFILE")
    [ -d "$parent" ] || (umask 077; mkdir -p "$parent") || die "cannot create $parent"
    if [ ! -e "$slot" ] && [ ! -L "$slot" ]; then
        for next in "$LOCKFILE".claim-* "$LOCKFILE".released; do
            [ ! -e "$next" ] && [ ! -L "$next" ] \
                || die "leftover lock history $next with missing $LOCKFILE; offline repair required"
        done
    fi
    while [ -e "$slot" ] || [ -L "$slot" ]; do
        observed=$(read_lock_owner "$slot")
        gen=$(lock_token_gen "$observed")
        # Older directory tokens omitted gen; the immutable root target
        # supplies it, but missing PID/start still fails closed below.
        if [ -z "$gen" ] && [ -L "$slot" ]; then
            next=$(readlink "$slot")
            case "$next" in */gen-*) gen=${next##*/gen-} ;; esac
        fi
        case "$gen" in ''|*[!0-9]*)
            die "leftover initialising lock $slot has incomplete owner token; offline repair required" ;;
        esac
        [ "${#gen}" -le 9 ] || die "leftover invalid generation at $slot"
        [ "$slot" = "$LOCKFILE" ] || [ "$slot" = "$LOCKFILE.claim-$gen" ] \
            || die "leftover mismatched generation at $slot; offline repair required"
        [ -n "$(lock_token_pid "$observed")" ] \
            || die "leftover initialising lock $slot has no owner PID; offline repair required"
        next="$LOCKFILE.claim-$((gen + 1))"
        if [ -e "$next" ] || [ -L "$next" ]; then
            slot=$next
            continue
        fi
        if [ "$(readlink "$slot.released" 2>/dev/null)" != "$observed" ] \
                && lock_owner_live "$observed"; then
            die "another install.sh run (pid $(lock_token_pid "$observed")) holds $slot — wait for it to finish, then rerun"
        fi
        slot=$next
        break
    done
    LOCK_GEN=$((gen + 1))
    LOCK_TOKEN=$(new_lock_token "$LOCK_GEN") || die "cannot create complete lock identity"
    # Preparation is private. These four former public crash windows cannot
    # reserve a shared slot. The only shared transition is atomic symlink(2).
    prep=$(mktemp -d "$WORK/lock-owner.XXXXXXXX") || die "cannot prepare lock token"
    # lock-boundary: generation-mkdir
    printf '%s\n' "$LOCK_TOKEN" > "$prep/owner.tmp" || die "cannot write lock token"
    # lock-boundary: token-temp
    mv "$prep/owner.tmp" "$prep/owner" || die "cannot prepare lock owner"
    # lock-boundary: token-rename
    LOCK_SLOT=$slot
    # lock-boundary: before-cas
    publish_lock_owner "$LOCK_SLOT" "$LOCK_TOKEN" \
        || die "another publication in progress or leftover state at $LOCK_SLOT — rerun"
    # lock-boundary: after-cas
    LOCK_OWNED=yes
    log "Lock acquired: $LOCKFILE generation $LOCK_GEN (pid $$)"
}

finish() {
    local rc=$?
    # Verification and recovery must finish before another owner can enter.
    if [ "${PUBLISH_STARTED:-no}" = yes ]; then
        if rollback_uncommitted "uncommitted publish (exit $rc)"; then
            rc=20
        else
            rc=$?
        fi
    fi
    if ! verify_bsd_final; then
        [ "$rc" -eq 0 ] && rc=3
    fi
    release_lock || { [ "$rc" -ne 0 ] || rc=3; }
    exit "$rc"
}

release_lock() {
    [ "$LOCK_OWNED" = yes ] || return 0
    [ "$(read_lock_owner "$LOCK_SLOT")" = "$LOCK_TOKEN" ] || return 1
    publish_lock_owner "$LOCK_SLOT.released" "$LOCK_TOKEN" || return 1
    LOCK_OWNED=no
}

verify_lock_held() {
    [ "$LOCK_OWNED" = yes ] || { echo 'lock not held' >&2; return 1; }
    [ "$(read_lock_owner "$LOCK_SLOT")" = "$LOCK_TOKEN" ] \
        && [ ! -e "$LOCKFILE.claim-$((LOCK_GEN + 1))" ] \
        && [ ! -L "$LOCKFILE.claim-$((LOCK_GEN + 1))" ] \
        || { echo 'lock replaced by another run' >&2; return 1; }
}

# R4-01: before exiting, /bsd must be a non-empty regular file whose sha256
# and size match what the manifest recorded for the installed kernel.
verify_bsd_final() {
    local want size got
    [ -f "$BSD_FILE" ] || { echo "WARNING: $BSD_FILE is not a regular file" >&2; return 1; }
    size=$(stat -f %z "$BSD_FILE" 2>/dev/null) \
        || { echo "WARNING: cannot stat $BSD_FILE" >&2; return 1; }
    [ "$size" -gt 0 ] || { echo "WARNING: $BSD_FILE is 0 bytes" >&2; return 1; }
    [ -f "$MANIFEST" ] || return 0
    want=$(manifest_get "$MANIFEST" installed_bsd_sha256)
    [ -n "$want" ] || return 0
    got=$(sha256 -q "$BSD_FILE" 2>/dev/null) \
        || { echo "WARNING: cannot hash $BSD_FILE" >&2; return 1; }
    if [ "$got" != "$want" ]; then
        echo "WARNING: $BSD_FILE sha256 $got (size $(stat -f %z "$BSD_FILE" 2>/dev/null)) does not match the manifest ($want, size $(manifest_get "$MANIFEST" installed_bsd_size))" >&2
        return 1
    fi
    return 0
}

trap finish EXIT

check_root() {
    [ "$(id -u)" -eq 0 ] || die "Run as root"
}

# Resolve the account that compiles. Only two values are possible: the
# configured builder, or root when --allow-root-build was passed explicitly.
# No silent fallback: a missing account is an operator action item.
resolve_build_user() {
    [ -n "$BUILD_USER" ] && return 0
    if [ "$ALLOW_ROOT_BUILD" = yes ]; then
        BUILD_USER=root
        log "WARNING: building as root (--allow-root-build): recovery mode, the"
        log "WARNING: tree is compiled from a location other accounts may alter"
        return 0
    fi
    id -u "$AWG_BUILD_USER" >/dev/null 2>&1 \
        || die "build user $AWG_BUILD_USER does not exist — create it first: useradd -s /sbin/nologin $AWG_BUILD_USER"
    BUILD_USER="$AWG_BUILD_USER"
    log "Building as $BUILD_USER"
}

# Print "trusted", or "untrusted: <path> owner <user> mode <mode>" for the first
# path component that root must not trust. A component is trusted when it is
# owned by the builder, or owned by root and not group/other writable (AWG-014).
tree_trust() {
    local p owner mode perm gw ow
    # Resolve symlinks first: a link owned by root points at a target that is
    # what the build actually reads and writes (/usr/src/sys -> /home/build/sys
    # is the usual lab setup), so the link's own ownership proves nothing.
    p=$(readlink -f "$1")
    [ -e "$p" ] || { echo "untrusted: $p does not exist"; return 0; }
    while :; do
        owner=$(stat -f '%Su' "$p" 2>/dev/null) || { echo "untrusted: $p cannot be stat'ed"; return 0; }
        mode=$(stat -f '%Sp' "$p" 2>/dev/null)
        perm=${mode#?}
        # rwxrwxrwx minus the type char: 1-3 owner, 4-6 group, 7-9 other.
        gw=$(printf '%s' "$perm" | cut -c5)
        ow=$(printf '%s' "$perm" | cut -c9)
        if [ "$owner" != "$BUILD_USER" ] && [ "$owner" != root ]; then
            echo "untrusted: $p owner $owner mode $mode (not $BUILD_USER)"
            return 0
        fi
        if [ "$owner" = root ] && { [ "$gw" = w ] || [ "$ow" = w ]; }; then
            echo "untrusted: $p owner $owner mode $mode (group/other writable)"
            return 0
        fi
        [ "$p" = / ] && break
        p=$(dirname "$p")
    done
    echo "trusted"
}

check_tree_trust() {
    local verdict=$1
    verdict=$(tree_trust "$1")
    [ "$verdict" = trusted ] || die "untrusted source tree: $verdict — $2"
}

# Hand the staged tree to the builder so config/make can write their objects.
hand_tree_to_builder() {
    local dir=$1
    resolve_build_user
    [ "$BUILD_USER" = root ] && { log "Staged tree stays root-owned (--allow-root-build)"; return 0; }
    log "Handing $dir to build user $BUILD_USER"
    chown -R "$BUILD_USER" "$dir" || die "cannot hand $dir to $BUILD_USER"
}

# builder_run <workdir> <command>: run one shell command as the build user.
builder_run() {   # builder_run <workdir> <command>
    local dir=$1 cmd=$2
    resolve_build_user
    [ -n "$BUILD_USER" ] || die "build user not resolved"
    if [ "$BUILD_USER" = root ]; then
        ( cd "$dir" && sh -c "$cmd" )
    elif [ "$(id -u)" -eq 0 ]; then
        # root needs no doas rule to change user; `doas -u` would require one
        # (AWG-039) and fails closed on a stock `permit ... as root` config.
        ( cd "$dir" && su -m "$BUILD_USER" -c "$cmd" )
    else
        ( cd "$dir" && doas -u "$BUILD_USER" sh -c "$cmd" )
    fi
}

# Every make in this script goes through builder_make, so the log names the
# account that compiled and root only runs make with --allow-root-build.
builder_make() {   # builder_make <workdir> [make args...]
    local dir=$1 cmd
    shift
    for a in "$@"; do cmd="$cmd \"$a\""; done
    resolve_build_user
    [ -n "$BUILD_USER" ] || die "build user not resolved before running make"
    [ "$BUILD_USER" != root ] || [ "$ALLOW_ROOT_BUILD" = yes ] \
        || die "build user resolved to root without --allow-root-build"
    log "building as $BUILD_USER"
    builder_run "$dir" "make$cmd"
}

# Verify a downloaded release file against the release's signed SHA256 list.
# signify(1) and /etc/signify are part of a stock OpenBSD install, so a file
# that cannot be verified must stop the install, not warn.
verify_release_file() {		# verify_release_file <filename>
    local name=$1 ver key sig
    ver=$(uname -r)
    key="${SIGNIFY_KEY:-/etc/signify/openbsd-$(echo "$ver" | tr -d '.')-base.pub}"
    [ -f "$key" ] || die "missing release key $key — cannot verify $name (install signify keys or fetch sources manually)"
    sig="$WORK/SHA256.sig"
    [ -f "$sig" ] || ftp -o "$sig" "$CDN/$ver/SHA256.sig" \
        || die "cannot fetch $CDN/$ver/SHA256.sig — refusing to build from unverified sources"
    sh "$REPODIR/tools/verify-release.sh" "$key" "$sig" "$WORK/$name" \
        >> "$LOGFILE" 2>&1 \
        || die "$name: signature or checksum verification failed (see $LOGFILE)"
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
        tar -C "$(dirname "$SRCDIR")" -xzf "$WORK/sys.tar.gz" || die "Failed to extract sys.tar.gz"
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

# Compare the pristine ifconfig file, not a CVS revision mistaken for an OS
# release. A patched tree must retain its matching original for this check.
verify_ifconfig_source() {
    local tree=$1 manifest=$2 original expected actual
    original="$tree/ifconfig.c"
    if grep -Eq 'A_AMNEZIAWG|awgversion' "$original"; then
        original="$tree/ifconfig.c.orig"
    fi
    [ -f "$manifest" ] && [ -f "$original" ] || die "ifconfig source identity unavailable"
    expected=$(awk '$2 == "sbin/ifconfig/ifconfig.c" {print $1}' "$manifest")
    actual=$(sha256 -q "$original") || die "cannot hash ifconfig source"
    [ -n "$expected" ] && [ "$actual" = "$expected" ] \
        || die "ifconfig source identity mismatch: move stale sources aside and fetch the recorded release"
}

# ── 3. ifconfig patch ─────────────────────────────────────────────────────────

# R4-02: build and publish are separate so --all can build ifconfig and the
# kernel first, then publish both. The artifact path is set by the build.
IFCFG_ARTIFACT=""

ifconfig_build() {
    step "ifconfig — AWG support patch (build)"

    local PATCH="$REPODIR/sbin/ifconfig/ifconfig_awg.patch"
    [ -f "$PATCH" ] || die "Patch not found: $PATCH"

    # If a kernel tree is present it must match the running release: the
    # ioctl ABI in the patch follows it. --tools-only does not download the
    # tree, so this is a check, not a requirement.
    if [ -f "$SRCDIR/conf/newvers.sh" ]; then
        local srcver
        srcver=$(sed -n 's/^osr="\(.*\)"$/\1/p' "$SRCDIR/conf/newvers.sh")
        [ "$srcver" = "$(uname -r)" ] \
            || die "$SRCDIR is OpenBSD $srcver, system is $(uname -r): refusing mixed sources"
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
        tar -C "$(dirname "$SRCDIR")" -xzf "$WORK/src.tar.gz" sbin/ifconfig \
            || die "Failed to extract sbin/ifconfig from src.tar.gz"
        [ -f "$IFCFG_SRC/ifconfig.c" ] || die "ifconfig.c not found after extraction"
        log "ifconfig sources extracted to $IFCFG_SRC"
    fi

    verify_ifconfig_source "$IFCFG_SRC" "$REPODIR/tools/upstream/$(uname -r).sha256"
    # Stage from the verified original and require an exact patch application.
    local original="$IFCFG_SRC/ifconfig.c"
    if grep -Eq 'A_AMNEZIAWG|awgversion' "$original"; then
        original="$IFCFG_SRC/ifconfig.c.orig"
    fi
    cp "$original" "$WORK/ifconfig.c"
    patch -C -F 0 "$WORK/ifconfig.c" "$PATCH" \
        || die "ifconfig patch does not match the recorded source"
    patch -F 0 "$WORK/ifconfig.c" "$PATCH" || die "ifconfig patch failed"
    if [ "$original" != "$IFCFG_SRC/ifconfig.c" ]; then
        cmp -s "$WORK/ifconfig.c" "$IFCFG_SRC/ifconfig.c" \
            || die "ifconfig has an old/local patch: preserve it elsewhere before rebuilding"
    else
        cp "$original" "$IFCFG_SRC/ifconfig.c.orig"
        cp "$WORK/ifconfig.c" "$IFCFG_SRC/ifconfig.c"
    fi
    # The driver header is staged in the run's private work directory;
    # publishing it into /usr/include is part of the pair transaction
    # (R4-02, R5). R6-N5: the build gets an explicit -I on that directory so
    # the patched ifconfig finds *this* header, not the installed one.
    stage_header

    # The build runs as the builder account; root only installs the artifact
    # (AWG-014). The staging directory must be readable by that account.
    check_tree_trust "$IFCFG_SRC" "rebuild the management tool from a tree the builder can write"
    hand_tree_to_builder "$IFCFG_SRC"
    hand_tree_to_builder "$WORK"
    log "Building ifconfig"
    builder_make "$IFCFG_SRC" "CPPFLAGS=-I$STAGE_DIR" || die "ifconfig build failed"
    # The binary lands in the object directory `make` chose for that tree, which
    # is not the caller's cwd now that the build runs in a subshell. Ask make
    # from inside the source directory instead of assuming ./obj.
    local objdir
    objdir=$(cd "$IFCFG_SRC" && make -V .OBJDIR) || die "cannot resolve the ifconfig object directory"
    [ -x "$objdir/ifconfig" ] || die "ifconfig build produced no binary in $objdir"
    IFCFG_ARTIFACT="$objdir/ifconfig"
    log "ifconfig built: $IFCFG_ARTIFACT"
}

publish_ifconfig() {
    [ -n "$IFCFG_ARTIFACT" ] || return 1
    install -c -s -o root -g bin -m 555 "$IFCFG_ARTIFACT" "$IFCFG_FILE" \
        || return 1
    [ -f "$IFCFG_SRC/ifconfig.8" ] \
        && install -c -o root -g bin -m 444 "$IFCFG_SRC/ifconfig.8" /usr/share/man/man8/ifconfig.8 2>/dev/null
    log "ifconfig installed to $IFCFG_FILE"
    return 0
}

# R5-R4-02: the header travels with the pair: staged by ifconfig_build,
# published here, never before the builds that read it.
publish_header() {
    [ -f "$IFAWG_STAGED" ] \
        || { log "cannot publish $IFAWG_HEADER: $IFAWG_STAGED was not staged"; return 1; }
    install -d -o root -g bin -m 755 /usr/include/net 2>/dev/null
    install -c -o root -g bin -m 444 "$IFAWG_STAGED" "$IFAWG_HEADER" || return 1
    cmp -s "$IFAWG_STAGED" "$IFAWG_HEADER" || return 1
    log "if_awg.h installed to $IFAWG_HEADER"
    return 0
}

# ── 4. awg-quick ─────────────────────────────────────────────────────────────

install_awg_quick() {
    step "awg-quick"

    install -d -o root -g wheel -m 755 "$(dirname "$AWG_QUICK_FILE")"
    install -m 755 -o root -g wheel "$REPODIR/sbin/awg-quick" "$AWG_QUICK_FILE"
    log "awg-quick installed to $AWG_QUICK_FILE"
}

# ── 5. AWG kernel driver ──────────────────────────────────────────────────────

# The management tool (awgctl) issues SIOCSAWG (223) and SIOCGAWG (224). Stock
# sys/kern/kern_pledge.c names neither in the PLEDGE_INET ioctl group, so the
# kernel charges them against PLEDGE_TTY and a pledged binary is SIGABRTed
# (docs/SECURITY-AUDIT.md AWG-038). R4-04: apply the minimal hunk that
# awg-openbsd.patch carries — extracted by file header and applied with
# patch(1) — instead of copying the whole release-specific file, and require
# the stock file to match the recorded release baseline. An unknown release
# file is refused, not overwritten.
install_pledge_hunk() {
    local base="$SRCDIR/kern/kern_pledge.c"
    local want awg have hunk

    hunk="$WORK/kern_pledge.awg.hunk"
    [ -f "$REPODIR/awg-openbsd.patch" ] \
        || die "missing $REPODIR/awg-openbsd.patch — the pledge hunk cannot be applied"
    # Extract the sys/kern/kern_pledge.c section (--- header through the next
    # --- header) so only the two cases and the include are applied.
    awk '/^---[ \t]sys\/kern\/kern_pledge\.c/{f=1; print; next}
         f && /^---[ \t]/{f=0}
         f' "$REPODIR/awg-openbsd.patch" > "$hunk" \
        || die "cannot extract the kern_pledge.c hunk from awg-openbsd.patch"
    [ -s "$hunk" ] || die "awg-openbsd.patch carries no kern_pledge.c hunk"
    grep -q 'SIOCSAWG' "$hunk" || die "kern_pledge.c hunk lacks SIOCSAWG"
    grep -q 'SIOCGAWG' "$hunk" || die "kern_pledge.c hunk lacks SIOCGAWG"

    # Identity gate: the hunk's context is only valid for the release the
    # baseline digest records. No recorded digest = refuse, do not guess.
    want=$(awk '$2 == "kern/kern_pledge.c" {print $1}' \
        "$REPODIR/tools/upstream/$(uname -r).sha256" 2>/dev/null)
    [ -n "$want" ] \
        || die "no $(uname -r) baseline recorded for kern/kern_pledge.c in tools/upstream — refusing"
    # R5-R4-04: the two accepted generations of the file — the stock release
    # baseline and the exact result of applying this hunk to it. A grep for
    # SIOCSAWG accepted a bogus one-liner and a partial patch (one case);
    # the hash of the whole file is the identity, and nothing else is taken.
    awg=$(awk '$2 == "kern/kern_pledge.c.awg" {print $1}' \
        "$REPODIR/tools/upstream/$(uname -r).sha256" 2>/dev/null)
    [ -n "$awg" ] \
        || die "no $(uname -r) patched hash recorded for kern/kern_pledge.c.awg in tools/upstream — refusing"

    if [ -f "$base" ]; then
        have=$(sha256 -q "$base") || die "cannot hash $base"
        case "$have" in
            "$want")
                ;;
            "$awg")
                log "kern/kern_pledge.c already carries SIOCSAWG/SIOCGAWG"
                return
                ;;
            *)
                die "$base is not a known kern_pledge.c generation for $(uname -r) (stock $want, patched $awg) — refusing to keep an unknown file"
                ;;
        esac
    else
        # No file in the tree: start from the tracked baseline so the applied
        # diff stays the minimal hunk (R4-04), not the whole repo file.
        [ -f "$REPODIR/src/kern_pledge.c.orig" ] \
            || die "missing $REPODIR/src/kern_pledge.c.orig — the pledge baseline is not available"
        [ "$(sha256 -q "$REPODIR/src/kern_pledge.c.orig")" = "$want" ] \
            || die "$REPODIR/src/kern_pledge.c.orig is not the recorded $(uname -r) baseline ($want)"
        mkdir -p "$SRCDIR/kern" && cp -p "$REPODIR/src/kern_pledge.c.orig" "$base" \
            || die "cannot create $base from the tracked baseline"
    fi

    log "Patching $SRCDIR/kern/kern_pledge.c for SIOCSAWG/SIOCGAWG"
    [ -f "$base" ] && cp -p "$base" "$base.awg-prev" 2>/dev/null
    patch -F 0 "$base" "$hunk" \
        || die "kern_pledge.c hunk did not apply cleanly to the $(uname -r) baseline"
    grep -q 'SIOCSAWG' "$base" || die "kern_pledge.c patch failed — SIOCSAWG not present"
    grep -q 'net/if_awg.h' "$base" || die "kern_pledge.c patch failed — net/if_awg.h not included"
    log "kern_pledge.c patched (SIOCSAWG + SIOCGAWG in PLEDGE_INET)"
}

# Build only: the artifact stays in the compile directory and /bsd is not
# touched. Used by CI and by anyone who wants to inspect a kernel before it
# becomes the boot kernel.
kernel_build() {
    step "AWG kernel driver"

    # R6-N5: stage the driver header in the run's private work directory even
    # when ifconfig is not rebuilt (--kernel-only), so publish_header always
    # publishes the header this run ships — never a stale root-level file.
    stage_header

    log "Copying driver files to $SRCDIR/net/"
    cp "$REPODIR/src/if_awg.h"    "$SRCDIR/net/if_awg.h"
    cp "$REPODIR/src/if_awg.c"    "$SRCDIR/net/if_awg.c"
    cp "$REPODIR/src/awg_noise.h" "$SRCDIR/net/awg_noise.h"
    cp "$REPODIR/src/awg_noise.c" "$SRCDIR/net/awg_noise.c"
    cp "$REPODIR/src/AWG.MP"      "$SRCDIR/arch/amd64/conf/AWG.MP"

    install_pledge_hunk

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

    # The verified tree is handed to the builder before config/make write into
    # it; root keeps only the fetch/verify/install roles (AWG-014).
    check_tree_trust "$SRCDIR" "the kernel build writes its objects here"
    hand_tree_to_builder "$SRCDIR"

    log "Configuring AWG.MP"
    builder_run "$SRCDIR/arch/amd64/conf" "config AWG.MP"

    # Not `make | tee`: the pipeline reports tee's status, so a build that died
    # half-way looks like a success and the installer carries on to install a
    # kernel that was never produced. Log to the file, report on failure.
    log "Building kernel — this takes ~15 minutes (log: $LOGFILE)"
    if ! builder_make "$SRCDIR/arch/amd64/compile/AWG.MP" -j"$(sysctl -n hw.ncpu)" >> "$LOGFILE" 2>&1; then
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

# R4-02: the kernel build (kernel_build above) and the publish are separate
# steps; --all builds ifconfig and the kernel, then publishes both.
kernel_publish() {
    local bsd

    step "Publish kernel"
    bsd=$(kernel_object_path)
    verify_kernel_artifact "$bsd"

    # Stage, then replace. A build that dies half-way must leave the running
    # kernel alone; the copy is verified again after the swap.
    log "Staging kernel to $BSD_STAGING"
    cp "$bsd" "$BSD_STAGING" || return 1
    chmod 700 "$BSD_STAGING"
    chown root:wheel "$BSD_STAGING"

    # The bootloader falls back to /obsd; keep it in step with the backup.
    ln -f "$BSD_BACKUP" /obsd 2>/dev/null || cp -p "$BSD_BACKUP" /obsd 2>/dev/null || true

    install -F -m 700 "$BSD_STAGING" "$BSD_FILE" || return 1
    rm -f "$BSD_STAGING"
    cmp -s "$bsd" "$BSD_FILE" || return 1
    sha256 -h "$KERNEL_SHA256" "$BSD_FILE" 2>/dev/null \
        || log "warning: could not update $KERNEL_SHA256 (syspatch will ask to rebuild it)"
    log "Kernel installed to $BSD_FILE — reboot to activate"
    log "If it does not boot: at the boot prompt, 'boot /bsd.awg-prev.$BACKUP_GEN'; after boot, sh $0 --rollback"
    return 0
}

# Release that produced this tree: git tag/commit, else the operator-provided
# AWG_RELEASE_TAG, else "unknown". Recorded so a rollback can be attributed.
release_tag() {
    local tag=""
    if command -v git >/dev/null 2>&1; then
        tag=$(git -C "$REPODIR" describe --tags --always 2>/dev/null || true)
    fi
    [ -n "$tag" ] || tag=${AWG_RELEASE_TAG:-unknown}
    printf '%s' "$tag"
}

# AWG-029 + R4-02: record the identity of the kernel, management tool and
# header, then back them up under a generation stamp. Each publish run gets a
# fresh generation; the stamped backups are immutable (R4-02: /obsd shared the
# backup inode when one file was re-copied). The manifest is written tmp +
# rename so a crash never leaves half a file.
#
# R5-R4-02: the generation is the max of the recorded one and every stamped
# backup that exists on disk. An interrupted manifest publication leaves the
# stamped files without a manifest, and a plain "manifest + 1" reused
# generation 1 and overwrote the kernel it had saved.
next_generation() {
    local g="" h f
    [ -f "$MANIFEST" ] && g=$(manifest_get "$MANIFEST" generation)
    case "$g" in ''|*[!0-9]*) g=0 ;; esac
    for f in "$BSD_BACKUP_BASE".* "$IFCFG_BACKUP_BASE".* \
             "$IFAWG_BACKUP_BASE".* "$AWGCTL_BACKUP_BASE".* \
             "$KERNEL_SHA256_BACKUP_BASE".*; do
        [ -e "$f" ] || continue
        case "$f" in *".awg-prev."*) h=${f##*.awg-prev.} ;; *) continue ;; esac
        case "$h" in ''|*[!0-9]*) continue ;; esac
        [ "$h" -gt "$g" ] && g=$h
    done
    echo $((g + 1))
}

# backup_member <source> <destination>: copy to a temp name, hash the copy and
# compare, then rename. R5-R4-02: the destination must not exist (a generation
# is used once, so `mv` is not forced) and the written bytes are verified
# against the source hash before the generation is considered taken.
backup_member() {   # backup_member <source> <destination>
    local src=$1 dst=$2 want got tmp
    [ -f "$src" ] || { echo "no $src to back up" >&2; return 1; }
    want=$(sha256 -q "$src") || { echo "cannot hash $src" >&2; return 1; }
    [ -e "$dst" ] && { echo "$dst already exists — the generation is not reusable" >&2; return 1; }
    tmp="$dst.tmp.$$"
    cp -p "$src" "$tmp" || { rm -f "$tmp"; echo "cannot copy $src to $tmp" >&2; return 1; }
    got=$(sha256 -q "$tmp") || { rm -f "$tmp"; echo "cannot hash $tmp" >&2; return 1; }
    [ "$got" = "$want" ] || { rm -f "$tmp"; echo "$tmp hash $got does not match $src ($want)" >&2; return 1; }
    mv "$tmp" "$dst" || { rm -f "$tmp"; echo "cannot publish $dst" >&2; return 1; }
    return 0
}

write_backup_pair() {
    local bsd_sha ifc_sha hdr_sha ctl_sha ksha_sha bsd_size ifc_size tmp

    if [ "$BACKUP_PAIR_DONE" = yes ]; then
        log "Backup pair already taken this run — keeping it"
        return 0
    fi
    BACKUP_PAIR_DONE=yes
    GENERATION=$(next_generation)
    BACKUP_GEN=$GENERATION
    BSD_BACKUP="${BSD_BACKUP_BASE}.$GENERATION"
    IFCFG_BACKUP="${IFCFG_BACKUP_BASE}.$GENERATION"
    IFAWG_BACKUP="${IFAWG_BACKUP_BASE}.$GENERATION"
    AWGCTL_BACKUP="${AWGCTL_BACKUP_BASE}.$GENERATION"
    KERNEL_SHA256_BACKUP="${KERNEL_SHA256_BACKUP_BASE}.$GENERATION"
    step "Backup pair (generation $GENERATION)"
    [ -f "$BSD_FILE" ] || die "no $BSD_FILE to back up"
    [ -f "$IFCFG_FILE" ] || die "no $IFCFG_FILE to back up"
    bsd_sha=$(sha256 -q "$BSD_FILE") || die "cannot hash $BSD_FILE"
    ifc_sha=$(sha256 -q "$IFCFG_FILE") || die "cannot hash $IFCFG_FILE"
    bsd_size=$(stat -f %z "$BSD_FILE") || die "cannot stat $BSD_FILE"
    ifc_size=$(stat -f %z "$IFCFG_FILE") || die "cannot stat $IFCFG_FILE"
    hdr_sha=""
    if [ -f "$IFAWG_HEADER" ]; then hdr_sha=$(sha256 -q "$IFAWG_HEADER"); fi
    ctl_sha=""
    if [ -f "$AWGCTL_FILE" ]; then ctl_sha=$(sha256 -q "$AWGCTL_FILE"); fi
    ksha_sha=""
    if [ -f "$KERNEL_SHA256" ]; then ksha_sha=$(sha256 -q "$KERNEL_SHA256"); fi

    mkdir -p "$INSTALL_DB" || die "cannot create $INSTALL_DB"
    chown root:wheel "$INSTALL_DB" 2>/dev/null || true
    chmod 700 "$INSTALL_DB" || die "cannot set mode on $INSTALL_DB"

    # Stamped backups: copy to a temp name, verify, then rename — the
    # generation file is complete and correct the moment it appears.
    backup_member "$BSD_FILE" "$BSD_BACKUP" \
        || die "could not save $BSD_FILE to $BSD_BACKUP"
    backup_member "$IFCFG_FILE" "$IFCFG_BACKUP" \
        || die "could not save $IFCFG_FILE to $IFCFG_BACKUP"
    if [ -f "$IFAWG_HEADER" ]; then
        backup_member "$IFAWG_HEADER" "$IFAWG_BACKUP" \
            || die "could not save $IFAWG_HEADER to $IFAWG_BACKUP"
    fi
    if [ -f "$AWGCTL_FILE" ]; then
        backup_member "$AWGCTL_FILE" "$AWGCTL_BACKUP" \
            || die "could not save $AWGCTL_FILE to $AWGCTL_BACKUP"
    fi
    if [ -f "$KERNEL_SHA256" ]; then
        backup_member "$KERNEL_SHA256" "$KERNEL_SHA256_BACKUP" \
            || die "could not save $KERNEL_SHA256 to $KERNEL_SHA256_BACKUP"
    fi

    tmp="$MANIFEST.tmp.$$"
    {
        printf 'schema=3\n'
        printf 'generation=%s\n' "$GENERATION"
        printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'uname_r=%s\n' "$(uname -r)"
        printf 'uname_v=%s\n' "$(uname -v)"
        printf 'release_tag=%s\n' "$(release_tag)"
        printf 'backup_generation=%s\n' "$BACKUP_GEN"
        printf 'backup_bsd_path=%s\n' "$BSD_BACKUP"
        printf 'backup_ifconfig_path=%s\n' "$IFCFG_BACKUP"
        printf 'backup_if_awg_h_path=%s\n' "$IFAWG_BACKUP"
        printf 'backup_awgctl_path=%s\n' "$AWGCTL_BACKUP"
        printf 'backup_bsd_sha256=%s\n' "$bsd_sha"
        printf 'backup_bsd_size=%s\n' "$bsd_size"
        printf 'backup_ifconfig_sha256=%s\n' "$ifc_sha"
        printf 'backup_ifconfig_size=%s\n' "$ifc_size"
        printf 'backup_if_awg_h_sha256=%s\n' "$hdr_sha"
        printf 'backup_awgctl_sha256=%s\n' "$ctl_sha"
        printf 'backup_kernel_sha256_path=%s\n' "$KERNEL_SHA256_BACKUP"
        printf 'backup_kernel_sha256_sha256=%s\n' "$ksha_sha"
    } > "$tmp" && mv -f "$tmp" "$MANIFEST" || die "cannot write $MANIFEST"
    chmod 600 "$MANIFEST" || die "cannot set mode on $MANIFEST"
    log "Backed up the pair: $BSD_BACKUP + $IFCFG_BACKUP (manifest: $MANIFEST)"
}

# Drop backup generations older than the previous one (kernel is ~30 MB).
prune_old_backups() {
    local keep_from g f
    keep_from=$((BACKUP_GEN - 1))
    for f in /bsd.awg-prev.* /sbin/ifconfig.awg-prev.* \
             /usr/include/net/if_awg.h.awg-prev.* /usr/local/sbin/awgctl.awg-prev.* \
             /var/db/kernel.SHA256.awg-prev.*; do
        [ -e "$f" ] || continue
        case "$f" in *".awg-prev."*) g=${f##*.awg-prev.} ;; *) continue ;; esac
        case "$g" in ''|*[!0-9]*) continue ;; esac
        [ "$g" -lt "$keep_from" ] && rm -f "$f" 2>/dev/null
    done
}

# manifest_get <key>: read one value out of the backup manifest.
manifest_get() {   # manifest_get <file> <key>
    awk -v k="$2" -F= '$1 == k { print substr($0, length(k) + 2); exit }' "$1"
}

# R5-R4-02 + R6 + R8: check the recorded hash of every member before any
# member is written, so a corrupt backup cannot leave a half-applied pair.
# R8: a member the manifest records (hash present) that is *missing* is an
# error, never a silent skip — "recorded but absent" names the unusable
# member and fails. A member the generation does not record is not an error.
prevalidate_pair_member() {   # <path> <expected-sha256> <label> <required>
    local path=$1 want=$2 label=$3 required=$4 got
    if [ ! -f "$path" ]; then
        [ "$required" = yes ] && { echo "missing $label backup $path" >&2; return 1; }
        return 0
    fi
    [ -n "$want" ] || { echo "$label backup $path has no recorded sha256" >&2; return 1; }
    got=$(sha256 -q "$path") || { echo "cannot hash $label backup $path" >&2; return 1; }
    [ "$got" = "$want" ] \
        || { echo "$label backup $path sha256 $got does not match the manifest ($want)" >&2; return 1; }
    return 0
}

# R4-14: a member the previous generation did not have is removed with it, so
# a rollback does not leave the newly installed awgctl (or header) behind.
remove_pair_member() {   # remove_pair_member <target>
    [ -e "$1" ] || return 0
    rm -f "$1" || { echo "could not remove $1" >&2; return 1; }
    log "Removed $1 (the restored generation did not have it)"
    return 0
}

# restore_pair_member <backup> <target> <expected-sha256> <mode> <group>
restore_pair_member() {
    local src=$1 dst=$2 want=$3 mode=$4 group=$5 got
    [ -f "$src" ] || { echo "missing backup $src" >&2; return 1; }
    got=$(sha256 -q "$src") || { echo "cannot hash $src" >&2; return 1; }
    if [ -n "$want" ] && [ "$got" != "$want" ]; then
        echo "$src sha256 $got does not match the manifest ($want)" >&2
        return 1
    fi
    install -F -o root -g "$group" -m "$mode" "$src" "$dst" \
        || { echo "could not install $dst from $src" >&2; return 1; }
    cmp -s "$src" "$dst" || { echo "$dst does not match $src after install" >&2; return 1; }
    log "Restored $dst (sha256 $got)"
    return 0
}

# Restore the pair. With --rollback-kernel-only only /bsd is restored, which is
# documented as unsafe because the management tool may not match the kernel.
# Record what was actually installed, next to the backup pair. KARL relinks
# /bsd at boot, so this hash is the *staged* artifact, not what /bsd will hold
# after the reboot: --check compares /var/db/kernel.SHA256 for integrity and
# only reports the difference here as a relink note (AWG-029). R4-01 records
# the size too so the pre-exit check can verify sha256 AND stat size; R4-02
# rewrites the whole manifest via tmp + rename.
prepare_durability_helper() {
    # OpenBSD has no fsync(1); compile a fixed, dependency-free helper with
    # base cc. sync(8) may return before writes finish and is not a commit.
    if [ ! -x "$WORK/awg-fsync" ]; then
        cc -x c -o "$WORK/awg-fsync" - <<'C' || die "cannot build commit durability helper"
        #include <sys/types.h>
        #include <fcntl.h>
        #include <unistd.h>
        #include <stdio.h>
        int main(int argc, char **argv) {
            int i, fd;
            for (i = 1; i < argc; i++) {
                fd = open(argv[i], O_RDONLY);
                if (fd == -1) { perror(argv[i]); return 1; }
                if (fsync(fd) == -1) { perror(argv[i]); close(fd); return 1; }
                if (close(fd) == -1) { perror(argv[i]); return 1; }
            }
            return 0;
        }
C
    fi
}

record_installed_pair() {
    local kv tmp bsd_sha ifc_sha bsd_size ifc_size hdr_sha ctl_sha
    prepare_durability_helper
    [ -f "$MANIFEST" ] || die "no manifest at $MANIFEST — cannot record the installed pair"
    bsd_sha=$(sha256 -q "$BSD_FILE") || die "cannot hash installed kernel"
    ifc_sha=$(sha256 -q "$IFCFG_FILE") || die "cannot hash installed ifconfig"
    bsd_size=$(stat -f %z "$BSD_FILE") || die "cannot stat installed kernel"
    ifc_size=$(stat -f %z "$IFCFG_FILE") || die "cannot stat installed ifconfig"
    hdr_sha=""
    ctl_sha=""
    [ ! -f "$IFAWG_HEADER" ] || hdr_sha=$(sha256 -q "$IFAWG_HEADER") || die "cannot hash installed header"
    [ ! -f "$AWGCTL_FILE" ] || ctl_sha=$(sha256 -q "$AWGCTL_FILE") || die "cannot hash installed awgctl"
    kv="$WORK/manifest.kv"
    {
        printf 'installed_bsd_sha256=%s\n' "$bsd_sha"
        printf 'installed_bsd_size=%s\n' "$bsd_size"
        printf 'installed_ifconfig_sha256=%s\n' "$ifc_sha"
        printf 'installed_ifconfig_size=%s\n' "$ifc_size"
        printf 'installed_if_awg_h_sha256=%s\n' "$hdr_sha"
        printf 'installed_awgctl_sha256=%s\n' "$ctl_sha"
        printf 'installed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$kv" || die "cannot write $kv — cannot record the installed pair"
    tmp="$MANIFEST.tmp.$$"
    awk -v kvf="$kv" '
        BEGIN {
            while ((getline line < kvf) > 0) {
                k = line; sub(/=.*/, "", k)
                if (!(k in seen)) { seen[k] = 1; order[++n] = k }
                val[k] = substr(line, index(line, "=") + 1)
            }
        }
        {
            k = $0; sub(/=.*/, "", k)
            if (k in seen) { print k "=" val[k]; delete seen[k]; next }
            print
        }
        END {
            for (i = 1; i <= n; i++) { k = order[i]; if (k in seen) print k "=" val[k] }
        }
    ' "$MANIFEST" > "$tmp" && "$WORK/awg-fsync" "$tmp" \
        && mv -f "$tmp" "$MANIFEST" \
        || die "could not record the installed pair in $MANIFEST"
    # The commit is not complete until both content and directory entry are
    # durable. Also flush installed members and their parent directories.
    for kv in "$BSD_FILE" "$IFCFG_FILE" "$IFAWG_HEADER" "$AWGCTL_FILE"; do
        [ ! -f "$kv" ] || "$WORK/awg-fsync" "$kv" "$(dirname "$kv")" \
            || die "cannot make installed member $kv durable"
    done
    "$WORK/awg-fsync" "$MANIFEST" "$(dirname "$MANIFEST")" \
        || die "cannot make commit record durable"
    log "Recorded the installed pair in $MANIFEST"
}

# R4-14: awgctl is part of the release delivery: built from sbin/awgctl with
# the same builder, installed next to awg-quick, recorded in the pair
# manifest and restored by --rollback together with kernel + tool + header.
install_awgctl() {
    step "awgctl — management tool (build)"
    hand_tree_to_builder "$REPODIR/sbin/awgctl"
    log "Building awgctl"
    builder_make "$REPODIR/sbin/awgctl" || die "awgctl build failed"
    [ -x "$REPODIR/sbin/awgctl/awgctl" ] || die "awgctl build produced no binary"
    AWGCTL_ARTIFACT="$REPODIR/sbin/awgctl/awgctl"
    log "awgctl built: $AWGCTL_ARTIFACT"
}

publish_awgctl() {
    [ -x "$AWGCTL_ARTIFACT" ] || return 1
    install -d -o root -g wheel -m 755 /usr/local/sbin || return 1
    install -c -s -o root -g wheel -m 555 "$AWGCTL_ARTIFACT" "$AWGCTL_FILE" || return 1
    # R4-14 (R6): the manpage ships with the binary.
    [ -f "$REPODIR/sbin/awgctl/awgctl.8" ] \
        && { install -d -o root -g bin -m 755 "$AWGCTL_MAN_DIR" 2>/dev/null; \
             install -c -o root -g bin -m 444 "$REPODIR/sbin/awgctl/awgctl.8" \
                 "$AWGCTL_MAN_DIR/awgctl.8" || return 1; }
    log "awgctl installed to $AWGCTL_FILE (with awgctl.8)"
    return 0
}

# R6-R4-02: signal handler for an interrupted publish: restore the recorded
# generation (documented state: the previous pair is live, the manifest keeps
# its hashes), then exit non-zero.
publish_interrupted() {
    publish_abort "Interrupted mid-publish"
}

# 20 means verified rollback; 21 means recovery failed (manual action, the
# message names the state the tree is left in); 22 means nothing to roll back
# (no recorded generation). Ignore further termination signals during
# recovery, never during normal work.
rollback_uncommitted() {
    local reason=$1 member
    trap '' HUP INT TERM
    PUBLISH_STARTED=no
    if [ ! -f "$MANIFEST" ]; then
        echo "ERROR: $reason: nothing to roll back (no manifest at $MANIFEST)" >&2
        return 22
    fi
    if restore_previous_pair; then
        if [ -f "$WORK/precommit.manifest" ]; then
            cp "$WORK/precommit.manifest" "$MANIFEST.rollback.$$" \
                && mv -f "$MANIFEST.rollback.$$" "$MANIFEST" \
                || { echo "ERROR: $reason: rollback failed (state: manifest restore)" >&2; return 21; }
        fi
        if [ -x "$WORK/awg-fsync" ]; then
            for member in "$BSD_FILE" "$IFCFG_FILE" "$IFAWG_HEADER" "$AWGCTL_FILE" "$MANIFEST"; do
                [ ! -f "$member" ] || "$WORK/awg-fsync" "$member" "$(dirname "$member")" \
                    || { echo "ERROR: $reason: rollback failed (state: durability)" >&2; return 21; }
            done
        fi
        echo "ERROR: $reason: rolled back; previous pair restored from generation $BACKUP_GEN ($BSD_BACKUP + $IFCFG_BACKUP)" >&2
        return 20
    fi
    echo "ERROR: $reason: rollback failed (state: ${RESTORE_STATE:-nothing written})" >&2
    return 21
}

publish_abort() {
    if rollback_uncommitted "$1"; then
        exit 20
    else
        exit $?
    fi
}

# R4-02: publish the built artifacts as one transaction. The backup pair is
# taken first; if any publish step fails after it begins, the previous pair is
# restored from the generation-stamped backups and the run says so.
# R6-R4-02: PUBLISH_STARTED is a recovery trap — a signal that lands mid-
# publish runs the restore, so an interrupted run does not leave a half-
# published pair (new kernel with the old tool).
publish_pair() {
    step "Publish kernel + management tool (transactional)"
    prepare_durability_helper
    write_backup_pair
    cp "$MANIFEST" "$WORK/precommit.manifest" || die "cannot save precommit manifest"
    PUBLISH_STARTED=yes
    trap 'publish_interrupted' HUP INT TERM
    if ! publish_ifconfig; then
        publish_abort "ifconfig publish failed"
    fi
    if ! publish_header; then
        publish_abort "header publish failed"
    fi
    if ! kernel_publish; then
        publish_abort "kernel publish failed"
    fi
    if ! publish_awgctl; then
        publish_abort "awgctl publish failed"
    fi
    # Catch die/errexit as well as an ordinary return from the recorder.
    if ! (record_installed_pair); then
        publish_abort "commit record failed"
    fi
    PUBLISH_STARTED=no
    prune_old_backups
    trap - HUP INT TERM
    log "Published generation $GENERATION: /bsd + /sbin/ifconfig + header + awgctl"
}

# R5-R4-02 + R4-14 + R8: restore one recorded generation as a unit. Every
# recorded member's hash is validated before the first write (a corrupt
# backup must not leave a half-applied pair), a missing member or a failed
# copy is an error, and members the recorded generation does not have are
# removed so a first-install rollback does not keep the newly installed
# awgctl/header. R8: header/awgctl are required when the manifest records
# their hash, and /var/db/kernel.SHA256 is restored as the final member so
# the integrity DB describes the kernel that comes back. RESTORE_STATE
# records how far the restore got, so a failure reports the honest state.
# restore_generation_pair <bsd_path> <ifc_path> <hdr_path> <ctl_path>
#                         <bsd_sha> <ifc_sha> <hdr_sha> <ctl_sha>
#                         <ksha_path> <ksha_sha>
restore_generation_pair() {
    local bsd_path=$1 ifc_path=$2 hdr_path=$3 ctl_path=$4
    local bsd_sha=$5 ifc_sha=$6 hdr_sha=$7 ctl_sha=$8
    local ksha_path=$9 ksha_sha=${10}
    local hdr_req=no ctl_req=no ksha_req=no
    [ -n "$hdr_sha" ] && hdr_req=yes
    [ -n "$ctl_sha" ] && ctl_req=yes
    [ -n "$ksha_sha" ] && ksha_req=yes

    RESTORE_STATE="nothing written"
    prevalidate_pair_member "$bsd_path" "$bsd_sha" kernel yes || return 1
    prevalidate_pair_member "$ifc_path" "$ifc_sha" ifconfig yes || return 1
    prevalidate_pair_member "$hdr_path" "$hdr_sha" header "$hdr_req" || return 1
    prevalidate_pair_member "$ctl_path" "$ctl_sha" awgctl "$ctl_req" || return 1
    prevalidate_pair_member "$ksha_path" "$ksha_sha" kernel.SHA256 "$ksha_req" \
        || return 1

    if ! restore_pair_member "$ifc_path" "$IFCFG_FILE" "$ifc_sha" 555 bin; then
        return 1
    fi
    RESTORE_STATE="ifconfig restored"
    if [ -f "$hdr_path" ]; then
        if ! restore_pair_member "$hdr_path" "$IFAWG_HEADER" "$hdr_sha" 444 bin; then
            return 1
        fi
    elif ! remove_pair_member "$IFAWG_HEADER"; then
        return 1
    fi
    RESTORE_STATE="ifconfig + header restored"
    if [ -f "$ctl_path" ]; then
        if ! restore_pair_member "$ctl_path" "$AWGCTL_FILE" "$ctl_sha" 555 wheel; then
            return 1
        fi
    elif ! remove_pair_member "$AWGCTL_FILE"; then
        return 1
    fi
    RESTORE_STATE="ifconfig + header + awgctl restored"
    if ! restore_pair_member "$bsd_path" "$BSD_FILE" "$bsd_sha" 700 wheel; then
        return 1
    fi
    RESTORE_STATE="ifconfig + header + awgctl + kernel restored"
    if [ -f "$ksha_path" ]; then
        if ! restore_pair_member "$ksha_path" "$KERNEL_SHA256" "$ksha_sha" 644 wheel; then
            return 1
        fi
    fi
    RESTORE_STATE="kernel + ifconfig + header + awgctl + kernel.SHA256 restored"
    return 0
}

# Restore the whole pair — kernel, tool, header and awgctl — from the
# generation-stamped backups of the current run (R5-R4-02: the kernel was
# missing from this path, so a failure after kernel publication left the new
# kernel with the old management tool while the log claimed a pair restore).
restore_previous_pair() {
    local bsd_path ifc_path hdr_path ctl_path bsd_sha ifc_sha hdr_sha ctl_sha
    local ksha_path ksha_sha
    step "Restoring the previous pair (generation $BACKUP_GEN)"
    bsd_path=$(manifest_get "$MANIFEST" backup_bsd_path)
    ifc_path=$(manifest_get "$MANIFEST" backup_ifconfig_path)
    hdr_path=$(manifest_get "$MANIFEST" backup_if_awg_h_path)
    ctl_path=$(manifest_get "$MANIFEST" backup_awgctl_path)
    ksha_path=$(manifest_get "$MANIFEST" backup_kernel_sha256_path)
    [ -n "$bsd_path" ] || bsd_path="$BSD_BACKUP"
    [ -n "$ifc_path" ] || ifc_path="$IFCFG_BACKUP"
    [ -n "$hdr_path" ] || hdr_path="$IFAWG_BACKUP"
    [ -n "$ctl_path" ] || ctl_path="$AWGCTL_BACKUP"
    [ -n "$ksha_path" ] || ksha_path="$KERNEL_SHA256_BACKUP"
    bsd_sha=$(manifest_get "$MANIFEST" backup_bsd_sha256)
    ifc_sha=$(manifest_get "$MANIFEST" backup_ifconfig_sha256)
    hdr_sha=$(manifest_get "$MANIFEST" backup_if_awg_h_sha256)
    ctl_sha=$(manifest_get "$MANIFEST" backup_awgctl_sha256)
    ksha_sha=$(manifest_get "$MANIFEST" backup_kernel_sha256_sha256)
    if ! restore_generation_pair "$bsd_path" "$ifc_path" "$hdr_path" "$ctl_path" \
            "$bsd_sha" "$ifc_sha" "$hdr_sha" "$ctl_sha" \
            "$ksha_path" "$ksha_sha"; then
        return 1
    fi
    log "Previous pair restored (generation $BACKUP_GEN: $BSD_FILE + $IFCFG_FILE + $IFAWG_HEADER + $AWGCTL_FILE + $KERNEL_SHA256)"
}

# R4-02 + R5: --rollback restores the whole recorded generation (kernel, tool,
# header, awgctl) from the generation-stamped paths in the manifest, removing
# members that generation did not have, then rewrites the installed-pair keys.
rollback_pair() {
    local bsd_path ifc_path hdr_path ctl_path bsd_sha ifc_sha hdr_sha ctl_sha
    local ksha_path ksha_sha

    step "Rollback to the previous kernel/tool/header/awgctl generation"
    [ -f "$MANIFEST" ] || die "no backup manifest at $MANIFEST — nothing to roll back"
    bsd_path=$(manifest_get "$MANIFEST" backup_bsd_path)
    ifc_path=$(manifest_get "$MANIFEST" backup_ifconfig_path)
    hdr_path=$(manifest_get "$MANIFEST" backup_if_awg_h_path)
    ctl_path=$(manifest_get "$MANIFEST" backup_awgctl_path)
    ksha_path=$(manifest_get "$MANIFEST" backup_kernel_sha256_path)
    [ -n "$bsd_path" ] || bsd_path="$BSD_BACKUP"
    [ -n "$ifc_path" ] || ifc_path="$IFCFG_BACKUP"
    [ -n "$hdr_path" ] || hdr_path="$IFAWG_BACKUP"
    [ -n "$ctl_path" ] || ctl_path="$AWGCTL_BACKUP"
    [ -n "$ksha_path" ] || ksha_path="$KERNEL_SHA256_BACKUP"
    bsd_sha=$(manifest_get "$MANIFEST" backup_bsd_sha256)
    ifc_sha=$(manifest_get "$MANIFEST" backup_ifconfig_sha256)
    hdr_sha=$(manifest_get "$MANIFEST" backup_if_awg_h_sha256)
    ctl_sha=$(manifest_get "$MANIFEST" backup_awgctl_sha256)
    ksha_sha=$(manifest_get "$MANIFEST" backup_kernel_sha256_sha256)
    [ -n "$bsd_sha" ] || die "manifest $MANIFEST records no bsd_sha256"
    [ -f "$bsd_path" ] || die "no $bsd_path to roll back to — boot /obsd from the bootloader instead"

    if [ "$ROLLBACK_KERNEL_ONLY" = yes ]; then
        if ! restore_pair_member "$bsd_path" "$BSD_FILE" "$bsd_sha" 700 wheel; then
            die "kernel rollback failed: $BSD_FILE not restored"
        fi
        sha256 -h "$KERNEL_SHA256" "$BSD_FILE" 2>/dev/null \
            || log "warning: could not update $KERNEL_SHA256 (syspatch will ask to rebuild it)"
        log "Kernel-only rollback: $BSD_FILE restored, $IFCFG_FILE left as-is"
    else
        [ -n "$ifc_sha" ] || die "manifest $MANIFEST records no ifconfig_sha256"
        [ -f "$ifc_path" ] \
            || die "no $ifc_path: the backup pair is incomplete — pass --rollback-kernel-only to restore /bsd alone (unsafe: kernel and management tool may not match)"
        if ! restore_generation_pair "$bsd_path" "$ifc_path" "$hdr_path" "$ctl_path" \
                "$bsd_sha" "$ifc_sha" "$hdr_sha" "$ctl_sha" \
                "$ksha_path" "$ksha_sha"; then
            die "generation $BACKUP_GEN rollback failed (state: ${RESTORE_STATE:-nothing written}): the recorded members were not applied as a set"
        fi
        # R8: the recorded generation carries its own kernel.SHA256; only a
        # generation that predates that member needs a re-hash.
        [ -n "$ksha_sha" ] || sha256 -h "$KERNEL_SHA256" "$BSD_FILE" 2>/dev/null \
            || log "warning: could not update $KERNEL_SHA256 (syspatch will ask to rebuild it)"
        log "Quadruple restored: $BSD_FILE + $IFCFG_FILE + $IFAWG_HEADER + $AWGCTL_FILE (+ $KERNEL_SHA256) — reboot to activate the kernel"
    fi
    record_installed_pair
}

# Report what an install would do and stop. Nothing is written, so this is safe
# to run on a production box before a maintenance window.
check_plan() {
    local rc=0 key ver ifcfg_ver m_tag m_v m_bsd m_ifc bsd_sha ifc_sha trust v

    step "Pre-flight check"
    ver=$(uname -r)
    printf 'os            : %s %s (%s)\n' "$(uname -s)" "$ver" "$(uname -m)"
    [ "$(uname -m)" = amd64 ] || { printf 'arch          : unsupported (amd64 only)\n'; rc=1; }

    key="${SIGNIFY_KEY:-/etc/signify/openbsd-$(echo "$ver" | tr -d '.')-base.pub}"
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
        if (verify_ifconfig_source "$IFCFG_SRC" "$REPODIR/tools/upstream/$ver.sha256"); then
            printf 'ifconfig src  : recorded %s baseline\n' "$ver"
        else
            printf 'ifconfig src  : identity check FAILED\n'
            rc=1
        fi
    else
        printf 'ifconfig src  : not present, would be downloaded\n'
    fi

    printf 'running kernel: %s\n' "$(uname -v)"

    # AWG-029: report the kernel + management tool pair against the manifest.
    if [ -f "$MANIFEST" ]; then
        m_tag=$(manifest_get "$MANIFEST" release_tag)
        m_v=$(manifest_get "$MANIFEST" uname_v)
        m_bsd=$(manifest_get "$MANIFEST" installed_bsd_sha256)
        m_ifc=$(manifest_get "$MANIFEST" installed_ifconfig_sha256)
        m_bsdp=$(manifest_get "$MANIFEST" backup_bsd_path)
        m_ifcp=$(manifest_get "$MANIFEST" backup_ifconfig_path)
        [ -n "$m_bsdp" ] || m_bsdp="$BSD_BACKUP"
        [ -n "$m_ifcp" ] || m_ifcp="$IFCFG_BACKUP"
        printf 'manifest      : %s (release_tag %s, kernel %s)\n' "$MANIFEST" "$m_tag" "$m_v"
        bsd_sha=$(sha256 -q "$BSD_FILE" 2>/dev/null || true)
        # KARL relinks /bsd at boot, so the hash of a correctly installed kernel
        # changes legitimately. Integrity is /var/db/kernel.SHA256's job; the
        # manifest hash is only meaningful before the first reboot.
        if [ -f "$KERNEL_SHA256" ]; then
            want_k=$(awk '/^SHA256 \(\/bsd\)/ { print $NF }' "$KERNEL_SHA256" 2>/dev/null)
            if [ -n "$want_k" ] && [ "$bsd_sha" = "$want_k" ]; then
                printf 'kernel sha256 : matches %s\n' "$KERNEL_SHA256"
            else
                printf 'kernel sha256 : MISMATCH against %s (%s)\n' "$KERNEL_SHA256" "${want_k:-unreadable}"
                rc=1
            fi
        elif [ -n "$m_bsd" ] && [ "$bsd_sha" = "$m_bsd" ]; then
            printf 'kernel sha256 : matches manifest\n'
        else
            printf 'kernel sha256 : not recorded by this installer (no %s)\n' "$KERNEL_SHA256"
        fi
        if [ -n "$m_bsd" ] && [ -n "$bsd_sha" ] && [ "$bsd_sha" != "$m_bsd" ]; then
            printf 'relink        : /bsd differs from the staged kernel (KARL relink, expected after boot)\n'
        fi
        ifc_sha=$(sha256 -q "$IFCFG_FILE" 2>/dev/null || true)
        if [ -n "$m_ifc" ] && [ "$ifc_sha" = "$m_ifc" ]; then
            printf 'ifconfig sha  : matches manifest\n'
        else
            printf 'ifconfig sha  : MISMATCH (%s vs manifest %s)\n' "${ifc_sha:-unreadable}" "$m_ifc"
            rc=1
        fi
    else
        printf 'manifest      : no %s yet (nothing installed by this installer)\n' "$MANIFEST"
    fi

    if [ -x "$IFCFG_FILE" ] && grep -aq awgversion "$IFCFG_FILE" 2>/dev/null; then
        printf 'ifconfig awg  : awgversion supported\n'
    else
        printf 'ifconfig awg  : no awgversion support (--tools-only adds it)\n'
    fi

    if [ -f "$m_bsdp" ] && [ -f "$m_ifcp" ]; then
        printf 'rollback      : pair present (%s + %s)\n' "$m_bsdp" "$m_ifcp"
    elif [ -f "$m_bsdp" ]; then
        printf 'rollback      : PARTIAL %s only (--rollback needs --rollback-kernel-only)\n' "$m_bsdp"
    elif [ -f "$m_ifcp" ]; then
        printf 'rollback      : PARTIAL %s only (--rollback needs --rollback-kernel-only)\n' "$m_ifcp"
    else
        printf 'rollback      : none yet (the first install keeps the current pair)\n'
    fi

    # AWG-014: which account compiles, and is the tree it compiles from trusted.
    resolve_build_user
    printf 'build user    : %s\n' "$BUILD_USER"
    trust=$(tree_trust "$SRCDIR")
    printf 'src trust     : %s (%s)\n' "$trust" "$SRCDIR"
    [ "$trust" = trusted ] || rc=1
    if [ -d "$IFCFG_SRC" ]; then
        v=$(tree_trust "$IFCFG_SRC")
        printf 'ifconfig trust: %s (%s)\n' "$v" "$IFCFG_SRC"
        [ "$v" = trusted ] || rc=1
    fi

    printf 'disk free     : %s\n' "$(df -h / | awk 'NR==2 {print $4}')"

    printf '\nplan: %s\n' \
        'fetch+verify sources as root, hand the verified tree to the build user,'
    printf '      %s\n' \
        'config AWG.MP + make as that user, verify the artifact, stage it,'
    printf '      %s\n' \
        'back up /bsd + /sbin/ifconfig as a pair, install, reboot.'
    [ "$rc" -eq 0 ] || printf '\nresult: %s\n' 'a precondition above would fail — fix it before running --kernel-only'
    [ "$rc" -eq 0 ] && printf '\nresult: ready\n'
    exit "$rc"
}

# ── 6. Config directory ───────────────────────────────────────────────────────

setup_confdir() {
    if [ ! -d "$AWG_CONF_DIR" ]; then
        log "Creating $AWG_CONF_DIR"
        mkdir -p "$AWG_CONF_DIR"
    fi
    # The directory holds private keys; enforce 0700 even when it already
    # existed with a looser mode.
    chmod 700 "$AWG_CONF_DIR"
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
Usage: $0 [--tools-only | --kernel-only | --build-only | --rollback | --check | --all]
          [--pf] [--rollback-kernel-only] [--allow-root-build]

  --check        Report what would be done and stop (nothing is changed)
  --tools-only   Patched ifconfig (+ ifconfig.8) + net/if_awg.h + awg-quick;
                 no awgctl (no reboot needed)
  --build-only   Build the kernel and stop; /bsd is not touched
  --kernel-only  AWG kernel + net/if_awg.h; no awgctl (requires reboot)
  --all          Kernel + patched ifconfig + net/if_awg.h + awgctl (with its
                 awgctl.8 manpage) + awg-quick (default)
  --rollback     Put the previous /bsd + /sbin/ifconfig pair back
  --rollback-kernel-only
                 With --rollback: restore /bsd only and leave /sbin/ifconfig
                 as-is. Unsafe: the kernel and its management tool may not
                 match; use it when only the ifconfig backup is missing.
  --allow-root-build
                 Compile as root instead of the build user. Recovery only:
                 the tree is then writable by other accounts (AWG-014).
  --pf           Also add the MSS clamp rule to an existing /etc/pf.conf
                 (default: print the rule and leave the firewall alone)

Environment:
  AWG_BUILD_USER   Account that runs config/make (default: _awgbuild).
                   Create it with: useradd -s /sbin/nologin _awgbuild
  AWG_RELEASE_TAG  Release tag recorded in the backup manifest (default: git
                   describe of the repo, else "unknown")

The kernel is staged and verified before /bsd is replaced. The kernel,
/sbin/ifconfig, the driver header, awgctl and the kernel integrity DB
(/var/db/kernel.SHA256) are kept as one pair, recorded in
/var/db/awg-install/manifest under a generation stamp
(/bsd.awg-prev.<gen>, /sbin/ifconfig.awg-prev.<gen>, ...): --all builds
every artifact before publishing any of them, and --rollback restores
kernel + tool + header (+ awgctl + kernel.SHA256) together and rewrites
the manifest.
One exclusive lock (/var/db/awg-install/lock) serialises two runs; a second
run refuses while the first holds it.

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
        --tools-only)          MODE=tools ;;
        --kernel-only)         MODE=kernel ;;
        --build-only)          MODE=build ;;
        --rollback)            MODE=rollback ;;
        --check)               MODE=check ;;
        --all)                 MODE=all ;;
        --pf)                  EDIT_PF=yes ;;
        --rollback-kernel-only) ROLLBACK_KERNEL_ONLY=yes ;;
        --allow-root-build)    ALLOW_ROOT_BUILD=yes ;;
        -h|--help)             usage ;;
        *) die "Unknown option: $arg. Run $0 --help for usage." ;;
    esac
done

check_root
acquire_lock
resolve_build_user
: > "$LOGFILE"

case "$MODE" in
    tools)
        prepare_durability_helper
        write_backup_pair
        cp "$MANIFEST" "$WORK/precommit.manifest" || die "cannot save precommit manifest"
        PUBLISH_STARTED=yes
        trap 'publish_interrupted' HUP INT TERM
        install_packages
        ifconfig_build
        publish_ifconfig
        publish_header
        record_installed_pair
        PUBLISH_STARTED=no
        trap - HUP INT TERM
        install_awg_quick
        setup_confdir
        setup_pf_mss
        ;;
    kernel)
        prepare_durability_helper
        write_backup_pair
        fetch_sys_sources
        kernel_build
        cp "$MANIFEST" "$WORK/precommit.manifest" || die "cannot save precommit manifest"
        PUBLISH_STARTED=yes
        trap 'publish_interrupted' HUP INT TERM
        publish_header
        kernel_publish
        record_installed_pair
        PUBLISH_STARTED=no
        trap - HUP INT TERM
        prune_old_backups
        ;;
    build)
        fetch_sys_sources
        kernel_build
        ;;
    rollback)
        rollback_pair
        ;;
    check)
        check_plan
        ;;
    all)
        # R4-02: build ifconfig, awgctl and the kernel first, then publish
        # the pair as one transaction.
        install_packages
        fetch_sys_sources
        ifconfig_build
        install_awgctl
        kernel_build
        publish_pair
        install_awg_quick
        setup_confdir
        setup_pf_mss
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
