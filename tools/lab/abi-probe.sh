#!/bin/sh
# ABI probe for awg(4) ioctl structures — UNEXECUTED OUTLINE
# This script documents the probe matrix; execution requires OpenBSD kernel with awg(4) loaded
# No ioctl calls are made; script exits 0 without performing any tests

set -eu

echo "=== ABI Probe: awg(4) ioctl structure validation (OUTLINE ONLY) ==="
echo "Testing SIOCSAWG (223) and SIOCGAWG (224) structure sizes"
echo ""
echo "This is an unexecuted outline. Actual probe requires OpenBSD kernel with awg(4) loaded."
echo "The following tests would need to be run on a disposable VM:"
echo ""

# Test 1: Size probe - minimum 1 byte
echo "Test 1: 1-byte structure"
echo "  Would test with a 1-byte buffer - expect EINVAL"
echo ""

# Test 2: Zeroed structure
echo "Test 2: Zeroed structure"
echo "  Would test with a fully zeroed struct - expect EINVAL or proper defaults"
echo ""

# Test 3: SIZE_MAX oversize
echo "Test 3: SIZE_MAX oversize"
echo "  Would test with an oversized buffer - expect EINVAL"
echo ""

# Test 4: Valid structure round-trip
echo "Test 4: Valid structure round-trip"
echo "  Would test a properly formed structure - expect success"
echo ""

echo "=== ABI Probe outline complete ==="
echo "NOTE: This script performs NO actual ioctl tests. It only documents the probe matrix."
echo "Evidence status: NONE (unexecuted outline)"
exit 0