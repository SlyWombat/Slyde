"""The frame's album data file (``AlbumData.json``), as produced by ``Cadre.Albums``.

Format: a flat JSON object with indexed keys, e.g.::

    {"AlbumName_0": "Photos_$%^&(*@#!", "ImageName_0": ["a.jpg", "b.jpg"],
     "AlbumName_1": "Holidays",         "ImageName_1": ["c.jpg"]}

Images are referenced by filename; the same file may appear in several albums. The frame keeps
three reserved albums (Photos / Evening / Remote); "Photos" holds the full library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Reserved album names (suffix is Albums.ms_AlbumsReservedKeys).
ALBUM_PHOTOS = "Photos_$%^&(*@#!"
ALBUM_EVENING = "Evening_$%^&(*@#!"
ALBUM_REMOTE = "Remote_$%^&(*@#!"
RESERVED = (ALBUM_PHOTOS, ALBUM_EVENING, ALBUM_REMOTE)

MAX_ALBUMS = 67
MAX_IMAGES = 3000


@dataclass
class Album:
    name: str
    images: list[str] = field(default_factory=list)

    @property
    def reserved(self) -> bool:
        return self.name in RESERVED

    @property
    def display_name(self) -> str:
        """The reserved suffix stripped, for showing in a UI."""
        return self.name.split("_$%^&(*@#!", 1)[0] if self.reserved else self.name


@dataclass
class AlbumData:
    albums: list[Album] = field(default_factory=list)

    def get(self, name: str) -> Album | None:
        return next((a for a in self.albums if a.name == name), None)

    def names(self) -> list[str]:
        return [a.name for a in self.albums]

    def add_album(self, name: str) -> Album:
        existing = self.get(name)
        if existing:
            return existing
        album = Album(name=name)
        self.albums.append(album)
        return album

    def add_image(self, album_name: str, filename: str) -> None:
        album = self.get(album_name) or self.add_album(album_name)
        if filename not in album.images:
            album.images.append(filename)

    def remove_album(self, name: str) -> bool:
        """Remove a (non-reserved) album/folder. Returns True if it existed."""
        before = len(self.albums)
        self.albums = [a for a in self.albums if not (a.name == name and not a.reserved)]
        return len(self.albums) != before

    def remove_image(self, album_name: str, filename: str) -> None:
        album = self.get(album_name)
        if album and filename in album.images:
            album.images.remove(filename)

    def to_json(self) -> str:
        """Serialize to the ``AlbumData.json`` shape (mirror of GenerateAlbumDataJSON)."""
        obj: dict[str, object] = {}
        for i, album in enumerate(self.albums):
            obj[f"AlbumName_{i}"] = album.name
            obj[f"ImageName_{i}"] = list(album.images)
        return json.dumps(obj)


def parse_album_data(text: str) -> AlbumData:
    obj = json.loads(text)
    data = AlbumData()
    for i in range(MAX_ALBUMS):
        name = obj.get(f"AlbumName_{i}")
        if name is None:
            continue
        images = [str(x) for x in obj.get(f"ImageName_{i}", []) if x]
        data.albums.append(Album(name=str(name), images=images))
    return data
