"""Backend settings helpers."""

from __future__ import annotations

from slyde_backend.config import Settings


def test_configured_hosts_merges_and_dedupes() -> None:
    s = Settings(frame_host="192.168.1.5", frame_hosts="192.168.1.5, 10.0.0.9 ,")
    assert s.configured_hosts == ["192.168.1.5", "10.0.0.9"]


def test_configured_hosts_empty() -> None:
    assert Settings(frame_host="", frame_hosts="").configured_hosts == []


def test_canvas_parses() -> None:
    assert Settings(frame_canvas="3240x2160").canvas == (3240, 2160)
