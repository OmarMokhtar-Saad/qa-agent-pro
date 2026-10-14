"""The mobile lane's handler package: shipped only with ``tools/mobile``.

``handlers._lane`` is the single importer of this package. Three entry points
are all the rest of the server needs: ``register`` (the lane's tools),
``register_defaults`` (its startup registrations) and ``doctor_section``
(the qa-doctor mobile section).
"""

from __future__ import annotations

from handlers.mobile import setup_doctor
from handlers.mobile.tools import register_mobile_tools
from tools.mobile import (
    dialog_defaults,
    dump_tree_source,
    install_sources,
    u2_tree_source,
)

__all__ = ["doctor_section", "register", "register_defaults"]


def register(mcp, kit, rt) -> None:
    """Register the lane's tools (the caller checked the lane gate)."""
    register_mobile_tools(mcp, kit, rt)


def register_defaults() -> None:
    """Register the default dialog handlers, install sources and tree sources.

    Every register call is idempotent."""

    dialog_defaults.register_default_handlers()
    install_sources.register_defaults()
    dump_tree_source.register_dump_source()
    u2_tree_source.register_u2_source()


async def doctor_section() -> list:
    """The qa-doctor mobile section rows (looked up at call time)."""
    return await setup_doctor._mobile_doctor_section()
