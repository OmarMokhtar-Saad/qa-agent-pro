"""Every size cap of the screen-perception lane, in one place.

One home per scalar: readers import the name from here (or read it qualified
as ``perception_limits.NAME``) so a patch of one cap bites everywhere it is read.
"""

from __future__ import annotations

# Matched against the TRANSPORT cap in ``adb.uiautomator_dump``. Both exist on
# purpose: the transport one stops a huge dump from being carried at all, this
# one stops a huge string reaching a parser by any other route (a replayed
# checkpoint, a test fixture, a future file-backed dump).
MAX_DUMP_BYTES = 4 * 1024 * 1024

# The packet cap. 150 elements is roughly three screens' worth of controls; the
# reason for a cap at all is that the packet goes to the tester's own model and
# a 900-node ScrollView would crowd out the case it is meant to execute.
MAX_ELEMENTS = 150

# HEADROOM on the packet, for elements the panel puts off-display. They are
# carried at all only because the panel is an estimate that can be wrong -- a
# stale device size classifies the tester's own controls as off-display -- so
# off-panel crowding must never delete an on-panel control.
#
# Four passes at spending ONE budget between the two classes each fixed the
# previous pass's worst case and created a new one further out, because at a
# fixed cap "every on-panel element survives" and "every element the dump listed
# first survives" are jointly unsatisfiable -- a counting fact, not a
# rule-design problem. So the TOTAL is what grew, and `prune` walks in DOCUMENT
# order: nothing downstream sees a reordering, and `element_seed`/`hash`, the
# rendered lines the model plans from, and the guard's equal-area tie-break all
# read the dump's own order, as they did before any of this.
#
# It bounds the TOTAL and is NOT a cap on the off-panel class. Capping that
# class was measured deleting 25 of the tester's own controls out of a
# 110-element tablet dump under a stale panel -- a dump today's code carries
# whole -- because the class we have decided we cannot trust the membership of
# is the one place a bound cannot be justified by the classification: if the
# panel is wrong, that class IS the screen. With the total bounded instead, no
# dump loses more elements than it loses today, whatever the panel says.
MAX_PACKET_HEADROOM = MAX_ELEMENTS // 2

# Per-attribute cap. Long enough for a paragraph of on-screen copy, short enough
# that one hostile node cannot dominate the packet.
MAX_ATTR_CHARS = 200

# A repeated string shorter than this is printed in full at every element rather
# than replaced by an `@id` reference. A reference costs four or five characters
# plus a lookup the reader has to make, so below this length the dedupe spends
# legibility to save nothing. The harmful direction is DOWNWARD -- see the
# CEILINGS row in tests/mobile/test_mobile_bounds_upper.py, which names the floor
# test that binds it.
MIN_DEDUPE_CHARS = 24

# How much of a control's label the `fields` / `controls` SUMMARIES print. The
# element line beneath still prints it in full, so this can never hide a string
# -- only fail to index it. Never applied in a way that lets two distinct
# controls read the same: see `_summary_labels`.
MAX_SUMMARY_LABEL_CHARS = 48

# Prompt-block cap handed to ``wrap_untrusted``.
MAX_BLOCK_CHARS = 12000

# The largest display dimension accepted from the device. `adb.display_size`
# parses it out of `wm size`, which is device output rather than something this
# process computed, so it is bounded like every other untrusted number here. It
# matches the seven digits `_BOUNDS_RE` allows a dump to carry, so a display and
# the windows on it cannot disagree about what a representable coordinate is.
MAX_DISPLAY_EXTENT = 9999999


#: How much of a wrapper its borrowed label must cover before the wrapper is
#: judged to BE that control. Measured, not guessed: on the 2026-09-04 chat
#: screen the Send wrapper is 144x144 and its label child 96x96 -- 44%. The
#: root FrameLayout of the same screen contains that child too, at a fraction
#: of a percent, and borrowing there gave the whole screen the name of whatever
#: small label happened to be smallest. A share plus the clickable test is what
#: separates "a control with its label inside it" from "a container with things
#: in it".
MIN_LABEL_AREA_SHARE = 0.25

# Below this many elements the list is not a screen a model can act on from
# text alone: a splash, a canvas with one overlay, a dump taken mid-transition.
MIN_USABLE_ELEMENTS = 3
