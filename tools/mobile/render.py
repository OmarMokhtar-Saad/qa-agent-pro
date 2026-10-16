"""Every tester-facing string the mobile lane prints, as pure functions.

Why a separate module from ``session``: the state machine decides, this renders,
and neither can be tested by accident through the other. Nothing here reads
settings, touches the filesystem or awaits a device, so every string in the lane
is reachable from a unit test without an emulator -- which is what makes the
"never raw XML in a packet" and "every menu option has a mapping line"
properties checkable at all.

Two rules this module exists to keep:

1. **An option is identified by its KEY, never by its position.** The host
   assistant re-presents these options in its OWN ask-user UI, and that UI is
   free to relabel them (Cursor renders a/b/c/d) and to reorder them -- a tester
   saw "4 2 1 3". A position that came back from a reordered list used to select
   a lane by index, so "explore" shown first started ``current_suite``. So every
   option line PRINTS its key, the instruction names the key to send, and there
   is no number-to-key mapping left to be applied to the wrong list. The count
   word is still DERIVED from :data:`MOBILE_SOURCES`: a hand-written count is
   how a seventh source becomes unreachable while the text still claims six, and
   that half of the old rule was never the problem.
2. **A packet is rendered, never rebuilt.** ``packet_block`` embeds the dict
   ``tools/mobile/run_packets`` produced and adds nothing to it. The pruned screen is
   already the only screen representation in there; the raw uiautomator XML has
   no route into this file.
"""

from __future__ import annotations

from tools.mobile.render_capture import (  # noqa: F401
    CAPTURE_SOURCES,
    capture_line,
    capture_menu_markdown,
)
from tools.mobile.render_crash import crash_note  # noqa: F401
from tools.mobile.render_device import (  # noqa: F401
    booting_devices_block,
    device_pending_block,
    starting_heading,
)
from tools.mobile.render_holder import device_busy_block  # noqa: F401
from tools.mobile.render_install import (  # noqa: F401
    APP_TESTER_HINT,
    APP_TESTER_PACKAGES,
    app_tester_hint,
    apply_refusal,
    install_menu_markdown,
)
from tools.mobile.render_menu import (  # noqa: F401
    INSTALL_SOURCES,
    MOBILE_SOURCES,
    _count_word,
    _keyed,
)
from tools.mobile.render_packet import (  # noqa: F401
    GOAL_FIELDS,
    GOAL_OMITTED_NOTE,
    NO_ECHO,
    STATIC_FIELDS,
    STATIC_OMITTED_NOTE,
    busy_block,
    packet_block,
    static_hash,
    without_goal,
    without_static,
)
from tools.mobile.render_preflight import preflight_block  # noqa: F401
from tools.mobile.render_relay import relay_block, takeover_block  # noqa: F401
from tools.mobile.render_report import report_line  # noqa: F401
from tools.mobile.render_run_summary import (  # noqa: F401
    SummaryOpts,
    summary_block,
)
from tools.mobile.render_setup import (  # noqa: F401
    ANDROID_STUDIO_URL,
    AVD_GUIDE_URL,
    SETUP_NO_AVD,
    SETUP_NO_SDK,
    setup_guide,
    setup_guide_block,
)
from tools.mobile.render_source import (  # noqa: F401
    SOURCE_IMPLICATIONS,
    implied_sources,
    install_source_for_label,
    source_for_label,
    source_labels,
)
from tools.mobile.render_source_menu import source_menu_markdown  # noqa: F401
from tools.mobile.render_status import status_block  # noqa: F401
from tools.mobile.render_summary import (  # noqa: F401
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_FIELD_CHARS,
    MAX_SUMMARY_LINES,
    compact_summary,
    screen_block,
)
from tools.mobile.render_tally import (  # noqa: F401
    INCONCLUSIVE_VERDICTS,
    _retry_note,
    _typed_field_tally,
    typed_field_tally,
)
from tools.mobile.render_verdict import gate_block, verdict_line  # noqa: F401
