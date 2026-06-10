"""Frame identity — a transport-independent handle for a managed frame.

Replaces the bare ``host: str`` that identified frames everywhere. A connected (LAN) frame's ``id``
is its host, so existing host-keyed call sites keep working; a served (cloud) frame's ``id`` is the
frame-code it presents, which the manager never connects to. See ``docs/framework-design.md`` §2.1.
"""

from __future__ import annotations

from dataclasses import dataclass

NULL_GUID = "00000000-0000-0000-0000-000000000000"  # default/blank — not a usable stable identity


@dataclass(frozen=True)
class Frame:
    """A managed frame, independent of how it's reached.

    ``id`` is the stable key used across the registry, curation, and scheduling. For a connected
    frame it equals ``address`` (its host); for a served frame it equals ``frame_code``.
    """

    id: str
    backend: str  # the FrameBackend name that drives this frame
    interaction: str  # "connected" | "served"
    name: str = ""
    address: str = ""  # LAN host (connected); empty for served frames
    frame_code: str = ""  # cloud identity (served); empty for connected frames
    last_seen: str | None = None  # ISO timestamp the registry last saw it

    @classmethod
    def connected(cls, address: str, *, backend: str, name: str = "", guid: str = "") -> Frame:
        """A frame the manager reaches over the network.

        Identified by its **stable GUID** when it reports a real one — so a DHCP address change
        never loses the frame or its curated library/delivery (#58). ``address`` (the current IP)
        is a mutable field refreshed on each discovery. Frames with no/zero GUID (e.g. the emulator)
        fall back to the address as id (legacy behaviour)."""
        stable = guid if guid and guid != NULL_GUID else address
        return cls(
            id=stable,
            backend=backend,
            interaction="connected",
            # Don't default the name to the address — an IP-shaped placeholder would look like a
            # real name and clobber the captured one (#58). The UI falls back to the id; the device's
            # Name is captured on the first config read (#51).
            name=name,
            address=address,
        )

    @classmethod
    def served(cls, frame_code: str, *, backend: str, name: str = "") -> Frame:
        """A frame that polls a server we run (id == frame-code); never connected to."""
        return cls(
            id=frame_code,
            backend=backend,
            interaction="served",
            name=name or frame_code,
            frame_code=frame_code,
        )
