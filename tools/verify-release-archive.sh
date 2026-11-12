#!/bin/sh
# Verify a release archive against an expected SHA256.
# Usage: verify-release-archive.sh <archive.tar.gz> <expected-sha256>
# Exits 0 if the archive matches, 1 if it differs.
set -eu

ARCHIVE="$1"
EXPECTED="$2"

if [ -z "$ARCHIVE" ] || [ -z "$EXPECTED" ]; then
    echo "usage: $0 <archive.tar.gz> <expected-sha256>" >&2
    exit 1
fi

if [ ! -f "$ARCHIVE" ]; then
    echo "error: archive not found: $ARCHIVE" >&2
    exit 1
fi

ACTUAL=$(sha256sum "$ARCHIVE" | awk '{print $1}')

if [ "$ACTUAL" = "$EXPECTED" ]; then
    echo "Verification PASSED: $ARCHIVE matches expected SHA256"
    exit 0
else
    echo "Verification FAILED: $ARCHIVE" >&2
    echo "  Expected: $EXPECTED" >&2
    echo "  Actual:   $ACTUAL" >&2
    exit 1
fi