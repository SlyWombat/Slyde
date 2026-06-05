"""The emulator CLI's firmware-version reporting (soft-frame reports its own version)."""

from __future__ import annotations

from pathlib import Path

from memento_emulator import __version__
from memento_emulator.cli import _bundle_version


def test_bundle_version_absent_falls_back_to_package_version() -> None:
    # No staged OTA bundle -> the soft-frame reports its own package version.
    assert _bundle_version(None) is None
    assert (_bundle_version(None) or __version__) == __version__


def test_bundle_version_reads_staged_bundle(tmp_path: Path) -> None:
    # Running from a staged OTA bundle -> that bundle's VERSION wins over the package version.
    (tmp_path / "VERSION").write_text("9.9.9\n")
    assert _bundle_version(tmp_path) == "9.9.9"
    assert (_bundle_version(tmp_path) or __version__) == "9.9.9"


def test_bundle_version_ignores_empty_version_file(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("   \n")
    assert _bundle_version(tmp_path) is None
    assert (_bundle_version(tmp_path) or __version__) == __version__
