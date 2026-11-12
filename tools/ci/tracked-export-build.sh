#!/bin/sh
# Tracked-export build: export HEAD, compile awgctl, negative control
# This is the exact sequence that must run in CI to prevent the v0.2.1-rc1 defect.

set -eu

echo "=== Tracked-export build: export HEAD ==="
TMPDIR=$(mktemp -d)
TMPDIR2=$(mktemp -d)
trap 'rm -rf "$TMPDIR" "$TMPDIR2"' EXIT

git archive --format=tar HEAD | tar -xf - -C "$TMPDIR"

echo "=== Verify export contains awgctl source, man, and Makefile ==="
for f in sbin/awgctl/Makefile sbin/awgctl/awgctl.8 sbin/awgctl/awgctl.c; do
    if [ ! -f "$TMPDIR/$f" ]; then
        echo "FAIL: tracked export is missing $f"
        exit 1
    fi
    echo "OK: $f present"
done

echo "=== Compile awgctl from tracked export ==="
if ! (cd "$TMPDIR" && make awgctl); then
    echo "FAIL: make awgctl failed in clean export"
    exit 1
fi

if [ ! -f "$TMPDIR/awgctl" ]; then
    echo "FAIL: make awgctl produced no binary in the export"
    exit 1
fi
echo "OK: awgctl compiled successfully from tracked export"

# Copy binary to out/ for CI verification step
mkdir -p out
cp "$TMPDIR/awgctl" out/awgctl
echo "OK: awgctl binary copied to out/awgctl"

echo "=== Negative control: remove source, verify build fails ==="
git archive --format=tar HEAD | tar -xf - -C "$TMPDIR2"
rm -f "$TMPDIR2/sbin/awgctl/awgctl.c"

if (cd "$TMPDIR2" && make awgctl); then
    echo "FAIL: make awgctl must fail without awgctl.c"
    exit 1
fi
echo "OK: negative control passed - build fails without source"

echo "=== All tracked-export build checks passed ==="
exit 0