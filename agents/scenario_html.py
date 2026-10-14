"""HTML stripping for Jira descriptions."""

from __future__ import annotations

import re

# Real HTML tag names only. A Jira UC table writes data-field names as
# <Order number> / <I no longer need the product>, and the blanket r"<[^>]+>"
# strip this replaced deleted every one of them before the model ever saw the
# spec -- on TICKET-5645 all three DF01 cancellation reasons became empty table
# cells. A match therefore needs BOTH a known tag name AND attribute-shaped
# text after it: either nothing (<p>, <br/>, </div>) or something containing
# "=". "<I no longer need the product>" has a tag-shaped head ("i") but its
# trailing words carry no "=", so it survives.
_HTML_TAG_NAMES = (
    "a|abbr|address|area|article|aside|audio|b|base|blockquote|body|br|button|"
    "canvas|caption|cite|code|col|colgroup|custom|data|datalist|dd|del|details|"
    "dfn|dialog|div|dl|dt|em|embed|fieldset|figcaption|figure|footer|form|h1|h2|"
    "h3|h4|h5|h6|head|header|hr|html|i|iframe|img|input|ins|kbd|label|legend|li|"
    "link|main|map|mark|menu|meta|meter|nav|noscript|object|ol|optgroup|option|"
    "output|p|param|picture|pre|progress|q|rp|rt|ruby|s|samp|script|section|"
    "select|small|source|span|strong|style|sub|summary|sup|svg|table|tbody|td|"
    "template|textarea|tfoot|th|thead|time|title|tr|track|u|ul|var|video|wbr"
)


_HTML_TAG_RE = re.compile(
    rf"</?(?:{_HTML_TAG_NAMES})\b(?:\s[^<>]*=[^<>]*)?\s*/?>",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = _HTML_TAG_RE.sub(" ", text)
    return text.strip()
