"""Match a frame's on-device thumbnail against candidate Immich assets by content (#72).

Filenames alone can't finish the job of mirroring a frame's albums:

- The frame truncates long names and re-appends ``.jpg``, so
  ``10530745_10152252874922401_402128259452737429_n.jpg`` reaches it as
  ``10530745_10152252874922401_40212825.jpg`` and matches nothing. A prefix search finds
  candidates but is not evidence — ``orange.jpg`` prefix-matches a Cézanne still life.
- Camera sequence names repeat. ``dscn0031.jpg`` exists three times in this library, captured in
  2013, 2016 and 2019: three different photos, one filename. Nothing in the name says which one is
  on the frame.

Both are answerable from pixels. ``ReadFile`` is dead on firmware 6.02, but ``GetThumbnails``
works — 256x170 in well under a second — and that is plenty to tell one photo from another.

The comparison is a **dHash**: grayscale, resize to 9x8, then one bit per horizontal
neighbour-pair brightness comparison, giving a 64-bit fingerprint compared by Hamming distance.
It survives the rescaling and re-encoding between the frame's thumbnail and Immich's, while
staying stable under the brightness and colour shifts those introduce. PIL only, no new deps.

A match must be both *close* and *clearly better than the runner-up*. Requiring the margin is what
keeps three near-identical burst frames from resolving to a coin flip — with no confident winner
the photo stays unlinked, which is the same principle the filename pass follows: a gap beats the
wrong photo in someone's album.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image

_log = logging.getLogger(__name__)

_HASH_SIDE = 8  # 8x8 comparisons -> a 64-bit fingerprint


@dataclass(frozen=True)
class ThumbMatch:
    """The winning candidate, with the evidence for it."""

    asset_id: str
    distance: int  # bits differing from the frame's thumbnail (0 = identical fingerprint)
    margin: int  # how much worse the runner-up was; large means the win was unambiguous


def dhash(data: bytes) -> int | None:
    """A 64-bit difference-hash of an image, or None if the bytes aren't a readable image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            small = img.convert("L").resize((_HASH_SIDE + 1, _HASH_SIDE), Image.Resampling.LANCZOS)
            # ``tobytes`` on an "L" image is one byte per pixel — the same values ``getdata``
            # yields, without the deprecation (it goes in Pillow 14) and without building a list.
            pixels = small.tobytes()
    except (OSError, ValueError):  # truncated, not an image, unsupported — all "can't compare"
        # Deliberately narrow: a broad ``except`` here silently turns any bug in this function into
        # "no match", which is indistinguishable from an honest one and hid exactly that once.
        return None
    bits = 0
    for row in range(_HASH_SIDE):
        base = row * (_HASH_SIDE + 1)
        for col in range(_HASH_SIDE):
            bits = (bits << 1) | int(pixels[base + col] > pixels[base + col + 1])
    return bits


def distance(left: int, right: int) -> int:
    """Hamming distance between two fingerprints, in bits."""
    return int(left ^ right).bit_count()


def best_match(
    reference: bytes,
    candidates: list[tuple[str, bytes]],
    *,
    max_distance: int,
    min_margin: int,
) -> ThumbMatch | None:
    """The candidate whose image matches ``reference``, or None when nothing wins clearly.

    ``max_distance`` is how different the winner may still be (in bits of 64); ``min_margin`` how
    far clear of the runner-up it must be. A single candidate only needs to be close enough — there
    is nothing for it to be confused with.
    """
    target = dhash(reference)
    if target is None:
        return None
    scored: list[tuple[int, str]] = []
    for asset_id, data in candidates:
        fingerprint = dhash(data)
        if fingerprint is not None:
            scored.append((distance(target, fingerprint), asset_id))
    if not scored:
        return None
    scored.sort()
    best, asset_id = scored[0]
    if best > max_distance:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else best + min_margin
    margin = runner_up - best
    if margin < min_margin:
        _log.debug(
            "thumbnail match rejected: %d vs runner-up %d (margin %d)", best, runner_up, margin
        )
        return None
    return ThumbMatch(asset_id, best, margin)
