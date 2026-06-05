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
from typing import Protocol

from memento_core import AlbumData, FrameInfo, Ports, Setup
from memento_core.protocol import JsonDict


class FrameConnection(Protocol):
    """The operations the manager performs on a connected frame, independent of transport.

    ``memento_core.FrameClient`` already implements this surface; new backends provide their own
    object exposing the same methods. The manager only depends on this Protocol.
    """

    def get_config(self) -> JsonDict: ...
    def change_setup(self, action: Setup, payload: JsonDict) -> None: ...
    def get_current_image_name(self) -> str: ...
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
    def trigger_update(self, url: str, md5: str) -> None: ...


@dataclass(frozen=True)
class FrameCapabilities:
    """What a backend's frames can do, so the manager/UI can adapt (and tests can assert)."""

    transport: str  # "lan" | "cloud"
    discovery: bool  # can frames be found on the LAN?
    albums: bool  # supports folder/album structure
    thumbnails: bool  # can enumerate/serve on-frame thumbnails
    upload: bool  # can receive pushed photos
    delete: bool  # can remove on-frame photos
    ota: bool  # supports a manager-triggered firmware/app update


class FrameBackend(ABC):
    """A kind of frame the manager can drive. Subclasses set ``name`` + ``capabilities``."""

    name: str
    capabilities: FrameCapabilities

    @abstractmethod
    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list[FrameInfo]:
        """Find frames of this kind. Returns ``[]`` if discovery doesn't apply (cloud frames)."""

    @abstractmethod
    def session(
        self, host: str, *, ports: Ports | None = None
    ) -> AbstractContextManager[FrameConnection]:
        """Open a session to ``host`` as a context manager yielding a ``FrameConnection``."""
