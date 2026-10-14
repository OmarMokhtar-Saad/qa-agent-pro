"""The system prompt, reassembled at the LINE level from a logcat slice."""

from __future__ import annotations

from typing import Any, NamedTuple, cast

from tools.mobile_evidence.grammar_consts import logcat_line
from tools.mobile_evidence.grammar_pat import _prefix_re
from tools.mobile_evidence.grammar_turns import turn_of_seq
from tools.mobile_evidence.grammar_values import _lines_of


def _logcat_messages(source, profile) -> list:
    """The (seq, message) pairs of a logcat slice, tag-filtered and prefix-stripped."""
    tag = str(getattr(profile, "logcat_tag", "") or "")
    prefix_re = _prefix_re(profile)
    msgs: list[Any] = []
    for raw in _lines_of(source):
        m = logcat_line(raw.rstrip("\n"))
        if not m or (tag and m.group("tag").strip() != tag):
            continue
        msg = m.group("msg")
        pre = prefix_re.match(msg)
        msgs.append((len(msgs), msg[pre.end() :] if pre else msg))
    return msgs


class _Markers(NamedTuple):
    """The profile markers that open a system prompt, open the next message, close a prompt."""

    sys_mark: str
    next_msg: str
    sys_end: str


class _PromptScan:
    """Walks the (seq, message) pairs once, keeping the prompt being read, if any."""

    def __init__(self, marks: _Markers) -> None:
        self.marks = marks
        self.out: list = []
        self.buf: list | None = None
        self.start = None

    def feed(self, seq, s: str) -> None:
        """Take one message: open, extend or close a prompt."""
        if self.buf is None:
            self._open(seq, s)
        elif s.startswith(self.marks.next_msg):
            self._close(False)
        elif self.marks.sys_end in s:
            self.buf.append(s.split(self.marks.sys_end)[0])
            self._close(True)
        else:
            self.buf.append(s)

    def _open(self, seq, s: str) -> None:
        i = s.find(self.marks.sys_mark)
        if i < 0:
            return
        head = s[i + len(self.marks.sys_mark) :]
        if self.marks.sys_end in head:
            self.out.append((head.split(self.marks.sys_end)[0], True, seq))
        else:
            self.buf, self.start = [head], seq

    def _close(self, complete: bool) -> None:
        self.out.append(("\n".join(cast("list", self.buf)), complete, self.start))
        self.buf = None

    def finish(self) -> list:
        """The prompts read, the one still open when the messages ran out counting as cut."""
        if self.buf is not None:
            self._close(False)
        return self.out


def _reassemble_prompts(msgs, marks: _Markers) -> list:
    """Join the messages between the open and close markers into (text, complete, seq)."""
    scan = _PromptScan(marks)
    for seq, s in msgs:
        scan.feed(seq, s)
    return scan.finish()


def system_prompts(source, profile):
    """Every system prompt in a logcat slice, as (text, complete, seq) triples.

    The markers that open a prompt, open the next message and close a prompt come from
    ``profile.markers`` (``sys_mark``, ``next_msg``, ``sys_end``); without them there is
    nothing to reassemble and the result is empty. ``complete`` is False for a prompt the
    logger's line cap cut.
    """
    markers = getattr(profile, "markers", None) or {}
    sys_mark = markers.get("sys_mark")
    next_msg = markers.get("next_msg")
    sys_end = markers.get("sys_end")
    if not (sys_mark and next_msg and sys_end):
        return []
    marks = _Markers(sys_mark, next_msg, sys_end)
    return _reassemble_prompts(_logcat_messages(source, profile), marks)


def _logcat_prompts(logcat, profile, parsed) -> list:
    """The system prompts of a logcat-only capture, each tied to its turn."""
    prompts = [
        {"text": t, "complete": ok, "seq": seq}
        for t, ok, seq in system_prompts(logcat, profile)
    ]
    for p in prompts:
        p["turnId"] = turn_of_seq(parsed, p["seq"])
    return prompts
