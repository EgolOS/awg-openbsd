# awg-openbsd — repository and lab checks.
#
# The kernel itself is built by install.sh on OpenBSD; these targets only
# validate the tree and drive the lab VM, so they run on any host.
#
#   make check          syntax, python, publish scan, generated-patch freshness
#   make check-vm       copy tools/lab/smoke-test.sh into the lab VM and run it
#   make baseline       divergence report against a stock OpenBSD sys/ tree
#
# Lab defaults match tools/lab/README.md; override from the environment:
#   make check-vm LAB_VM_HOST=10.0.0.5 LAB_VM_PORT=22 LAB_SSH_KEY=~/.ssh/lab

SHELL   ?= /bin/sh
PYTHON  ?= python3

LAB_VM_HOST ?= 127.0.0.1
LAB_VM_PORT ?= 2201
LAB_SSH_KEY ?= $(HOME)/.hermes/cache/scratch/awg-lab/lab_ed25519
LAB_VM_USER ?= lab
LAB_VM_PORT_B ?= 2202
LAN_A ?= 192.168.99.1
LAN_B ?= 192.168.99.2
TREE        ?= /usr/src/sys

LAB_KNOWN_HOSTS ?= $(HOME)/.hermes/cache/scratch/awg-lab/known_hosts
AWGCTL_FIX_IFCONFIG ?= $(CURDIR)/tools/lab/fixture-ifconfig.py
SSH_OPTS = -o BatchMode=yes -o StrictHostKeyChecking=yes \
           -o UserKnownHostsFile="$(LAB_KNOWN_HOSTS)" \
           -o ConnectTimeout=10 -i "$(LAB_SSH_KEY)"
SSH  = ssh $(SSH_OPTS) -p $(LAB_VM_PORT)
SCP  = scp $(SSH_OPTS) -P $(LAB_VM_PORT)

.PHONY: check check-syntax check-py check-tests check-cgate check-oracle check-scan check-patch check-gitclean check-upstream check-citations check-matrix check-citation-tests check-repro check-vm check-interop baseline manifest awgctl awgctl-fixture install clean

check: check-syntax check-py check-tests check-cgate check-oracle check-scan check-patch check-upstream check-citations check-matrix check-repro
	@echo "repository checks: OK"

# E2-HYGIENE: a build must leave the tree porcelain-clean. v0.2.1-rc1 shipped
# with sbin/awgctl/obj/ (the bsd.prog.mk object directory) untracked, where a
# later git add/rsync can misjudge it. Run after a clean build+test:
#   make awgctl check check-gitclean
# Fails (exit 2) listing every dirty entry when `git status --porcelain` is
# not empty; needs a git checkout (porcelain exits 128 outside one).
check-gitclean:
	@dirty=$$(git status --porcelain) || { echo "git status failed" >&2; exit 2; }; \
	if [ -n "$$dirty" ]; then \
	    printf '%s\n' "$$dirty"; \
	    echo "tree is dirty after build+test: $$dirty" >&2; \
	    exit 2; \
	fi
	@echo "git tree: clean"

# The compiled-C integration gate is only worth running if it can fail. This
# self-check proves that property: a clean run is green, and a run with I2
# rewritten by the fixture must fail with the changed value in the message.
# It runs the gate twice, so it is the slowest part of `check`.
# R13: declared phony so a file named check-cgate cannot skip the recipe.
check-cgate:
	@sh tools/lab/awgctl-gate-selftest.sh

# R13: the state-oracle controls (C1..C8) are part of the mandatory gate, so
# the effective-address and I-spec semantics stay guarded after a repair.
check-oracle:
	@sh tools/lab/oracle-controls.sh
	@$(PYTHON) tools/lab/gate-controls.py

# awg-quick is bash (arrays, [[ ]]); install.sh and the lab scripts are POSIX
# sh. Checking them all with one shell is how CI caught a false failure: dash
# rejects bash syntax, so `sh -n sbin/awg-quick` fails on a correct script.
check-syntax:
	@sh -n install.sh
	@sh -n tools/gen-config.sh
	@sh -n tools/verify-release.sh
	@bash -n sbin/awg-quick
	@for f in tools/lab/*.sh; do sh -n "$$f" || exit 1; done
	@echo "shell syntax: OK"

check-py:
	@$(PYTHON) -B -m py_compile tools/*.py tools/lab/*.py tools/tests/*.py
	@rm -rf tools/__pycache__ tools/lab/__pycache__ tools/tests/__pycache__
	@echo "python: OK"

check-tests:
	@$(PYTHON) -B -m unittest discover -s tools/tests -v
	@$(PYTHON) tools/test_check_citations.py

# The citation guard's own behavioural regressions, runnable on their own.
check-citation-tests:
	@$(PYTHON) tools/test_check_citations.py

check-scan:
	@$(PYTHON) -B tools/publish-scan.py --self-test
	@$(PYTHON) -B tools/publish-scan.py
	@echo "publish scan: clean"

check-patch:
	@sh generate-patch.sh --check
	@echo "generated patch: current"

# E2: the recorded upstream digest (tools/upstream/<release>.sha256) must
# match the vendored stock kern_pledge.c and the generated patch artifact.
# A silent upstream drift (stock refreshed, manifest forgotten, or the
# reverse) fails the gate before a release ships the mismatched pair.
check-upstream:
	@$(PYTHON) tools/upstream-digest-check.py
	@echo "upstream digest: current"

# Citation consistency check: verifies that all commit SHAs, test names,
# report paths, and evidence paths cited in documentation actually exist
# in the current tree. A hash that does not resolve passes only when
# docs/EXTERNAL-REFS.md declares its provenance *and* the citing file is one
# the table allows; those hits are reported as external (unverifiable here),
# never as verified, and docs/ may not cite out-of-tree workspace hashes.
check-citations:
	@$(PYTHON) tools/check-citations.py

# Matrix gate: validates structure, source inventory, evidence semantics,
# Summary Counts, every FILE:LINE anchor (in range, and on a line that shares
# the row's own subject words) and every Evidence Index commit (the artifact
# must actually be present in the commit the index names).
check-matrix:
	@$(PYTHON) tools/check_matrix_totals.py

# Reproducible archive verification: build twice, verify identical hashes,
# and verify the check fails when deliberately perturbed.
# When run from a git repo, pass version/commit from git; when run from
# an export, these will be empty and the test script uses env vars.
# AWG_SOURCE_REPO must be set for clean exports (no .git directory).
check-repro:
	@AWG_VERSION="$${AWG_VERSION:-$(KVER)}" \
	 AWG_COMMIT="$${AWG_COMMIT:-$(shell git -C $(CURDIR) rev-parse HEAD 2>/dev/null || echo unknown)}" \
	 AWG_SOURCE_REPO="$${AWG_SOURCE_REPO:-$(CURDIR)}" \
	 sh tools/test-reproducible-archive.sh

check-vm:
	@test "$(AWG_LAB_DISPOSABLE)" = 1 || { echo "set AWG_LAB_DISPOSABLE=1 for disposable lab VMs" >&2; exit 1; }
	@for t in smoke-test wire-test matrix-test stress-test; do \
	    echo "== $$t"; \
	    $(SCP) -q tools/lab/$$t.sh \
	        $(LAB_VM_USER)@$(LAB_VM_HOST):/home/$(LAB_VM_USER)/$$t.sh || exit 1; \
	    $(SSH) $(LAB_VM_USER)@$(LAB_VM_HOST) \
	        'doas sh /home/$(LAB_VM_USER)/'$$t'.sh' || exit 1; \
	done
	@echo "lab tests: all cases passed"

# Two-VM interop: a real tunnel between obsd-a and obsd-b over their LAN link.
# Needs two VMs running the AWG kernel with vio1 configured (tools/lab/README.md).
# Both sides run the commands through `doas sh -s` so the test can pipe a
# script over ssh instead of quoting it through make, ssh and doas.
A_SSH ?= ssh $(SSH_OPTS) -p $(LAB_VM_PORT) $(LAB_VM_USER)@$(LAB_VM_HOST) doas sh -s
B_SSH ?= ssh $(SSH_OPTS) -p $(LAB_VM_PORT_B) $(LAB_VM_USER)@$(LAB_VM_HOST) doas sh -s
check-interop:
	@test "$(AWG_LAB_DISPOSABLE)" = 1 || { echo "set AWG_LAB_DISPOSABLE=1 for disposable lab VMs" >&2; exit 1; }
	@A_SSH='$(A_SSH)' B_SSH='$(B_SSH)' \
	    LAN_A=$(LAN_A) LAN_B=$(LAN_B) sh tools/lab/interop-test.sh

baseline:
	@$(PYTHON) tools/upstream-diff.py --tree "$(TREE)"

# Standalone management tool (docs/ADMIN-INTERFACE.md Option B). Built with
# the host C toolchain; on OpenBSD this uses bsd.prog.mk, elsewhere a plain
# cc invocation of the same source keeps `make awgctl` usable for smoke
# builds.
awgctl:
	@if [ -f /usr/share/mk/bsd.prog.mk ]; then \
		$(MAKE) -C sbin/awgctl; \
	else \
		$(CC) $(CFLAGS) -DAWGCTL_LINUX -Isrc -Isbin/awgctl -o awgctl sbin/awgctl/awgctl.c -lutil; \
	fi


# Host fixture: the production awgctl.c linked against an ioctl/getifaddrs
# stub (tools/lab/fixture-stubs.c) plus a recording ifconfig stand-in, so the
# R5 regression cases (CAS token, address read-back, arity) run without an
# OpenBSD kernel. Needs a POSIX host with cc; not part of the release.
# R1 review: the binary is built into a per-run directory under $TMPDIR so
# two worktrees compiling the fixture at once do not share one object file.
AWGCTL_FIXTURE ?= $(shell echo "$${TMPDIR:-/tmp}")/awgctl-fixture
awgctl-fixture:
	$(CC) $(CFLAGS) -DAWGCTL_LINUX -Isrc -Isbin/awgctl \
		-DIFCONFIG_PATH='"$(AWGCTL_FIX_IFCONFIG)"' \
		-o "$(AWGCTL_FIXTURE)" sbin/awgctl/awgctl.c \
		tools/lab/fixture-stubs.c -lutil

# Release metadata for a built kernel: hashes, versions, test results.
#   make manifest KERNEL=/usr/src/sys/arch/amd64/compile/AWG.MP/obj/bsd \
#       RELEASE=8.0 COMMIT=$(git rev-parse --short HEAD) TESTS="smoke=pass"
KERNEL  ?= /usr/src/sys/arch/amd64/compile/AWG.MP/obj/bsd
RELEASE ?= $(shell uname -r)      # OpenBSD release the kernel is built for
KVER    ?= awg-$(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
COMMIT  ?= unknown
TESTS   ?=
manifest:
	@$(PYTHON) tools/release-manifest.py --repo . --kernel $(KERNEL) \
	    --release $(RELEASE) --version $(KVER) --commit $(COMMIT) \
	    $(foreach t,$(TESTS),--tests $(t))

# E2-HYGIENE: explicit install contract. Without this rule GNU make used its
# implicit "%: %.sh" rule and `make install` silently created an untracked
# executable copy named ./install in the source tree (copy of install.sh,
# rc=0, nothing installed, `git status --porcelain` dirty). The release
# packaging mechanism is install.sh: run it directly, with the mode selectable
# (AWG_INSTALL_MODE = --all | --tools-only | --kernel-only, default --all).
# It creates no files in the source tree, so check-gitclean stays clean.
AWG_INSTALL_MODE ?= --all
install:
	@sh install.sh $(AWG_INSTALL_MODE)

clean:
	@rm -rf tools/__pycache__ tools/lab/__pycache__ tools/tests/__pycache__
	@rm -rf sbin/awgctl/obj
