"""Pick the package a tester means, from what is installed on THIS device.

Order of authority, strongest first:

1. an explicit ``package`` argument -- always wins, only validated for shape;
2. an answer the tester already gave for this serial and text, if that package
   is still installed;
3. resolve-or-ask over ``pm list packages``: one match is used and named,
   several (qa / master / prod ...) are asked about, none is reported.

Nothing here guesses: an ambiguous or empty request returns an unresolved
:class:`Resolution` whose question the caller puts to the tester.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Sequence

from tools.device_manager import valid_package_name
from tools.mobile import adb
from tools.mobile.app_label import LabelLister, known_labels
from tools.mobile.resolve import (
    NONE,
    RESOLVED,
    AskCb,
    Candidate,
    Resolution,
    resolve_or_ask,
)
from tools.mobile.resolve_cache import RunAnswers

PackageLister = Callable[[str], Awaitable[dict]]


@dataclass(frozen=True)
class PickRequest:
    app_text: str
    serial: str
    explicit_package: str = ""


def package_candidates(packages: Sequence[str], labels: Optional[dict] = None) -> list:
    names = labels or {}
    return [Candidate(p, str(names.get(p, ""))) for p in packages]


def _unresolved(question: str) -> Resolution:
    return Resolution(NONE, question=question)


async def _resolve_installed(
    request: PickRequest,
    installed: Sequence[str],
    ask: Optional[AskCb],
    answers: Optional[RunAnswers],
    labels: Optional[dict] = None,
) -> Resolution:
    if answers is not None:
        answers.drop_missing(request.serial, installed)
        remembered = answers.get(request.serial, request.app_text)
        if remembered:
            return Resolution(RESOLVED, remembered, how="asked")
    cands = package_candidates(installed, labels)
    got = await resolve_or_ask(request.app_text, cands, noun="app", ask=ask)
    if answers is not None and got.resolved and got.how == "asked":
        answers.remember(request.serial, request.app_text, got.value)
    return got


async def pick_package(
    request: PickRequest,
    *,
    ask: Optional[AskCb] = None,
    packages: Optional[PackageLister] = None,
    answers: Optional[RunAnswers] = None,
    labeler: Optional[LabelLister] = None,
) -> Resolution:
    explicit = request.explicit_package.strip()
    if explicit:
        if not valid_package_name(explicit):
            return _unresolved(
                repr(explicit) + " is not a valid package id. Ask the user for the "
                "exact package id."
            )
        return Resolution(RESOLVED, explicit, how="explicit")
    if not request.app_text.strip():
        return _unresolved(
            "No app name was given. Ask the user which app to use; they can name "
            "part of its name."
        )
    listing = await (packages or adb.installed_packages)(request.serial)
    if listing.get("error"):
        return _unresolved(str(listing["error"]))
    installed = list(listing.get("content") or [])
    labels = await (labeler or known_labels)(request.serial, installed)
    return await _resolve_installed(request, installed, ask, answers, labels)
