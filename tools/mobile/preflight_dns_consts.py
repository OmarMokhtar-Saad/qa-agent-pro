"""Constants of the device DNS check: probe host and address, budgets, remedy sentences."""

from __future__ import annotations

#: The name the DNS probe asks for. The platform's own connectivity check uses
#: this host, so the probe adds no lookup the device was not already making.
DNS_PROBE_HOST = "connectivitycheck.gstatic.com"

#: The address the probe pings once a NAME has failed, to tell a broken resolver
#: apart from a device with no route at all. A literal address is the whole
#: point: resolving anything here would beg the question being asked.
DNS_PROBE_IP = "8.8.8.8"

#: Seconds the ADB CALL is given for ONE probe. This is the whole budget for a
#: probe, and deliberately NOT a `ping -W`: `-W` bounds the wait for a REPLY,
#: and a lookup that never resolves never gets as far as waiting for one.
#: MEASURED on this lane's API 35 emulator against a stale resolver: a failing
#: lookup costs 20.06-20.12s across 16 samples, and does not move at `-W 1` or
#: `-W 10` -- what bounds it is Android's own resolver retry schedule, which
#: `-W` cannot reach. The budget must therefore clear ~20s with room for a
#: slower host. Under it the check reports its own timeout instead of the
#: diagnosis it exists to give, which is exactly what a tester was left with.
DNS_PROBE_TIMEOUT_S = 30

#: Seconds `ping -W` waits for a REPLY. It bounds the ADDRESS probe, whose host
#: needs no resolving, and is inert on the name probe as measured above. Kept
#: small for that reason: an address that does not answer promptly is not
#: routing, and waiting longer tells the tester nothing more.
DNS_PING_WAIT_S = 3

#: What the tester is told when names fail and packets still flow. The remedy is
#: the emulator's own behaviour, not a guess: it reads the host's resolvers ONCE,
#: at boot, so a host that changed network leaves the guest pointing at servers
#: that are no longer there.
DNS_EMULATOR_FIX = (
    "The emulator reads the host's DNS servers once, when it starts, so "
    "changing network on this machine leaves it pointing at resolvers that are "
    "gone. Restart the emulator, or start it with `-dns-server 8.8.8.8,1.1.1.1`."
)
DNS_DEVICE_FIX = (
    "The device answers pings but resolves no name. Check its own Wi-Fi or "
    "mobile data, and any VPN or private-DNS setting on it."
)
DNS_NO_ROUTE_FIX = (
    "The device reached nothing at all, by name or by address. Check that it is "
    "on a network before running cases that need one."
)

#: What the tester is told when the lookup OUTLASTS the budget rather than
#: failing outright. Reporting "adb did not answer within 30s" would be true and
#: useless: the thing that did not answer is the RESOLVER, and no longer wait
#: separates a resolver this slow from a broken one -- an app under test stalls
#: on it the same way either way. So the check says that in its own words, and
#: still sends the address probe, so the REMEDY is measured rather than guessed.
DNS_SLOW_NOTE = (
    "a resolver this slow cannot be told apart from a broken one by waiting "
    "longer, and an app under test stalls on it the same way"
)
