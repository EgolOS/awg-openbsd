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
    local ts
    ts=$(date '+%a %b %e %T %Y')
    diff -u /dev/null "$src" | awk \
        -v ts="$ts" \
        -v name="$dst_name" \
        'NR==1 { print "--- /dev/null" "\t" ts; next }
         NR==2 { print "+++ " name "\t" ts; next }
         { print }'
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
