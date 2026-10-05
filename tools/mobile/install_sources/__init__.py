"""Install source implementations.

``register_defaults`` registers the sources that ship: the on-device Firebase App
Tester (``app_tester``). There is no API or CLI source; another one plugs in by
calling ``install_source.register_source`` with its own name.
"""

from tools.mobile.install_source import register_source
from tools.mobile.install_sources.app_tester_ui import AppTesterUiSource


def register_defaults() -> None:
    """Register the shipped sources. Safe to call more than once."""
    register_source(AppTesterUiSource())
