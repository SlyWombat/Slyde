"""In-memory state for the emulated frame: config, photo store, albums."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from memento_core.protocol import JsonDict

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
    current_album: str = "Default"
    current_image: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.config["Name"] = self.name
        self.config["GUID"] = self.config.get("GUID", DEFAULT_CONFIG["GUID"])

    # -- photos ---------------------------------------------------------------
    def add_photo(self, name: str, data: bytes) -> None:
        with self._lock:
            self.photos[name.lower()] = data
            if not self.current_image:
                self.current_image = name.lower()

    def remove_photo(self, name: str) -> bool:
        with self._lock:
            return self.photos.pop(name.lower(), None) is not None

    def photo_names(self) -> list[str]:
        with self._lock:
            return sorted(self.photos)

    # -- config ---------------------------------------------------------------
    def update_config(self, patch: JsonDict) -> None:
        with self._lock:
            self.config.update(patch)

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
