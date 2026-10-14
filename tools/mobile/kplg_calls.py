"""Argument bundles for the executor plug seams (pre_step / resolve_target / post_step)."""

from __future__ import annotations

from typing import Any, NamedTuple


class Pre(NamedTuple):
    """``pre_step(ctx, call)``: the entry, step and screen about to run."""

    entry: Any
    step: Any
    screen: Any


class Resolve(NamedTuple):
    """``resolve_target(ctx, call)``: the step, screen and default resolution."""

    step: Any
    screen: Any
    default: Any


class Post(NamedTuple):
    """``post_step(ctx, call)``: the step entry and the screens before/after."""

    entry: Any
    before: Any
    after: Any
