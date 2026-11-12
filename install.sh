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
REPODIR=${REPODIR:-$(dirname "$SCRIPT")}
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
# AWG-014 R1 (D-2): the fsync helper is compiled once into the root-owned
# install DB, not into the builder-writable $WORK, so a builder-planted
# executable cannot be the binary root executes.
FSYNC_HELPER=${AWG_FSYNC_HELPER:-$INSTALL_DB/awg-fsync}
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
# Overridable paths for uninstall artifact restoration (test fixtures)
OBSD_FILE=${OBSD_FILE:-/obsd}
PF_CONF_FILE=${PF_CONF_FILE:-/etc/pf.conf}
# F9: kernel relink (KARL) objects. reorder_kernel(8) relinks the kernel of the
# ident in kern.osversion from this directory; the ident is AWG.MP because that
# is the config this installer builds and publishes.
RELINK_KERNEL_DIR=${RELINK_KERNEL_DIR:-/usr/share/relink/kernel}
KERNEL_IDENT=${KERNEL_IDENT:-AWG.MP}
RELINK_SKIP=no
RELINK_STATE=unknown
RELINK_BACKUP=""
ROLLBACK_KERNEL_ONLY=no
BACKUP_PAIR_DONE=no
GENERATION=0
BACKUP_GEN=""
PUBLISH_STARTED=no
# F9: how far publish_relink_objects got in this process (none|swapped|rolled).
# The interrupted-publish restore reads this instead of the manifest, which at
# that point still carries the previous generation's keys.
RELINK_PUBLISH_STATE=none

# Nothing here may live at a predictable /tmp path: this script runs as root
# and an unprivileged user can pre-create a symlink at a fixed name. The work
# directory holds the release tarballs (~300 MB), so prefer /usr/src when it
# exists: on a VPS / is small and /tmp lives on it.
LOGFILE=$(mktemp "${TMPDIR:-/tmp}/awg-install-log.XXXXXX") || { echo "mktemp failed" >&2; exit 1; }
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


# F8: with KARL, /etc/rc runs /usr/libexec/reorder_kernel at every boot and
# that script relinks /bsd from the objects in /usr/share/relink/kernel and
# rewrites /var/db/kernel.SHA256 for the kernel it just installed. After a
# relink the manifest's installed_bsd_sha256 (the *staged* artifact) can never
# match /bsd again, so a check that only compared the manifest reported a
# healthy host as broken. The integrity DB is the authority the system itself
# trusts: when it records a hash for /bsd and /bsd hashes to it, the installed
# kernel is verified — the manifest difference is the expected relink.
kernel_db_sha() {   # print the sha256 recorded for $BSD_FILE, or nothing
    [ -n "$KERNEL_SHA256" ] && [ -f "$KERNEL_SHA256" ] || return 0
    awk -v f="$BSD_FILE" '$1 == "SHA256" && $2 == "(" f ")" { print $NF; exit }' \
        "$KERNEL_SHA256" 2>/dev/null
}

# R4-01 + F8: before exiting, /bsd must be a non-empty regular file whose
# integrity is verifiable. Two authorities are accepted, in this order:
#   1. /var/db/kernel.SHA256 — the kernel integrity DB the boot-time relink
#      also rewrites. A recorded hash that matches /bsd is proof; a recorded
#      hash that does not is corruption and fails closed.
#   2. the manifest's installed_bsd_sha256 — the staged artifact, which is
#      still what /bsd holds before the first reboot.
# A DB that exists but has no SHA256 (/bsd) line is not ignorable: the host
# cannot prove what it boots, so the run fails rather than guessing.
verify_bsd_final() {
    local want size got db
    [ -f "$BSD_FILE" ] || { echo "WARNING: $BSD_FILE is not a regular file" >&2; return 1; }
    size=$(stat -f %z "$BSD_FILE" 2>/dev/null) \
        || { echo "WARNING: cannot stat $BSD_FILE" >&2; return 1; }
    [ "$size" -gt 0 ] || { echo "WARNING: $BSD_FILE is 0 bytes" >&2; return 1; }
    db=$(kernel_db_sha)
    if [ -n "$db" ]; then
        got=$(sha256 -q "$BSD_FILE" 2>/dev/null) \
            || { echo "WARNING: cannot hash $BSD_FILE" >&2; return 1; }
        if [ "$got" != "$db" ]; then
            echo "WARNING: $BSD_FILE sha256 $got does not match $KERNEL_SHA256 ($db) — the installed kernel is corrupt or was replaced" >&2
            return 1
        fi
        return 0
    fi
    if [ -n "$KERNEL_SHA256" ] && [ -f "$KERNEL_SHA256" ]; then
        echo "WARNING: $KERNEL_SHA256 records no SHA256 ($BSD_FILE) entry — cannot verify the installed kernel" >&2
        return 1
    fi
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

# F3/F4: the extraction parent does not exist on a first install, so tree_trust
# cannot stat it. Walk up to the nearest existing ancestor and require *that*
# chain to be trusted before root creates directories or extracts an archive
# there. A /usr/src symlink is followed (tree_trust resolves with readlink -f),
# so a link planted by another account cannot redirect root's extraction into a
# directory this check has not approved.
tree_trust_pending() {   # tree_trust_pending <path that may not exist yet>
    local p=$1
    while [ ! -e "$p" ]; do
        [ "$p" = / ] && break
        p=$(dirname "$p")
    done
    tree_trust "$p"
}

check_pending_trust() {   # check_pending_trust <path> <why>
    local verdict
    verdict=$(tree_trust_pending "$1")
    [ "$verdict" = trusted ] \
        || die "untrusted path $1: $verdict — $2 (use a directory owned by $AWG_BUILD_USER or by root and not group/other writable)"
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

# Can the account that compiles write into <dir>? Asked as that account, so a
# directory root can write but the builder cannot (stock /usr/obj) is visible.
builder_can_write() {   # builder_can_write <existing dir>
    local dir=$1
    resolve_build_user
    [ -d "$dir" ] || return 1
    builder_run "$dir" "test -w ." >/dev/null 2>&1
}

# F2: stock OpenBSD 8.0 ships /usr/obj as build:wobj 0770, so the documented
# builder account (`useradd -s /sbin/nologin _awgbuild`) cannot create the
# object directory `config AWG.MP` derives from the stock source path:
#   mkdir: /usr/obj/sys: Permission denied     (config rc 2, before any compile)
# Pre-creating /usr/obj/sys is not enough on its own: a builder that is not in
# the group that owns /usr/obj cannot even traverse the root, so the subtree
# stays unreachable. Two native remedies, in order, neither of which widens a
# mode or touches the shared root:
#   1. add the account to the group that already owns the object root (that is
#      what `wobj` exists for — `usermod -G wobj <account>`);
#   2. if the root is traversable but not writable, pre-create only the subtree
#      config needs and hand that subtree to the builder.
# When the source root is redirected outside /usr/src, config(8) keeps its
# objects in-tree, and the tree has already been handed to the builder — both
# layouts are covered, and kernel_objdir() later finds whichever one was used.
prepare_objdir() {
    local root group bgroup
    resolve_build_user
    if [ "$BUILD_USER" = root ]; then
        log "Building as root — object directory ownership is not enforced"
        return 0
    fi
    root=${BSDOBJDIR:-/usr/obj}
    if builder_can_write "$root"; then
        log "Object directory $root is writable by $BUILD_USER"
        return 0
    fi
    log "note: $root is not writable by $BUILD_USER (stock 8.0 is build:wobj 0770)"
    group=$(stat -f %Sg "$root" 2>/dev/null)
    bgroup=$(id -gn "$BUILD_USER" 2>/dev/null)
    if [ -n "$group" ] && [ "$group" != "$bgroup" ]; then
        log "Adding $BUILD_USER to group $group (the group that owns $root)"
        usermod -G "$group" "$BUILD_USER" \
            || log "warning: usermod -G $group $BUILD_USER failed"
        if builder_can_write "$root"; then
            log "$root is writable by $BUILD_USER now (group $group; mode unchanged)"
            return 0
        fi
    fi
    mkdir -p "$root/sys/arch/amd64/compile" \
        || die "cannot create $root/sys/arch/amd64/compile"
    chown -R "$BUILD_USER" "$root/sys" \
        || die "cannot hand $root/sys to $BUILD_USER"
    builder_can_write "$root/sys/arch/amd64/compile" \
        || die "$root/sys/arch/amd64/compile is still not writable by $BUILD_USER — set BSDOBJDIR to a directory that account owns, or add it to group $group"
    log "Created $root/sys/arch/amd64/compile for $BUILD_USER (mode of $root unchanged)"
    # Objects and the KARL relink set are large; a full /usr turns a build into
    # an out-of-space failure hours in. Report it before compiling (F10).
    log "Free space on $(df -h "$root" | awk 'NR==2 {print $6}') : $(df -h "$root" | awk 'NR==2 {print $4}') (kernel objects need ~1 GB, relink objects another ~700 MB)"
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

    # F3/F4: the extraction target is written by root, and a supported
    # SRCDIR override may point at a tree that does not exist yet. Three
    # hazards the old code had:
    #   * `mkdir -p /usr/src` under set -e aborted the whole run when the
    #     operator's /usr/src is a symlink ("mkdir: /usr/src: File exists");
    #   * `tar -C "$(dirname "$SRCDIR")"` failed with "Failed chdir" when the
    #     redirected parent did not exist;
    #   * neither checked who owns the target first, so root could be made to
    #     extract a verified archive through a link another account planted.
    local parent
    parent=$(dirname "$SRCDIR")

    # Validate sources (files.rasops being empty is a known bad-extraction symptom)
    if [ ! -f "$SRCDIR/net/if_wg.c" ] || ! grep -q 'OpenBSD' "$SRCDIR/dev/rasops/files.rasops" 2>/dev/null; then
        local ver url
        ver=$(uname -r)
        url="$CDN/${ver}/sys.tar.gz"
        # Trust before write: the nearest existing ancestor of the parent must
        # be owned by root or the builder and not group/other writable. A
        # symlinked /usr/src is followed to its real target and checked there.
        check_pending_trust "$parent" "the verified sys.tar.gz is extracted here as root"
        if [ -L "$parent" ]; then
            log "note: $parent is a symlink to $(readlink -f "$parent") — extracting through it"
        fi
        if [ ! -d "$parent" ]; then
            log "Creating source parent $parent"
            mkdir -p "$parent" || die "cannot create $parent"
        fi
        log "Fetching kernel sources from $url"
        ftp -o "$WORK/sys.tar.gz" "$url" \
            || die "Failed to download $url — CDN only carries 7.7+. Download manually and extract to $parent"
        verify_release_file sys.tar.gz
        tar -C "$parent" -xzf "$WORK/sys.tar.gz" || die "Failed to extract sys.tar.gz into $parent"
        [ -f "$SRCDIR/net/if_wg.c" ] || die "sys.tar.gz extracted but $SRCDIR/net/if_wg.c not found"
        log "Kernel sources installed to $SRCDIR"
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
    # from inside the source directory through the builder (AWG-014 R1 D-1:
    # `make -V` parses the makefile, so it must not run as root) instead of
    # assuming ./obj.
    local objdir
    objdir=$(builder_run "$IFCFG_SRC" "make -V .OBJDIR") || die "cannot resolve the ifconfig object directory"
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

    # F2: config's object directory must be writable by that account.
    prepare_objdir

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
    log "Kernel objects: $(kernel_objdir || echo 'not found')"
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

# config(8) puts the objects (and the linked bsd) under an object directory,
# and the location depends on the source root: with the stock /usr/src it uses
# $BSDOBJDIR/sys/arch/amd64/compile/AWG.MP, with a redirected SRCDIR it keeps
# the objects in-tree (<SRCDIR>/arch/amd64/compile/AWG.MP/obj, either a real
# directory or the obj symlink config leaves behind). F2: the old fixed
# in-tree path only worked for the redirected case and would have "verified" a
# missing kernel on a stock install, so the object directory is discovered.
kernel_objdir_candidates() {
    printf '%s\n' \
        "$SRCDIR/arch/amd64/compile/AWG.MP/obj" \
        "$SRCDIR/arch/amd64/compile/AWG.MP" \
        "${BSDOBJDIR:-/usr/obj}/sys/arch/amd64/compile/AWG.MP" \
        "${BSDOBJDIR:-/usr/obj}/sys/arch/amd64/compile/AWG.MP/obj"
}

# The object directory the build actually used: the first candidate holding a
# linked kernel.
kernel_objdir() {
    local d
    for d in $(kernel_objdir_candidates); do
        [ -f "$d/bsd" ] && { printf '%s' "$d"; return 0; }
    done
    return 1
}

kernel_object_path() {
    local d
    d=$(kernel_objdir) || d=$(kernel_objdir_candidates | head -1)
    echo "$d/bsd"
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

# ── KARL relink objects (F9) ─────────────────────────────────────────────────
# /etc/rc runs /usr/libexec/reorder_kernel at boot; that script cd's into
# $RELINK_KERNEL_DIR/<ident> (ident read from kern.osversion) and drives the
# compile Makefile's newbsd / reconfig / newinstall targets. A stock GENERIC.MP dir ships a compile
# Makefile, makegap.sh, ld.script and the object files; `make install` of a
# *custom* kernel does not create that directory, so relink fails on every boot
# ("make: don't know how to make newbsd") and /bsd is never relinked. Publishing
# the objects is what `make update-link` in the compile directory does:
#   tar -chf - Makefile makegap.sh ld.script *.o | tar -C .../AWG.MP -xf -
# It is reimplemented here instead of invoking that target because update-link
# starts with `rm -rf /usr/share/relink/kernel/AWG.MP`: it destroys the previous
# set before it knows the new one is complete, which is exactly what a paired
# rollback needs to keep. This publishes by staging, verifying and renaming.

# Where the objects for this ident live, and how big the set is.
relink_dir() {
    printf '%s' "${RELINK_KERNEL_DIR:-/usr/share/relink/kernel}/${KERNEL_IDENT:-AWG.MP}"
}

relink_object_count() {
    local t
    t=$(relink_dir)
    [ -d "$t" ] || { echo 0; return 0; }
    ls "$t"/*.o 2>/dev/null | wc -l | tr -d ' '
}

relink_state_now() {
    local t
    t=$(relink_dir)
    [ -d "$t" ] || { echo missing; return 0; }
    [ "$(relink_object_count)" -gt 0 ] && { echo present; return 0; }
    echo missing
}

relink_object_kb() {   # size of the object set that would be published
    local o
    o=$(kernel_objdir) || return 1
    du -ck "$o"/*.o 2>/dev/null | awk 'END { print $1 }'
}

# Journal the relink transition in the manifest. Recovery reads these keys, so
# they are written *before* the directory is touched: an interruption between
# the two renames then lands in a state the restore can undo ("pending" with no
# backup on disk means the live directory is still the previous set).
manifest_set_relink() {   # manifest_set_relink <state> <backup-path> <objects>
    local kv
    [ -f "$MANIFEST" ] || return 0
    kv="$WORK/relink.kv"
    {
        printf 'relink_state=%s\n' "$1"
        printf 'relink_backup_path=%s\n' "$2"
        printf 'relink_objects=%s\n' "$3"
        printf 'relink_path=%s\n' "$(relink_dir)"
    } > "$kv" || { echo "cannot write $kv" >&2; return 1; }
    manifest_apply_kv "$kv" >> "$LOGFILE" 2>&1 \
        || { echo "cannot journal the relink state in $MANIFEST" >&2; return 1; }
    return 0
}

# Publish this build's relink objects for $KERNEL_IDENT. Transactional: the new
# set is complete and verified in a staging directory on the same filesystem,
# the previous set is kept by rename (so a rollback can put it back), and only
# then does the new set take its place.
publish_relink_objects() {
    local objdir target stage prev need avail have staged

    step "KARL relink objects ($KERNEL_IDENT)"

    if [ "$RELINK_SKIP" = yes ]; then
        log "WARNING: --skip-relink-objects: $KERNEL_IDENT will not be relinked at boot"
        log "WARNING: the console will print 'reorder_kernel: failed' until 'make update-link' is run in the kernel object directory"
        RELINK_STATE=skipped
        return 0
    fi

    objdir=$(kernel_objdir) || {
        echo "cannot find the kernel object directory — the relink objects of this kernel cannot be published" >&2
        return 1
    }
    target=$(relink_dir)
    [ -f "$objdir/Makefile" ] \
        || { echo "$objdir has no compile Makefile — not a kernel object directory" >&2; return 1; }
    [ -f "$objdir/makegap.sh" ] \
        || { echo "$objdir has no makegap.sh — the relink needs it" >&2; return 1; }
    [ -f "$objdir/ld.script" ] \
        || { echo "$objdir has no ld.script — the relink needs it" >&2; return 1; }
    have=$(ls "$objdir"/*.o 2>/dev/null | wc -l | tr -d ' ')
    [ "$have" -gt 0 ] || { echo "no object files in $objdir" >&2; return 1; }

    # F10: the relink set is roughly the size of the object directory and it
    # lives under /usr, which is where a small install runs out of space. Say so
    # with numbers before copying ~700 MB.
    need=$(relink_object_kb) || need=0
    mkdir -p -m 700 "$RELINK_KERNEL_DIR" \
        || { echo "cannot create $RELINK_KERNEL_DIR" >&2; return 1; }
    avail=$(df -k "$RELINK_KERNEL_DIR" 2>/dev/null | awk 'NR==2 { print $4 }')
    if [ -n "$avail" ] && [ "$need" -gt 0 ] && [ "$avail" -lt $((need + 102400)) ]; then
        echo "not enough space on $RELINK_KERNEL_DIR for the relink objects: need $((need / 1024 + 100)) MB, ${avail} KB free" >&2
        echo "free space on that filesystem, or rerun with --skip-relink-objects to publish the kernel without boot-time relink" >&2
        return 1
    fi

    stage="$RELINK_KERNEL_DIR/.awg-stage.$$"
    rm -rf "$stage"
    mkdir -m 700 "$stage" || { echo "cannot create $stage" >&2; return 1; }
    # Copy the same set `make update-link` copies, preserving mode and times so
    # the objects keep their identity.
    for f in Makefile makegap.sh ld.script; do
        cp -p "$objdir/$f" "$stage/$f" || {
            rm -rf "$stage"
            echo "could not copy $objdir/$f to $stage" >&2
            return 1
        }
    done
    for f in "$objdir"/*.o; do
        cp -p "$f" "$stage/" || {
            rm -rf "$stage"
            echo "could not copy $f to $stage" >&2
            return 1
        }
    done
    # Verify the staged set before it becomes the boot-time input: the script
    # needs the newbsd target, and every object of the build must be there.
    if ! grep -q '^newbsd:' "$stage/Makefile"; then
        rm -rf "$stage"
        echo "$objdir/Makefile has no newbsd target — it is not a relink Makefile" >&2
        return 1
    fi
    staged=$(ls "$stage"/*.o 2>/dev/null | wc -l | tr -d ' ')
    if [ "$staged" != "$have" ]; then
        rm -rf "$stage"
        echo "staged $staged objects but the build has $have — refusing to publish an incomplete relink set" >&2
        return 1
    fi

    prev="$target.awg-prev.$GENERATION"
    RELINK_BACKUP=""
    # Journal first: if the run dies between these renames, the restore knows
    # which set was live and where the backup was being kept.
    manifest_set_relink pending "$prev" "$staged" || { rm -rf "$stage"; return 1; }
    if [ -d "$target" ]; then
        rm -rf "$prev"
        if ! mv "$target" "$prev"; then
            rm -rf "$stage"
            echo "cannot move $target aside to publish new relink objects" >&2
            return 1
        fi
        RELINK_BACKUP=$prev
    fi
    # From here the live set is the *new* one until the pair commits, so an
    # interrupted publish has to put RELINK_BACKUP back (F9).
    RELINK_PUBLISH_STATE=swapped
    if ! mv "$stage" "$target"; then
        RELINK_PUBLISH_STATE=rolled
        [ -n "$RELINK_BACKUP" ] && mv "$RELINK_BACKUP" "$target"
        rm -rf "$stage"
        echo "cannot install the relink objects to $target" >&2
        return 1
    fi
    RELINK_STATE=present
    manifest_set_relink present "$RELINK_BACKUP" "$staged" || return 1
    log "Published relink objects for $KERNEL_IDENT: $target ($staged objects, $(du -sh "$target" 2>/dev/null | awk '{ print $1 }'))"
    [ -n "$RELINK_BACKUP" ] && log "Previous relink objects kept at $RELINK_BACKUP (removed once the next generation commits)"
    log "Boot-time reorder_kernel now has the Makefile and objects it needs — /bsd will be relinked on the next boot"
    return 0
}

# Move <backup> into <target>'s place, keeping the current set aside only until
# the rename succeeds.
relink_restore_backup() {   # relink_restore_backup <target> <backup>
    local target=$1 backup=$2 discard
    discard="$target.awg-discard.$$"
    rm -rf "$discard"
    if [ -d "$target" ] && ! mv "$target" "$discard"; then
        echo "cannot move $target aside to restore $backup" >&2
        return 1
    fi
    if mv "$backup" "$target"; then
        rm -rf "$discard"
        return 0
    fi
    [ -d "$discard" ] && mv "$discard" "$target"
    echo "could not restore $backup to $target" >&2
    return 1
}

# Put the relink objects back in step with the kernel a rollback just restored.
# Leaving the *new* set in place would make the next boot relink a kernel the
# operator rolled away from, which is worse than no relink at all.
# Two callers, two sources of truth:
#   interrupt — this process is aborting mid-publish, so the only reliable
#               record of what it did is RELINK_PUBLISH_STATE/RELINK_BACKUP;
#               the manifest at that moment still carries the *previous*
#               generation's keys, and acting on those deleted a live set.
#   committed — an operator --rollback of a generation that fully committed;
#               the manifest is authoritative there.
restore_relink_generation() {   # restore_relink_generation [interrupt|committed]
    local target state backup mode=${1:-committed}
    target=$(relink_dir)
    if [ "$mode" = interrupt ]; then
        case "${RELINK_PUBLISH_STATE:-none}" in
        none)
            log "Relink objects were not touched by this run: $target still holds the previous objects"
            return 0
            ;;
        rolled)
            log "Relink publish rolled back in place: $target holds the previous objects"
            return 0
            ;;
        swapped)
            if [ -n "${RELINK_BACKUP:-}" ] && [ -d "$RELINK_BACKUP" ]; then
                relink_restore_backup "$target" "$RELINK_BACKUP" || return 1
                log "Restored the relink objects of the previous kernel: $target"
            else
                echo "WARNING: this run published new relink objects in $target and kept no copy of the previous set —" >&2
                echo "WARNING: removing $target so the boot relink cannot relink objects that do not match the restored kernel" >&2
                [ -d "$target" ] && rm -rf "$target"
            fi
            RELINK_PUBLISH_STATE=rolled
            RELINK_BACKUP=""
            return 0
            ;;
        esac
    fi
    state=$(manifest_get "$MANIFEST" relink_state)
    backup=$(manifest_get "$MANIFEST" relink_backup_path)
    [ -n "$state" ] || state=unknown
    case "$state" in
    present|pending)
        if [ -n "$backup" ] && [ -d "$backup" ]; then
            relink_restore_backup "$target" "$backup" || return 1
            log "Restored the relink objects of the previous kernel: $target"
        elif [ "$state" = pending ]; then
            # The publish was interrupted before it moved the old set aside, so
            # the live directory is still the previous kernel's objects.
            log "Relink publish was interrupted before the swap: $target still holds the previous objects"
        else
            echo "WARNING: generation $BACKUP_GEN recorded relink objects but $backup is missing —" >&2
            echo "WARNING: removing $target so the boot relink cannot relink objects that do not match the restored kernel" >&2
            [ -d "$target" ] && rm -rf "$target"
        fi
        ;;
    missing|skipped)
        # The generation being restored had no relink objects (first install, or
        # --skip-relink-objects): leave none behind rather than stale ones.
        if [ -d "$target" ]; then
            rm -rf "$target"
            log "Removed $target: the restored generation had no installed relink objects"
        fi
        ;;
    *)
        # No relink record at all: the publish died before it journaled
        # anything, so this directory is still whatever the previous state was.
        # Touching it would destroy the only copy of a working relink set.
        log "No relink record in $MANIFEST: leaving $target as it is"
        ;;
    esac
    RELINK_BACKUP=""
    return 0
}

# Only the newest relink backup can be restored by --rollback; older ones cost
# ~700 MB each and are dropped with the older backup generations.
prune_relink_backups() {
    local target g f
    target=$(relink_dir)
    for f in "$target".awg-prev.*; do
        [ -e "$f" ] || continue
        g=${f##*.awg-prev.}
        case "$g" in ''|*[!0-9]*) continue ;; esac
        [ "$g" -lt "$BACKUP_GEN" ] && rm -rf "$f" 2>/dev/null
    done
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

# Merge a key=value file into the manifest: keys the file carries replace the
# existing line in place, missing keys are appended, every other line is kept.
# (The manifest is the only record of what is installed, so a write that only
# knows about backups must not drop the installed_* keys — that is F5.)
manifest_apply_kv() {   # manifest_apply_kv <kvfile>
    local kvf=$1 tmp
    tmp="${MANIFEST}.new.$$"
    awk -v kvf="$kvf" '
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
    ' "$MANIFEST" > "$tmp" || { rm -f "$tmp"; return 1; }
    mv -f "$tmp" "$MANIFEST"
}

write_backup_pair() {
    local bsd_sha ifc_sha hdr_sha ctl_sha ksha_sha bsd_size ifc_size tmp kv

    if [ "$BACKUP_PAIR_DONE" = yes ]; then
        log "Backup pair already taken this run — keeping it"
        return 0
    fi
    BACKUP_PAIR_DONE=yes

    # Record pre-install inventory on the very first install (no manifest yet).
    # This captures the state of non-pair artifacts before any AWG changes.
    [ -f "$MANIFEST" ] || record_preinstall_inventory

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
    kv="$WORK/backup.kv"
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
    } > "$kv" || die "cannot write $kv"
    # F5: keep the installed_* keys of the manifest this run is replacing. The
    # pair is still the previous one until record_installed_pair commits, so
    # those keys are correct *and* a run that dies between here and the commit
    # leaves a manifest that still describes the kernel and tool on disk.
    if [ -f "$MANIFEST" ]; then
        manifest_apply_kv "$kv" || die "cannot update $MANIFEST"
    else
        mv "$kv" "$tmp" && mv -f "$tmp" "$MANIFEST" || die "cannot write $MANIFEST"
    fi
    chmod 600 "$MANIFEST" || die "cannot set mode on $MANIFEST"
    log "Backed up the pair: $BSD_BACKUP + $IFCFG_BACKUP (manifest: $MANIFEST)"
}

# Drop backup generations older than the previous one (kernel is ~30 MB).
# Generation 1 (the stock baseline) is never pruned — it is required for
# --uninstall to restore the original system state.
prune_old_backups() {
    local keep_from g f
    keep_from=$((BACKUP_GEN - 1))
    for f in /bsd.awg-prev.* /sbin/ifconfig.awg-prev.* \
             /usr/include/net/if_awg.h.awg-prev.* /usr/local/sbin/awgctl.awg-prev.* \
             /var/db/kernel.SHA256.awg-prev.*; do
        [ -e "$f" ] || continue
        case "$f" in *".awg-prev."*) g=${f##*.awg-prev.} ;; *) continue ;; esac
        case "$g" in ''|*[!0-9]*) continue ;; esac
        # Never prune generation 1 (stock baseline)
        [ "$g" -eq 1 ] && continue
        [ "$g" -lt "$keep_from" ] && rm -f "$f" 2>/dev/null
    done
    prune_relink_backups
}

# manifest_get <key>: read one value out of the backup manifest.
manifest_get() {   # manifest_get <file> <key>
    awk -v k="$2" -F= '$1 == k { print substr($0, length(k) + 2); exit }' "$1"
}

# F5: does the manifest already describe the kernel + tool that are live now?
# Used after an interrupted publish: the recovery restores the previous pair, so
# the manifest must describe that pair, not one the run never finished writing.
# A generation that installed KARL objects legitimately has a /bsd that differs
# from the staged hash after the first boot (reorder_kernel relinks it and
# rewrites /var/db/kernel.SHA256). Treat that as described when the live kernel
# matches the integrity DB of a generation that recorded relink objects —
# otherwise every interrupted publish on a booted machine "repairs" a manifest
# that was already correct, which is exactly the churn F5 is about.
manifest_describes_live_pair() {
    local want live state
    [ -f "$MANIFEST" ] || return 1
    want=$(manifest_get "$MANIFEST" installed_bsd_sha256)
    [ -n "$want" ] || return 1
    live=$(sha256 -q "$BSD_FILE" 2>/dev/null)
    if [ "$want" != "$live" ]; then
        state=$(manifest_get "$MANIFEST" relink_state)
        case "$state" in
        present|pending)
            [ -f "$KERNEL_SHA256" ] || return 1
            [ "$(kernel_db_sha)" = "$live" ] || return 1
            ;;
        *) return 1 ;;
        esac
    fi
    want=$(manifest_get "$MANIFEST" installed_ifconfig_sha256)
    [ -n "$want" ] || return 1
    [ "$want" = "$(sha256 -q "$IFCFG_FILE" 2>/dev/null)" ] || return 1
    return 0
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
    # The helper lives in the root-owned install DB (AWG-014 R1 D-2): $WORK is
    # chowned to the builder, so a helper there can be pre-seeded and replaced.
    if [ ! -x "$FSYNC_HELPER" ]; then
        cc -x c -o "$FSYNC_HELPER" - <<'C' || die "cannot build commit durability helper"
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

# The installed-pair keys, in manifest order, written to <file>. Shared by the
# commit (which fsyncs) and the interruption recovery (which cannot rely on the
# durability helper: the run that failed may be the reason it is unavailable).
installed_pair_kv() {   # installed_pair_kv <file>
    local kv=$1 bsd_sha ifc_sha bsd_size ifc_size hdr_sha ctl_sha
    bsd_sha=$(sha256 -q "$BSD_FILE") || return 1
    ifc_sha=$(sha256 -q "$IFCFG_FILE") || return 1
    bsd_size=$(stat -f %z "$BSD_FILE") || return 1
    ifc_size=$(stat -f %z "$IFCFG_FILE") || return 1
    hdr_sha=""
    ctl_sha=""
    [ ! -f "$IFAWG_HEADER" ] || hdr_sha=$(sha256 -q "$IFAWG_HEADER") || return 1
    [ ! -f "$AWGCTL_FILE" ] || ctl_sha=$(sha256 -q "$AWGCTL_FILE") || return 1
    {
        printf 'installed_bsd_sha256=%s\n' "$bsd_sha"
        printf 'installed_bsd_size=%s\n' "$bsd_size"
        printf 'installed_ifconfig_sha256=%s\n' "$ifc_sha"
        printf 'installed_ifconfig_size=%s\n' "$ifc_size"
        printf 'installed_if_awg_h_sha256=%s\n' "$hdr_sha"
        printf 'installed_awgctl_sha256=%s\n' "$ctl_sha"
        printf 'installed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        # F9: the relink state is read back from disk, not remembered from the
        # publish: an install, a rollback and a repair all land here, and the
        # manifest must describe what is on the filesystem now.
        printf 'relink_state=%s\n' "$(relink_state_now)"
        printf 'relink_path=%s\n' "$(relink_dir)"
        printf 'relink_objects=%s\n' "$(relink_object_count)"
        printf 'relink_backup_path=%s\n' "$RELINK_BACKUP"
    } > "$kv"
}

record_installed_pair() {
    local kv tmp
    prepare_durability_helper
    [ -f "$MANIFEST" ] || die "no manifest at $MANIFEST — cannot record the installed pair"
    kv="$WORK/manifest.kv"
    installed_pair_kv "$kv" || die "cannot hash the installed pair"
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
    ' "$MANIFEST" > "$tmp" && "$FSYNC_HELPER" "$tmp" \
        && mv -f "$tmp" "$MANIFEST" \
        || die "could not record the installed pair in $MANIFEST"
    # The commit is not complete until both content and directory entry are
    # durable. Also flush installed members and their parent directories.
    for kv in "$BSD_FILE" "$IFCFG_FILE" "$IFAWG_HEADER" "$AWGCTL_FILE"; do
        [ ! -f "$kv" ] || "$FSYNC_HELPER" "$kv" "$(dirname "$kv")" \
            || die "cannot make installed member $kv durable"
    done
    "$FSYNC_HELPER" "$MANIFEST" "$(dirname "$MANIFEST")" \
        || die "cannot make commit record durable"
    log "Recorded the installed pair in $MANIFEST"
}

# Recovery path (F5): the pair has just been restored, so rewrite the
# installed_* keys from the files that are live now. No durability helper: the
# interrupted run may be why it is unusable, and the restore already fsync'd
# the members it wrote.
recover_installed_record() {
    local kv
    [ -f "$MANIFEST" ] || { echo "no manifest at $MANIFEST" >&2; return 1; }
    kv="$WORK/manifest.recover.kv"
    installed_pair_kv "$kv" 2>>"$LOGFILE" || { echo "cannot hash the restored pair" >&2; return 1; }
    manifest_apply_kv "$kv" >> "$LOGFILE" 2>&1 \
        || { echo "cannot rewrite the installed-pair keys in $MANIFEST" >&2; return 1; }
    log "Recorded the restored pair in $MANIFEST"
    return 0
}

# R4-14: awgctl is part of the release delivery: built from sbin/awgctl with
# the same builder, installed next to awg-quick, recorded in the pair
# manifest and restored by --rollback together with kernel + tool + header.
install_awgctl() {
    step "awgctl — management tool (build)"
    # R1 review: objects go to a per-run directory under $TMPDIR (bsd.prog.mk
    # honours OBJDIR), so two workers building the same repo layout in two
    # worktrees do not share one object directory.
    AWGCTL_OBJDIR=$(mktemp -d "${TMPDIR:-/tmp}/awg-awgctl-obj.XXXXXX") \
        || die "cannot create the awgctl object directory"
    hand_tree_to_builder "$REPODIR/sbin/awgctl"
    hand_tree_to_builder "$AWGCTL_OBJDIR"
    log "Building awgctl (objects in $AWGCTL_OBJDIR)"
    builder_make "$REPODIR/sbin/awgctl" "OBJDIR=$AWGCTL_OBJDIR" \
        || die "awgctl build failed"
    [ -x "$AWGCTL_OBJDIR/awgctl" ] \
        || die "awgctl build produced no binary in $AWGCTL_OBJDIR"
    AWGCTL_ARTIFACT="$AWGCTL_OBJDIR/awgctl"
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

# F5: the manifest as it stood before this run touched anything. It must be
# taken *before* write_backup_pair, which rewrites the file from scratch with
# only backup_* keys: copying it afterwards snapshotted a manifest that had
# already lost every installed_* key, so recovery "restored" a manifest that
# described no installed member at all and the next --check reported
# `ifconfig sha : MISMATCH (… vs manifest )`.
snapshot_precommit_manifest() {
    rm -f "$WORK/precommit.manifest"
    [ -f "$MANIFEST" ] || return 0
    cp "$MANIFEST" "$WORK/precommit.manifest" \
        || die "cannot save precommit manifest"
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
        # F9: the relink objects are part of what a publish changes; the
        # restored kernel must come back with the objects that match it.
        restore_relink_generation interrupt \
            || { echo "ERROR: $reason: rollback failed (state: relink objects)" >&2; return 21; }
        # F5: the manifest must describe the pair that is live again.
        # 1. the snapshot taken before write_backup_pair is the exact previous
        #    manifest (all five members *and* every installed_* key);
        # 2. if it is missing or describes something else (an older run, a
        #    damaged snapshot), rewrite the installed_* keys from the pair that
        #    was just restored rather than leaving a manifest that promises a
        #    kernel nobody installed.
        if [ -f "$WORK/precommit.manifest" ]; then
            cp "$WORK/precommit.manifest" "$MANIFEST.rollback.$$" \
                && mv -f "$MANIFEST.rollback.$$" "$MANIFEST" \
                || { echo "ERROR: $reason: rollback failed (state: manifest restore)" >&2; return 21; }
        fi
        if ! manifest_describes_live_pair; then
            if [ -f "$WORK/precommit.manifest" ]; then
                echo "note: the pre-run manifest does not describe the restored pair — rewriting its installed_* keys" >&2
            else
                echo "note: no pre-run manifest snapshot — recording the restored pair" >&2
            fi
            if ! (recover_installed_record) >> "$LOGFILE" 2>&1; then
                echo "ERROR: $reason: rollback failed (state: manifest record)" >&2
                return 21
            fi
        fi
        if [ -x "$FSYNC_HELPER" ]; then
            for member in "$BSD_FILE" "$IFCFG_FILE" "$IFAWG_HEADER" "$AWGCTL_FILE" "$MANIFEST"; do
                [ ! -f "$member" ] || "$FSYNC_HELPER" "$member" "$(dirname "$member")" \
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
    snapshot_precommit_manifest
    write_backup_pair
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
    # F9: the kernel is only relinkable if its objects travel with it.
    if ! publish_relink_objects; then
        publish_abort "KARL relink objects publish failed"
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
    local ksha_path ksha_sha restore_gen
    restore_gen=$(manifest_get "$MANIFEST" backup_generation)
    [ -n "$restore_gen" ] || restore_gen=$(manifest_get "$MANIFEST" generation)
    case "$restore_gen" in ''|*[!0-9]*) restore_gen=0 ;; esac
    step "Restoring the previous pair (generation $restore_gen)"
    bsd_path=$(manifest_get "$MANIFEST" backup_bsd_path)
    ifc_path=$(manifest_get "$MANIFEST" backup_ifconfig_path)
    hdr_path=$(manifest_get "$MANIFEST" backup_if_awg_h_path)
    ctl_path=$(manifest_get "$MANIFEST" backup_awgctl_path)
    ksha_path=$(manifest_get "$MANIFEST" backup_kernel_sha256_path)
    if [ "$restore_gen" -gt 0 ]; then
        [ -n "$bsd_path" ] || bsd_path="${BSD_BACKUP_BASE}.$restore_gen"
        [ -n "$ifc_path" ] || ifc_path="${IFCFG_BACKUP_BASE}.$restore_gen"
        [ -n "$hdr_path" ] || hdr_path="${IFAWG_BACKUP_BASE}.$restore_gen"
        [ -n "$ctl_path" ] || ctl_path="${AWGCTL_BACKUP_BASE}.$restore_gen"
        [ -n "$ksha_path" ] || ksha_path="${KERNEL_SHA256_BACKUP_BASE}.$restore_gen"
    else
        # Old manifest format (schema < 3) without generation stamping
        [ -n "$bsd_path" ] || bsd_path="$BSD_BACKUP"
        [ -n "$ifc_path" ] || ifc_path="$IFCFG_BACKUP"
        [ -n "$hdr_path" ] || hdr_path="$IFAWG_BACKUP"
        [ -n "$ctl_path" ] || ctl_path="$AWGCTL_BACKUP"
        [ -n "$ksha_path" ] || ksha_path="$KERNEL_SHA256_BACKUP"
    fi
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
    log "Previous pair restored (generation $restore_gen: $BSD_FILE + $IFCFG_FILE + $IFAWG_HEADER + $AWGCTL_FILE + $KERNEL_SHA256)"
}

# R4-02 + R5: --rollback restores the whole recorded generation (kernel, tool,
# header, awgctl) from the generation-stamped paths in the manifest, removing
# members that generation did not have, then rewrites the installed-pair keys.
rollback_pair() {
    local bsd_path ifc_path hdr_path ctl_path bsd_sha ifc_sha hdr_sha ctl_sha
    local ksha_path ksha_sha restore_gen

    restore_gen=$(manifest_get "$MANIFEST" backup_generation)
    [ -n "$restore_gen" ] || restore_gen=$(manifest_get "$MANIFEST" generation)
    case "$restore_gen" in ''|*[!0-9]*) restore_gen=0 ;; esac

    step "Rollback to the previous kernel/tool/header/awgctl generation"
    [ -f "$MANIFEST" ] || die "no backup manifest at $MANIFEST — nothing to roll back"
    # F6: BACKUP_GEN only exists in a run that took a backup; on --rollback it
    # was 0, so a corrupt generation was reported as "generation 0 rollback
    # failed" instead of the generation that actually failed.
    BACKUP_GEN=$(manifest_get "$MANIFEST" backup_generation)
    case "$BACKUP_GEN" in ''|*[!0-9]*) BACKUP_GEN=$(manifest_get "$MANIFEST" generation) ;; esac
    case "$BACKUP_GEN" in ''|*[!0-9]*) BACKUP_GEN=0 ;; esac
    bsd_path=$(manifest_get "$MANIFEST" backup_bsd_path)
    ifc_path=$(manifest_get "$MANIFEST" backup_ifconfig_path)
    hdr_path=$(manifest_get "$MANIFEST" backup_if_awg_h_path)
    ctl_path=$(manifest_get "$MANIFEST" backup_awgctl_path)
    ksha_path=$(manifest_get "$MANIFEST" backup_kernel_sha256_path)
    if [ "$restore_gen" -gt 0 ]; then
        [ -n "$bsd_path" ] || bsd_path="${BSD_BACKUP_BASE}.$restore_gen"
        [ -n "$ifc_path" ] || ifc_path="${IFCFG_BACKUP_BASE}.$restore_gen"
        [ -n "$hdr_path" ] || hdr_path="${IFAWG_BACKUP_BASE}.$restore_gen"
        [ -n "$ctl_path" ] || ctl_path="${AWGCTL_BACKUP_BASE}.$restore_gen"
        [ -n "$ksha_path" ] || ksha_path="${KERNEL_SHA256_BACKUP_BASE}.$restore_gen"
    else
        # Old manifest format (schema < 3) without generation stamping
        [ -n "$bsd_path" ] || bsd_path="$BSD_BACKUP"
        [ -n "$ifc_path" ] || ifc_path="$IFCFG_BACKUP"
        [ -n "$hdr_path" ] || hdr_path="$IFAWG_BACKUP"
        [ -n "$ctl_path" ] || ctl_path="$AWGCTL_BACKUP"
        [ -n "$ksha_path" ] || ksha_path="$KERNEL_SHA256_BACKUP"
    fi
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
            die "generation $restore_gen rollback failed (state: ${RESTORE_STATE:-nothing written}): the recorded members were not applied as a set"
        fi
        # R8: the recorded generation carries its own kernel.SHA256; only a
        # generation that predates that member needs a re-hash.
        [ -n "$ksha_sha" ] || sha256 -h "$KERNEL_SHA256" "$BSD_FILE" 2>/dev/null \
            || log "warning: could not update $KERNEL_SHA256 (syspatch will ask to rebuild it)"
        log "Quadruple restored: $BSD_FILE + $IFCFG_FILE + $IFAWG_HEADER + $AWGCTL_FILE (+ $KERNEL_SHA256) — reboot to activate the kernel"
    fi
    # F9: the relink objects are part of the pair. Restoring a kernel while
    # leaving the newer objects in place makes the next boot relink a kernel
    # the operator just rolled away from.
    restore_relink_generation \
        || die "generation $BACKUP_GEN rollback: the kernel was restored but its relink objects were not — check $RELINK_KERNEL_DIR"
    record_installed_pair
}

# Identify the stock baseline generation (generation 1). The stock baseline is
# the first generation recorded by this installer; it represents the pre-AWG
# system state. Subsequent installs/upgrades create higher generations, but the
# stock baseline must be preserved for --uninstall. If generation 1 backups or
# manifest are missing/corrupt, or if the generation 1 backups don't match the
# pre-install inventory hashes, the stock baseline is unavailable and uninstall
# must fail closed without discarding recovery data.
identify_stock_generation() {
    local stock_bsd stock_ifc stock_hdr stock_ctl stock_ksha
    stock_bsd="${BSD_BACKUP_BASE}.1"
    stock_ifc="${IFCFG_BACKUP_BASE}.1"
    stock_hdr="${IFAWG_BACKUP_BASE}.1"
    stock_ctl="${AWGCTL_BACKUP_BASE}.1"
    stock_ksha="${KERNEL_SHA256_BACKUP_BASE}.1"

    [ -f "$stock_bsd" ] || { echo "stock baseline missing: $stock_bsd not found" >&2; return 1; }
    [ -f "$stock_ifc" ] || { echo "stock baseline missing: $stock_ifc not found" >&2; return 1; }

    STOCK_GEN=1
    STOCK_BSD_PATH="$stock_bsd"
    STOCK_IFCFG_PATH="$stock_ifc"
    STOCK_IFAWG_PATH="$stock_hdr"
    STOCK_AWGCTL_PATH="$stock_ctl"
    STOCK_KSHA_PATH="$stock_ksha"

    # Verify the stock backup files are readable and non-empty
    for f in "$STOCK_BSD_PATH" "$STOCK_IFCFG_PATH"; do
        [ -s "$f" ] || { echo "stock baseline corrupt: $f empty or unreadable" >&2; return 1; }
    done

    # If pre-install inventory exists, verify generation 1 backups match the
    # recorded pre-install hashes. This authenticates that the generation 1
    # backups are truly the original stock baseline, not a later generation.
    local inv="$INSTALL_DB/preinstall.inventory"
    if [ -f "$inv" ]; then
        local expected_bsd expected_ifc expected_hdr expected_ctl expected_ksha
        expected_bsd=$(awk -F= -v k="stock_bsd_sha256" '$1==k {print $2}' "$inv")
        expected_ifc=$(awk -F= -v k="stock_ifconfig_sha256" '$1==k {print $2}' "$inv")
        expected_ksha=$(awk -F= -v k="stock_kernel_sha256_sha256" '$1==k {print $2}' "$inv")

        local actual_bsd actual_ifc actual_hdr actual_ctl actual_ksha
        actual_bsd=$(sha256 -q "$STOCK_BSD_PATH") || return 1
        actual_ifc=$(sha256 -q "$STOCK_IFCFG_PATH") || return 1
        [ -f "$STOCK_IFAWG_PATH" ] && actual_hdr=$(sha256 -q "$STOCK_IFAWG_PATH")
        [ -f "$STOCK_AWGCTL_PATH" ] && actual_ctl=$(sha256 -q "$STOCK_AWGCTL_PATH")
        [ -f "$STOCK_KSHA_PATH" ] && actual_ksha=$(sha256 -q "$STOCK_KSHA_PATH")

        [ -z "$expected_bsd" ] || [ "$actual_bsd" = "$expected_bsd" ] \
            || { echo "stock baseline hash mismatch: kernel backup does not match pre-install inventory" >&2; return 1; }
        [ -z "$expected_ifc" ] || [ "$actual_ifc" = "$expected_ifc" ] \
            || { echo "stock baseline hash mismatch: ifconfig backup does not match pre-install inventory" >&2; return 1; }
        # Header and awgctl are AWG additions; they don't exist in stock OpenBSD.
        # If generation 1 has them (from a kernel-only install), they are not
        # authenticated against stock hashes (which don't exist).
        [ -z "$expected_ksha" ] || [ "$actual_ksha" = "$expected_ksha" ] \
            || { echo "stock baseline hash mismatch: kernel.SHA256 backup does not match pre-install inventory" >&2; return 1; }

        log "Stock baseline generation 1 authenticated via pre-install inventory"
    else
        # No pre-install inventory: cannot authenticate the stock baseline.
        # This happens on older installations before this feature. Fail closed
        # to preserve recovery data.
        echo "stock baseline cannot be authenticated: no pre-install inventory at $inv" >&2
        return 1
    fi

    return 0
}

# Record pre-install state of non-pair artifacts so uninstall can restore or
# document what it cannot. This runs on the *first* install (when no manifest
# exists yet) and captures:
#   - awg-quick: existence, content hash, mode, owner, group
#   - awgctl.8 manpage: existence, content hash, mode, owner, group
#   - config directory: existence, mode, owner, group, file listing with hashes
#   - /obsd: existence, content hash (if different from /bsd)
#   - pf.conf: existence, content hash, and whether .awg.bak exists
# The record is stored in $INSTALL_DB/preinstall.inventory and is immutable
# once written (like the manifest, written tmp+rename).
record_preinstall_inventory() {
    local inv="$INSTALL_DB/preinstall.inventory"
    local tmp

    [ -f "$inv" ] && { log "Pre-install inventory already recorded at $inv"; return 0; }

    mkdir -p "$INSTALL_DB" || die "cannot create $INSTALL_DB"
    chown root:wheel "$INSTALL_DB" 2>/dev/null || true
    chmod 700 "$INSTALL_DB" || die "cannot set mode on $INSTALL_DB"

    tmp="$inv.tmp.$$"
    {
        printf 'schema=1\n'
        printf 'recorded_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

        # Stock baseline hashes (live pair members before any AWG changes)
        # These are used to authenticate generation 1 backups during uninstall.
        # Stock OpenBSD has bsd, ifconfig, and kernel.SHA256. The header
        # (if_awg.h) and awgctl are AWG additions and don't exist in stock.
        [ -f "$BSD_FILE" ] && printf 'stock_bsd_sha256=%s\n' "$(sha256 -q "$BSD_FILE")"
        [ -f "$IFCFG_FILE" ] && printf 'stock_ifconfig_sha256=%s\n' "$(sha256 -q "$IFCFG_FILE")"
        [ -f "$KERNEL_SHA256" ] && printf 'stock_kernel_sha256_sha256=%s\n' "$(sha256 -q "$KERNEL_SHA256")"

        # awg-quick
        if [ -f "$AWG_QUICK_FILE" ]; then
            printf 'awg_quick_existed=yes\n'
            printf 'awg_quick_sha256=%s\n' "$(sha256 -q "$AWG_QUICK_FILE")"
            printf 'awg_quick_mode=%s\n' "$(stat -f '%Sp' "$AWG_QUICK_FILE")"
            printf 'awg_quick_owner=%s\n' "$(stat -f '%Su' "$AWG_QUICK_FILE")"
            printf 'awg_quick_group=%s\n' "$(stat -f '%Sg' "$AWG_QUICK_FILE")"
        else
            printf 'awg_quick_existed=no\n'
        fi

        # awgctl.8 manpage
        local manpage="$AWGCTL_MAN_DIR/awgctl.8"
        if [ -f "$manpage" ]; then
            printf 'awgctl_man_existed=yes\n'
            printf 'awgctl_man_sha256=%s\n' "$(sha256 -q "$manpage")"
            printf 'awgctl_man_mode=%s\n' "$(stat -f '%Sp' "$manpage")"
            printf 'awgctl_man_owner=%s\n' "$(stat -f '%Su' "$manpage")"
            printf 'awgctl_man_group=%s\n' "$(stat -f '%Sg' "$manpage")"
        else
            printf 'awgctl_man_existed=no\n'
        fi

        # config directory
        if [ -d "$AWG_CONF_DIR" ]; then
            printf 'conf_dir_existed=yes\n'
            printf 'conf_dir_mode=%s\n' "$(stat -f '%Sp' "$AWG_CONF_DIR")"
            printf 'conf_dir_owner=%s\n' "$(stat -f '%Su' "$AWG_CONF_DIR")"
            printf 'conf_dir_group=%s\n' "$(stat -f '%Sg' "$AWG_CONF_DIR")"
            # List files with hashes
            find "$AWG_CONF_DIR" -type f -print | sort | while read -r f; do
                printf 'conf_file=%s:%s\n' "$f" "$(sha256 -q "$f")"
            done
        else
            printf 'conf_dir_existed=no\n'
        fi

        # /obsd
        if [ -f "$OBSD_FILE" ]; then
            printf 'obsd_existed=yes\n'
            printf 'obsd_sha256=%s\n' "$(sha256 -q "$OBSD_FILE" 2>/dev/null || echo '')"
        else
            printf 'obsd_existed=no\n'
        fi

        # pf.conf and .awg.bak
        if [ -f "$PF_CONF_FILE" ]; then
            printf 'pf_conf_existed=yes\n'
            printf 'pf_conf_sha256=%s\n' "$(sha256 -q "$PF_CONF_FILE")"
        else
            printf 'pf_conf_existed=no\n'
        fi
        local pf_bak="${PF_CONF_FILE}.awg.bak"
        if [ -f "$pf_bak" ]; then
            printf 'pf_conf_awg_bak_existed=yes\n'
            printf 'pf_conf_awg_bak_sha256=%s\n' "$(sha256 -q "$pf_bak")"
        else
            printf 'pf_conf_awg_bak_existed=no\n'
        fi

    } > "$tmp" && mv -f "$tmp" "$inv" || die "cannot write $inv"
    chmod 600 "$inv" || die "cannot set mode on $inv"
    log "Recorded pre-install inventory at $inv"
}

# Restore non-pair artifacts from the pre-install inventory. This is best-
# effort: artifacts that didn't exist pre-install are removed; artifacts that
# did exist are restored to their recorded state. The inventory is read-only;
# it is never modified by uninstall.
restore_preinstall_inventory() {
    local inv="$INSTALL_DB/preinstall.inventory"
    local key val

    [ -f "$inv" ] || { log "No pre-install inventory at $inv; cannot restore non-pair artifacts precisely"; return 1; }

    # awg-quick
    key="awg_quick_existed"
    val=$(awk -F= -v k="$key" '$1==k {print $2}' "$inv")
    if [ "$val" = "yes" ]; then
        local sha mode owner group
        sha=$(awk -F= -v k="awg_quick_sha256" '$1==k {print $2}' "$inv")
        mode=$(awk -F= -v k="awg_quick_mode" '$1==k {print $2}' "$inv")
        owner=$(awk -F= -v k="awg_quick_owner" '$1==k {print $2}' "$inv")
        group=$(awk -F= -v k="awg_quick_group" '$1==k {print $2}' "$inv")
        # We cannot restore content from hash alone; document the mismatch.
        log "NOTICE: awg-quick existed pre-install (sha256 $sha, mode $mode, owner $owner:$group) but content cannot be restored from hash alone; manual restore required"
    else
        [ -f "$AWG_QUICK_FILE" ] && rm -f "$AWG_QUICK_FILE" && log "Removed $AWG_QUICK_FILE (not present pre-install)"
    fi

    # awgctl.8 manpage
    key="awgctl_man_existed"
    val=$(awk -F= -v k="$key" '$1==k {print $2}' "$inv")
    local manpage="$AWGCTL_MAN_DIR/awgctl.8"
    if [ "$val" = "yes" ]; then
        local sha mode owner group
        sha=$(awk -F= -v k="awgctl_man_sha256" '$1==k {print $2}' "$inv")
        mode=$(awk -F= -v k="awgctl_man_mode" '$1==k {print $2}' "$inv")
        owner=$(awk -F= -v k="awgctl_man_owner" '$1==k {print $2}' "$inv")
        group=$(awk -F= -v k="awgctl_man_group" '$1==k {print $2}' "$inv")
        log "NOTICE: awgctl.8 existed pre-install (sha256 $sha, mode $mode, owner $owner:$group) but content cannot be restored from hash alone; manual restore required"
    else
        [ -f "$manpage" ] && rm -f "$manpage" && log "Removed $manpage (not present pre-install)"
    fi

    # config directory
    key="conf_dir_existed"
    val=$(awk -F= -v k="$key" '$1==k {print $2}' "$inv")
    if [ "$val" = "yes" ]; then
        local mode owner group
        mode=$(awk -F= -v k="conf_dir_mode" '$1==k {print $2}' "$inv")
        owner=$(awk -F= -v k="conf_dir_owner" '$1==k {print $2}' "$inv")
        group=$(awk -F= -v k="conf_dir_group" '$1==k {print $2}' "$inv")
        if [ "$PURGE_CONFIGS" = yes ]; then
            if [ -d "$AWG_CONF_DIR" ]; then
                rm -rf "$AWG_CONF_DIR" && log "Removed $AWG_CONF_DIR (--purge-configs requested)"
                rmdir "$(dirname "$AWG_CONF_DIR")" 2>/dev/null
            fi
        else
            # Re-apply the recorded mode if it differs
            if [ -d "$AWG_CONF_DIR" ]; then
                local current_mode
                current_mode=$(stat -f '%Sp' "$AWG_CONF_DIR" 2>/dev/null)
                if [ "$current_mode" != "$mode" ]; then
                    chmod "$mode" "$AWG_CONF_DIR" 2>/dev/null && log "Re-applied config dir mode $mode"
                fi
            fi
            log "Preserving $AWG_CONF_DIR and its contents (pre-install dir, use --purge-configs to remove)"
        fi
    else
        if [ -d "$AWG_CONF_DIR" ]; then
            rm -rf "$AWG_CONF_DIR" && log "Removed $AWG_CONF_DIR (not present pre-install)"
            rmdir "$(dirname "$AWG_CONF_DIR")" 2>/dev/null
        fi
    fi

    # /obsd - restore from the restored stock /bsd if it existed pre-install
    key="obsd_existed"
    val=$(awk -F= -v k="$key" '$1==k {print $2}' "$inv")
    if [ "$val" = "yes" ]; then
        local expected_sha
        expected_sha=$(awk -F= -v k="obsd_sha256" '$1==k {print $2}' "$inv")
        if [ -f "$BSD_FILE" ]; then
            local actual_sha
            actual_sha=$(sha256 -q "$BSD_FILE" 2>/dev/null)
            if [ -n "$expected_sha" ] && [ "$actual_sha" = "$expected_sha" ]; then
                ln -f "$BSD_FILE" "$OBSD_FILE" 2>/dev/null || cp -p "$BSD_FILE" "$OBSD_FILE" 2>/dev/null || true
                log "Restored $OBSD_FILE from restored stock /bsd"
            else
                log "NOTICE: $OBSD_FILE existed pre-install (sha256 $expected_sha) but restored /bsd differs; cannot restore automatically"
            fi
        else
            log "NOTICE: $OBSD_FILE existed pre-install but /bsd not available; cannot restore automatically"
        fi
    else
        # If /obsd exists now and wasn't there pre-install, remove it
        if [ -f "$OBSD_FILE" ]; then
            rm -f "$OBSD_FILE" && log "Removed $OBSD_FILE (not present pre-install)"
        fi
    fi

    # pf.conf - restore from .awg.bak if it exists and matches recorded hash
    key="pf_conf_existed"
    val=$(awk -F= -v k="$key" '$1==k {print $2}' "$inv")
    if [ "$val" = "yes" ]; then
        local recorded_sha
        recorded_sha=$(awk -F= -v k="pf_conf_sha256" '$1==k {print $2}' "$inv")
        local pf_bak="${PF_CONF_FILE}.awg.bak"
        if [ -f "$pf_bak" ]; then
            local bak_sha
            bak_sha=$(sha256 -q "$pf_bak" 2>/dev/null)
            if [ "$bak_sha" = "$recorded_sha" ]; then
                cp -p "$pf_bak" "$PF_CONF_FILE"
                pfctl -f "$PF_CONF_FILE" 2>/dev/null && log "Restored $PF_CONF_FILE from .awg.bak" || log "WARNING: restored pf.conf but reload failed"
                rm -f "$pf_bak" && log "Removed $pf_bak (restored)"
            else
                log "NOTICE: $pf_bak hash ($bak_sha) does not match pre-install ($recorded_sha); manual restore required"
            fi
        else
            log "NOTICE: $PF_CONF_FILE existed pre-install but no .awg.bak found; cannot restore automatically"
        fi
    else
        # pf.conf didn't exist pre-install; if .awg.bak exists, it was created by us, remove it
        local pf_bak="${PF_CONF_FILE}.awg.bak"
        if [ -f "$pf_bak" ]; then
            rm -f "$pf_bak" && log "Removed $pf_bak (pf.conf not present pre-install)"
        fi
    fi

    return 0
}

# Remove all AWG backup generations (generation 1 through current). This is
# called after successful stock baseline restoration.
remove_all_backup_generations() {
    local f g
    for f in "$BSD_BACKUP_BASE".* "$IFCFG_BACKUP_BASE".* \
             "$IFAWG_BACKUP_BASE".* "$AWGCTL_BACKUP_BASE".* \
             "$KERNEL_SHA256_BACKUP_BASE".*; do
        [ -e "$f" ] || continue
        case "$f" in *".awg-prev."*) g=${f##*.awg-prev.} ;; *) continue ;; esac
        case "$g" in ''|*[!0-9]*) continue ;; esac
        rm -f "$f" 2>/dev/null && log "Removed backup $f"
    done
}

# uninstall_all: restore the original stock baseline (generation 1) and remove
# AWG artifacts. This reuses the paired rollback machinery (restore_generation_pair)
# with the stock generation's backups. Fails closed if the stock baseline cannot
# be authenticated. Idempotent: if already uninstalled (stock live, no AWG artifacts),
# succeeds without changes.
uninstall_all() {
    step "Uninstall: restore original stock baseline (generation 1), remove AWG artifacts"

    # Idempotency check: if already uninstalled, succeed quietly.
    # Stock is live when /bsd matches the stock baseline (generation 1 backup)
    # and no AWG artifacts (awg-quick, awgctl, header) are present.
    if [ -f "${BSD_BACKUP_BASE}.1" ] && [ -f "${IFCFG_BACKUP_BASE}.1" ]; then
        local live_bsd_sha stock_bsd_sha
        live_bsd_sha=$(sha256 -q "$BSD_FILE" 2>/dev/null)
        stock_bsd_sha=$(sha256 -q "${BSD_BACKUP_BASE}.1" 2>/dev/null)
        if [ -n "$live_bsd_sha" ] && [ "$live_bsd_sha" = "$stock_bsd_sha" ]; then
            # Stock kernel is live. Check if AWG artifacts exist.
            if [ ! -f "$AWG_QUICK_FILE" ] && [ ! -f "$AWGCTL_FILE" ] && [ ! -f "$IFAWG_HEADER" ]; then
                log "Already uninstalled: stock baseline live, no AWG artifacts present"
                return 0
            fi
        fi
    fi

    # First, identify and authenticate the stock baseline (generation 1)
    if ! identify_stock_generation; then
        die "stock baseline unavailable or corrupt — refusing to uninstall; recovery data preserved at $INSTALL_DB"
    fi

    # Validate the stock generation's backup files have matching hashes before
    # any write. We compute hashes of the stock backup files and use them as
    # the expected values for restore_generation_pair.
    local bsd_sha ifc_sha hdr_sha ctl_sha ksha_sha
    bsd_sha=$(sha256 -q "$STOCK_BSD_PATH") || die "cannot hash stock kernel backup"
    ifc_sha=$(sha256 -q "$STOCK_IFCFG_PATH") || die "cannot hash stock ifconfig backup"
    hdr_sha=""
    [ -f "$STOCK_IFAWG_PATH" ] && hdr_sha=$(sha256 -q "$STOCK_IFAWG_PATH")
    ctl_sha=""
    [ -f "$STOCK_AWGCTL_PATH" ] && ctl_sha=$(sha256 -q "$STOCK_AWGCTL_PATH")
    ksha_sha=""
    [ -f "$STOCK_KSHA_PATH" ] && ksha_sha=$(sha256 -q "$STOCK_KSHA_PATH")

    # Restore the stock pair using the validated hashes
    if ! restore_generation_pair "$STOCK_BSD_PATH" "$STOCK_IFCFG_PATH" \
            "$STOCK_IFAWG_PATH" "$STOCK_AWGCTL_PATH" \
            "$bsd_sha" "$ifc_sha" "$hdr_sha" "$ctl_sha" \
            "$STOCK_KSHA_PATH" "$ksha_sha"; then
        die "stock baseline restoration failed (state: ${RESTORE_STATE:-nothing written}): recovery data preserved at $INSTALL_DB"
    fi

    log "Stock baseline restored (generation 1: $BSD_FILE + $IFCFG_FILE + $IFAWG_HEADER + $AWGCTL_FILE + $KERNEL_SHA256)"

    # Restore non-pair artifacts from pre-install inventory (best effort)
    restore_preinstall_inventory || log "Pre-install inventory restore had warnings (see above)"

    # Remove all backup generations (now that stock is live)
    remove_all_backup_generations

    # Remove the manifest and fsync helper (the install DB record)
    rm -f "$MANIFEST" || die "cannot remove $MANIFEST"
    [ -f "$FSYNC_HELPER" ] && rm -f "$FSYNC_HELPER" || true

    # R7: the lock and its immutable claim/release history are kept
    log "Uninstalled: stock baseline restored, AWG artifacts removed (lock history kept at $LOCKFILE)"
}

# Report what an install would do and stop. Nothing is written, so this is safe
# to run on a production box before a maintenance window.
check_plan() {
    local rc=0 key ver ifcfg_ver m_tag m_v m_bsd m_ifc bsd_sha ifc_sha trust v
    local want_k r_state r_count r_rec

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
        want_k=$(kernel_db_sha)
        if [ -n "$want_k" ] && [ "$bsd_sha" = "$want_k" ]; then
            printf 'kernel sha256 : matches %s\n' "$KERNEL_SHA256"
        elif [ -n "$want_k" ]; then
            printf 'kernel sha256 : MISMATCH against %s (%s)\n' "$KERNEL_SHA256" "$want_k"
            rc=1
        elif [ -n "$KERNEL_SHA256" ] && [ -f "$KERNEL_SHA256" ]; then
            printf 'kernel sha256 : %s records no SHA256 (%s) entry\n' "$KERNEL_SHA256" "$BSD_FILE"
            rc=1
        elif [ -n "$m_bsd" ] && [ "$bsd_sha" = "$m_bsd" ]; then
            printf 'kernel sha256 : matches manifest\n'
        else
            printf 'kernel sha256 : not recorded by this installer (no %s)\n' "$KERNEL_SHA256"
        fi
        if [ -n "$m_bsd" ] && [ -n "$bsd_sha" ] && [ "$bsd_sha" != "$m_bsd" ]; then
            printf 'relink        : /bsd differs from the staged kernel (KARL relink, expected after boot)\n'
        fi
        # F9: is the boot-time relink actually able to run? A custom kernel with
        # an empty relink directory fails at every boot with
        # "reorder_kernel: failed" and nothing in the installer reported it.
        r_state=$(relink_state_now)
        r_count=$(relink_object_count)
        r_rec=$(manifest_get "$MANIFEST" relink_state)
        if [ "$r_state" = present ]; then
            printf 'relink objects: present (%s objects at %s)\n' "$r_count" "$(relink_dir)"
        elif [ "$r_rec" = skipped ]; then
            printf 'relink objects: not installed (--skip-relink-objects): reorder_kernel will fail at boot\n'
        elif [ "$r_rec" = present ]; then
            printf 'relink objects: MISSING at %s but the manifest recorded them — rerun --kernel-only\n' "$(relink_dir)"
            rc=1
        else
            printf 'relink objects: not present at %s (install with --kernel-only to enable boot relink)\n' "$(relink_dir)"
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
Usage: $0 [--tools-only | --kernel-only | --build-only | --rollback | --check | --all | --uninstall]
          [--pf] [--rollback-kernel-only] [--allow-root-build] [--purge-configs] [--skip-relink-objects]

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
  --uninstall    Restore the original stock baseline (generation 1) via the
                 paired rollback machinery, then remove AWG artifacts
                 (awg-quick, awgctl.8, all .awg-prev generations, config
                 directory, install DB record). The R7 lock history is kept.
                 Fails closed if the stock baseline cannot be authenticated.
  --allow-root-build
                 Compile as root instead of the build user. Recovery only:
                 the tree is then writable by other accounts (AWG-014).
  --purge-configs
                 With --uninstall: also remove the config directory and its
                 contents even when it existed pre-install. Default: preserve
                 user configs.
  --pf           Also add the MSS clamp rule to an existing /etc/pf.conf
                 (default: print the rule and leave the firewall alone)
  --skip-relink-objects
                 Do not install the KARL relink objects (default: install
                 them). Only for a host with no room under /usr: without them
                 the boot-time reorder_kernel fails and /bsd is never relinked.
                 Recorded in the manifest; --check reports it.

Disk space (F10). Measured on OpenBSD 8.0/amd64 with the AWG.MP config: the
kernel object directory is ~700 MB-1 GB and the KARL relink set
/usr/share/relink/kernel/<ident> is 2486 objects / 677 MB — the relink set is
a near-copy of the objects, not a small subset. An upgrade needs room for both
sets at once (the previous one is kept as <ident>.awg-prev.<generation> until
the next generation commits), so budget ~2.2 GB on the filesystem holding
/usr/share/relink, plus the same order for the object directory. The installer
prints the free space before compiling and refuses to publish a relink set
when less than its size plus 100 MB is available; --skip-relink-objects is the
documented way out on a host that cannot meet that.

Environment:
  AWG_BUILD_USER   Account that runs config/make (default: _awgbuild).
                   Create it with:  useradd -s /sbin/nologin _awgbuild
                   On stock 8.0 /usr/obj is build:wobj 0770, so that account
                   cannot create the object directory config(8) asks for. The
                   installer changes no mode: it adds the account to the group
                   that owns /usr/obj (wobj, the group that exists for exactly
                   this) and, if that is not enough, pre-creates
                   /usr/obj/sys/arch/amd64/compile and hands only that subtree
                   to the builder. Point the build elsewhere with
                   BSDOBJDIR=<dir> (or build with --allow-root-build on a
                   throwaway machine).
  SRCDIR           Kernel source root (default: /usr/src/sys). A redirect to
                   another filesystem is supported; its parent must be owned by
                   root or AWG_BUILD_USER and not group/other writable, /usr/src
                   may be a symlink (it is followed and checked), and a missing
                   parent is created before the signed archive is extracted.
  IFCFG_SRC        ifconfig source directory (default: /usr/src/sbin/ifconfig).
  AWG_RELEASE_TAG  Release tag recorded in the backup manifest (default: git
                   describe of the repo, else "unknown")

Disk space (F10):
  The kernel object directory needs ~1 GB and the KARL relink set in
  /usr/share/relink/kernel/AWG.MP another ~700 MB; the previous relink set is
  kept (by rename) as .../AWG.MP.awg-prev.<gen> until the next generation
  commits, so plan ~2 GB free on the filesystem holding /usr. The installer
  checks the free space before it copies the objects and refuses with the
  numbers when they do not fit.

Verifying a kernel by hand (F1):
  sha256 -h HASHFILE FILE writes the hash into HASHFILE. \`sha256 -h /bsd\` is
  NOT a read: -h names the output file, so it overwrites /bsd with a hash line
  and leaves the machine unbootable. To read a kernel hash use:
      sha256 -q /bsd
      sha256 -C /var/db/kernel.SHA256 /bsd      # verify against the DB

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
PURGE_CONFIGS=no
for arg in "$@"; do
    case "$arg" in
        --tools-only)          MODE=tools ;;
        --kernel-only)         MODE=kernel ;;
        --build-only)          MODE=build ;;
        --rollback)            MODE=rollback ;;
        --check)               MODE=check ;;
        --all)                 MODE=all ;;
        --uninstall)           MODE=uninstall ;;
        --pf)                  EDIT_PF=yes ;;
        --rollback-kernel-only) ROLLBACK_KERNEL_ONLY=yes ;;
        --allow-root-build)    ALLOW_ROOT_BUILD=yes ;;
--purge-configs)       PURGE_CONFIGS=yes ;;
        --skip-relink-objects) RELINK_SKIP=yes ;;
        -h|--help)             usage ;;
        *) die "Unknown option: $arg. Run $0 --help for usage." ;;
    esac
done

check_root
acquire_lock
: > "$LOGFILE"

# resolve_build_user is only needed for build modes. --uninstall, --check,
# --rollback, --rollback-kernel-only do not compile anything.
case "$MODE" in
    tools|kernel|build|all)
        resolve_build_user
        ;;
esac

case "$MODE" in
    tools)
        prepare_durability_helper
        snapshot_precommit_manifest
        write_backup_pair
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
        snapshot_precommit_manifest
        write_backup_pair
        fetch_sys_sources
        kernel_build
        PUBLISH_STARTED=yes
        trap 'publish_interrupted' HUP INT TERM
        publish_header
        kernel_publish || publish_abort "kernel publish failed"
        # F9: the relink objects are part of the kernel publish.
        publish_relink_objects || publish_abort "KARL relink objects publish failed"
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
    uninstall)
        uninstall_all
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
