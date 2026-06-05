"""Runtime configuration (12-factor). Nothing deployment-specific is baked in here."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment (or an ``.env`` file)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # Frame -------------------------------------------------------------------
    frame_backend: str = Field(
        "memento-lan",
        description="Which frame backend to drive: memento-lan (default) | sungale-cloud",
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

    # Immich ------------------------------------------------------------------
    immich_base_url: str = Field(
        "", description="Immich instance base URL, e.g. http://immich:2283"
    )
    immich_api_key: str = Field("", description="Immich API key", repr=False)
    immich_asset_size: str = Field("preview", description="Immich thumbnail size or 'original'")
    sync_interval_minutes: int = Field(
        15, description="How often to re-mirror kept-in-sync albums (0 disables the scheduler)"
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


def get_settings() -> Settings:
    return Settings()
