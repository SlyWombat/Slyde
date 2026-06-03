"""In-memory state for the emulated frame: config, photo store, albums.

Mirrors the real frame's behaviour: on receiving a photo the device generates a 256x170 PNG
thumbnail (Albums.THUMBNAIL_SIZE_X/Y) and serves it via GetThumbnails. The emulator does the same.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from memento_core.albums import ALBUM_PHOTOS, AlbumData, parse_album_data
from memento_core.client import image_to_thumb, thumb_to_image
from memento_core.protocol import JsonDict

# The frame's thumbnail size (Albums.THUMBNAIL_SIZE_X / THUMBNAIL_SIZE_Y).
THUMBNAIL_SIZE = (256, 170)

# Fallback 1x1 PNG for inputs that aren't decodable images (e.g. synthetic test bytes).
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _make_thumbnail(data: bytes) -> bytes:
    """Generate a 256x170 PNG thumbnail from image bytes, as the real frame does."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            oriented = ImageOps.exif_transpose(img) or img
            fitted = ImageOps.contain(oriented.convert("RGB"), THUMBNAIL_SIZE)
            canvas = Image.new("RGB", THUMBNAIL_SIZE, (0, 0, 0))
            canvas.paste(
                fitted,
                ((THUMBNAIL_SIZE[0] - fitted.width) // 2, (THUMBNAIL_SIZE[1] - fitted.height) // 2),
            )
            out = io.BytesIO()
            canvas.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return _TINY_PNG


# Modelled on a real firmware-6.02 GetConfig response. Wi-Fi values are placeholders — the real
# device leaks its actual credentials here, which is exactly why this emulator uses fakes.
DEFAULT_CONFIG: JsonDict = {
    "Name": "Test Frame",
    "DisplayOn": True,
    "IsAway": False,
    "NightModeOn": False,
    "ShuffleOn": False,
    "PortraitMode": False,
    "DisplayTime": 60,
    "LightSensor": [20000, 12000, 2000, 1000, 500, 250, 100, 50, 20, 1, 0],
    "Brightness": [160, 140, 90, 75, 55, 40, 28, 1, -100, -180, -255],
    "OffThresholdOffset": 0,
    "CalibrationTableName": "Standard",
    "ContrastOffset": 0,
    "ExposureOffset": 0,
    "SaturationOffset": 0,
    "TemperatureOffset": 0,
    "AwayDay": None,
    "AwayOffTime": None,
    "AwayOnTime": None,
    "AwayEnable": None,
    "SoftwareVersion": 6.02,
    "HardwareVersion": 1,
    "ScreenSize": 35,
    "Width": 3240,
    "Height": 2160,
    "Orientation": "Landscape",
    "WiFiSSID": "emulator-wifi",
    "WiFiPSWD": "placeholder",
    "TimeZoneName": "(UTC-05:00) Eastern Time (US & Canada)",
    "SideBars": 0,
    "SideBarsColor": 0,
    "GUID": "00000000-0000-0000-0000-000000000000",
}


@dataclass
class FrameState:
    """Thread-safe state for one emulated frame."""

    name: str = "Test Frame"
    mac: str = "02:00:00:00:00:01"
    ip: str = "127.0.0.1"
    config: JsonDict = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    photos: dict[str, bytes] = field(default_factory=dict)
    thumbnails: dict[str, bytes] = field(default_factory=dict)
    albums: AlbumData = field(default_factory=AlbumData)
    current_album: str = ALBUM_PHOTOS
    current_image: str = ""
    # When set, config/photos/albums are persisted here so they survive a restart (like a real
    # frame's NVRAM/flash). When None the frame is purely in-memory (tests/ephemeral dev).
    data_dir: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.data_dir is not None:
            self.data_dir = Path(self.data_dir)
        if self.data_dir is not None and self._load():
            self.name = str(self.config.get("Name", self.name))
            return
        self.config["Name"] = self.name
        self.config["GUID"] = self.config.get("GUID", DEFAULT_CONFIG["GUID"])
        # The frame always keeps the reserved "Photos" album holding the full library.
        self.albums.add_album(ALBUM_PHOTOS)
        self._save()

    # -- photos ---------------------------------------------------------------
    def add_photo(self, name: str, data: bytes) -> None:
        thumb = _make_thumbnail(data)  # generated outside the lock (matches the real frame)
        with self._lock:
            key = name.lower()
            self.photos[key] = data
            self.thumbnails[key] = thumb
            self.albums.add_image(ALBUM_PHOTOS, key)
            if not self.current_image:
                self.current_image = key
        self._write_photo(key, data)
        self._save()

    def remove_photo(self, name: str) -> bool:
        with self._lock:
            key = name.lower()
            for album in self.albums.albums:
                if key in album.images:
                    album.images.remove(key)
            self.thumbnails.pop(key, None)
            existed = self.photos.pop(key, None) is not None
            if key == self.current_image:
                photos = self.albums.get(self.current_album)
                self.current_image = next(iter(photos.images), "") if photos else ""
        self._delete_photo_file(key)
        self._save()
        return existed

    def advance(self, *, shuffle: bool = False, step: int = 1) -> str | None:
        """Move to the next/previous image in the current album (the slideshow tick)."""
        with self._lock:
            album = self.albums.get(self.current_album) or self.albums.get(ALBUM_PHOTOS)
            names = list(album.images) if album else sorted(self.photos)
            if not names:
                self.current_image = ""
            elif shuffle and len(names) > 1:
                self.current_image = random.choice(
                    [n for n in names if n != self.current_image]
                )
            else:
                try:
                    i = names.index(self.current_image)
                except ValueError:
                    i = -step
                self.current_image = names[(i + step) % len(names)]
            current = self.current_image
        self._save()
        return current or None

    def photo_names(self) -> list[str]:
        with self._lock:
            return sorted(self.photos)

    # -- albums & thumbnails --------------------------------------------------
    def set_albums(self, album_data: AlbumData) -> None:
        with self._lock:
            self.albums = album_data
        self._save()

    def thumbnails_list_text(self) -> str:
        version = self.config.get("SoftwareVersion", "6.02")
        with self._lock:
            lines = [f"Memento Version {version}"]
            for name in sorted(self.photos):
                md5 = hashlib.md5(self.thumbnails.get(name, b"")).hexdigest()
                lines.append(f"{image_to_thumb(name)}|{md5}")
        return "\n".join(lines)

    def thumbnail_for(self, thumb_filename: str) -> bytes:
        image = thumb_to_image(thumb_filename).lower()
        with self._lock:
            return self.thumbnails.get(image, _TINY_PNG)

    # -- config ---------------------------------------------------------------
    def update_config(self, patch: JsonDict) -> None:
        with self._lock:
            self.config.update(patch)
        self._save()

    # -- persistence (survives a restart, like the real frame's flash) --------
    def _photo_path(self, key: str) -> Path:
        assert self.data_dir is not None
        return self.data_dir / "photos" / f"{hashlib.sha1(key.encode()).hexdigest()}.bin"

    def _write_photo(self, key: str, data: bytes) -> None:
        if self.data_dir is None:
            return
        path = self._photo_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _delete_photo_file(self, key: str) -> None:
        if self.data_dir is None:
            return
        self._photo_path(key).unlink(missing_ok=True)

    def _save(self) -> None:
        if self.data_dir is None:
            return
        with self._lock:
            snapshot = json.dumps(
                {
                    "config": self.config,
                    "albums": self.albums.to_json(),
                    "current_album": self.current_album,
                    "current_image": self.current_image,
                    "photos": sorted(self.photos),
                }
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.data_dir / "state.json.tmp"
        tmp.write_text(snapshot)
        tmp.replace(self.data_dir / "state.json")

    def _load(self) -> bool:
        assert self.data_dir is not None
        path = self.data_dir / "state.json"
        if not path.exists():
            return False
        snapshot = json.loads(path.read_text())
        self.config = {**DEFAULT_CONFIG, **snapshot.get("config", {})}
        albums_json = snapshot.get("albums")
        self.albums = parse_album_data(albums_json) if albums_json else AlbumData()
        if self.albums.get(ALBUM_PHOTOS) is None:
            self.albums.add_album(ALBUM_PHOTOS)
        for key in snapshot.get("photos", []):
            photo_file = self._photo_path(key)
            if photo_file.exists():
                data = photo_file.read_bytes()
                self.photos[key] = data
                self.thumbnails[key] = _make_thumbnail(data)
        self.current_album = snapshot.get("current_album", ALBUM_PHOTOS)
        self.current_image = snapshot.get("current_image", "")
        return True

    def discovery_info(self) -> JsonDict:
        with self._lock:
            return {
                "name": self.name,
                "softver": str(self.config["SoftwareVersion"]),
                "hardver": str(self.config["HardwareVersion"]),
                "size": str(self.config["ScreenSize"]),
                "orientation": self.config["Orientation"],
                "ip": self.ip,
                "mac": self.mac,
                "guid": self.config["GUID"],
                "IsConnected": False,
                "TryAndBuyMode": False,
                "ServerImageDownload": False,
                "hasInternet": True,
            }
