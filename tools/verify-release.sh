#!/bin/sh
# Verify exactly one archive against an embedded signify checksum manifest.
set -eu
[ "$#" -eq 3 ] || { echo 'usage: verify-release.sh KEY SHA256.sig ARCHIVE' >&2; exit 1; }
key=$(readlink -f "$1")
sig=$(readlink -f "$2")
archive=$(readlink -f "$3")
[ -f "$key" ] && [ -f "$sig" ] && [ -f "$archive" ] || exit 1
name=${archive##*/}
case "$name" in sys.tar.gz|src.tar.gz) ;; *) echo 'unsupported source archive' >&2; exit 1 ;; esac
cd "$(dirname "$archive")"
# Explicit selection avoids failures for unrelated sets we did not download.
# Do not parse human-readable output or mask signify's exit status.
if command -v signify >/dev/null 2>&1; then
    exec signify -C -p "$key" -x "$sig" "$name"
fi
exec signify-openbsd -C -p "$key" -x "$sig" "$name"
