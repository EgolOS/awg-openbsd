#!/bin/sh
# Test script for reproducible release archive verification.
# Builds the archive twice, verifies they are identical, and shows
# that perturbing the archive makes verification fail.
# Usage: test-reproducible-archive.sh [repo-path] [version] [commit-sha]
# All arguments optional; defaults to current repo, version from git describe, and HEAD.
# Environment variables: AWG_VERSION, AWG_COMMIT, AWG_SOURCE_REPO can override args.
# For clean exports (no git repo), AWG_SOURCE_REPO must point to a git repository.
set -eu

# Resolve this script's directory to find sibling tools
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO="${1:-$SCRIPT_DIR/..}"
CREATE_SCRIPT="$SCRIPT_DIR/create-release-archive.sh"
VERIFY_SCRIPT="$SCRIPT_DIR/verify-release-archive.sh"

# Determine version and commit from repo if not provided via args or env
if [ -n "${2:-}" ]; then
    VERSION="$2"
elif [ -n "${AWG_VERSION:-}" ]; then
    VERSION="$AWG_VERSION"
else
    VERSION=$(git -C "$REPO" describe --tags --always --dirty 2>/dev/null || echo "dev")
fi

if [ -n "${3:-}" ]; then
    COMMIT="$3"
elif [ -n "${AWG_COMMIT:-}" ]; then
    COMMIT="$AWG_COMMIT"
else
    COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "unknown")
fi

# Source repository for git archive (required for clean exports without .git)
# Defaults to REPO if it's a git repo, otherwise must be set via AWG_SOURCE_REPO
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
    SOURCE_REPO="$REPO"
elif [ -n "${AWG_SOURCE_REPO:-}" ]; then
    SOURCE_REPO="$AWG_SOURCE_REPO"
else
    echo "error: not in a git repository and AWG_SOURCE_REPO not set" >&2
    exit 1
fi

# Respect TMPDIR for all temporary directories (fallback to /tmp)
TMPDIR="${TMPDIR:-/tmp}"

# Create temporary directories for two independent builds
BUILD1=$(mktemp -d "$TMPDIR/awg-build1.XXXXXX")
BUILD2=$(mktemp -d "$TMPDIR/awg-build2.XXXXXX")
PERTURBED=$(mktemp -d "$TMPDIR/awg-perturbed.XXXXXX")

trap 'rm -rf "$BUILD1" "$BUILD2" "$PERTURBED"' EXIT

echo "=== Building archive in $BUILD1 ==="
"$CREATE_SCRIPT" "$BUILD1/archive1.tar.gz" "$VERSION" "$COMMIT" "$SOURCE_REPO"
HASH1=$(sha256sum "$BUILD1/archive1.tar.gz" | awk '{print $1}')
echo "Hash 1: $HASH1"

echo "=== Building archive in $BUILD2 ==="
"$CREATE_SCRIPT" "$BUILD2/archive2.tar.gz" "$VERSION" "$COMMIT" "$SOURCE_REPO"
HASH2=$(sha256sum "$BUILD2/archive2.tar.gz" | awk '{print $1}')
echo "Hash 2: $HASH2"

echo "=== Verifying archives are identical ==="
if [ "$HASH1" = "$HASH2" ]; then
    echo "SUCCESS: Both builds produced identical archives (hash: $HASH1)"
else
    echo "FAILURE: Builds produced different archives!" >&2
    echo "  Build 1: $HASH1" >&2
    echo "  Build 2: $HASH2" >&2
    exit 1
fi

# Verify byte-for-byte identity
if cmp -s "$BUILD1/archive1.tar.gz" "$BUILD2/archive2.tar.gz"; then
    echo "SUCCESS: Archives are byte-identical"
else
    echo "FAILURE: Archives differ at byte level!" >&2
    exit 1
fi

echo "=== Verifying with correct expected hash ==="
"$VERIFY_SCRIPT" "$BUILD1/archive1.tar.gz" "$HASH1"
echo "SUCCESS: Verification with correct hash passes"

echo "=== Testing perturbation: adding a stray byte ==="
cp "$BUILD1/archive1.tar.gz" "$PERTURBED/perturbed.tar.gz"
# Perturb by appending a byte (simulating a stray file or corruption)
echo "x" >> "$PERTURBED/perturbed.tar.gz"
PERTURBED_HASH=$(sha256sum "$PERTURBED/perturbed.tar.gz" | awk '{print $1}')
echo "Perturbed hash: $PERTURBED_HASH"

if "$VERIFY_SCRIPT" "$PERTURBED/perturbed.tar.gz" "$HASH1" 2>/dev/null; then
    echo "FAILURE: Verification should have failed but passed!" >&2
    exit 1
else
    echo "SUCCESS: Verification correctly fails on perturbed archive"
fi

echo "=== Testing perturbation: changing mtime via tar recreation ==="
# Extract and re-tar without deterministic options (changes mtime)
mkdir -p "$PERTURBED/extract"
tar -xzf "$BUILD1/archive1.tar.gz" -C "$PERTURBED/extract"
# Re-create with default tar (non-deterministic)
tar -czf "$PERTURBED/mtime-changed.tar.gz" -C "$PERTURBED/extract" .
MTIME_HASH=$(sha256sum "$PERTURBED/mtime-changed.tar.gz" | awk '{print $1}')
echo "Mtime-changed hash: $MTIME_HASH"

if [ "$MTIME_HASH" = "$HASH1" ]; then
    echo "FAILURE: Non-deterministic tar produced same hash (unexpected)!" >&2
    exit 1
else
    echo "SUCCESS: Non-deterministic tar produces different hash"
fi

if "$VERIFY_SCRIPT" "$PERTURBED/mtime-changed.tar.gz" "$HASH1" 2>/dev/null; then
    echo "FAILURE: Verification should have failed but passed!" >&2
    exit 1
else
    echo "SUCCESS: Verification correctly fails on mtime-changed archive"
fi

echo ""
echo "=== ALL TESTS PASSED ==="
echo "Reproducible archive verification is working correctly."
echo "Expected SHA256 for version $VERSION at commit $COMMIT:"
echo "$HASH1"