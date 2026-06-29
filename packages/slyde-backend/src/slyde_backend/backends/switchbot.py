"""SwitchBot AI Art Frame backend — push-to-vendor-cloud delivery (#64).

A third interaction model alongside connected (LAN) and served (cloud-impersonation): the SwitchBot
AI Art Frame is reached by **pushing to SwitchBot's own signed cloud API** by ``deviceId``
(``uploadImage``). We *initiate* delivery — so to the UI/registry it's ``interaction="connected"`` —
but there is no LAN session to open and no server the frame polls. Two pieces sit outside this thin
descriptor:

- discovery is **account-scoped** (``SwitchBotClient.art_frames``), driven by ``SwitchBotService``,
  not the LAN ``discover()`` hook (which returns ``[]``);
- delivery prepares the photo to the panel's native **480x800 portrait** and calls
  ``upload_image_bytes`` — see ``delivery_service``; the per-frame processing profile is built in
  ``processing.profile_for`` (a plain JPEG: SwitchBot's cloud renders it to the Spectra-6 panel).

Credentials come from ``Settings`` (``SWITCHBOT_TOKEN`` / ``SWITCHBOT_SECRET``) — never hardcoded.
"""

from __future__ import annotations

from memento_core import FrameInfo, Ports

from .base import FrameBackend, FrameCapabilities

# The 7.3" Spectra-6 panel is 480x800 PORTRAIT. SwitchBot's cloud center-crops uploads to fill
# (cover), so Slyde pre-fits each photo to this canvas (FRAME_FIT smart) before upload — the cloud's
# crop is then a no-op. (Live-validated; see experiments/switchbot-frame/FINDINGS.md.)
SWITCHBOT_CANVAS = (480, 800)


class SwitchBotBackend(FrameBackend):
    """The SwitchBot AI Art Frame, driven through SwitchBot's official signed cloud OpenAPI."""

    name = "switchbot"
    canvas = SWITCHBOT_CANVAS
    capabilities = FrameCapabilities(
        interaction="connected",  # we initiate delivery (push to the vendor cloud), not polled
        transport="cloud",
        color_model="epaper",  # 7.3" Spectra-6 e-paper; the cloud renders our JPEG onto the panel
        discovery=False,  # not LAN-discoverable; frames are listed from the SwitchBot account
        albums=False,
        thumbnails=False,
        upload=True,
        delete=False,  # the cloud API exposes no per-image delete (only next/previous + upload)
        ota=False,
    )

    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list[FrameInfo]:
        # SwitchBot frames aren't on the LAN; they're enumerated from the account over the cloud API
        # (SwitchBotService.discover_frames), not via this LAN-broadcast discovery hook.
        return []
