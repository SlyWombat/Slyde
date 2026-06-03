"""memento-core — local-network client library for the Memento Smart Frame.

Reverse-engineered from the discontinued official app; LAN-only, no cloud. See docs/protocol.md.
"""

from .client import FrameClient, FrameError
from .discovery import FrameInfo, discover, parse_broadcast
from .protocol import DEFAULT_PORTS, Flow, Ports, Setup, Transfer

__all__ = [
    "DEFAULT_PORTS",
    "Flow",
    "FrameClient",
    "FrameError",
    "FrameInfo",
    "Ports",
    "Setup",
    "Transfer",
    "discover",
    "parse_broadcast",
]
__version__ = "0.1.0"
