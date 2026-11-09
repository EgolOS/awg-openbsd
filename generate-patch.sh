#!/bin/sh
# Generate awg-openbsd.patch from current src/ files.
# Run before commit/push to keep the patch in sync with sources.
#
#   sh generate-patch.sh          regenerate awg-openbsd.patch
#   sh generate-patch.sh --check  exit 1 if the committed patch is stale
#
# The patch is the documented way to apply the driver to a source tree, so a
# stale one silently ships an older driver than src/.

set -e

REPODIR=$(dirname "$(readlink -f "$0")")
OUT="$REPODIR/awg-openbsd.patch"
TAB="$(printf '\t')"

patch_file() {
    local src="$1" dst_name="$2"
    local ts rc tmp
    [ -f "$src" ] || { echo "generate-patch: missing source $src" >&2; exit 1; }
    ts=$(date '+%a %b %e %T %Y')
    tmp=$(mktemp "${TMPDIR:-/tmp}/awgdiff.XXXXXX") || exit 1
    # diff exits 0 (same) or 1 (differ) for a good run; >1 means trouble.
    # The pipeline used to hide that behind awk's status (R4-03).
    rc=0; diff -u /dev/null "$src" > "$tmp" || rc=$?
    [ "$rc" -le 1 ] || { echo "generate-patch: diff of $src failed (rc $rc)" >&2; rm -f "$tmp"; exit 1; }
    awk -v ts="$ts" -v name="$dst_name" \
        'NR==1 { print "--- /dev/null" "\t" ts; next }
         NR==2 { print "+++ " name "\t" ts; next }
         { print }' "$tmp" || { echo "generate-patch: awk of $src failed" >&2; rm -f "$tmp"; exit 1; }
    rm -f "$tmp"
}

# Modification hunk: the pre-change upstream file is kept next to the patched
# one (kern_pledge.c.orig) so the generated hunk carries real context.
patch_modify() {   # patch_modify <orig> <patched> <dst_name>
    local orig="$1" patched="$2" dst_name="$3"
    local ts rc tmp
    [ -f "$orig" ] || { echo "generate-patch: missing baseline $orig" >&2; exit 1; }
    [ -f "$patched" ] || { echo "generate-patch: missing patched $patched" >&2; exit 1; }
    ts=$(date '+%a %b %e %T %Y')
    tmp=$(mktemp "${TMPDIR:-/tmp}/awgdiff.XXXXXX") || exit 1
    rc=0; diff -u "$orig" "$patched" > "$tmp" || rc=$?
    [ "$rc" -le 1 ] || { echo "generate-patch: diff of $orig vs $patched failed (rc $rc)" >&2; rm -f "$tmp"; exit 1; }
    awk -v ts="$ts" -v name="$dst_name" \
        'NR==1 { print "--- " name "\t" ts; next }
         NR==2 { print "+++ " name "\t" ts; next }
         { print }' "$tmp" || { echo "generate-patch: awk of $orig failed" >&2; rm -f "$tmp"; exit 1; }
    rm -f "$tmp"
}

emit() {	# emit <output-file>
    local out="$1"
    : > "$out"
    patch_file "$REPODIR/src/if_awg.h"     "net/if_awg.h"      >> "$out"
    printf '\n'                                                   >> "$out"
    patch_file "$REPODIR/src/if_awg.c"     "net/if_awg.c"      >> "$out"
    printf '\n'                                                   >> "$out"
    patch_file "$REPODIR/src/awg_noise.h"  "net/awg_noise.h"   >> "$out"
    printf '\n'                                                   >> "$out"
    patch_file "$REPODIR/src/awg_noise.c"  "net/awg_noise.c"   >> "$out"
    printf '\n'                                                   >> "$out"

    cat >> "$out" <<EOF
--- sys/conf/files.orig
+++ sys/conf/files
@@ pseudo-device wg: ifnet
+pseudo-device awg: ifnet
@@ file net/if_wg.c
+file net/if_awg.c${TAB}${TAB}${TAB}awg
+file net/awg_noise.c${TAB}${TAB}${TAB}awg
@@ net/wg_cookie.c
+ | awg
@@ crypto/blake2s.c
+ | awg
@@ crypto/curve25519.c
+ | awg
@@ crypto/chachapoly.c
+ | awg
EOF

    # sys/kern/kern_pledge.c: stock OpenBSD does not name SIOCSAWG (223) or
    # SIOCGAWG (224) in the PLEDGE_INET ioctl group, so a pledged management
    # tool is SIGABRTed by the kernel. The hunk adds both cases (and the
    # header that declares them) — see docs/SECURITY-AUDIT.md AWG-038.
    printf '\n'                                                   >> "$out"
    patch_modify "$REPODIR/src/kern_pledge.c.orig" \
                 "$REPODIR/src/kern_pledge.c" \
                 "sys/kern/kern_pledge.c" >> "$out"
}

# Compare two patch files ignoring the embedded timestamps.
same_content() {
    local a=$1 b=$2
    awk -F'\t' '/^(---|\+\+\+) /{print $1; next} {print}' "$a" > "$a.norm"
    awk -F'\t' '/^(---|\+\+\+) /{print $1; next} {print}' "$b" > "$b.norm"
    cmp -s "$a.norm" "$b.norm"
    local rc=$?
    rm -f "$a.norm" "$b.norm"
    return $rc
}

if [ "$1" = "--check" ]; then
    tmp=$(mktemp "${TMPDIR:-/tmp}/awgpatch.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    emit "$tmp"
    if same_content "$tmp" "$OUT"; then
        echo "awg-openbsd.patch is up to date"
        exit 0
    fi
    echo "awg-openbsd.patch is STALE — run: sh generate-patch.sh" >&2
    exit 1
fi

emit "$OUT"
echo "Generated $OUT ($(wc -l < "$OUT" | tr -d ' ') lines)"
