"""The structural parse of a rendered report that the self-check pins read."""

from __future__ import annotations

from html.parser import HTMLParser

from tools.mobile import report
from tools.mobile.rchk_consts import MEDIA_TAGS


# Attribute notes, moved here from ``__init__`` (which stays <=20 lines):
# * ``crashes`` is ``tc_id -> data-crash``, collected beside the cards so the
#   crash pin can be an EQUALITY against the store rather than a containment.
# * ``media`` is ``(tag, src)`` for every media tag, so the asset pin can judge
#   the SOURCE it would actually fetch, not the text around it.
# * ``images`` is every URL an ``img`` can fetch -- its ``src``, each ``srcset``
#   candidate and the lightbox's ``data-full`` -- so the asset pin can check
#   IDENTITY on all of them, not on the one the browser starts with.
class _Page(HTMLParser):
    """Structure only: cards, the summary attrs, the terminator, style text.

    Parsed rather than grepped on purpose -- a malformed tag becomes a
    structural difference instead of a substring that happens to match.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list = []
        self.crashes: dict = {}
        self.summary: dict = {}
        self.end: dict = {}
        self.styles: list = []
        self.scripts = 0
        self.script_text: list = []
        self.links: list = []
        self.media: list = []
        self.images: list = []
        self.handlers: list = []
        self._in_style = False
        self._in_script = False

    def handle_starttag(self, tag, attrs) -> None:
        pairs = {
            str(name).lower(): ("" if value is None else str(value))
            for name, value in attrs
        }
        if tag == "script":
            self.scripts += 1
            self._in_script = True
        if tag == "link":
            self.links.append(pairs.get("href", ""))
        self._record_media(tag, pairs)
        if tag == "img":
            self._record_images(pairs)
        if tag == "style":
            self._in_style = True
        self._record_handlers(tag, pairs)
        self._record_cards(pairs)

    # Only a tag that CARRIES a src fetches anything. The <video> start tag
    # does not: its fetch is the child <source>. Recording the parent scored an
    # empty src as off-folder and failed the pin on every real clip. A poster
    # is fetched as soon as the page paints: judged as media.
    def _record_media(self, tag, pairs) -> None:
        if tag in MEDIA_TAGS and "src" in pairs:
            self.media.append((tag, pairs["src"]))
        if tag == "video" and "poster" in pairs:
            self.media.append(("poster", pairs["poster"]))

    def _record_cards(self, pairs) -> None:
        if "data-tc" in pairs:
            self.cards.append(pairs["data-tc"])
            self.crashes[pairs["data-tc"]] = pairs.get("data-crash", "")
        if pairs.get("id") == report.SUMMARY_ID:
            self.summary = pairs
        if pairs.get("id") == report.END_ID:
            self.end = pairs

    def _record_images(self, pairs) -> None:
        self.images.append(pairs.get("src", ""))
        for candidate in pairs.get("srcset", "").split(","):
            if candidate.strip():
                self.images.append(candidate.split()[0])
        if "data-full" in pairs:
            self.images.append(pairs["data-full"])

    def _record_handlers(self, tag, pairs) -> None:
        for name in pairs:
            if name.startswith("on"):
                self.handlers.append(tag + "@" + name)

    def handle_endtag(self, tag) -> None:
        if tag == "style":
            self._in_style = False
        if tag == "script":
            self._in_script = False

    def handle_data(self, data) -> None:
        if self._in_style:
            self.styles.append(data)
        if self._in_script:
            self.script_text.append(data)
