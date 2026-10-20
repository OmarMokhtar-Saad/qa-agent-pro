"""The run's ONE recording, joined start to end.

The source is the run-long recording of :mod:`tools.mobile.media_run`: its
``run-seg-NNN.mp4`` segments cover the run from its first step to its end. A
run without segments (recorded before fix/1560, or whose recorder never
started) falls back to the step clips, one per replayed step. The PAGE shows
one video: this module joins the files, in order, into :data:`RUN_CLIP` with
ffmpeg's concat demuxer. Every file comes from the same device at the same
``adb.SCREENRECORD_SIZE``, so the streams are copied, never re-encoded.

Without ffmpeg, or when the join fails, the page still shows ONE player: it
carries the files in ``data-playlist`` and the shell plays them back to back.
A stretch where a credential was typed, or a lost file, has no pixels, so the
recording skips it rather than inventing them.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from tools.mobile import media
from tools.mobile.media_run import SEGMENT_PREFIX
from tools.mobile.rimg_core import _ffmpeg as find_ffmpeg
from tools.mobile.rmed_book import _IMAGES
from tools.mobile.rmed_html import _poster_attr
from tools.mobile.rmed_map import _media_src
from tools.mobile.rshl_paths import MEDIA_DIR
from tools.mobile.rtxt_secret import esc

logger = logging.getLogger("tools.mobile.report")

RUN_CLIP = "run.mp4"
#: The concat list of the last join, kept so a re-render reuses the file.
RUN_LIST = "run.mp4.txt"
#: Seconds one join may take; a copy-join of a run's clips takes about one.
JOIN_TIMEOUT_S = 120


def clip_names(cases: list) -> list:
    """The stored clip file names of every case, unordered."""
    return [
        str(rec["file"])
        for case in cases or []
        if isinstance(case, dict)
        for rec in list(case.get("media") or [])
        if isinstance(rec, dict)
        and str(rec.get("kind") or "") == media.CLIP
        and _media_src(rec)
    ]


def segments(media_dir: Path) -> list:
    """The run-long recording's segments on disk, in recording order."""
    return sorted(media_dir.glob(SEGMENT_PREFIX + "*.mp4"))


def run_clips(cases: list, media_dir: Path) -> list:
    """The run's recording files on disk, in order: segments, else step clips."""
    return segments(media_dir) or _step_clips(cases, media_dir)


def _step_clips(cases: list, media_dir: Path) -> list:
    """Every step clip of the run that is on disk, in the order it was recorded."""
    paths = [media_dir / name for name in set(clip_names(cases))]
    present = [p for p in paths if p.is_file()]
    return sorted(present, key=lambda p: (p.stat().st_mtime, p.name))


def _concat(clips: list, listing: str, media_dir: Path) -> bool:
    """Run ffmpeg's concat demuxer over *clips* into :data:`RUN_CLIP`."""
    tool = find_ffmpeg()
    if not tool:
        return False
    tmp, tmp_list = media_dir / ("tmp-" + RUN_CLIP), media_dir / ("tmp-" + RUN_LIST)
    tmp_list.write_text(listing, encoding="utf-8")
    argv = [tool, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i"]
    argv += [str(tmp_list), "-c", "copy", str(tmp)]
    done = subprocess.run(argv, capture_output=True, timeout=JOIN_TIMEOUT_S)
    if done.returncode or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        tmp_list.unlink(missing_ok=True)
        return False
    # The list is renamed only AFTER the video, so a failed join can never
    # leave a list that vouches for an older recording.
    tmp.replace(media_dir / RUN_CLIP)
    tmp_list.replace(media_dir / RUN_LIST)
    return True


def _listing(clips: list) -> str:
    """The concat list; a ``#`` line (ignored by ffmpeg) stamps each file's size and mtime."""
    stamps = [(p.name, p.stat()) for p in clips]
    return "".join(
        "file '%s'\n# %d %d\n" % (name, st.st_size, st.st_mtime_ns)
        for name, st in stamps
    )


def _kept(listing: str, media_dir: Path) -> bool:
    """Whether :data:`RUN_CLIP` was joined from exactly *listing*."""
    kept = media_dir / RUN_LIST
    if not ((media_dir / RUN_CLIP).is_file() and kept.is_file()):
        return False
    return kept.read_text(encoding="utf-8") == listing


def join_run(clips: list, media_dir: Path) -> bool:
    """Whether :data:`RUN_CLIP` holds exactly *clips*, joining them when it does not.

    A clip re-recorded under the same name changes its size or mtime stamp, so
    the stale join is redone rather than reused.
    """
    try:
        listing = _listing(clips)
        if _kept(listing, media_dir):
            return True
        return _concat(clips, listing, media_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("mobile.report: could not join the run's clips: %s", exc)
        return False


def run_sources(cases: list) -> list:
    """The page-relative clip(s) the run's one player plays, in order."""
    book = _IMAGES.get()
    if book is None:
        return []
    clips = run_clips(cases, book["media_dir"])
    if len(clips) > 1 and join_run(clips, book["media_dir"]):
        return [MEDIA_DIR + "/" + RUN_CLIP]
    return [MEDIA_DIR + "/" + p.name for p in clips]


#: What the run's recording is, said once under the player.
RUN_NOTE = (
    "The whole run on the device, every replayed step in order. A step that "
    "typed a credential is not recorded, so the video skips it."
)

#: The same note when the run-long recording is the source.
RUN_NOTE_WHOLE = (
    "The whole run on the device, from its first step to its end. Stretches "
    "where a credential was typed are cut out."
)


def _run_note() -> str:
    """The note for the source the player carries."""
    book = _IMAGES.get()
    whole = book is not None and bool(segments(book["media_dir"]))
    return RUN_NOTE_WHOLE if whole else RUN_NOTE


def run_video_html(cases: list) -> str:
    """The run's ONE player, or ``""`` when no step of the run was recorded."""
    sources = run_sources(cases)
    if not sources:
        return ""
    playlist = " ".join(sources) if len(sources) > 1 else ""
    poster = _poster_attr({"file": sources[0].rsplit("/", 1)[-1]})
    return (
        '<div class="stepcliprow"><video class="stepclip" controls '
        'preload="metadata" playsinline'
        + ((' data-playlist="' + esc(playlist, 8000) + '"') if playlist else "")
        + poster
        + '><source src="'
        + esc(sources[0], 140)
        + '" type="video/mp4"></video><p class="stepclipnote">'
        + esc(_run_note(), 300)
        + "</p></div>"
    )
