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
TREE        ?= /usr/src/sys

SSH  = ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
       -o ConnectTimeout=10 -i $(LAB_SSH_KEY) -p $(LAB_VM_PORT)
SCP  = scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
       -i $(LAB_SSH_KEY) -P $(LAB_VM_PORT)

.PHONY: check check-syntax check-py check-scan check-patch check-vm baseline clean

check: check-syntax check-py check-scan check-patch
	@echo "repository checks: OK"

check-syntax:
	@sh -n install.sh
	@sh -n sbin/awg-quick
	@sh -n tools/lab/smoke-test.sh
	@sh -n tools/lab/build-in-vm.sh
	@echo "shell syntax: OK"

check-py:
	@$(PYTHON) -m py_compile tools/publish-scan.py tools/sync-check.py \
	    tools/upstream-diff.py tools/lab/install_vm.py
	@rm -rf tools/__pycache__ tools/lab/__pycache__
	@echo "python: OK"

check-scan:
	@$(PYTHON) tools/publish-scan.py --self-test
	@$(PYTHON) tools/publish-scan.py
	@echo "publish scan: clean"

check-patch:
	@sh generate-patch.sh --check
	@echo "generated patch: current"

check-vm:
	@for t in smoke-test wire-test matrix-test stress-test; do \
	    echo "== $$t"; \
	    $(SCP) -q tools/lab/$$t.sh \
	        $(LAB_VM_USER)@$(LAB_VM_HOST):/home/$(LAB_VM_USER)/$$t.sh || exit 1; \
	    $(SSH) $(LAB_VM_USER)@$(LAB_VM_HOST) \
	        'doas sh /home/$(LAB_VM_USER)/'$$t'.sh' || exit 1; \
	done
	@echo "lab tests: all cases passed"

baseline:
	@$(PYTHON) tools/upstream-diff.py --tree $(TREE)

clean:
	@rm -rf tools/__pycache__ tools/lab/__pycache__
