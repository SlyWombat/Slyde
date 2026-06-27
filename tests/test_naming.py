"""Canonical on-frame dest_name scheme (#61)."""

from __future__ import annotations

from slyde_backend.naming import dest_name_for, frame_dest_name, upload_dest_name


def test_immich_dest_name_is_readable_deterministic_and_bounded() -> None:
    d = dest_name_for("Beach Trip!.HEIC", "7f3a9c20-1d4e-4a8b")
    assert d == "beach-trip-7f3a9c20.jpg"  # readable slug + 8-char asset-id prefix
    assert dest_name_for("x.jpg", "abc") == dest_name_for("x.jpg", "abc")  # deterministic
    assert len(dest_name_for("z" * 200, "deadbeef")) <= 64  # bounded to the frame filename limit
    assert dest_name_for("", "id12") == "photo-id12.jpg"  # empty name falls back to 'photo'


def test_source_namespaces_are_distinct() -> None:
    assert upload_dest_name("1782575143209386805") == "up-1782575143209386805.jpg"
    assert frame_dest_name("IMG_1234.JPG") == "img_1234.jpg"  # device filename, verbatim/lowercased
