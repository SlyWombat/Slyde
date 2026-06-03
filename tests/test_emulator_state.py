"""FrameState slideshow (advance) and on-disk persistence."""

from __future__ import annotations

import io

from PIL import Image

from memento_emulator import FrameState


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _seed(state: FrameState) -> list[str]:
    names = ["a.jpg", "b.jpg", "c.jpg"]
    for i, name in enumerate(names):
        state.add_photo(name, _png((i * 40, 0, 0)))
    return names


def test_advance_sequential_wraps() -> None:
    state = FrameState()
    _seed(state)
    state.current_image = "a.jpg"
    assert state.advance() == "b.jpg"
    assert state.advance() == "c.jpg"
    assert state.advance() == "a.jpg"  # wraps
    assert state.advance(step=-1) == "c.jpg"  # previous wraps backwards


def test_advance_shuffle_picks_a_different_image() -> None:
    state = FrameState()
    _seed(state)
    state.current_image = "a.jpg"
    # Shuffle never repeats the current image.
    for _ in range(20):
        nxt = state.advance(shuffle=True)
        assert nxt != "a.jpg"
        state.current_image = "a.jpg"


def test_advance_with_no_photos_is_safe() -> None:
    assert FrameState().advance() is None


def test_state_persists_across_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = FrameState(name="Frame", data_dir=tmp_path)
    _seed(first)
    first.update_config({"DisplayTime": 5, "ShuffleOn": True})
    first.current_image = "b.jpg"
    first._save()

    # A fresh instance pointed at the same dir restores config, photos and the current image.
    second = FrameState(name="ignored-when-loaded", data_dir=tmp_path)
    assert second.config["DisplayTime"] == 5
    assert second.config["ShuffleOn"] is True
    assert second.config["Name"] == "Frame"
    assert set(second.photo_names()) == {"a.jpg", "b.jpg", "c.jpg"}
    assert second.current_image == "b.jpg"
    assert "a.jpg" in second.thumbnails  # thumbnails are regenerated on load


def test_removed_photo_is_dropped_from_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    state = FrameState(data_dir=tmp_path)
    _seed(state)
    assert state.remove_photo("b.jpg") is True
    restored = FrameState(data_dir=tmp_path)
    assert "b.jpg" not in restored.photo_names()
