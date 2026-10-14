"""The state of one flow while the records are read."""

from __future__ import annotations

from tools.mobile_evidence.pcap_dns_http import dns_names, http_request
from tools.mobile_evidence.pcap_tls import sni_of


def _client_evidence(layer: dict):
    """What in THIS packet proves the sender is the client. None when nothing does."""
    payload = layer["payload"]
    name = sni_of(payload)
    if name:
        return {"kind": "sni", "sni": name}
    if layer["transport"] == "udp":
        names = dns_names(payload)
        if names:
            return {"kind": "dns", "names": names}
    request = http_request(payload)
    if request:
        return {"kind": "http", "host": request["host"]}
    if layer["syn"]:
        return {"kind": "syn"}
    return None


def _key(layer: dict):
    ends = tuple(
        sorted([(layer["src"], layer["sport"]), (layer["dst"], layer["dport"])])
    )
    return (layer["transport"],) + ends


def _new_flow(key):
    return {
        "transport": key[0],
        "ends": key[1][0]
        + ":"
        + str(key[1][1])
        + " and "
        + key[2][0]
        + ":"
        + str(key[2][1]),
        "packets": 0,
        "bytes": 0,
        "first_ms": None,
        "last_ms": None,
        "client": None,
        "server": None,
        "sni": "",
        "http_host": "",
        "qnames": {},
    }


def _absorb(conv: dict, layer: dict) -> None:
    evidence = _client_evidence(layer)
    if evidence is None:
        return
    if conv["client"] is None:
        conv["client"] = (layer["src"], layer["sport"])
        conv["server"] = (layer["dst"], layer["dport"])
    kind = evidence["kind"]
    if kind == "sni":
        if not conv["sni"]:
            conv["sni"] = evidence["sni"]
    elif kind == "dns":
        for name in evidence["names"]:
            conv["qnames"][name] = conv["qnames"].get(name, 0) + 1
    elif kind == "http":
        if evidence["host"] and not conv["http_host"]:
            conv["http_host"] = evidence["host"]


def _count_packet(conv: dict, incl: int, stamp: int) -> None:
    """Add one packet of *incl* bytes seen at *stamp* ms to its flow."""
    conv["packets"] += 1
    conv["bytes"] += incl
    conv["first_ms"] = (
        stamp if conv["first_ms"] is None else min(conv["first_ms"], stamp)
    )
    conv["last_ms"] = stamp if conv["last_ms"] is None else max(conv["last_ms"], stamp)
