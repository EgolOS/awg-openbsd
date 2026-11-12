#!/bin/sh
# Create a reproducible release archive for awg-openbsd.
# Usage: create-release-archive.sh <output.tar.gz> <version> <commit-sha> [repo-path]
# The archive is deterministic: sorted entries, fixed mtime/uid/gid/mode, no host paths.
set -eu

OUTPUT="$1"
VERSION="$2"
COMMIT="$3"
REPO="${4:-$(pwd)}"

if [ -z "$OUTPUT" ] || [ -z "$VERSION" ] || [ -z "$COMMIT" ]; then
    echo "usage: $0 <output.tar.gz> <version> <commit-sha> [repo-path]" >&2
    exit 1
fi

# Respect TMPDIR for temporary directories (fallback to /tmp)
TMPDIR="${TMPDIR:-/tmp}"

# Create a temporary directory for the export
EXPORT_DIR=$(mktemp -d "$TMPDIR/awg-export.XXXXXX")
trap 'rm -rf "$EXPORT_DIR"' EXIT

# Export the repository at the given commit, excluding unwanted paths
# Use git archive for a clean export, then re-package with deterministic options
git -C "$REPO" archive --format=tar --prefix="awg-openbsd-${VERSION}/" "$COMMIT" \
    -- . \
    ':!evidence/' \
    ':!reports/' \
    ':!docs/evidence/' \
    ':!*.tmp.*' \
    | tar -C "$EXPORT_DIR" -xf -

# Now create the deterministic archive from the exported directory
# Use tar with reproducible options:
# --sort=name           : sort entries by name
# --mtime='@0'          : fixed timestamp (epoch, unambiguous across timezones)
# --owner=0 --group=0   : fixed uid/gid (root)
# --numeric-owner       : use numeric owner/group
# --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime : reproducible pax headers
# --mode=go=rX,u+rw     : fixed permissions (directories: 755, files: 644, symlinks preserved)
# The gzip -n avoids embedding timestamp in gzip header

cd "$EXPORT_DIR"
find "awg-openbsd-${VERSION}" -type f -o -type d -o -type l | LC_ALL=C sort | \
    tar --no-recursion --sort=name \
        --mtime='@0' \
        --owner=0 --group=0 --numeric-owner \
        --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
        --mode=go=rX,u+rw \
        -cf - -T - | gzip -n > "$OUTPUT"

# Verify the archive can be listed
tar -tzf "$OUTPUT" >/dev/null

echo "Created reproducible archive: $OUTPUT"
echo "Version: $VERSION"
echo "Commit: $COMMIT"
sha256sum "$OUTPUT"