"""Pluggable frame-backend abstraction.

The manager talks to *frames* through a ``FrameBackend`` so it isn't tied to one device or one
transport. The original Memento frame is driven over its reverse-engineered LAN protocol
(``MementoLanBackend``); other frames — e.g. the Aluratek/Sungale ePaper frame, which is a cloud
device — are reached by impersonating their cloud (``SungaleCloudBackend``). Both implement the same
interface, so the UI/sync engine stay transport-agnostic (just as they already treat the emulator
exactly like a real frame).

Two pieces:
- ``FrameConnection`` — the per-session operations the manager performs on a connected frame. The
  existing ``memento_core.FrameClient`` satisfies it structurally; a new backend implements the same
  surface for its own transport.
- ``FrameBackend`` — discovery + opening a session, plus a capability descriptor.

To add a frame, implement ``FrameBackend`` and register it (see ``docs/frame-backends.md``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from memento_core import AlbumData, FrameInfo, Ports, Setup
from memento_core.protocol import JsonDict

if TYPE_CHECKING:
    from fastapi import APIRouter, Request, Response

    from ..frame import Frame


class FrameConnection(Protocol):
    """The operations the manager performs on a connected frame, independent of transport.

    ``memento_core.FrameClient`` already implements this surface; new backends provide their own
    object exposing the same methods. The manager only depends on this Protocol.
    """

    def get_config(self) -> JsonDict: ...
    def change_setup(self, action: Setup, payload: JsonDict) -> None: ...
    def get_current_image_name(self) -> str: ...
    def display_image(self, name: str) -> None: ...
    def change_picture_duration(self, seconds: int) -> None: ...
    def change_shuffle(self, on: bool) -> None: ...
    def next_image(self) -> None: ...
    def previous_image(self) -> None: ...
    def delete_image(self, filename: str) -> None: ...
    def upload_image(
        self,
        data: bytes,
        dest_name: str,
        *,
        info: JsonDict | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None: ...
    def get_album_data(self) -> AlbumData: ...
    def send_album_data(self, album_data: AlbumData) -> None: ...
    def get_thumbnails_list(self) -> list[tuple[str, str]]: ...
    def get_thumbnail(self, image_filename: str) -> bytes: ...
    def download_image(self, image_filename: str) -> bytes: ...
    def trigger_update(self, url: str, md5: str) -> None: ...


@runtime_checkable
class SessionLiveness(Protocol):
    """A session that can say whether the frame has hung up on it, without sending anything.

    Optional: a backend whose connection implements this lets the manager hold a *warm* session and
    reconnect the instant the frame drops it. That matters on the Memento LAN frame, which services
    a new connection only once per ~21s tick and tears the session down each tick, so reconnecting
    immediately (rather than on the next request) is what keeps reads sub-second (#71).
    """

    def closed_by_peer(self, timeout: float = 0.0) -> bool: ...


@dataclass(frozen=True)
class FrameCapabilities:
    """What a backend's frames can do, so the manager/UI can adapt (and tests can assert)."""

    interaction: str  # "connected" (manager initiates) | "served" (frame polls a server we run)
    transport: str  # "lan" | "cloud"
    color_model: str  # "full" (LCD, JPEG) | "epaper" (limited palette + dither) — drives processing
    discovery: bool  # can frames be found on the LAN?
    albums: bool  # supports folder/album structure
    thumbnails: bool  # can enumerate/serve on-frame thumbnails
    upload: bool  # can receive pushed photos
    delete: bool  # can remove on-frame photos
    ota: bool  # supports a manager-triggered firmware/app update
    # Can this frame show a recurring "interlude" image between slideshow photos (#70)? TWO
    # independent constraints, deliberately folded into one flag so the engine never re-derives
    # them (ADR-009 rule 2 -- no special-casing the core):
    #   1. the panel tolerates the extra redraws -- false for e-paper, where every refresh is
    #      ~15-30s of visible flashing and a slice of a finite wear budget;
    #   2. the frame is ``interaction="connected"`` AND exposes an on-demand "show *this* image
    #      now" command, so the manager can conduct the rotation itself.
    # A future full-colour LAN frame gets interludes by setting this true; nothing else changes.
    interludes: bool = False


class FrameBackend(ABC):
    """A kind of frame the manager can drive. Subclasses set ``name`` + ``capabilities``.

    Two interaction models subclass this (see ``docs/framework-design.md`` §2.2):
    ``ConnectedFrameBackend`` (the manager opens a session and pushes — e.g. Memento LAN) and
    ``ServedFrameBackend`` (the manager runs a server the frame polls — e.g. a cloud frame).
    """

    name: str
    capabilities: FrameCapabilities

    @abstractmethod
    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list[FrameInfo]:
        """Find frames of this kind. Returns ``[]`` if discovery doesn't apply (cloud frames)."""


class ConnectedFrameBackend(FrameBackend):
    """Backend the manager drives by *initiating* a connection and pushing/reading (e.g. LAN)."""

    @abstractmethod
    def session(
        self, host: str, *, ports: Ports | None = None, timeout: float | None = None
    ) -> AbstractContextManager[FrameConnection]:
        """Open a session to ``host`` as a context manager yielding a ``FrameConnection``.

        ``timeout`` (seconds), when given, bounds BOTH the control and transfer channels so a quick
        UI read fails fast instead of hanging on an unresponsive frame; ``None`` keeps the backend's
        defaults (longer transfer timeout for bulk uploads/downloads) (#68).
        """


class ServedFrameBackend(FrameBackend):
    """Backend whose frames *poll a server we run* (the manager impersonates their cloud).

    The manager never connects to the frame. Instead it mounts ``router()`` into the app; when a
    frame polls, ``identify()`` resolves which registered frame is calling and ``respond()`` answers
    by pulling from the frame library + its processing profile. The full wiring lands in the
    served-mounting and curation/delivery work (see ``docs/framework-design.md`` §2.2/§2.4).
    """

    @abstractmethod
    def router(self) -> APIRouter:
        """The HTTP surface the frame polls, to be mounted into the manager app."""

    @abstractmethod
    def identify(self, request: Request) -> str | None:
        """Resolve which frame (its frame-code/id) is making this request, or None if unknown."""

    @abstractmethod
    async def respond(self, frame: Frame, request: Request) -> Response:
        """Answer a frame's poll (e.g. its photo list / next image), prepared for that frame."""
