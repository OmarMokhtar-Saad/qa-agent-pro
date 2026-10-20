"""Screen perception: the uiautomator XML dump, pruned to what a model needs.

**The dump is attacker-influenced content.** Any app on the device controls the
``text``, ``content-desc`` and ``resource-id`` of its own nodes, and a malicious
or merely careless app can put anything at all in them -- including markup that
looks like ours. Three consequences, each pinned by a test:

1. **No DOCTYPE, no ENTITY.** ``xml.etree.ElementTree`` does not fetch external
   entities, but it DOES expand internal ones, which is the billion-laughs
   amplification. ``defusedxml`` would close that for us; it is not a dependency
   of this project and the programme contract forbids adding one. So the parse
   refuses outright when the document declares either -- a real uiautomator dump
   never does, so nothing legitimate is lost.
2. **Byte cap BEFORE parsing.** ``MAX_DUMP_BYTES`` is checked against the encoded
   length before a parser ever sees the string, because a cap applied afterwards
   is not a cap.
3. **Every attribute is untrusted text.** Attribute values are length-capped,
   control characters are stripped, and anything that could be mistaken for our
   own prompt scaffolding (an ``<untrusted_content>`` tag, the ``_GUARD``
   security-note opener) is neutralised HERE -- ``untrusted.wrap_untrusted``
   strips the tags but knows nothing about the guard note, and a screen that
   could forge one would be talking directly to the tester's model.

**An element id is CONTENT, not a row number.** ``prune`` derives every id from
that element's own class, text, description, resource id and bounds
(:func:`element_id`), so an id the model planned against one dump can never
silently resolve to a DIFFERENT element on the next one. See that function for
the tap-the-wrong-widget defect this closes.

Nothing here raises; every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

from tools.mobile import screen_audit  # noqa: F401
from tools.mobile.perception_block import (  # noqa: F401
    _drops_of,
    _fit_block,
    _header_lines,
    _rendered_pair,
    _verdict,
    dump_unusable,
    logger,
    to_prompt_block,
)
from tools.mobile.perception_bounds import (  # noqa: F401
    _BOUNDS_RE,
    _area,
    _dominant_package,
    _extent,
    _package_area,
    parse_bounds,
)
from tools.mobile.perception_cap import (  # noqa: F401
    _budgeted,
    _cap_elements,
    _classify_by_panel,
    _intersects,
)
from tools.mobile.perception_consts import (  # noqa: F401
    _DECL_RE,
    _LEGEND_STAGES,
    DOCTYPE_REFUSAL,
    EDITABLE_CLASS_HINTS,
    NOT_XML_REFUSAL,
    OVERSIZE_REFUSAL,
    REFERENCE_LEGEND,
    REFERENCE_LEGEND_SHORT,
    ROLE_INPUT,
    ROLE_LEXICON,
    ROLE_PASSWORD,
    SEGMENT_BREAK,
    SYSTEM_DIALOG_PACKAGES,
)
from tools.mobile.perception_content import (  # noqa: F401
    _audit,
    _cap_and_label,
    _content_hash,
    _screen_content,
    _system_dialog_package,
)
from tools.mobile.perception_display import (  # noqa: F401
    _display_rect,
    _display_size,
    _int_pair,
    _panel_dims,
    _quarter_turned,
    _rects_and_axes,
)
from tools.mobile.perception_display_doc import _DISPLAY_RECT_DOC  # noqa: F401
from tools.mobile.perception_fields import _geometry_fields, _panel_fields  # noqa: F401
from tools.mobile.perception_fit import (  # noqa: F401
    _best_fit,
    _fit_stage,
    _trim_to_fit,
)
from tools.mobile.perception_guard import (  # noqa: F401
    _CONTROL_RE,
    _WS_RE,
    GUARD_MARKERS,
    GUARD_SENTINEL,
    _clean,
    _guard_sentinel,
)
from tools.mobile.perception_ids import (  # noqa: F401
    ID_HASH_CHARS,
    ID_PREFIX,
    _assign_ids,
    _screen_id,
    element_id,
    element_seed,
)
from tools.mobile.perception_label import (  # noqa: F401
    _bounds_tuple,
    _child_label,
    _contained_area,
    _largest_contained_label,
    _own_label,
    label_of,
)
from tools.mobile.perception_limits import (  # noqa: F401
    MAX_ATTR_CHARS,
    MAX_BLOCK_CHARS,
    MAX_DISPLAY_EXTENT,
    MAX_DUMP_BYTES,
    MAX_ELEMENTS,
    MAX_PACKET_HEADROOM,
    MAX_SUMMARY_LABEL_CHARS,
    MIN_DEDUPE_CHARS,
    MIN_LABEL_AREA_SHARE,
    MIN_USABLE_ELEMENTS,
)
from tools.mobile.perception_line import (  # noqa: F401
    Slot,
    _eid,
    _field_part,
    _head_parts,
    _label_parts,
    _rows,
    _tail_parts,
    element_line,
)
from tools.mobile.perception_nodes import (  # noqa: F401
    _CONTENT_KEYS,
    _collect_elements,
    _flag,
    _is_editable,
    _node_element,
    _node_flags,
    _off_screen,
    _parse_dump,
    _short_class,
    _visible_nodes,
)
from tools.mobile.perception_notice import (  # noqa: F401
    _block_notice,
    _last_resort_text,
)
from tools.mobile.perception_prune import (  # noqa: F401
    ScreenView,
    _pruned_content,
    prune,
)
from tools.mobile.perception_render import (  # noqa: F401
    _anchor_label,
    _field_refs,
    plain_lines,
    render_lines,
    slots_of,
)
from tools.mobile.perception_role import (  # noqa: F401
    _UNREADABLE_RE,
    _role_from_tokens,
    annotate,
    has_unreadable_text,
    role_of,
    with_camel_split,
)
from tools.mobile.perception_summary import (  # noqa: F401
    _summary_labels,
    _summary_lines,
)
from tools.mobile.perception_text import (  # noqa: F401
    _CAMEL_LOWER_UPPER,
    _CAMEL_UPPER_WORD,
    _WORD_RE,
    _count,
    split_camel,
    words,
)
from tools.safe_parse import XML_DECL_RE, has_xml_decl, parse_xml  # noqa: F401
from tools.untrusted import (  # noqa: F401
    _GUARD,
    MAX_GUARD_SENTINEL_CHARS,
    NEUTRALIZED,
    derive_guard_sentinel,
    guard_markers,
    wrap_untrusted,
)
