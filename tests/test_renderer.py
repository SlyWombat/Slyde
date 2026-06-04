"""Pure geometry/orientation helpers of the display renderer (no pygame needed)."""

from __future__ import annotations

from memento_emulator.renderer import effective_canvas, fit_size, is_portrait


def test_effective_canvas_landscape_and_portrait() -> None:
    assert effective_canvas((1920, 1080), portrait=False) == (1920, 1080)
    assert effective_canvas((1920, 1080), portrait=True) == (1080, 1920)
    # Order of the native tuple doesn't matter — orientation decides which is wider.
    assert effective_canvas((1080, 1920), portrait=False) == (1920, 1080)


def test_fit_size_contains_preserving_aspect() -> None:
    assert fit_size((1000, 500), (800, 800)) == (800, 400)  # width-bound
    assert fit_size((500, 1000), (800, 800)) == (400, 800)  # height-bound
    assert fit_size((800, 600), (800, 600)) == (800, 600)  # exact
    assert fit_size((0, 0), (640, 480)) == (640, 480)  # degenerate -> screen


def test_is_portrait_reads_orientation_or_flag() -> None:
    assert is_portrait({"Orientation": "Portrait"}) is True
    assert is_portrait({"PortraitMode": True}) is True
    assert is_portrait({"Orientation": "Landscape"}) is False
    assert is_portrait({}) is False
