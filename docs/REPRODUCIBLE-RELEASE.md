# Reproducible Release Archive — Verification Recipe

This document describes how to verify the integrity and reproducibility of the awg-openbsd release archive.

## Archive Details

- **Project**: awg-openbsd (AmneziaWG for OpenBSD)
- **Version**: v0.2.1-rc1
- **Source Commit**: (the commit containing the release tools; see verification procedure)
- **Archive Name**: awg-openbsd-v0.2.1-rc1.tar.gz
- **Expected SHA256**: (computed by the verification script; see below)

## Toolchain Used

The archive was created using:
- **git**: 2.56.0 (for `git archive` export)
- **tar**: GNU tar 1.35 (with `--sort=name`, `--mtime=@0`, `--owner`, `--group`, `--pax-option`, `--mode`)
- **gzip**: 1.14 (with `-n` flag to omit timestamp)
- **sha256sum**: GNU coreutils 9.10

All tools are standard POSIX/Linux utilities available on any modern system.

## Reproducible Archive Creation

The archive is created deterministically with these properties:
- Entries sorted by name (`--sort=name`)
- Fixed modification time: Unix epoch 1970-01-01 00:00:00 UTC (`--mtime='@0'`, unambiguous across timezones)
- Fixed ownership: uid=0, gid=0 (`--owner=0 --group=0 --numeric-owner`)
- Fixed permissions: directories 0755, non-executable files 0644, executable files 0755, symlinks preserved (`--mode=go=rX,u+rw`)
- No atime/ctime in pax headers (`--pax-option=delete=atime,delete=ctime`)
- No gzip timestamp (`gzip -n`)
- No host-specific paths (uses `git archive` for clean export)
- Excluded paths: `evidence/`, `reports/`, `docs/evidence/`, `*.tmp.*`

## Verification Procedure

### Quick Verification (Single Archive)

```sh
# Obtain the archive from the official release artifacts (see project releases page)
# or build it locally using the instructions below.

# Verify SHA256 (the expected hash is printed by the verification script)
sha256sum awg-openbsd-v0.2.1-rc1.tar.gz
# Compare with the expected hash published alongside the release artifact
```

Or use the provided verification script with the expected hash from the release:

```sh
./tools/verify-release-archive.sh awg-openbsd-v0.2.1-rc1.tar.gz <EXPECTED_SHA256_FROM_RELEASE>
# Output: Verification PASSED: awg-openbsd-v0.2.1-rc1.tar.gz matches expected SHA256
```

### Full Reproducibility Verification (Two Independent Builds)

To prove the archive is reproducible, build it twice from the same source commit in different directories:

```sh
# Clone the repository
git clone https://github.com/EgolOS/awg-openbsd
cd awg-openbsd

# Check out the exact commit containing the release tools
# (This is the commit tagged with the release version, e.g., v0.2.1-rc1)
git checkout <RELEASE_COMMIT>

# Verify you're at the correct commit
git rev-parse HEAD
# Should output: <RELEASE_COMMIT>

# Build 1
BUILD1=$(mktemp -d /tmp/awg-build1.XXXXXX)
./tools/create-release-archive.sh "$BUILD1/archive1.tar.gz" v0.2.1-rc1 <RELEASE_COMMIT> .

# Build 2
BUILD2=$(mktemp -d /tmp/awg-build2.XXXXXX)
./tools/create-release-archive.sh "$BUILD2/archive2.tar.gz" v0.2.1-rc1 <RELEASE_COMMIT> .

# Compare hashes
sha256sum "$BUILD1/archive1.tar.gz" "$BUILD2/archive2.tar.gz"
# Both should output the same hash (printed by the script)

# Verify byte-identical
cmp "$BUILD1/archive1.tar.gz" "$BUILD2/archive2.tar.gz" && echo "Byte-identical"
```

Replace `<RELEASE_COMMIT>` with the actual commit hash of the release (e.g., the commit tagged `v0.2.1-rc1`). The test script will print the expected SHA256 for that commit.

### Automated Test

Run the comprehensive test suite:

```sh
./tools/test-reproducible-archive.sh
```

This test:
1. Builds the archive twice in independent temporary directories (respecting TMPDIR)
2. Verifies both builds produce identical hashes
3. Verifies byte-for-byte identity
4. Verifies the verification script passes with the correct hash
5. Verifies the verification script **fails** when:
   - A stray byte is appended to the archive (corruption)
   - The archive is recreated with non-deterministic tar (mtime change)

### Cross-Timezone Reproducibility Test

To verify the archive is reproducible across timezones:

```sh
# Build in UTC
TZ=UTC ./tools/test-reproducible-archive.sh . v0.2.1-rc1 <RELEASE_COMMIT>

# Build in Europe/Istanbul
TZ=Europe/Istanbul ./tools/test-reproducible-archive.sh . v0.2.1-rc1 <RELEASE_COMMIT>

# Both should produce the same SHA256 hash
```

### Clean Export Verification (No Git Repository)

For verifying from a `git archive` export (no .git directory):

```sh
# Create a clean export of the release commit
git archive --format=tar --prefix=awg-openbsd-export/ <RELEASE_COMMIT> | tar -xf -
cd awg-openbsd-export

# Run the test with explicit source repository (path to a git clone with the release tools)
AWG_VERSION=v0.2.1-rc1 \
AWG_COMMIT=<RELEASE_COMMIT> \
AWG_SOURCE_REPO=/path/to/awg-openbsd.git \
./tools/test-reproducible-archive.sh
```

Or via Makefile:
```sh
AWG_VERSION=v0.2.1-rc1 \
AWG_COMMIT=<RELEASE_COMMIT> \
AWG_SOURCE_REPO=/path/to/awg-openbsd.git \
make check-repro
```

Replace `<RELEASE_COMMIT>` with the actual commit hash of the release, and `/path/to/awg-openbsd.git` with the path to a git repository containing the release tools (the commit must be present in that repository).

## Verification Script

The `tools/verify-release-archive.sh` script implements the verification check:

```sh
#!/bin/sh
# verify-release-archive.sh <archive> <expected-sha256>
# Exit 0 = match, Exit 1 = mismatch
```

Usage:
```sh
./tools/verify-release-archive.sh awg-openbsd-v0.2.1-rc1.tar.gz 88ab1e3dc159914af5fb3982234ae0aa2be90eab3ffdc8f1694560572f51d77a
```

## Failure Modes

The verification **will fail** (exit code 1) when:
- The archive has been modified (even by a single byte)
- The archive was created with different tool versions or options
- The source commit differs from the expected one
- The archive contains different files (added/removed/modified)
- Timestamps, ownership, or permissions differ in the archive

## CI Integration

To integrate into CI, add this check:

```yaml
# Example GitHub Actions step
- name: Verify release archive reproducibility
  run: |
    ./tools/test-reproducible-archive.sh
```

The test will fail if reproducibility is broken, preventing release of non-reproducible archives.

## Archive Contents

The archive contains the complete source tree needed to build and install awg-openbsd:

```
awg-openbsd-v0.2.1-rc1/
├── .gitattributes
├── .github/workflows/ci.yml
├── .gitignore
├── CHANGELOG.md
├── LICENSE
├── Makefile
├── README.md
├── SECURITY.md
├── awg-openbsd.patch
├── docs/           # All documentation (no evidence/)
├── examples/       # Client/server config examples
├── generate-patch.sh
├── install.sh
├── sbin/
│   ├── awg-quick
│   ├── awgctl/
│   │   ├── Makefile
│   │   ├── awgctl.8
│   │   └── awgctl.c
│   └── ifconfig/
│       └── ifconfig_awg.patch
├── src/
│   ├── AWG.MP
│   ├── awg_noise.c
│   ├── awg_noise.h
│   ├── if_awg.c
│   ├── if_awg.h
│   ├── kern_pledge.c
│   ├── kern_pledge.c.orig
│   └── net -> .    # Symlink preserved
└── tools/          # Build, test, and release tools (no evidence/, reports/)
```

Excluded from archive:
- `evidence/` — test run logs and artifacts
- `reports/` — engineering reports
- `docs/evidence/` — detailed test evidence
- `*.tmp.*` — temporary files
- `.git/` — git metadata (excluded by `git archive`)

## Security Notes

- The archive contains **no secrets, keys, or personal paths**
- Build host information is not embedded
- The fixed timestamp (epoch `@0`) and ownership (root:root) ensure no host information leaks
- Verification uses only SHA256, no external trust anchors required

## Contact

For questions about this verification process, see the project repository at:
https://github.com/EgolOS/awg-openbsd