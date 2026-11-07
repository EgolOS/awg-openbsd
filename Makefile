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
SSH_OPTS = -o BatchMode=yes -o StrictHostKeyChecking=yes \
           -o UserKnownHostsFile="$(LAB_KNOWN_HOSTS)" \
           -o ConnectTimeout=10 -i "$(LAB_SSH_KEY)"
SSH  = ssh $(SSH_OPTS) -p $(LAB_VM_PORT)
SCP  = scp $(SSH_OPTS) -P $(LAB_VM_PORT)

.PHONY: check check-syntax check-py check-tests check-scan check-patch check-vm check-interop baseline manifest clean

check: check-syntax check-py check-tests check-scan check-patch
	@echo "repository checks: OK"

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
	@$(PYTHON) -m py_compile tools/*.py tools/lab/*.py tools/tests/*.py
	@rm -rf tools/__pycache__ tools/lab/__pycache__
	@echo "python: OK"

check-tests:
	@$(PYTHON) -m unittest discover -s tools/tests -v

check-scan:
	@$(PYTHON) tools/publish-scan.py --self-test
	@$(PYTHON) tools/publish-scan.py
	@echo "publish scan: clean"

check-patch:
	@sh generate-patch.sh --check
	@echo "generated patch: current"

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

clean:
	@rm -rf tools/__pycache__ tools/lab/__pycache__
