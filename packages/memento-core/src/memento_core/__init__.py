"""memento-core — local-network client library for the Memento Smart Frame.

Reverse-engineered from the discontinued official app; LAN-only, no cloud. See docs/protocol.md.
"""

from .albums import Album, AlbumData, parse_album_data
from .client import FrameClient, FrameError, image_to_thumb, thumb_to_image
from .discovery import FrameInfo, discover, parse_broadcast
from .protocol import DEFAULT_PORTS, Flow, Ports, Setup, Transfer

__all__ = [
    "DEFAULT_PORTS",
    "Album",
    "AlbumData",
    "Flow",
    "FrameClient",
    "FrameError",
    "FrameInfo",
    "Ports",
    "Setup",
    "Transfer",
    "discover",
    "image_to_thumb",
    "parse_album_data",
    "parse_broadcast",
    "thumb_to_image",
]
__version__ = "0.1.0"
