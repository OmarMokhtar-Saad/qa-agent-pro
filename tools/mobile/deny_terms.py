"""Whole-word matching of the dialog-handler denylist.

The old rule was a substring test, so release-notes prose ("... library removed
...") was refused as if it were a "Remove" button. The rule now:

* the value is NORMALISED to space-separated word tokens: NFKD case-folded,
  combining marks dropped (Arabic diacritics, and hamza on alef, so the alef
  variants unify), tatweel dropped, and every non-letter and non-digit (space,
  ``_``, ``.``, ``:``, ``/``) turned into a space. A resource id such as
  ``permission_allow_always_button`` therefore reads as the words
  ``permission allow always button``, and a camelCase id such as ``btnDelete``
  is split at its lower-to-upper boundaries first;
* a term matches only as a WHOLE-WORD run of those tokens (``\\w`` is Unicode,
  so the boundary is correct for Arabic too);
* a SHORT value (at most ``MAX_LABEL_WORDS`` words) is a control label and any
  term or inflection in it denies; a LONGER free-text value is a paragraph, not
  a control label, and is refused only when it OPENS with a term's base
  form ("Delete my account and all ..."); prose that opens "Removed ..." or
  "تم حذف ..." is not. Resource ids are never
  paragraphs, so the length rule applies to visible text only.

Inflections are explicit term variants (``removed``, ``deleting``, ...), not
stems: a short label "Removed items" IS denied, deliberately -- a short label
naming a removal is more likely a control than prose, and the cost of a false
refusal there is one declined tap.
"""

from __future__ import annotations

import re
import unicodedata

#: Words a value may have and still count as a control label.
MAX_LABEL_WORDS = 8

_NON_WORD = re.compile(r"[\W_]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_ENGLISH = (
    "uninstall|uninstalled|uninstalling|uninstalls|uninstallation",
    "delete|deleted|deleting|deletes|deletion",
    "clear data|clear storage",
    "allow all the time|always allow|allow always|allow all",
    "erase|erased|erasing|erases",
    "factory reset",
    "force stop",
    "remove|removed|removing|removes|removal",
    "disable|disabled|disabling|disables",
)
_ARABIC = (
    "حذف|احذف|الحذف|يحذف|تحذف|محذوف",
    "إزالة|الإزالة|ازل|يزيل",
    "إلغاء التثبيت|إلغاء تثبيت|حذف التطبيق",
    "مسح البيانات|مسح التخزين|مسح الكل|محو",
    "تعطيل|عطل",
    "فرض الإيقاف|إيقاف إجباري",
    "السماح دائما|السماح للكل",
)


def normalize(value: str) -> str:
    """Case-folded, mark-free, separator-free word string (camelCase split)."""
    plain = unicodedata.normalize("NFKD", _CAMEL.sub(" ", value or "")).casefold()
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return _NON_WORD.sub(" ", plain.replace("ـ", "")).strip()


def _terms() -> tuple:
    groups = "|".join(_ENGLISH + _ARABIC).split("|")
    return tuple(sorted({normalize(t) for t in groups if t}))


_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, _terms())) + r")(?!\w)")


def is_control_label(text: str) -> bool:
    """False for paragraph-length free text (not a control's own label)."""
    return len(normalize(text).split()) <= MAX_LABEL_WORDS


def has_deny_term(value: str) -> bool:
    """True when the normalised value holds a deny term as whole words."""
    return _PATTERN.search(normalize(value)) is not None


def _heads() -> tuple:
    return tuple(normalize(group.split("|")[0]) for group in _ENGLISH + _ARABIC)


_LEAD = re.compile(r"(?:" + "|".join(map(re.escape, _heads())) + r")(?!\w)")


def leads_with_deny_term(value: str) -> bool:
    """True when the value OPENS with a term's base (imperative) form."""
    return _LEAD.match(normalize(value)) is not None
