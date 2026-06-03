"""Album data file parse/serialize (the frame's AlbumData.json format)."""

from __future__ import annotations

from memento_core.albums import ALBUM_PHOTOS, Album, AlbumData, parse_album_data


def test_round_trip() -> None:
    data = AlbumData()
    data.add_album(ALBUM_PHOTOS)
    data.add_image(ALBUM_PHOTOS, "a.jpg")
    data.add_album("Trip")
    data.add_image("Trip", "a.jpg")
    back = parse_album_data(data.to_json())
    assert back.names() == [ALBUM_PHOTOS, "Trip"]
    trip = back.get("Trip")
    assert trip is not None and trip.images == ["a.jpg"]


def test_parse_indexed_keys_and_empty_album() -> None:
    text = (
        '{"AlbumName_0":"Photos_$%^&(*@#!","ImageName_0":["x.jpg"],'
        '"AlbumName_1":"Trip","ImageName_1":[]}'
    )
    data = parse_album_data(text)
    assert len(data.albums) == 2
    assert data.albums[1].name == "Trip" and data.albums[1].images == []


def test_reserved_and_display_name() -> None:
    photos = Album(ALBUM_PHOTOS)
    assert photos.reserved and photos.display_name == "Photos"
    assert not Album("Trip").reserved
    assert Album("Trip").display_name == "Trip"


def test_add_image_dedupes() -> None:
    data = AlbumData()
    data.add_image("A", "x.jpg")
    data.add_image("A", "x.jpg")
    album = data.get("A")
    assert album is not None and album.images == ["x.jpg"]
