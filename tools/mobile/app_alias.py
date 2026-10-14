"""Record "this app name means that package" for the resolved-app cache.

The install-source resolvers (App Tester, Play Store) learn a package from a
name the tester typed. Calling :func:`remember_alias` once they know it lets the
next run skip the lookup: the run's own cache is read with the same key.
"""

from __future__ import annotations

from tools.mobile import resolve_cache, run_store


def remember_alias(serial: str, hint: str, package: str) -> None:
    """Remember that *hint* names *package* on device *serial*. Never raises."""
    key = resolve_cache.cache_key(serial, hint)
    if not package or key.endswith("|"):
        return
    run_store.write_resolved_app(key, run_store.ResolvedApp(package, serial, {}))
