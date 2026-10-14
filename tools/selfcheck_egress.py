"""Egress guard of the self-check: block and record every non-loopback connection."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Any, Callable, Iterator, cast

from tools.selfcheck_patterns import _LOOPBACK
from tools.selfcheck_types import EgressBlocked


def _host_of(address: Any) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _blocked(host: str) -> bool:
    return bool(host) and host not in _LOOPBACK and not host.startswith("/")


def _make_connect(recorder: list, where: Callable, real_connect: Callable) -> Callable:
    def connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if _blocked(host):
            recorder.append((where(), host))
            raise EgressBlocked(f"self-check blocked an outbound connection to {host}")
        return real_connect(self, address, *args, **kwargs)

    return connect


def _make_create(recorder: list, where: Callable, real_create: Callable) -> Callable:
    def create_connection(address, *args, **kwargs):
        host = _host_of(address)
        if _blocked(host):
            recorder.append((where(), host))
            raise EgressBlocked(f"self-check blocked an outbound connection to {host}")
        return real_create(address, *args, **kwargs)

    return create_connection


# Each attempt is recorded as ``(surface, host)``. The ATTRIBUTION is the
# point: "something tried to reach the network" is not a testable claim,
# while "the push tool tried to reach the network" is, and that is the
# assertion that keeps this pass from becoming a third route to the one
# credentialed outbound write in the tree.
#
# Restores both entry points in a ``finally``, so a raising handler cannot
# leave the process without networking.
@contextmanager
def egress_guard(recorder: list, label: Callable | None = None) -> Iterator:
    """Block and record every non-loopback connection for the duration."""
    where = label or (lambda: "")
    real_connect = socket.socket.connect
    real_create = socket.create_connection
    cast("Any", socket.socket).connect = _make_connect(recorder, where, real_connect)
    socket.create_connection = _make_create(recorder, where, real_create)
    try:
        yield
    finally:
        cast("Any", socket.socket).connect = real_connect
        socket.create_connection = real_create
