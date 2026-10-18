"""Keep a pre-run stage's slow dumps out of the run's dump latency state."""

from __future__ import annotations

from tools.mobile import dump_latency_state as state


def forget_slow(serial: str) -> None:
    """Drop *serial*'s uiautomator samples (App Tester reads before the run's
    a11y service is on); the run's own p90 must start from its own dumps."""
    key = str(serial or "")
    state._UIA.pop(key, None)
    state._BY_SOURCE.get(key, {}).pop(state._SLOW_SOURCE, None)
    if state._LAST_SOURCE.get(key) == state._SLOW_SOURCE:
        state._LAST_SOURCE.pop(key, None)
    state._SLOW.discard(key)
