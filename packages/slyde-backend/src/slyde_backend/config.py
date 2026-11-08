"""Runtime configuration (12-factor). Nothing deployment-specific is baked in here."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment (or an ``.env`` file)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # Frame -------------------------------------------------------------------
    frame_backend: str = Field(
        "memento-lan",
        description="The primary frame backend to drive: memento-lan (default) | sungale-cloud "
        "| switchbot",
    )
    frame_served_backends: str = Field(
        "",
        description="Comma-separated served (cloud) backends to ALSO mount alongside the primary, "
        "so one hub drives both a connected frame and a polled one (e.g. 'sungale-cloud')",
    )
    frame_host: str = Field("", description="Explicit frame IP/host; empty enables discovery")
    frame_hosts: str = Field(
        "", description="Comma-separated extra frame hosts to always list (e.g. an emulator)"
    )
    frame_discovery: bool = Field(True, description="Use UDP broadcast discovery when no host set")
    frame_canvas: str = Field("3240x2160", description="Target image size WxH")
    frame_fit: Literal["contain", "cover", "blur", "smart"] = Field(
        "smart", description="Fit images to the canvas: contain|cover|blur|smart"
    )
    frame_crop_tolerance: float = Field(
        0.12,
        ge=0.0,
        le=1.0,
        description="Smart mode: crop if <= this fraction of the long edge is lost, else blur-fill",
    )

    # Interlude: a recurring non-photo image shown between slideshow photos (#70) ------------
    interlude_dir: str = Field(
        "",
        description="Directory holding each frame's interlude image, so a SEPARATE PROCESS can "
        "update what the frame shows just by writing a file. Defaults to <CACHE_DIR>/interlude. "
        "Slyde reads '<dir>/<frame-id>.img' (any common image format); delete the file and the "
        "frame goes straight back to its normal slideshow.",
    )
    interlude_poll_seconds: float = Field(
        5.0,
        gt=0,
        description="How often the conductor re-checks whether a frame's interlude image exists "
        "and has changed. Also the granularity at which a REMOVED image is noticed, so the frame "
        "returns to normal rotation within about this long.",
    )
    interlude_park_seconds: int = Field(
        0,
        ge=0,
        description="What the frame's own slide timer is parked at while Slyde conducts the "
        "rotation. 0 (default) computes a finite value from the slide/dwell time, which makes the "
        "frame's own timer a DEAD-MAN SWITCH: Slyde's transitions keep re-arming it so it never "
        "fires, but if Slyde dies the frame resumes its own slideshow instead of holding one "
        "picture. (Verified on fw 6.02: DisplayImage re-arms the countdown, and the frame honours "
        "arbitrary second values, not just the app picker's 15 rungs.) Set 2419200 for the old "
        "behaviour -- the frame holds the last image until Slyde restarts and restores it.",
    )
    interlude_flap_threshold: int = Field(
        2,
        ge=1,
        description="Consecutive polls agreeing the interlude image is present (or absent) before "
        "the conductor engages or stands down. Stops a process that rewrites its image in place "
        "from driving repeated engage/stand-down cycles.",
    )
    interlude_fetch_timeout: float = Field(
        10.0, gt=0, description="Timeout for fetching a url-sourced interlude image"
    )

    # SwitchBot AI Art Frame (#64) — driven via SwitchBot's official signed cloud OpenAPI
    # or Home Assistant's switchbot_art_frame integration (BLE wake + 10-slot FIFO delete).
    switchbot_token: str = Field(
        "", description="SwitchBot OpenAPI token (for the 'switchbot' backend)", repr=False
    )
    switchbot_secret: str = Field(
        "", description="SwitchBot OpenAPI secret (for the 'switchbot' backend)", repr=False
    )
    switchbot_device_id: str = Field(
        "",
        description="Optional explicit SwitchBot AI Art Frame deviceId/MAC to register when using "
        "ha_service without OpenAPI account discovery",
    )
    switchbot_frame_size: Literal["7.3", "13.3", "31.5"] = Field(
        "7.3",
        description="SwitchBot AI Art Frame size in inches: 7.3 (480x800) | 13.3 (1200x1600) | "
        "31.5 (1440x2560)",
    )
    switchbot_orientation: Literal["portrait", "landscape"] = Field(
        "portrait",
        description="SwitchBot AI Art Frame panel orientation: portrait | landscape",
    )
    switchbot_max_image_bytes: int = Field(
        650_000,
        ge=0,
        description="Maximum JPEG payload size in bytes for SwitchBot uploads (650 KB stays "
        "safely under SwitchBot's ~1 MiB base64 body limit; 0 disables the cap)",
    )
    switchbot_slideshow: Literal["off", "slyde", "frame"] = Field(
        "off",
        description="Who runs the slideshow over a SwitchBot frame's library: 'off' (deliver every "
        "photo, the classic behaviour) | 'slyde' (Slyde pushes ONE new photo every interval, never "
        "repeating one before the whole library was shown; set the frame to a static "
        "image in the SwitchBot app) | 'frame' (Slyde swaps a batch of 10 photos every interval "
        "and the frame's own slideshow cycles them; with ha_service the photos are stored without "
        "being shown, which needs ha-switchbot-art-frame with show_image's 'show' option)",
    )
    switchbot_slideshow_interval_minutes: int = Field(
        60,
        ge=1,
        description="'slyde': minutes between photo changes; 'frame': minutes between swapping "
        "in the next batch of 10",
    )
    switchbot_quiet_hours: str = Field(
        "",
        pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$",
        description="Local-time window with no slideshow changes, e.g. 22:00-07:00 (saves the "
        "frame's battery overnight; empty = none). 'Next photo' in the UI still works.",
    )
    switchbot_presence_entity: str = Field(
        "",
        pattern=r"^$|^[a-z_]+\.[a-z0-9_]+$",
        description="Home Assistant entity that says someone is home (zone.home, a person, a "
        "group, binary_sensor or input_boolean): the slideshow only changes while it reads "
        "home/on or a count above 0. Empty = always.",
    )
    switchbot_delivery_mode: Literal["cloud_only", "ha_service"] = Field(
        "cloud_only",
        description="How Slyde pushes to a SwitchBot frame: 'cloud_only' (SwitchBot OpenAPI "
        "uploadImage) or 'ha_service' (Home Assistant switchbot_art_frame.show_image for BLE wake "
        "+ 10-slot FIFO delete)",
    )
    ha_base_url: str = Field(
        "",
        description="Home Assistant base URL (e.g. http://homeassistant.local:8123; defaults to "
        "http://supervisor/core when running as a Home Assistant Add-on with SUPERVISOR_TOKEN)",
    )
    ha_token: str = Field(
        "",
        description="Home Assistant Long-Lived Access Token (falls back to SUPERVISOR_TOKEN when "
        "running as a Home Assistant Add-on)",
        repr=False,
    )
    ha_art_frame_entity_id: str = Field(
        "",
        pattern=r"^(sensor\.[a-z0-9_]+)?$",
        description="Home Assistant entity_id of the ha-switchbot-art-frame local images sensor "
        "(e.g. sensor.art_frame_local_images)",
    )

    # Immich ------------------------------------------------------------------
    immich_base_url: str = Field(
        "", description="Immich instance base URL, e.g. http://immich:2283"
    )
    immich_api_key: str = Field("", description="Immich API key", repr=False)
    immich_asset_size: str = Field("preview", description="Immich thumbnail size or 'original'")
    sync_interval_minutes: int = Field(
        15, description="How often to re-mirror kept-in-sync albums (0 disables the scheduler)"
    )
    delivery_interval_seconds: int = Field(
        20,
        description="How often the backend drains the delivery queue to frames, decoupled from "
        "album re-mirroring so a fresh curation syncs in seconds, not minutes (0 disables)",
    )
    frame_scan_cidr: str = Field(
        "",
        description="LAN subnet (CIDR) the manual 'Scan' button probes for frames; derived as a "
        "/24 from a known frame / FRAME_HOST when blank. Manual-only.",
    )
    frame_settle_delay: float = Field(
        0.3,
        ge=0,
        description="Seconds to pause after each control-protocol op to a connected frame. Control "
        "ops to one frame are serialized; this paces them so we don't overload a low-power frame "
        "(some stop answering under concurrent/rapid requests). 0 disables.",
    )
    frame_quick_timeout: float = Field(
        3.5,
        gt=0,
        description="Seconds a quick UI read (current image / a single thumbnail) waits before it "
        "fails fast as offline. Bounds both control + transfer channels and SKIPS DHCP "
        "re-discovery, so a slow/unresponsive frame can't hang the overview or hold the per-frame "
        "lock for minutes (#68).",
    )
    frame_warm_sessions: bool = Field(
        True,
        description="Hold ONE kept-warm control session per connected frame and run every op on "
        "it, instead of opening a session per op. A real Memento frame services a NEW connection "
        "only once per ~21s tick, so a per-op connect costs up to ~21s and blows any sane timeout; "
        "on a warm session the same ops answer in well under a second (#71).",
    )
    frame_connect_timeout: float = Field(
        60.0,
        gt=0,
        description="Seconds a warm session may spend claiming the frame's service tick (its first "
        "reply after connecting) before it's treated as unreachable. Must exceed two ticks (~42s) "
        "so a missed tick doesn't look like an offline frame. Also bounds that session's ops "
        "(#71).",
    )
    frame_warm_idle_ttl: float = Field(
        180.0,
        ge=0,
        description="Seconds without an op before a warm session is released, freeing the frame's "
        "single client slot (it starves a second client) for e.g. the vendor phone app. 0 keeps "
        "the session for as long as the process runs (#71).",
    )
    frame_warm_retry_delay: float = Field(
        5.0,
        ge=0,
        description="Seconds to wait before reconnecting a warm session whose connect attempt "
        "failed, so an offline frame isn't hammered (#71).",
    )
    thumb_match_max_distance: int = Field(
        14,
        ge=0,
        le=64,
        description="How many bits (of 64) two image fingerprints may differ and still count as "
        "the same photo, when matching a frame's thumbnail to an Immich asset (#72). Higher is "
        "more permissive; the frame's thumbnail is a rescaled re-encode, so it is never identical.",
    )
    thumb_match_min_margin: int = Field(
        6,
        ge=0,
        le=64,
        description="How far clear of the runner-up a thumbnail match must be to be trusted. This "
        "is what stops near-identical burst frames resolving to a coin flip — without a clear "
        "winner the photo is left unlinked rather than guessed at (#72).",
    )
    thumb_match_candidates: int = Field(
        25,
        ge=1,
        description="Most Immich assets to compare a frame thumbnail against per photo (#72).",
    )
    current_preview_interval_seconds: int = Field(
        60,
        ge=0,
        description="How often a background refresh quick-fetches each connected frame's current "
        "image and caches it as that frame's preview, so the overview renders it from cached bytes "
        "with no live call. 0 disables the refresh.",
    )
    frame_import_delay: float = Field(
        0.5,
        ge=0,
        description="Extra seconds to pause between images when pulling a frame's existing photos "
        "into the library — on top of serialization, to stay gentle on the device.",
    )
    sync_chunk_size: int = Field(
        16,
        ge=1,
        description="Folder sync prepares + uploads photos in chunks of this many, so a large "
        "album streams with bounded memory (not buffering every prepared image at once)",
    )

    # Firmware / app updates --------------------------------------------------
    firmware_repo: str = Field(
        "", description="GitHub owner/repo whose releases hold soft-frame update bundles"
    )
    firmware_track: str = Field(
        "memento-softframe", description="Release asset prefix / device track to update"
    )
    firmware_github_token: str = Field(
        "",
        description="GitHub token for release checks (required if the repo is private)",
        repr=False,
    )
    manager_base_url: str = Field(
        "", description="Frame-reachable base URL of this manager (for update serve URLs)"
    )

    # Service -----------------------------------------------------------------
    database_url: str = Field("sqlite:///./memento.db", description="State store URL")
    cache_dir: str = Field(
        "./cache",
        description="Where prepared (edited) frame images are cached, ready to serve/push",
    )
    bind_host: str = Field("0.0.0.0", description="API bind host")
    bind_port: int = Field(8080, description="API bind port")
    static_dir: str = Field("", description="Built SPA directory to serve, if any")
    log_level: str = Field("INFO", description="Logging level")

    @property
    def configured_hosts(self) -> list[str]:
        """Explicit frame hosts to always include in the picker (FRAME_HOST + FRAME_HOSTS)."""
        raw = [self.frame_host, *self.frame_hosts.split(",")]
        out: list[str] = []
        for host in (h.strip() for h in raw):
            if host and host not in out:
                out.append(host)
        return out

    @property
    def served_backend_names(self) -> list[str]:
        """Served backends to mount in addition to the primary (FRAME_SERVED_BACKENDS), de-duped."""
        out: list[str] = []
        for name in (n.strip() for n in self.frame_served_backends.split(",")):
            if name and name not in out:
                out.append(name)
        return out

    @property
    def canvas(self) -> tuple[int, int]:
        w, _, h = self.frame_canvas.lower().partition("x")
        return int(w), int(h)

    @property
    def sqlite_path(self) -> str:
        """Filesystem path from a ``sqlite:///`` URL (the only scheme supported today)."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError(f"unsupported DATABASE_URL: {self.database_url!r}")
        return self.database_url[len(prefix) :]

    @property
    def shuffle_batch_size(self) -> int:
        """Photos per Shuffle-Deck draw on a SwitchBot frame: 1 when Slyde runs the slideshow, the
        frame's 10 local slots when the frame does, 0 (whole album, no shuffle) when off."""
        return {"slyde": 1, "frame": 10}.get(self.switchbot_slideshow, 0)

    @property
    def effective_ha_token(self) -> str:
        """Configured Home Assistant token, or ``SUPERVISOR_TOKEN`` when running as a HA Add-on."""
        return self.ha_token or os.environ.get("SUPERVISOR_TOKEN", "")

    @property
    def effective_ha_base_url(self) -> str:
        """Configured Home Assistant URL, or ``http://supervisor/core`` inside a HA Add-on."""
        if self.ha_base_url:
            return self.ha_base_url.rstrip("/")
        if os.environ.get("SUPERVISOR_TOKEN"):
            return "http://supervisor/core"
        return ""


def load_ha_addon_options(options_path: Path = Path("/data/options.json")) -> None:
    """Populate ``os.environ`` defaults from a Home Assistant Add-on ``/data/options.json`` file.

    Only non-empty values are applied via ``setdefault`` so explicit environment variables win.
    """
    if not options_path.is_file():
        return
    try:
        raw = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    for key, val in raw.items():
        if val is None or val == "":
            continue
        env_key = str(key).upper()
        if isinstance(val, bool):
            os.environ.setdefault(env_key, "true" if val else "false")
        else:
            os.environ.setdefault(env_key, str(val))


def get_settings() -> Settings:
    load_ha_addon_options()
    return Settings()
