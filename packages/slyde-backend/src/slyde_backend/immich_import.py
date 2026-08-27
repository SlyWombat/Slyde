"""Import a frame's on-device albums into Immich (#72).

A frame that had a life before Slyde holds photos and folders Slyde knows nothing about — on the
Living Room Memento frame, ~1164 images across 30 user albums, loaded through the vendor app. This
reads the frame's own album manifest, pulls each image off the device once, uploads it to Immich,
and mirrors the frame's folders as albums: ``<prefix> - All`` holding everything, plus
``<prefix> - <folder>`` for each user folder.

Deliberate choices, each answering a trap called out in #72:

- **Upload once, add many.** ``AlbumData.json`` references images by filename and the same file
  appears in several albums, so downloads and uploads are keyed by filename and the resulting asset
  is added to every album it belongs to.
- **Reserved albums are not folders.** ``Photos_$%^&(*@#!`` and friends are the device's own
  "all photos" set, not something the user made; their *contents* go into ``- All``, but they never
  become albums of their own.
- **Dates come from the photo, not the clock.** These are the frame's re-encodes, so EXIF may be
  gone; we try EXIF, then a date parsed from the filename, and only then fall back to now — which
  keeps an imported library from piling up at one end of the Immich timeline.
- **Idempotent.** Immich dedupes uploads by checksum and returns the existing asset's id, album
  lookup is by name, and adding an asset already in an album is a no-op — so a re-run after a
  partial import adds only what's missing.

These files are the frame's panel-sized re-encodes, NOT the originals. If the originals are already
in Immich, these arrive as near-duplicates that checksum dedupe cannot catch (different bytes) —
which is exactly why they land in obviously-named albums.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from memento_core import AlbumData

from .config import Settings
from .frame import Frame
from .frames import FrameService
from .immich import ImmichClient
from .immich_write import ImmichWriter
from .naming import is_reserved_dest
from .schemas import SyncItem, SyncResult
from .thumbmatch import best_match

_log = logging.getLogger(__name__)

# pxl_20221010_232749871.jpg, IMG_20190104_101112.jpg, 2021-07-04 12.13.14.jpg, …
_DATE_IN_NAME = re.compile(
    r"(?P<y>19\d{2}|20\d{2})[-_]?(?P<m>0[1-9]|1[0-2])[-_]?(?P<d>0[1-9]|[12]\d|3[01])"
)
_TIME_IN_NAME = re.compile(
    r"(?:^|[_\- ])(?P<h>[01]\d|2[0-3])[.:_]?(?P<mi>[0-5]\d)[.:_]?(?P<s>[0-5]\d)"
)


def _exif_taken(data: bytes) -> datetime | None:
    """DateTimeOriginal from the JPEG, if the frame's re-encode kept any EXIF at all."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            exif = img.getexif()
            raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal, then DateTime
    except Exception:  # not a JPEG, truncated, no EXIF — all equally "we don't know"
        return None
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _name_taken(filename: str) -> datetime | None:
    """A date (and time, when present) parsed out of the filename the camera chose."""
    date = _DATE_IN_NAME.search(filename)
    if not date:
        return None
    hour = minute = second = 0
    clock = _TIME_IN_NAME.search(filename[date.end() :])
    if clock:
        hour, minute, second = (int(clock.group(g)) for g in ("h", "mi", "s"))
    try:
        return datetime(
            int(date.group("y")),
            int(date.group("m")),
            int(date.group("d")),
            hour,
            minute,
            second,
            tzinfo=UTC,
        )
    except ValueError:
        return None


def taken_at(data: bytes, filename: str) -> datetime:
    """Best available capture time: EXIF, else the filename, else now (import time)."""
    return _exif_taken(data) or _name_taken(filename) or datetime.now(UTC)


def plan_albums(album_data: AlbumData, prefix: str) -> tuple[list[str], dict[str, list[str]]]:
    """Work out what to import and where it goes.

    Returns every image filename to pull (unique, stable order) and, per Immich album name, the
    filenames belonging to it. Slyde-owned files (interlude buffers) are excluded entirely — they
    are not the user's photos and would put a clock in their timeline.
    """
    every: list[str] = []
    seen: set[str] = set()
    folders: dict[str, list[str]] = {}
    for album in album_data.albums:
        members: list[str] = []
        for image in album.images:
            if is_reserved_dest(image):
                continue
            if image not in seen:
                seen.add(image)
                every.append(image)
            if image not in members:
                members.append(image)
        # The frame's reserved albums are its own "all photos" set, not user folders: their
        # contents still reach "- All" via ``every``, but they get no album of their own.
        if not album.reserved and members:
            folders[f"{prefix} - {album.display_name}"] = members
    return every, folders


async def import_frame_albums_to_immich(
    *,
    frame: Frame,
    frame_service: FrameService,
    settings: Settings,
    prefix: str,
    writer: ImmichWriter,
    limit: int = 0,
    result: SyncResult | None = None,
) -> SyncResult:
    """Pull every photo off ``frame`` into Immich and mirror its folders as albums.

    ``limit`` (0 = every photo) caps how many images are pulled — a smoke run of a handful before
    committing a low-power frame and someone's Immich library to a thousand-photo import.

    ``result`` is filled in as it goes so a job can be polled for progress: ``total`` is the number
    of images to pull, ``prepared``/``uploaded`` count those fetched and stored, ``skipped`` those
    Immich already had, and ``failed`` those that couldn't be read off the frame.
    """
    result = result or SyncResult()
    album_data = await frame_service.get_album_data(frame.id)
    names, folders = plan_albums(album_data, prefix)
    if limit > 0:
        names = names[:limit]  # folders keep their full membership; absent assets are skipped below
    result.total = len(names)
    _log.info(
        "immich import: %d image(s) from %s across %d folder(s) -> %r",
        len(names),
        frame.id,
        len(folders),
        prefix,
    )

    device_id = f"slyde-frame-{frame.id}"
    asset_ids: dict[str, str] = {}
    for name in names:
        try:
            data = await frame_service.download_image(frame.id, name)
            if not data:
                raise ValueError("empty image off the frame")
            result.prepared += 1
            uploaded = await writer.upload_asset(
                data,
                filename=name,
                device_asset_id=name,
                device_id=device_id,
                created_at=taken_at(data, name),
            )
            asset_ids[name] = uploaded.id
            if uploaded.duplicate:
                result.skipped += 1  # Immich already had these bytes; still filed into the albums
            else:
                result.uploaded += 1
        except Exception as exc:  # one unreadable image must not abort a 1000-photo import
            result.failed += 1
            _log.warning("immich import: skipping %s on %s: %s", name, frame.id, exc)
        if settings.frame_import_delay:
            await asyncio.sleep(settings.frame_import_delay)  # stay gentle on a low-power frame

    # Albums last: an album is only worth creating once its members exist as assets.
    wanted = {f"{prefix} - All": names, **folders}
    for album_name, members in wanted.items():
        ids = [asset_ids[m] for m in members if m in asset_ids]
        if not ids:
            continue
        try:
            album_id, created = await writer.ensure_album(
                album_name, description=f"Imported from the {frame.name or frame.id} frame."
            )
            added = await writer.add_assets(album_id, ids)
            _log.info(
                "immich import: album %r (%s) +%d of %d asset(s)",
                album_name,
                "created" if created else "existing",
                added,
                len(ids),
            )
        except Exception as exc:
            result.failed += 1
            _log.warning("immich import: album %r failed: %s", album_name, exc)
    return result


@dataclass
class LinkReport:
    """What a link pass found, per filename on the frame — the numbers a dry run reports."""

    matched: dict[str, str] = field(default_factory=dict)  # filename -> Immich asset id
    missing: list[str] = field(default_factory=list)  # not in Immich at all
    ambiguous: dict[str, int] = field(default_factory=dict)  # filename -> how many assets matched

    @property
    def considered(self) -> int:
        return len(self.matched) + len(self.missing) + len(self.ambiguous)


async def link_frame_albums_to_immich(
    *,
    frame: Frame,
    frame_service: FrameService,
    settings: Settings,
    prefix: str,
    writer: ImmichWriter,
    dry_run: bool = False,
    resolve: bool = False,
    immich_factory: Callable[[], ImmichClient] | None = None,
    result: SyncResult | None = None,
) -> tuple[SyncResult, LinkReport]:
    """Rebuild a frame's folder structure as Immich albums over the photos ALREADY in Immich (#72).

    Preferred over importing the frame's own copies: firmware 6.02 will not hand back a full-size
    image — ``ReadFile`` answers ``ReadFileStarted`` with ``file_size=0`` for a filename the
    thumbnail path fetches happily — and the only bytes it does give up are 256x170 thumbnails.
    The frame's *manifest* reads perfectly, though, so the structure is recoverable even when the
    pixels aren't: each filename is looked up in Immich and the matching original is filed into
    ``<prefix> - <folder>``.

    A filename matching several Immich assets is left alone and reported as ambiguous — putting the
    wrong photo in someone's album is worse than leaving a gap. ``dry_run`` reports the match rate
    and writes nothing, which is how you find out whether a link pass is worth running at all.
    """
    result = result or SyncResult()
    report = LinkReport()
    album_data = await frame_service.get_album_data(frame.id)
    names, folders = plan_albums(album_data, prefix)
    result.total = len(names)

    for name in names:
        try:
            found = await writer.find_assets_by_filename(name)
        except Exception as exc:
            result.failed += 1
            _log.warning("immich link: lookup failed for %s: %s", name, exc)
            continue
        if len(found) == 1:
            report.matched[name] = found[0]
            result.prepared += 1
        elif not found:
            report.missing.append(name)
            result.skipped += 1
            # Recorded per photo, not just counted: which ones didn't link is the whole point of a
            # dry run, and the job's return value is discarded by the job manager.
            result.items.append(
                SyncItem(asset_id="", dest_name=name, status="skipped", detail="not in Immich")
            )
        else:
            report.ambiguous[name] = len(found)
            result.skipped += 1
            result.items.append(
                SyncItem(
                    asset_id="",
                    dest_name=name,
                    status="skipped",
                    detail=f"ambiguous: {len(found)} assets share this filename",
                )
            )
    _log.info(
        "immich link: %d on the frame -> %d matched, %d missing, %d ambiguous%s",
        len(names),
        len(report.matched),
        len(report.missing),
        len(report.ambiguous),
        " (dry run)" if dry_run else "",
    )
    # Filenames can't settle everything: the frame truncates long names, and camera sequence names
    # repeat across years. Those are decided on pixels instead, from the frame's own thumbnail.
    if resolve and immich_factory is not None:
        settled = await resolve_unlinked_by_thumbnail(
            frame=frame,
            frame_service=frame_service,
            settings=settings,
            writer=writer,
            immich_factory=immich_factory,
            report=report,
            result=result,
        )
        result.prepared += settled
        result.skipped -= settled

    if dry_run:
        return result, report

    wanted = {f"{prefix} - All": names, **folders}
    for album_name, members in wanted.items():
        ids = [report.matched[m] for m in members if m in report.matched]
        if not ids:
            continue
        try:
            album_id, created = await writer.ensure_album(
                album_name,
                description=f"The {frame.name or frame.id} frame's '{album_name}' folder.",
            )
            added = await writer.add_assets(album_id, ids)
            result.uploaded += added
            _log.info(
                "immich link: album %r (%s) +%d of %d",
                album_name,
                "created" if created else "existing",
                added,
                len(ids),
            )
        except Exception as exc:
            result.failed += 1
            _log.warning("immich link: album %r failed: %s", album_name, exc)
    return result, report


def name_stem(filename: str) -> str:
    """The searchable part of a frame filename, with the frame's mangling undone.

    The frame truncates a long name and re-appends ``.jpg``, leaving debris like ``…~2.jp.jpg`` or
    ``….portrait..jpg``. Stripping that back gives a stem worth searching Immich for — a lead to
    check with pixels, never an answer on its own (#72).
    """
    stem = filename.lower().removesuffix(".jpg")
    for leftover in (".portrait.", ".portrait", ".mp", ".jpe", ".jp", ".j", "."):
        if stem.endswith(leftover):
            stem = stem[: -len(leftover)]
            break
    return stem.strip()


async def resolve_unlinked_by_thumbnail(
    *,
    frame: Frame,
    frame_service: FrameService,
    settings: Settings,
    writer: ImmichWriter,
    immich_factory: Callable[[], ImmichClient],
    report: LinkReport,
    result: SyncResult | None = None,
) -> int:
    """Settle the photos filenames couldn't, by comparing the frame's own thumbnail (#72).

    Handles both leftovers from the filename pass, because they need the same evidence:
    *ambiguous* names, where several Immich assets share a filename and only pixels say which photo
    is on the frame; and *missing* ones, where the frame's truncated filename needs a stem search
    whose candidates must then be confirmed rather than trusted.

    Mutates ``report.matched`` with what it settles and returns how many it added. Anything without
    a clear winner is left unlinked — the whole point is to stop guessing, not to guess harder.
    """
    result = result or SyncResult()
    unresolved = list(report.ambiguous) + list(report.missing)
    if not unresolved:
        return 0
    _log.info("thumbnail match: %d photo(s) filenames couldn't settle", len(unresolved))
    settled = 0
    async with immich_factory() as immich:
        for name in unresolved:
            try:
                # Candidates: the exact-name collisions, or a stem search for a mangled name.
                if name in report.ambiguous:
                    pairs = [(a, name) for a in await writer.find_assets_by_filename(name)]
                else:
                    stem = name_stem(name)
                    pairs = (
                        await writer.find_asset_candidates(
                            stem, limit=settings.thumb_match_candidates
                        )
                        if len(stem) >= 6  # a 3-character stem matches half the library
                        else []
                    )
                if not pairs:
                    continue
                reference = await frame_service.get_thumbnail(frame.id, name, quick=False)
                candidates = [
                    (asset_id, await immich.asset_bytes(asset_id, "thumbnail"))
                    for asset_id, _ in pairs[: settings.thumb_match_candidates]
                ]
                match = best_match(
                    reference,
                    candidates,
                    max_distance=settings.thumb_match_max_distance,
                    min_margin=settings.thumb_match_min_margin,
                )
            except Exception as exc:  # a frame that won't serve one thumbnail isn't a failed run
                _log.warning("thumbnail match: %s could not be resolved: %s", name, exc)
                continue
            if match is None:
                continue
            report.matched[name] = match.asset_id
            settled += 1
            result.items.append(
                SyncItem(
                    asset_id=match.asset_id,
                    dest_name=name,
                    status="uploaded",
                    detail=f"matched by thumbnail (distance {match.distance}, "
                    f"margin {match.margin}, {len(candidates)} candidates)",
                )
            )
    _log.info("thumbnail match: settled %d of %d", settled, len(unresolved))
    return settled
