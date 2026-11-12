#!/bin/sh
# Prove the selected suite's comparator sensitivity and bind each injected
# failure to its exact comparison, with the clean run as an independent witness.
set -u
suite=${AWGCTL_GATE_SUITE:-tools/lab/awgctl-fixture-test.sh}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
records="$here/gate-records.py"
python=${PYTHON:-python3}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/awgctl-gate-selftest.XXXXXX") || exit 1
cleanup() { s=$?; rm -rf "$tmp"; exit "$s"; }
trap cleanup EXIT INT TERM
rc=0
want_pass=${AWGCTL_GATE_PASS:-81}
want_fail=${AWGCTL_GATE_FAIL:-0}
inject=${AWGCTL_GATE_INJECT_ISPEC2:-'<b 0xdead>'}
want_inject_ids=${AWGCTL_GATE_INJECT_IDS:-'F13a F13c'}
adversary=${AWGCTL_GATE_ADVERSARY:-tools/lab/guard-adversary.sh}
count() { grep -c "$1" "$2" || :; }

# These are field-only sensitivity inputs, NOT fixture baselines for attribution.
# Extract the comparator from the suite actually selected, not the in-tree suite.
if ! sed -n '/^canon_state() {/,/^}/p' "$suite" > "$tmp/render.sh" ||
    [ "$(grep -c '^canon_state() {' "$tmp/render.sh")" != 1 ] ||
    ! sh -n "$tmp/render.sh"; then
	echo 'gate-selftest: FAIL — selected suite comparator extraction failed'
	exit 1
fi
cat >> "$tmp/render.sh" <<'EOF'
canon_state "$1"
EOF
printf 'ispec2=<b 0x01>\n' > "$tmp/probe.a"
printf 'ispec2=%s\n' "$inject" > "$tmp/probe.b"
if ! sh "$tmp/render.sh" "$tmp/probe.a" > "$tmp/render.a" ||
    ! sh "$tmp/render.sh" "$tmp/probe.b" > "$tmp/render.b" ||
    ! "$python" "$records" probe "$tmp/render.a" "$tmp/render.b" "$inject"; then
	echo 'gate-selftest: FAIL — selected suite extracted-function I2-only render probe failed'
	exit 1
fi

sh "$suite" > "$tmp/clean.log" 2>&1
clean_rc=$?
clean_pass=$(count '^PASS:' "$tmp/clean.log")
clean_fail=$(count '^FAIL:' "$tmp/clean.log")
if [ "$clean_rc" != 0 ] || [ "$clean_pass" != "$want_pass" ] || [ "$clean_fail" != "$want_fail" ]; then
	echo "gate-selftest: FAIL — clean run not green (rc=$clean_rc, $clean_pass PASS, $clean_fail FAIL; expected $want_pass/$want_fail)"
	cat "$tmp/clean.log"
	rc=1
else
	echo "gate-selftest: clean run green ($clean_pass PASS, $clean_fail FAIL)"
fi
AWGFIX_MUTATE_ISPEC2="$inject" sh "$suite" > "$tmp/injected.log" 2>&1
inj_rc=$?
inj_ids=$(grep '^FAIL:' "$tmp/injected.log" | cut -d: -f2 | sed 's/^ *//' | sort -u | tr '\n' ' ' | sed 's/ *$//')
if [ "$inj_rc" != 1 ] || [ "$inj_ids" != "$want_inject_ids" ]; then
	echo "gate-selftest: FAIL — injected run wrong status/locations (rc=$inj_rc, ids: $inj_ids)"
	rc=1
elif ! "$python" "$records" runs "$tmp/clean.log" "$tmp/injected.log" "$inject"; then
	echo 'gate-selftest: FAIL — comparison provenance/schema/cause rejected'
	rc=1
else
	echo "gate-selftest: injected I2 caught (2 FAIL, ids: $inj_ids, cause: ispec2)"
fi

# A generic nonzero exit is not a negative-control receipt. The control must
# first prove a built 81/0 clean run and an MTU-only 79/2 injected comparison.
if [ -z "${AWGCTL_GATE_SKIP_ADVERSARY:-}" ]; then
	if [ ! -f "$adversary" ]; then
		echo 'gate-selftest: FAIL — mandatory adversary missing'
		rc=1
	else
		AWGCTL_GATE_ADVERSARY_EVIDENCE="$tmp/evidence" sh "$adversary" > "$tmp/adversary.log" 2>&1
		adv_rc=$?
		cat "$tmp/adversary.log"
		if [ "$adv_rc" != 1 ] ||
		    [ "$(grep -c '^guard-adversary: VERIFIED built; clean=81/0; injected=79/2; cause=mtu; guard=rejected$' "$tmp/adversary.log")" != 1 ] ||
		    ! "$python" "$records" mtu "$tmp/evidence/clean.log" "$tmp/evidence/injected.log" "${ADVERSARY_MTU:-1419}" ||
		    ! grep -q '^gate-selftest: FAIL — selected suite extracted-function I2-only render probe failed$' "$tmp/evidence/guard.log"; then
			echo "gate-selftest: FAIL — mandatory adversary did not prove an executed MTU-only rejection (rc=$adv_rc)"
			rc=1
		else
			echo 'gate-selftest: negative control verified (built, clean green, injected MTU-only, rejected)'
		fi
	fi
fi
[ "$rc" = 0 ] && echo 'gate-selftest: PASS — stable and sensitive'
exit "$rc"
