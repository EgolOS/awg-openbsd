#!/usr/bin/env python3
"""Fixture stand-in for ifconfig(8) (R5 host tests).

Records the argv it is given and mutates the same state file the ioctl stub
(tools/lab/fixture-stubs.c) uses, so `awgctl up` and its read-back agree on
what is installed. Address handling follows the kernel: each family keeps a
primary address plus its aliases, an address that is already on the interface
answers EEXIST ("File exists") regardless of the prefix length (exactly the
AWG-041 note in docs/SECURITY.md), `-inet`/`-inet6` drop every address of
that family, and `up`/`down` flip the IFF_UP flag (`-up` is not an
OpenBSD command and is rejected here, like the real ifconfig). The list lives in the
`addrs` key ("fam:addr/prefix" tokens, the first of a family is its primary);
`addr`/`prefix`/`family` mirror the first entry for the older cases. The
derived host routes are written to $AWGFIX_ROUTES so a test can diff the
netstat(8) view of the same state (R9/R4-08).
"""
import ipaddress
import os
import sys

STATE = os.environ.get("AWGFIX_STATE") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "awgctl-fixture.state")
LOG = os.environ.get("AWGFIX_IFC_LOG")
ROUTES = os.environ.get("AWGFIX_ROUTES") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "awgctl-fixture.routes")


def read():
    st = {}
    try:
        with open(STATE) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    st[k] = v
    except FileNotFoundError:
        st = {"mtu": "1420", "addrs": "10:fd00::1/128", "up": "1"}
    return st


def entries(st):
    """[(family, address, prefix)] — `addrs` wins, addr/prefix is the fallback.

    The fallback only applies when the state carries no `addrs` key at all:
    a present-but-empty list means the family was cleared (`-inet`), and
    re-materialising the mirrored primary from addr/prefix put the address
    the clear just removed back on the interface.

    R13: one schema and one duplicate rule for all three models. A token
    needs a family prefix — an untagged token (e.g. `10.9.9.9/32`) is not a
    family:address pair, so the kernel's decode drops it and this model drops
    it too. The kept entries are deduplicated on (family, address) with the
    prefix ignored (the kernel's EEXIST ignores the prefix too), so the first
    entry of a pair wins and the C observer, this writer and the oracle
    render the same list.
    """
    out = []
    seen = set()

    def add(fam, addr, prefix):
        if not fam or not addr:
            return
        # Match the C token decoder in the entry itself: mutation and
        # persistence must see the same family as getifaddrs, not the raw tag.
        fam = "2" if fam.startswith("2") else "10"
        key = (fam, addr.split("%", 1)[0])
        if key in seen:
            return
        seen.add(key)
        out.append((fam, addr, prefix))

    if "addrs" in st:
        for ent in st["addrs"].split():
            fam, _, rest = ent.partition(":")
            addr, _, prefix = rest.partition("/")
            add(fam, addr, prefix)
        return out
    if st.get("addr"):
        add(st.get("family", ""), st["addr"], st.get("prefix", ""))
    # Legacy states list the extra addresses under `aliases`; without this the
    # first write would silently drop them (R9/R4-08 test fixture).
    for ent in st.get("aliases", "").split():
        fam, _, rest = ent.partition(":")
        addr, _, prefix = rest.partition("/")
        add(fam, addr, prefix)
    return out


def write(st):
    """Persist the list; addr/prefix/family mirror the first entry.

    AWGFIX_MUTATE_ISPEC2=<value> rewrites I2 on every write. It exists so the
    gate can be shown to be sensitive: with this set, a restore that leaves a
    different I2 must fail F13a. Default is off, so the suite's normal run is
    untouched.

    R10: the mirrored `addr` is the address bytes in presentation form, so
    the %scope suffix (fe80::1%ixn) is dropped there; the canonical `addrs`
    token keeps the scope as the text form the interface reports.
    """
    mut = os.environ.get("AWGFIX_MUTATE_ISPEC2")
    if mut:
        st = dict(st)
        st["ispec2"] = mut
    ents = entries(st)
    out = dict(st)
    out["addrs"] = " ".join(f"{f}:{a}/{p}" for f, a, p in ents)
    out.pop("aliases", None)
    if ents:
        out["family"] = ents[0][0]
        out["addr"] = ents[0][1].split("%", 1)[0]
        out["prefix"] = ents[0][2]
    else:
        out.pop("addr", None)
        out.pop("prefix", None)
    tmp = STATE + ".ifc.tmp"
    with open(tmp, "w") as f:
        for k, v in out.items():
            f.write(f"{k}={v}\n")
    os.replace(tmp, STATE)
    write_routes(out)


def write_routes(st):
    """Render the routing-table view of the address list (netstat -rn)."""
    tmp = ROUTES + ".tmp"
    with open(tmp, "w") as f:
        for fam, addr, prefix in entries(st):
            # The kernel installs one route per address with its prefix; the
            # presentation is stable, so a before/after diff is byte-exact.
            name = "inet" if fam in ("2", "inet") else "inet6"
            f.write(f"{name} {addr}/{prefix} - awg9\n")
    os.replace(tmp, ROUTES)


def main():
    argv = sys.argv[1:]
    if LOG:
        with open(LOG, "a") as f:
            f.write(" ".join(argv) + "\n")
    if len(argv) < 2:
        print("fixture-ifconfig: need <ifname> <subcommand>", file=sys.stderr)
        return 1
    sub = argv[1]
    st = read()
    if sub in ("create", "up"):
        # R8: knob for an operational `up` failure after the SET succeeded.
        rc = 0
        if sub == "up" and os.environ.get("AWGFIX_UP_ERR"):
            print("ifconfig: SIOCSIFADDR: Invalid argument", file=sys.stderr)
            rc = 1
        st["up"] = "1"
        # R13: knob for a wrapper whose `up` recovery loses an address
        # (AWGFIX_UP_DROP=<address>, prefix-less). The address is dropped
        # from the list on the same write that sets IFF_UP, so a restore that
        # re-installs the list and then fails the `up` leaves the loss behind.
        # Confine the loss injection to failed-UP recovery (F13c); successful
        # restore-UP calls in the MTU case must not acquire this separate fault.
        drop = (os.environ.get("AWGFIX_UP_DROP")
                if sub == "up" and os.environ.get("AWGFIX_UP_ERR") else None)
        if drop:
            ents = entries(st)
            kept = [f"{f}:{a}/{p}" for f, a, p in ents
                    if a.split("%", 1)[0] != drop]
            st["addrs"] = " ".join(kept)
        write(st)
        return rc
    if sub == "down":
        st["up"] = "0"
        write(st)
        return 0
    if sub == "-up":
        # OpenBSD ifconfig has no `-up`: it answers `bad value`. Rejecting it
        # here (R10) is what makes this fixture faithful — the R9 restore
        # emitted `-up` and only passed because this file accepted it.
        print("ifconfig: -up: bad value", file=sys.stderr)
        return 1
    if sub == "destroy":
        # A real destroy drops the MTU and the addresses with the
        # interface, so the fixture resets them (R7/R4-08 restore proof).
        if os.environ.get("AWGFIX_DESTROY_ERR"):
            print("ifconfig: SIOCGIFMEDIA: No such device", file=sys.stderr)
            return 1
        write({"mtu": "1420", "addrs": "10:fd00::1/128", "up": "1",
               "peers": "0", "flags": st.get("flags", "4"),
               "port": "0", "version": "0", "tx": "0", "rx": "0",
               "handshake": "0"})
        return 0
    if sub == "mtu":
        # R8: knob for an operational MTU failure, so the restore path runs.
        if os.environ.get("AWGFIX_MTU_ERR"):
            print("ifconfig: SIOCSIFMTU: Invalid argument", file=sys.stderr)
            return 1
        st["mtu"] = argv[2]
        write(st)
        return 0
    if sub in ("inet", "inet6", "-inet", "-inet6"):
        family = "2" if sub in ("inet", "-inet") else "10"
        ents = entries(st)
        if sub.startswith("-"):
            # -inet|-inet6: drop the primary and every alias of the family.
            st["addrs"] = " ".join(
                f"{f}:{a}/{p}" for f, a, p in ents if f != family)
            write(st)
            return 0
        raw = argv[2]
        addr, _, prefix = raw.partition("/")
        scope = addr.split("%", 1)
        try:
            ip = ipaddress.ip_address(scope[0])
        except ValueError:
            print(f"fixture-ifconfig: bad address {raw}", file=sys.stderr)
            return 1
        if prefix == "":
            # R10: a prefix-less address takes the kernel's default netmask:
            # the classful mask for IPv4 (10.9.19.1 -> /8), 128 for IPv6.
            # The restore path re-installs a holed-mask address without a
            # prefix, and the fixed /32 dropped the modelled /8 (R11/N13).
            if ip.version == 4:
                first = int(str(ip).split(".")[0])
                prefix = "8" if first < 128 else ("16" if first < 192 else "24")
            else:
                prefix = str(ip.max_prefixlen)
        alias = len(argv) > 3 and argv[3] == "alias"
        # The kernel's EEXIST is generic: the prefix does not matter, and an
        # address on any slot (primary or alias) answers it. The scope id is
        # not part of the address bytes, so it is ignored for the comparison.
        if any(a.split("%", 1)[0] == str(ip) for _, a, _ in ents):
            print("File exists", file=sys.stderr)
            return 1
        tok = f"{family}:{addr}/{prefix}"
        if alias and any(f == family for f, _, _ in ents):
            st["addrs"] = " ".join(
                [f"{f}:{a}/{p}" for f, a, p in ents] + [tok])
        else:
            # A plain inet/inet6 replaces the primary of that family (and
            # with it the aliases); the other family keeps its own primary.
            kept = [f"{f}:{a}/{p}" for f, a, p in ents if f != family]
            st["addrs"] = " ".join([tok] + kept)
        write(st)
        return 0
    return 0


sys.exit(main())
